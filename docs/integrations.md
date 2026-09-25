# Integrations & data exchange (V9)

> **Status statement.** MedFlow supports a validated integration framework and controlled data-import/API connectors.
> Production hospital-system integrations require access to the corresponding hospital/vendor systems and have not been
> claimed unless actually tested.
>
> Nothing in this repository has been connected to a real hospital ERP, inventory, procurement or HIS system. The
> end-to-end REST tests run against MedFlow's own **reference ERP simulator** (§14), which is labelled *simulated*
> everywhere it appears.

## 1. Purpose

Up to V8, data entered MedFlow by hand (receipts, issues, counts), from the V8 onboarding CSV import, or from the demo
seed. A hospital that already runs an ERP / stores system / procurement system should not have to key the same data
twice. V9 is the layer that **receives operational data from those systems, checks it, and hands it to the existing
MedFlow services**.

**Rule: no forecasting, risk, supplier-intelligence or procurement algorithm was changed.** V9 only brings better data in.

## 2. Architecture

```text
Hospital systems                          MedFlow
──────────────────                        ─────────────────────────────────────────────────────────────────────────
ERP / inventory / procurement ─┐
CSV / Excel files ─────────────┼─► connector ─► mapping ─► validation ─► idempotency ─► existing services ─► PostgreSQL
REST API (pushed to MedFlow) ──┤   upload        source     types, dates, external_refs   stock ledger          │
REST API (pulled by MedFlow) ──┤   api_push      field →    unknown codes, (same id +      supplier orders       ▼
Scheduled data feed ───────────┘   rest_pull     MedFlow    negatives,     same content   catalogue       V2 forecasts
                                                 field      duplicates     = unchanged)   reconciliation  V3 risk · V4
                                                                                                          V5 · V6 · V7
every exchange → sync_runs (audit trail) · rejected records → sync_records (inspect, retry) · stock differences → reconciliation_issues
```

Code: `backend/app/integrations/` (`entities.py`, `engine.py`, `fields.py`, `readers.py`, `connectors.py`,
`credentials.py`, `reconciliation.py`, `sources.py`, `reference_erp.py`, `demo.py`, `cli.py`), API
`backend/app/api/integrations.py`, page `frontend/app/(app)/integrations/`.

## 3. Where the data goes

| Incoming entity | Applied through | Read by |
|---|---|---|
| consumption | `stock.issue` (FEFO, department) | **V2** daily consumption panel (forecasts), V3 |
| inventory (stock counts) | compared with the ledger → reconciliation; opening balance via `stock.receive` for items with no ledger yet | **V3** stock position (after a person resolves differences) |
| purchase_orders | `supplier_orders.create_order` / `cancel_or_close` | **V4** order log → reliability, **V5** in-transit orders |
| deliveries | `stock.receive` + `supplier_orders.record_delivery` (linked movement and batch) | V1 batches/expiry, **V4** OTIF, V5 |
| suppliers, supplier_items | upsert of `suppliers` / `supplier_products` (price, lead time, MOQ) | **V4 / V5** quoted lead times, prices, MOQ |
| items, departments | upsert of `consumables` / `departments` | everything |
| (all of the above) | — | **V6** graph projection (re-synced from PostgreSQL), **V7** assistant tools |

Retraining (V2/V3) still happens only through the existing training paths — an import never trains a model implicitly.
Alerts and V3 risk are refreshed for the touched items after each run (`alerts.evaluate`), exactly as after a manual movement.

## 4. Concepts

* **Source** (`integration_sources`) — one hospital system, per hospital: name, system type, connector, enabled flag,
  the entities it may send, non-secret configuration, status (last run / last success / last failure / last error).
  Each source acts through its own **service account** ("Integration: <name>"): inactive, no hospital membership, so it
  grants no access; it only appears as the actor of ledger movements.
* **Connector** — `upload` (files), `api_push` (the hospital system calls MedFlow with an API key), `rest_pull` (MedFlow
  calls the hospital system on demand or on a schedule).
* **Entity** — one of the eight record types in §5.
* **Run** (`sync_runs`) — one import / sync of one entity from one source. The audit trail (§12).

## 5. Entities and fields

MedFlow field names (`*` = required). `GET /api/integrations/entities` returns this list with descriptions.

| Entity | Fields | Key (idempotency) |
|---|---|---|
| departments | code\*, name\*, description, active | code |
| suppliers | code\*, name\*, city, email, phone, contact_person, default_lead_time_days, active | code |
| items | code\* (SKU), name\*, unit\*, category (created if missing), unit_cost, reorder_level, max_level, description, active | code |
| supplier_items | supplier_code\*, item_code\*, unit_price\*, lead_time_days, moq, supplier_sku, preferred | supplier/item |
| purchase_orders | po_number\*, supplier_code\*, item_code\*, quantity\*, order_date\*, expected_date, unit_price, status (open / cancelled / closed) | po_number |
| deliveries | external_id\* (GRN), po_number\*, quantity\*, received_at\*, lot_number\*, expiry_date, unit_price, item_code | external_id |
| consumption | external_id\* (transaction id), item_code\*, department_code\*, quantity\*, occurred_at\* | external_id |
| inventory | item_code\*, quantity\*, as_of, lot_number / expiry_date / unit_cost (opening balance only) | item + date |

Every entity also accepts `updated_at` (the incremental-sync cursor, §8). One purchase order = one item (a PO with several
lines is sent as several records with distinct PO numbers, e.g. `4500012345-10`), as in the V4 order log.

## 6. Field mapping

Each source can map its own field names onto MedFlow's, per entity (`integration_mappings`: MedFlow field → source field,
plus per-field defaults and an optional date format such as `%d/%m/%Y`). Unmapped fields use the MedFlow name. Example —
the demo ERP mapping for items:

| Source field | → MedFlow field |
|---|---|
| material_code | item.code |
| material_description | item.name |
| uom | item.unit |
| material_group | item.category |
| std_price | item.unit_cost |
| department_code / cost_center | department.code |
| quantity / qty_on_hand | stock.quantity |
| supplier_code / vendor_code | supplier.code |
| po_number | supplier_order.reference |

Edited on the Integrations page (Sources & mapping) or with `PUT /api/integrations/sources/{id}/mappings/{entity}`.
Unknown MedFlow fields and invalid defaults are rejected (422). Codes are normalised to upper case.

## 7. Validation

Every record is checked before anything is written, and **every** problem in a record is reported (not just the first):

| Reason code | When |
|---|---|
| `missing_field` | a required field is empty (names the source field that was read) |
| `invalid_value` | not a number, code with illegal characters, out of range… |
| `invalid_date` | unparseable, in the future, expected before order date, batch expired before delivery, … |
| `negative_quantity` | a quantity below zero (reversals are not accepted — see §17) |
| `unknown_item` / `unknown_supplier` / `unknown_department` / `unknown_purchase_order` | the code does not exist **in this hospital** |
| `invalid_department` | the department exists but is inactive |
| `duplicate_record` | the same key appears twice in one batch |
| `changed_transaction` | a consumption / delivery already applied is re-sent with different values |
| `out_of_order` | a transaction older than the item's latest ledger movement (the ledger is append-only) |
| `insufficient_stock` | an issue larger than the usable stock |
| `conflict` | e.g. a PO number MedFlow already uses for an order this source did not create; closed orders; price change after delivery |

## 8. Sync engine

For each run (`engine.run_sync`): map + validate all records → sort transactions chronologically → for each record:
in-batch duplicate check → idempotency check → apply **inside a SAVEPOINT** (a failing record never leaves half a
change) → counters. Then: status, error summary, rejected records, checkpoint, alerts/risk refresh, source status, audit row.

* **Initial vs incremental.** A `rest_pull` sync without a checkpoint is *initial* (everything); afterwards *incremental*:
  MedFlow sends `updated_since=<checkpoint>` and stores the largest `updated_at` received as the new checkpoint
  (`sync_checkpoints`). "Full resync" ignores the checkpoint. The cursor is **inclusive** — boundary records come back once
  more and are recognised as unchanged, so equal timestamps are never skipped.
* **Idempotency.** `external_refs` stores, per source/entity/key, the hash of the mapped content and the MedFlow row it
  became. Same key + same content → *unchanged* (nothing written). Master data with changed content → *updated*.
  Transactions (consumption, deliveries) with changed content → *rejected* (`changed_transaction`): the stock ledger is
  append-only; corrections are new transactions.
* **Retry.** Rejected records are kept (`sync_records`: row number, key, raw record, reasons). `POST /runs/{id}/retry`
  re-processes exactly those, as a new run linked to the original (records marked RETRIED). Typical use: add the missing
  item, then retry.
* **Dry run.** Does everything inside an outer savepoint and rolls it back: the run is recorded (mode `dry_run`) with the
  counts that *would* result; no data, reference or checkpoint changes.
* **Order of entities** when a source sends several: departments, suppliers, items, supplier_items, purchase_orders,
  deliveries, consumption, inventory (snapshots last, so they are compared after the transactions).
* **Status.** SUCCESS (nothing rejected), PARTIAL (some rejected), FAILED (all rejected, or the run could not start —
  unreachable system, HTTP 401, bad file; recorded with the reason).
* **Time.** A transaction's time is its `occurred_at` / `received_at`; a bare date of today means "now". A transaction older
  than the item's latest ledger movement is rejected (`out_of_order`) because the ledger's running balance and V2's
  stock-out censoring depend on chronological order.

## 9. Connectors

### 9.1 File upload (CSV / Excel)

`POST /api/integrations/sources/{id}/upload` (multipart: `file`, `entity`, `dry_run`). CSV in UTF-8 with `,` `;` or tab;
Excel `.xlsx` (the sheet named after the entity, else the first sheet). Row 1 = column names; rejected records carry their
spreadsheet line number. Limits: `INTEGRATION_MAX_UPLOAD_MB` (10 MB), `INTEGRATION_MAX_RECORDS_PER_REQUEST` (5,000) per
run. The file's name and SHA-256 are stored with the run. (The V8 onboarding CSV import in Settings still exists for first
set-up; it is all-or-nothing and never overwrites. V9 uploads are per-record and upsert master data.)

### 9.2 API push (hospital system → MedFlow)

```http
POST /api/ingest/v1/{entity}
Authorization: Bearer mfk_<prefix>_<secret>          (or X-API-Key: …)
Content-Type: application/json

{"records": [{"external_id": "TXN-1", "item_code": "GLV-EXM-M", "department_code": "ICU", "quantity": 20,
              "occurred_at": "2026-09-24T09:15:00+05:30"}], "dry_run": false}
```

Response: run id, status, counts, the rejected records with reasons. `GET /api/ingest/v1/status` checks a key.

* **Keys** are created per source by a hospital admin, shown once, stored only as HMAC-SHA256(pepper, key) with the pepper
  from the environment (`INTEGRATION_KEY_PEPPER`), revocable, with last-used time.
* **Tenant** = the key's source's hospital. There is no hospital parameter; a key can only ever write into its own hospital.
* **Checks:** 401 invalid/revoked key; 403 source disabled, not an `api_push` source, entity not allowed, hospital suspended;
  404 unknown entity; 413 more than `INTEGRATION_MAX_RECORDS_PER_REQUEST`; 422 empty body; **429** above
  `INTEGRATION_RATE_LIMIT_PER_MINUTE` per key (sliding minute, `Retry-After` header).

### 9.3 REST pull (MedFlow → hospital system)

Configuration (non-secret, validated): `base_url`, `auth_env` (the **name** of an environment variable holding the
bearer token — `MEDFLOW_INTEGRATION_*` or `REFERENCE_ERP_TOKEN`; the token itself is never stored in the database),
`resources` (entity → path), `page_size`, `schedule_minutes`, `since_param`, `items_key`, `next_key`, `health_path`,
`reconciliation_tolerance`. Contract expected from the remote API:

```text
GET {base_url}/{resource}?page=1&page_size=200&updated_since=<ISO time>  →  {"items": [...], "next_page": 2 | null}
```

Records should be returned in ascending `updated_at`. All pages of an entity are fetched before anything is applied, so
a failure half-way changes nothing (and the checkpoint stays). Transport errors, HTTP 5xx and 429 are retried with
exponential back-off (`INTEGRATION_HTTP_RETRIES`, default 3; `Retry-After` honoured up to 10 s); 401/403/other 4xx fail
at once. **SSRF guard:** `base_url` must be on the operator's `INTEGRATION_ALLOWED_HOSTS` list (empty by default = only the
simulator) — a hospital admin cannot point MedFlow at internal addresses. "Test connection" calls `health_path`.

### 9.4 Scheduled data feeds

`python -m app.integrations.cli run-due` syncs every enabled `rest_pull` source (of active hospitals) whose
`schedule_minutes` has elapsed, one hospital at a time; a failing source is recorded and never stops the others. Docker
Compose runs it every 5 minutes in the `integrations-scheduler` service. Also: `cli sync --source ID [--full]`, `cli list`.

## 10. Reconciliation

An inventory snapshot ("the hospital system says 1,000 gloves") is compared with **MedFlow's ledger balance at the
snapshot time** (`balance_after` of the last movement at or before `as_of`, all batches). If they differ by more than the
source's `reconciliation_tolerance` (default 0) a **reconciliation issue** is created ("1,000 vs 970 → +30"); a later
snapshot updates it, and one that matches closes it (`matched_later`). **MedFlow never corrects stock automatically.** A
person resolves each issue with a note:

| Decision | Effect | Permission |
|---|---|---|
| adjust — MedFlow is wrong | V1 ADJUSTMENT movements for the difference (added to the latest-expiring usable batch, or removed FEFO), reason recorded | `integrations:run` + `stock:receive` |
| external_wrong — the hospital system is wrong | nothing changes in MedFlow | `integrations:run` |
| accept — known difference (timing, unit) | nothing changes | `integrations:run` |

Items that have **no ledger history at all** receive the snapshot as an opening balance (a normal receipt) instead.

## 11. Monitoring dashboard

Integrations → Monitoring (`GET /api/integrations/overview`): connected systems (enabled / configured), health per
source (healthy / degraded / failing / never run / disabled = worst latest run over its entities, plus staleness), last
sync, last success, last failure and last error, **data freshness** (age of the last successful sync; stale after 3×
the schedule, or 24 h without one), records processed / applied / rejected in the last 7 days, open reconciliation issues,
active API keys and the latest run per entity (click for details).

## 12. Audit trail

Every import / sync / push / retry / dry run is a `sync_runs` row with **hospital, source, entity, mode, trigger,
started_at, completed_at, records_received / created / updated / unchanged / rejected, status, error_summary** (fatal
message, reason counts, info such as reconciliation issues opened), checkpoint before/after, file name + SHA-256, parent
run (retries) and who triggered it — plus an `integration.sync` / `integration.dry_run` row in the hospital's audit log.
Configuration changes (`integration.source.create/update`, `integration.mapping.update/reset`, `integration.key.create/revoke`),
pushes (`integration.ingest`, with key prefix and IP) and reconciliation decisions are audited too. Runs & audit tab:
`GET /api/integrations/runs`, `GET /api/integrations/runs/{id}` (with rejected records).

## 13. Security and tenancy

* All integration tables carry `hospital_id`; routes use `require(...)` + `get_owned` (foreign ids → 404); the V8 ORM guard
  and new PostgreSQL triggers (7 tables) reject rows linking two hospitals. The V8 id-route sweep now covers the 16 new
  (method, path) pairs. Lookups of codes (items, suppliers, departments, PO numbers) are always within the source's hospital.
* Permissions: `integrations:run` (admin, procurement manager, inventory manager — monitoring, upload, sync, retry,
  reconciliation) and `integrations:manage` (admin — sources, mappings, API keys). Viewers and department managers have neither.
* Secrets: inbound keys hashed with a server-side pepper and shown once; outbound tokens only in environment variables
  referenced by name; configuration refuses keys that look like secrets. No credentials in Git.
* No scraping, no screen automation: only files people upload, APIs the hospital calls, and APIs the hospital exposes to MedFlow.
* No regulatory compliance (HIPAA, GDPR, ISO) is claimed. Integrations carry operational supply data only — **no patient data**.

## 14. The reference ERP simulator

`/api/reference-erp/{hospital_code}/{resource}` is a **local stand-in for a hospital ERP**, used to exercise the REST
connector over real HTTP (tests, E2E, demo). It is not a hospital system.

* Off by default (`REFERENCE_ERP_ENABLED=false` → 404); requires `REFERENCE_ERP_TOKEN`; serves **demo hospitals only**
  (`hospitals.is_demo`), never a real hospital's data. Every response says `"simulated": true`; the source is labelled
  *simulated* in the UI.
* It speaks ERP-style field names (material_code, vendor_code, cost_center, qty_on_hand, po_number, grn_number, …), so the
  mapping layer is really used. Resources: health, departments, vendors, materials, vendor-materials, stock, consumption,
  purchase-orders, deliveries.
* Its master data mirrors the demo hospital's catalogue. Once per hospital per day it adds labelled changes: a new vendor
  (HLS) and material (GLV-NIT-L) with a catalogue price, an ERP list price for CAP-BOUF, three consumption postings, one
  purchase order with a partial goods receipt (80 of 200), stock counts that differ from the ledger for GLV-EXM-M (+30) and
  SYR-5ML (−12), and two invalid postings (unknown material, negative quantity) so validation is visible.
* The day's feed is generated on first request and kept in memory; restarting the API regenerates it.

## 15. Configuration

| Variable | Default | Meaning |
|---|---|---|
| `INTEGRATION_KEY_PEPPER` | derived from `JWT_SECRET` | pepper for API-key hashes (set explicitly in production) |
| `INTEGRATION_RATE_LIMIT_PER_MINUTE` | 60 | per API key |
| `INTEGRATION_MAX_RECORDS_PER_REQUEST` | 5000 | per push / per run |
| `INTEGRATION_MAX_UPLOAD_MB` | 10 | upload size |
| `INTEGRATION_HTTP_TIMEOUT_SECONDS` / `INTEGRATION_HTTP_RETRIES` | 20 / 3 | REST pull |
| `INTEGRATION_ALLOWED_HOSTS` | `[]` | hosts `rest_pull` may call (JSON list) |
| `MEDFLOW_INTEGRATION_*` | — | outbound bearer tokens, referenced by name in a source's `auth_env` |
| `REFERENCE_ERP_ENABLED` / `REFERENCE_ERP_TOKEN` / `REFERENCE_ERP_BASE_URL` | false / — / `http://localhost:8000` | simulator (the demo `.env.example` turns it on) |

## 16. Connecting a real hospital system — checklist

1. Agree the data contract with the hospital's IT/vendor: which entities, field names, codes (item, department, supplier),
   units of measure, time zone, how PO lines and corrections are represented. **Written permission** to access the system.
2. Create the source (connector, entities), map the fields, set a date format if needed.
3. `api_push`: create a key, give it to the hospital's integration team through their secret store. `rest_pull`: have the
   operator add the host to `INTEGRATION_ALLOWED_HOSTS` and set the token in `MEDFLOW_INTEGRATION_<NAME>`.
4. Dry-run with real extracts; fix mappings until nothing unexpected is rejected.
5. Run master data first, then transactions; reconcile stock; enable the schedule.
6. Watch Monitoring for freshness, rejects and reconciliation; test it with the hospital before calling it "connected".

## 17. Known limitations

* **No real hospital system has been connected or tested.** REST pull was tested against the local simulator and mocked
  HTTP servers; API push and uploads with synthetic data.
* One item per purchase order record; no multi-line PO object. No returns/reversals from external systems (negative
  quantities are rejected) — post corrections in MedFlow.
* Transactions older than the item's latest ledger movement are rejected rather than back-dated (append-only ledger).
* The inclusive cursor re-delivers boundary records: valid ones count as unchanged; invalid ones are rejected again until
  fixed at the source (they appear in the rejected counts of each run).
* In-process rate limiter and in-memory simulator feed (single API worker); the scheduler is a polling loop, not a queue.
* Units of measure are not converted: the source must send MedFlow base units (or map a field that already is).
* No outbound exports to hospital systems and no connection to purchasing systems: MedFlow still recommends and records;
  it never sends orders anywhere.
* HL7 / FHIR / EDI formats and vendor-specific ERP adapters are not built (the REST contract is generic).
