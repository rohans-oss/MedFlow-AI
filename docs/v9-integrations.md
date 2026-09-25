# MedFlow AI — V9 release report: Real hospital integrations & data exchange

Tag `v9.0.0` · branch `feature/v9-real-integrations` from the frozen `v8.0.0` · design and operations:
[`integrations.md`](integrations.md).

> MedFlow supports a validated integration framework and controlled data-import/API connectors. Production
> hospital-system integrations require access to the corresponding hospital/vendor systems and have not been claimed
> unless actually tested.

## 1. What V9 is

V9 changes **how data gets into MedFlow**. It does not change what MedFlow computes from it. Hospital systems send data in
three ways, all per hospital:

* an ERP / inventory / procurement system **pushes** it with an API key;
* MedFlow **pulls** it from the hospital's REST API, either on a schedule or on demand;
* people **upload** CSV or Excel files.

Every record then goes through the same steps: map → validate → de-duplicate. It is applied through the existing V1/V4
services, so:

* consumption reaches V2;
* stock reaches V3;
* orders and deliveries reach V4 and V5;
* everything reaches V6 and V7.

Each exchange is recorded in an audit trail. Rejected records are kept so they can be retried. Stock that disagrees with
the ledger becomes a reconciliation issue, and a person resolves it.

**No ML, risk, supplier-intelligence or procurement algorithm was modified** (`app/ml`, `app/risk`, `app/supplier_intel`,
`app/procurement`, `app/graph` and `app/assistant` are untouched). The same applies to `app/services/stock.py` and
`supplier_orders.py`: V9 calls them and does not change them.

## 2. The ten requested features

| # | Feature | Delivered |
|---|---|---|
| 1 | Integration framework | `integration_sources` per hospital: enable / disable, connection test + status, last success / last failure / last error, sync history |
| 2 | CSV / Excel import | inventory, items, suppliers, consumption, purchase orders, delivery records (+ departments, supplier catalogue); `.csv` (`,` `;` tab) and `.xlsx`; dry run; line numbers in errors |
| 3 | REST API ingestion | `POST /api/ingest/v1/{entity}`; per-source keys stored as HMAC hashes (shown once, revocable); hospital-specific configuration; authentication; validation; per-key rate limiting (429 + Retry-After); 401 / 403 / 404 / 413 / 422 error handling. Plus a `rest_pull` connector: env-var credentials, host allowlist, retries, pagination |
| 4 | Data mapping | per source and entity: source field → MedFlow field (e.g. `material_code → item.code`, `material_description → item.name`, `cost_center → department.code`, `qty_on_hand → stock.quantity`, `vendor_code → supplier.code`, `po_number → supplier_order.reference`), defaults, date formats; editable in the UI |
| 5 | Data validation | missing fields, invalid dates, unknown items / suppliers / departments / purchase orders, duplicates, negative quantities, inactive departments, plus out-of-order transactions, insufficient stock, changed transactions and conflicts |
| 6 | Sync engine | initial and incremental sync (checkpoints), retry of failed records, idempotency (`external_refs`), per-record savepoints, dry run, scheduled feeds (`cli run-due`, compose scheduler) |
| 7 | Reconciliation | "hospital says 1,000 / MedFlow says 970" → a +30 issue. It is compared with the ledger *at the snapshot time*. A person resolves it: adjust, external wrong or accept. A later matching snapshot closes it automatically |
| 8 | Monitoring dashboard | connected systems, health, sync status per entity, records processed / applied / rejected, errors, last sync / success / failure, data freshness, open reconciliation |
| 9 | Audit trail | every run: hospital, source, started_at, completed_at, records_received / created / updated / rejected (and unchanged), status, error_summary; plus audit-log rows for runs, pushes, configuration, keys and decisions |
| 10 | No fake external integration | a local **reference ERP simulator** (off by default, token-protected, demo hospitals only, labelled *simulated* in the API, the UI and the docs). The limitation statement appears on the Integrations page, in the README and in the docs |

## 3. Architecture

`backend/app/integrations/` contains:

* `fields.py` — types, parsing and reason codes
* `entities.py` — the 8 entities and how each is applied through the existing services
* `engine.py` — runs, mapping, validation, idempotency, savepoints, dry run, retry, checkpoints, audit
* `readers.py` — CSV and XLSX
* `connectors.py` — REST pull, retries, pagination, the schedule
* `credentials.py` — API keys and the rate limit
* `reconciliation.py`
* `sources.py` — configuration validation (secret refusal, SSRF allowlist) and monitoring
* `reference_erp.py` — the simulator
* `demo.py` — demo sources
* `cli.py`

The API lives in `app/api/integrations.py`: 20 operations for users plus the API-key `ingest_router`. The page is
`frontend/app/(app)/integrations/`, with Monitoring, Sources & mapping, Runs & audit and Reconciliation tabs.

## 4. Database

Migration `5684eca6bc5c` is purely additive:

* 8 tables (§ `database.md`);
* 7 new PostgreSQL tenant-guard triggers, bringing the total to 23;
* no change to any existing table.

## 5. Where the data goes (verified)

* **Consumption → V2.** The `test_imported_data_reaches_the_existing_services` test pushes three consumption records. They
  appear as V1 FEFO issues in the daily series V2 reads, the item's stock falls by the same amount, and the pushed PO is
  listed by the V4 order log that V5 reads as in-transit.
* **Deliveries.** A delivery is received against its PO: V1 batch + RECEIPT movement + V4 `supplier_deliveries` row, with
  the order going OPEN → PARTIAL. Closing the PO closes it short.
* **E2E.** The simulator's consumption shows up on the Stock movements page, performed by
  "Integration: Reference ERP (simulated)".

## 6. Security and tenancy

* A key writes only into its own source's hospital, and codes are resolved within that hospital.
* The V8 sweep of every id route now also covers the **16 new (method, path) pairs**: 66 per role × 3 roles, all
  404 / 403 / 422, with no leak.
* A pushed item with a code that exists in another hospital creates a new item in the key's hospital and leaves the other
  hospital untouched (tested).
* Other secrets:
  * outbound tokens are only referenced by environment-variable name;
  * configuration that looks like a secret is refused;
  * the SSRF allowlist blocks non-allow-listed hosts (tested with `169.254.169.254`).
* Permissions:
  * `integrations:run` — admin, procurement manager, inventory manager;
  * `integrations:manage` — admin;
  * adjusting stock during reconciliation additionally needs `stock:receive` (tested: procurement 403, inventory 200).
* No scraping. No patient data. No compliance certification is claimed.

## 7. Tests (exact)

| Check | Result |
|---|---|
| Backend, SQLite + FalkorDB | **214 passed, 1 skipped** (the PostgreSQL-trigger test) — 197 V1–V8 + 18 V9 |
| Backend, PostgreSQL 16 + FalkorDB | **215 passed, 0 skipped** |
| New V9 tests (`tests/test_integrations.py`) | 18. Cover: permissions; config validation (SSRF, secrets, env names); CSV mapping + idempotency; all validation reasons + retry; XLSX + dry run; API keys (hash, once, revoke, disable); rate limit + size; append-only transactions (unchanged / changed / out-of-order / future); PO + delivery → V4; reconciliation (tolerance, opening balance, auto-close, adjust permissions); REST pull end to end against the simulator; simulator protection (401 / non-demo 404 / disabled / stale token); retries + pagination + checkpoints + 401 not retried + bounded 5xx retries + missing env; scheduler; audit trail; isolation; data reaching V1–V5; future-stamped demo movements |
| V8 isolation sweep | extended to the new routes (`checked == 3 * 66`) |
| Ruff · `npm run lint` · `npm run typecheck` · `npm run build` | clean · clean · clean · success |
| E2E (Playwright, freshly seeded + trained demo, PostgreSQL + FalkorDB, API with the simulator enabled) | **41 passed, 1 skipped** (screenshot spec). 36 V1–V8 + 5 V9: honest monitoring; pull from the simulator → rejected records → reconciliation +30 resolved by adjusting → integration account visible on movements; CSV upload with rejections; API key shown once + push + other hospital gets 404; viewer has no page |
| Migration, empty DB | upgrade → check → downgrade -1 → upgrade → check, no drift; 23 guard triggers |
| Migration on the V8 demo DB | the development database holding V8 demo data upgraded in place ✔ |
| Downgrade of V9 data (copy) | 26 runs / 6 sources dropped with their tables; all 29,674 stock movements kept (imported movements are ordinary ledger rows); the 6 inactive integration service accounts remain; re-upgrade + check ✔ |
| Sunrise demo data vs V8 | a row-level fingerprint of every Sunrise table under V8 code and under V9 code (seeded the same day) is **identical**. V9 only adds 3 configured sources + 8 mappings per CareNet hospital; nothing is imported at seed time |

**Demo verification** (Sunrise, first "Sync now" on a freshly seeded database). All 8 entities synced. Consumption was
PARTIAL, as designed: the simulator's unknown material and negative quantity were rejected. Reconciliation found exactly
the simulator's injected differences:

| Item | Difference | Outcome |
|---|---|---|
| GLV-EXM-M | +30 | resolved in the E2E by an adjustment |
| SYR-5ML | −12 | open |

A second sync was incremental and wrote nothing new.

**Found and fixed during E2E.** The synthetic seed stamps some of "today's" movements later in the day. The first
chronology check compared against those future timestamps and rejected every current transaction. It now ignores
movements stamped in the future, as a manual issue does. Snapshots are compared with the ledger rewound to the snapshot
time. A test covers this.

## 8. Performance (indicative)

Measured on this 2-CPU container, with the backend test suite running at the same time:

| Operation | Time |
|---|---|
| Lakeview initial pull of all 8 entities from the simulator over HTTP | 1.30 s |
| Incremental re-sync | 0.28 s |
| `GET /integrations/overview` | 12 ms median |

Each record costs a few indexed queries plus a savepoint. 5,000 records per run is the configured limit. Throughput with
real volumes has not been measured.

## 9. Deployment and verification limits

* **Docker: NOT VERIFIED.** `docker build` still fails here: Docker Hub answers "Forbidden" for `python:3.11-slim`. The
  compose file gained an `integrations-scheduler` service and the V9 environment variables, but that service was not run
  in Docker. `python -m app.integrations.cli run-due` itself is covered through `connectors.due_sources` / `sync_source`
  tests.
* **Neo4j: NOT VERIFIED locally.** The graph implementation was tested using FalkorDB, an openCypher-compatible graph
  engine. Neo4j is configured as the production graph store and is covered by CI configuration, but Neo4j execution could
  not be locally verified because the required Neo4j image/download endpoints returned HTTP 403 in the development
  environment.
* **CI: NOT VERIFIED.** The workflow now enables the simulator for E2E but was not executed; there is no remote.
* **Real hospital systems: none connected or tested.** REST pull was verified against the local simulator and mocked HTTP
  servers only.
* No HIPAA / GDPR / ISO assessment.

## 10. Known limitations

See `integrations.md` §17. In short:

* one item per PO record;
* no external returns or reversals;
* transactions older than the ledger's latest movement are rejected;
* the inclusive cursor re-delivers boundary records, so invalid ones are rejected again each sync until fixed at source;
* the rate limiter and the simulator feed are in-process;
* no unit-of-measure conversion;
* no HL7 / FHIR / EDI;
* no outbound orders.

## 11. Next: V10 — Real hospital pilot & business validation

Connect one real hospital's system (with its permission and IT team) using this framework. Measure data quality, reject
rates, reconciliation volume and freshness, and validate the V2–V5 outputs on real data. Only then can "integrated with
<system>" be claimed.
