# V8 — Multi-hospital SaaS

> **Goal:** one MedFlow installation serves many hospitals, grouped into organizations. **Hospital A can never see,
> query, modify, forecast, explain or infer Hospital B's data.** That is enforced in the database, in every request,
> in every ML / graph / assistant path, not only in the UI.

V8 adds tenant architecture *around* V1–V7. It does not change any forecasting, risk, supplier, procurement, graph or
assistant method (see [Testing](#15-testing) for how that was checked).

## 1. Goal

| | Before V8 | V8 |
|---|---|---|
| Accounts | one account ↔ one hospital (`users.hospital_id`) | one account, many hospitals (**memberships**) |
| Groups | none | **organizations** (hospital groups) with organization admins |
| Administration | hospital admin only | hospital admin, organization admin, platform admin |
| Hospital context | implicit | **server-verified active hospital**, per request; switching |
| Isolation | router filters (`get_owned`) | router filters **+ ORM guard + PostgreSQL triggers + token binding + tests over every route** |
| Onboarding | seed script | organization onboarding flow + hospital-scoped CSV import |

## 2. Tenancy architecture

```text
Platform (platform admins: users.is_platform_admin)
 └─ Organization (organizations; organization admins: organization_memberships)
     └─ Hospital (hospitals.organization_id, status)
         ├─ memberships (hospital_memberships: user, role, department, status) ← the ONLY source of hospital access
         └─ everything operational — departments, items, batches, ledger, procedures, forecasts & models, risk,
            suppliers & orders, procurement settings & recommendations, alerts, graph projection, assistant
            conversations, audit rows — keyed by hospital_id (directly or through its parent)
```

Request flow:

```text
access token (JWT: user id + hospital id it was issued for)
 → user (active account)
 → token hospital == user's active hospital?            else 401 "hospital context changed" (client refreshes)
 → ACTIVE membership in an ACTIVE hospital of an ACTIVE organization?   else the context is cleared → 403
 → role / department taken from that membership
 → X-MedFlow-Hospital header (if sent) == active hospital?   else 409 (a stale browser tab never acts on another hospital)
 → require(permission) — needs an active hospital (403 "No hospital selected" otherwise)
 → router loads hospital-owned rows with get_owned(…, user.hospital_id) → 404 for any other hospital's id
 → service / ML / graph / assistant tool — all take the hospital id from that verified context
 → ORM guard + PostgreSQL triggers reject any write linking two hospitals
```

## 3. Organization model

`organizations`: `id, name, code (unique), status (ACTIVE | SUSPENDED), is_demo, created_at, updated_at`.
A suspended organization makes all its hospitals unreachable. Organization admins are rows in
`organization_memberships (user, organization, role = organization_admin, status)`.

## 4. Hospital model

The existing `hospitals` table is extended (no duplicate concept): `organization_id` (NOT NULL, RESTRICT) and `status`
(ACTIVE | SUSPENDED). Profile fields are unchanged. Hospital-specific configuration stays where it was:
`hospitals.expiry_warning_days` and the V5 `procurement_settings` row (one per hospital — service level, stockout
multiplier, holding rate, review period, budget, …). Changing one hospital's settings cannot change another's (tested).

## 5. Membership model

`hospital_memberships (user_id, hospital_id, role, department_id, status, created_by_id, timestamps)`, unique per
(user, hospital). The role is **per hospital** — one account can be *Hospital administrator* in Sunrise and
*Viewer* in Lakeview.

`users.hospital_id / role / department_id` remain, with a new meaning: the **active hospital context**, a cache of one
ACTIVE membership. They change only through `POST /api/hospitals/{id}/switch` (membership verified), at login/refresh
(falls back to the first usable membership), and when a membership changes. A deferred PostgreSQL constraint trigger
rejects a pointer to a hospital without an active membership; the ORM guard does the same on every database.
`users.hospital_id` is NULL for an account with no selected hospital (e.g. a platform admin).

Why keep the pointer instead of adding a tenant id to every call? Every V1–V7 code path already reads
`user.hospital_id`; keeping it as a verified cache means none of those paths had to change, and the verification is in
one place (`app/services/tenancy.py`, `app/api/deps.py`).

Conversations of the V7 assistant are **user + hospital** scoped (`assistant_conversations.user_id` and `hospital_id`):
after switching hospitals, the previous hospital's conversations are neither listed nor openable.

## 6. Role model

| Role | Scope | What it grants |
|---|---|---|
| Hospital administrator (`admin`), procurement manager, inventory manager, department manager, viewer | one hospital (membership) | exactly the V1–V7 permission matrix, unchanged (tested for every role) |
| Organization administrator | an organization | organization overview, organization audit, onboarding hospitals, profile/status and members of the organization's hospitals. **No operational data** unless also a member of that hospital |
| Platform administrator | the platform | create / suspend organizations, add organization admins, platform audit. **No hospital data** unless also a member |

Hospital admins administer only their **active** hospital. Nobody gets platform or organization rights implicitly.

## 7. Database isolation

Audit of every table (`app/db/tenant_guard.py` derives the list from the metadata):

| Class | Tables |
|---|---|
| Hospital-owned, `hospital_id` column | departments, consumable_categories, consumables, suppliers, stock_movements, alerts, procedure_types / mappings / schedules, model_versions, forecasts, risk_model_versions, stockout_predictions, supplier_orders, supplier_deliveries, procurement_settings, procurement_recommendations, graph_sync_runs, assistant_conversations, hospital_memberships |
| Hospital-owned through a parent | stock_batches (→ consumable), supplier_products (→ supplier + consumable), model_item_metrics (→ model version), assistant_messages (→ conversation) |
| Tenant structure | organizations, hospitals, organization_memberships |
| Accounts | users (global identity; access only through memberships) |
| Audit | audit_logs — `hospital_id` (hospital events) and/or `organization_id` (organization events); neither = platform event |

Nothing is global/reference data: suppliers, prices, lead times and performance stay per hospital, even when two
hospitals use a supplier with the same code (CPS delivers well to Sunrise and badly to Lakeview in the demo).

Constraints and guards:

* Uniqueness is **per hospital**, never global: `(hospital_id, sku)`, `(hospital_id, code)` for suppliers and
  departments, `(hospital_id, name)` categories, `(hospital_id, reference)` orders — tested: A and B both have SKU
  `GLV-7`, supplier `CHEAP`, department `ORTHO`.
* **PostgreSQL triggers** (`medflow_tenant_guard`, installed by migration `623279e318d6`) on the 15 tables whose rows
  reference other hospital-owned rows: an insert/update linking two hospitals (A's supplier + B's item, an issue of A's
  item to B's department, a membership with another hospital's department, …) fails with `tenant violation`, even for
  raw SQL (tested on PostgreSQL).
* A deferred constraint trigger on `users.hospital_id` (active pointer ⇒ active membership).
* The same rules in an ORM `before_flush` guard, so SQLite (tests) and every code path are covered.
* Indexes: `hospital_memberships(user_id)`, `(hospital_id)`, unique `(user_id, hospital_id)`; `hospitals(organization_id)`;
  `audit_logs(organization_id, created_at)`. Existing `hospital_id` indexes are unchanged.

Migration `623279e318d6` preserves all data: it creates a "Default organization" for existing hospitals, gives every
existing user a membership with their current role and department, backfills `audit_logs.organization_id`, and installs
the triggers. Upgrade → check → downgrade → upgrade was run on an empty database and on a copy of the V7 demo database
(14,586 movements, 5 users, 84 audit rows: all preserved). Downgrade removes the V8-only concepts (see
`docs/v8-multi-hospital.md`).

## 8. API isolation

* Every hospital endpoint depends on `require(permission)`, which needs a verified active hospital.
* Ids of other hospitals answer **404** (never 403 with a hint). Tested **systematically**: every one of the 50
  `(method, path)` pairs that take an id is called by three roles of hospital A with hospital B's ids — 150 calls, none
  succeeds, no response contains B's data, and no row is created. Ids smuggled in request bodies (issue to B's department,
  order from B's supplier, schedule B's procedure type, map B's item, what-if with B's supplier, generate for B's items,
  …) are rejected too.
* Every list endpoint is called with search / filter / pagination variants (200+ calls) — B's data never appears.
* New endpoints: see `docs/api.md` (V8 section).

## 9. Graph isolation

The V6 architecture is unchanged: PostgreSQL is the source of truth, the graph is a projection. It was already keyed
per hospital (`<hospital>:<Label>:<id>` keys, `hospital_id` on every node and relationship, every query anchored on a
hospital-scoped key). V8 verifies it with two hospitals in one graph store: syncing B (initial or after a change) leaves
A's node / relationship counts untouched; every node key starts with its own hospital id; predefined queries, explain and
impact never return B's nodes; B's item / supplier ids answer 404. Sync runs, status, counts and verification are per
hospital (`graph_sync_runs.hospital_id`); `python -m app.ml.train` and `python -m app.graph.cli` loop over hospitals
explicitly.

## 10. Assistant isolation

* All 20 tools call the existing endpoint functions with the verified user → they only see the active hospital. Tested
  by calling **every tool** with hospital B's ids and names from hospital A's context: no B data, and id arguments come
  back as "not found" (`supplier_performance` was hardened to say so instead of returning an empty list).
* Tools have no `hospital_id` parameter; the LLM tool loop rejects unexpected arguments (tested with a scripted model
  that tries `hospital_id`).
* **Questions about another hospital are refused before any tool or model runs** ("Show me Hospital B's inventory",
  "What is the stock at LAKEVIEW-MYS?", "…across hospitals", "hospital id 2", another hospital's distinctive name). The
  reply is identical whether or not that hospital exists, so it cannot be used to enumerate tenants. Asking about the
  current hospital by name still works.
* Conversations are user + hospital scoped; assistant audit rows carry the active hospital.
* V7's read-only behaviour is unchanged (write requests refused, no write tools, optional LLM grounded, no external AI
  API required).

## 11. Hospital switching

UI: the hospital selector in the header lists the account's hospitals, grouped by organization, with the role held in
each. Switching calls `POST /api/hospitals/{id}/switch`; the server verifies the membership, moves the active pointer,
issues a new access token bound to the new hospital and writes a `hospital.switch` audit row in the new hospital. The
client then **clears its entire query cache** and reloads the dashboard, so nothing from the previous hospital stays on
screen — and even if it did, the backend would refuse it.

Multiple tabs: the active hospital belongs to the account (server-side), not to a tab. Every request carries
`X-MedFlow-Hospital`; a tab still showing the previous hospital receives 409 and reloads instead of acting on the new
hospital. Access tokens issued before a switch stop working (401 → refresh).

## 12. Organization reporting

`GET /api/organizations/{id}/overview` (organization admins) — an explicit aggregate service
(`app/services/org_reporting.py`), not a reuse of hospital dashboards:

* per hospital: members, items, HIGH/MEDIUM/LOW risk (each hospital's newest stored V3 snapshot, with its date),
  active and critical alerts, pending V5 recommendations and their purchase value, the hospital's V4 OTIF rate;
* totals = sums over the listed hospitals, with the list of contributing hospitals;
* the same supplier (matched by code) in several hospitals, side by side — each hospital's own OTIF, never pooled.

The UI separates **Hospital view** (every normal page) from **Organization view** (Organization page).

## 13. Onboarding

`POST /api/organizations/{id}/hospitals` (organization or platform admin), one transaction: hospital → administrator
(new account, or an existing account of the same organization) → departments → procurement settings (defaults or
custom, validated with the V5 schema) → audit row. The new administrator signs in straight into the new, empty
hospital and loads data with the **CSV import** (Settings → Import CSV): departments, suppliers, items, opening stock.
Imports go into the active hospital only (no hospital column exists to fill in), validate every row first (line-numbered
errors), are all-or-nothing, never overwrite an existing code/SKU, offer a dry run, and write opening stock through the
V1 ledger. No ERP / HIS integration (V9).

## 14. Security model

Technical controls implemented (formal compliance — HIPAA, GDPR, ISO 27001 or any other — has **not** been assessed or
certified):

* tenant isolation at four layers: request context (membership + token binding + stale-tab check), router ownership
  checks (404), ORM guard, PostgreSQL triggers;
* role-based access per hospital membership; separate organization and platform roles that grant no operational data;
* audit logging per hospital, organization and platform (`hospital.switch`, `hospital.onboard`, membership changes,
  imports, `assistant.ask`, …); hospital admins see their hospital's audit, organization admins their organization's,
  platform admins platform/organization events only;
* the assistant is read-only, needs no external AI API, keeps an optional LLM grounded in tool results, and refuses
  cross-hospital questions;
* the graph is a derived, hospital-keyed projection; its failure does not affect V1–V5;
* **no patient data** is stored (procedures are counts only); demo data is synthetic and labelled.

## 15. Testing

* `backend/tests/test_multi_hospital.py` — database guard + triggers, per-hospital uniqueness, memberships, switching,
  token binding, stale-tab 409, revoked membership, platform / organization / hospital admin scope, onboarding, members,
  every id route × 3 roles, body-id attacks, lists/search/pagination, settings, alerts & risk refresh, recommendations,
  audit, CSV import, role matrix, assistant (cross-hospital questions, all tools, scripted LLM, conversations), graph
  sync/query/impact per hospital, and ML: Sunrise + Lakeview seeded into **one** database and trained — training
  panels, model metrics and forecasts contain only each hospital's items, glove forecasts differ (high vs low glove
  use), CPS OTIF differs, the same assistant question gives each hospital's own answer.
* All V1–V7 tests still pass (fixture change only: test hospitals now belong to organizations).
* `frontend/e2e/v8-multi-hospital.spec.ts` — the full A → switch → B journey through inventory, suppliers, stockout
  risk, supplier intelligence, procurement, knowledge graph and assistant; direct and assistant attacks from a
  single-hospital account; organization overview and members; platform admin without hospital data.
* The Sunrise demo data are identical to V7 (same seeds; row-level comparison of a V7-code seed and a V8-code seed on the
  same day differs only in the wall-clock timestamps of "today" events, as before). After re-training, Sunrise's V3
  (PR-AUC 0.508 vs cover rule 0.347), V5 (13 recommendations, ₹2,53,123) and V6 (992 nodes, 2,144 relationships) demo
  results are the same numbers as in the V7 release; V2 metrics were not compared separately (same data, deterministic
  training).

Exact counts are in `docs/v8-multi-hospital.md`.

## 16. Known limitations

* The active hospital is per **account**, not per browser tab: switching in one tab makes other tabs reload (409).
* Organization reporting reads each hospital's latest stored snapshots; it does not refresh risk.
* Supplier comparison across hospitals matches by supplier code; there is no shared supplier master (by design).
* The assistant's cross-hospital detection is rule-based (names, codes, phrases); it errs towards refusing. Because the
  tools cannot reach another hospital anyway, a missed phrase would still only return the active hospital's data.
* Rate limits (login, assistant) are in-process — use a shared store for several API workers.
* No billing, subscriptions, SSO or real integrations (not built, by design).
* Deleting an organization or hospital through the API is not offered (suspend instead).

## 17. Deployment notes

* `alembic upgrade head` applies migration `623279e318d6` (existing data → "Default organization", memberships created,
  PostgreSQL triggers installed). Nothing else changes in `docker-compose.yml` or `.env`.
* `SEED_DEMO=true` now seeds three synthetic hospitals (Sunrise + Lakeview in "CareNet Hospitals Group (Demo)", Harbor in
  "Harbor Health Trust (Demo)") and the accounts `admin@carenet.demo` (organization admin, admin of both CareNet
  hospitals) and `platform@medflow.demo` (platform admin, no hospital).
* The graph remains: Neo4j configured as the production graph store (CI configuration), FalkorDB used for local/test Cypher
  execution. The graph implementation was tested using FalkorDB, an openCypher-compatible graph engine. Neo4j is
  configured as the production graph store and is covered by CI configuration, but Neo4j execution could not be locally
  verified because the required Neo4j image/download endpoints returned HTTP 403 in the development environment.
* Docker images still could not be built in the development environment (Docker Hub returned 403 / "Forbidden").
