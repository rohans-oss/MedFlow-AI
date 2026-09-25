# V8 release report — Multi-hospital SaaS (`v8.0.0`)

Base: `v7.0.0` (bfa6833) · branch `feature/v8-multi-hospital-saas` · design and usage: [`multi-hospital.md`](multi-hospital.md).
Everything below states what was actually run in the development environment; anything not run says **NOT VERIFIED**.

## 1. What changed

* Organizations, hospital memberships (role per hospital), organization admins, platform admins.
* A server-verified **active hospital** per request (membership + hospital/organization status + access token bound to
  the hospital + stale-tab header), switching from the header.
* Defence in depth: an ORM tenant guard and PostgreSQL triggers reject rows that link two hospitals.
* Administration: hospital (members, profile, departments, settings, CSV import), organization (overview, hospitals,
  members, onboarding, audit), platform (organizations, organization admins, platform audit).
* Demo: Sunrise (unchanged) + Lakeview (same group, deliberately different data) + Harbor (another organization); a group
  admin who can switch between Sunrise and Lakeview; a platform admin with no hospital.
* V1–V7 methods unchanged. Two V7 hardenings: the assistant refuses questions about other hospitals, and the
  `supplier_performance` tool answers "not found" for a supplier id outside the active hospital.

## 2. Architecture

`Platform → Organization → Hospital → memberships → everything operational`. All V1–V7 code already scoped queries by
`user.hospital_id`; V8 turns that field into a verified cache of the active membership (`app/services/tenancy.py`,
`app/api/deps.py`), so none of the ML, graph or assistant code had to change to become tenant-aware. New code:
`app/api/tenancy.py`, `app/db/tenant_guard.py`, `app/services/org_reporting.py`, `app/services/imports.py`; frontend
`components/hospital-switcher.tsx`, `components/tenancy.tsx`, pages `/organization`, `/platform`, Settings → Import CSV.

## 3. Tenant isolation

Four layers (request context → router ownership checks → ORM guard → PostgreSQL triggers); all hospital-owned tables
classified in `multi-hospital.md` §7. No table was made global; suppliers remain per hospital.

## 4. Organization and hospital model

`organizations(name, code, status, is_demo)`; `hospitals.organization_id` (NOT NULL) and `status`. Suspending a hospital
or organization makes it unreachable for everyone (tested).

## 5. User membership

`hospital_memberships(user, hospital, role, department, status)` is the only source of hospital access. One account can
hold different roles in different hospitals (tested: admin in A, viewer in B; organization admin adds an A account to A2
as procurement manager without changing its role in A). A hospital admin cannot change the name/password of an account
that also belongs to other hospitals.

## 6. Role permissions

The five hospital roles keep exactly the V1–V7 permission matrix (tested for every role). Organization admins and
platform admins have administration rights only; without a membership they get 403 on every hospital endpoint (tested).
No new hospital permission was added.

## 7. Database changes

Migration `623279e318d6`: `organizations`, `hospital_memberships`, `organization_memberships`;
`hospitals.organization_id/status`; `users.is_platform_admin`, `users.hospital_id/role` nullable (FK now SET NULL);
`audit_logs.organization_id` (+ `hospital_id` nullable); indexes on the new foreign keys; PostgreSQL triggers
`medflow_tenant_guard` (15 tables) and `trg_active_hospital_guard` (deferred, on `users`).

## 8. API changes

Additive only (V1 contracts kept): `/auth/me` fields, `/users` now = members of the active hospital, and the new
`/hospitals…`, `/organizations…`, `/platform/audit-logs`, `/imports/{kind}` endpoints — see `api.md` (V8 section).

## 9. Graph changes

None to the V6 code or its query semantics. Verified with two hospitals in one FalkorDB graph: syncing B (initially and
after a change) leaves A's node/relationship counts unchanged; every node key carries its own hospital id; predefined
queries, explain and impact never return B's data; B's ids answer 404.

## 10. Assistant changes

Cross-hospital questions are refused in `planner.plan` before any tool or LLM (identical reply whether the named
hospital exists or not); all 20 tools were called with the other hospital's ids/names (no data, "not found"); a scripted
LLM that passes another hospital's ids or a `hospital_id` argument gets errors and its answer is discarded; conversations
are user + hospital scoped; assistant audit rows carry the active hospital. V7 read-only / grounding behaviour unchanged
(all V7 tests pass).

## 11. Security tests (backend, `tests/test_multi_hospital.py`, 29 tests)

| Area | What is attacked / checked |
|---|---|
| Database | cross-hospital rows rejected by the ORM guard (supplier product, procedure mapping, membership department, stock movement) and by PostgreSQL triggers even with raw SQL; active pointer without membership rejected; codes unique per hospital only |
| Authentication | memberships in `/auth/me`; switching changes data, role and permissions server-side; old access token → 401; stale-tab header → 409; switching without / with suspended membership, suspended hospital, suspended organization → 404; revoking the active membership → immediate 403 |
| Direct API attack | **every** (method, path) with an id parameter — 50 pairs × 3 roles = 150 calls with the other hospital's ids: all 4xx, no data, no rows created |
| Body-id attack | 14 writes smuggling the other hospital's ids (issue, receive, orders, schedule, mappings, supplier products, generate, what-if, users, items): all rejected, nothing written |
| Lists | every list endpoint × 6 search / filter / pagination variants (200+ calls): no other-hospital data |
| Config / derived data | procurement settings per hospital; alert evaluation and risk refresh touch only their hospital; recommendations and approvals per hospital; audit logs per hospital |
| Administration | platform admin (no hospital data), organization admin scope (own organization only, no operational data), hospital admin (active hospital only, cannot suspend it), onboarding, members across hospitals, organization overview = sum of per-hospital rows |
| Import | line-numbered validation, all-or-nothing, never overwrite, other hospital's supplier code not resolvable, permissions |
| Assistant attack | 6 cross-hospital questions × deterministic and scripted-LLM mode: no tool, no model call; all 20 tools with other-hospital ids; scripted LLM passing other ids / `hospital_id`; conversations across a switch |
| Graph attack | per-hospital sync, counts, keys, queries, explain and impact |
| ML | Sunrise + Lakeview seeded into one database and trained (V2, V3, V5): training panels, model metrics and forecasts contain only the hospital's own items/departments; examination-glove forecasts differ (high vs low use); CPS OTIF differs by hospital; the same question "Why is Suture 2-0 at risk?" answers from each hospital's own risk; recommendations disjoint |

## 12. E2E tests (`frontend/e2e/v8-multi-hospital.spec.ts`, 4 tests)

1. Group admin in Sunrise: inventory, suppliers, stockout risk, supplier intelligence, procurement, knowledge graph,
   assistant → switch to Lakeview → suppliers, inventory, knowledge graph and assistant show Lakeview's own data;
   Sunrise's conversation is not listed; the same question gets a different, Lakeview-only answer; a question about
   Sunrise is refused.
2. A Sunrise-only account: no switcher; direct API attempts on Lakeview's hospital, members, item, risk and graph → 404;
   the assistant refuses "Show me Lakeview's inventory" without calling a tool.
3. Organization admin: overview lists Sunrise and Lakeview (not Harbor), supplier comparison, per-hospital members.
4. Platform admin: no hospital selected, organizations visible, `/api/inventory` → 403.

## 13. Test and migration results (exact)

| Check | Result |
|---|---|
| Backend, SQLite + FalkorDB (`TEST_GRAPH_URL`) | **196 passed, 1 skipped** (the skipped test is the PostgreSQL-trigger test, which needs PostgreSQL) — 168 V1–V7 + 29 V8 |
| Backend, PostgreSQL 16 + FalkorDB | **197 passed, 0 skipped** (includes the PostgreSQL-trigger test and all graph-store tests) |
| Ruff (`ruff check app tests`) | clean |
| Frontend `npm run lint`, `npm run typecheck`, `npm run build` | clean / clean / success |
| E2E (Playwright, full suite, stack on PostgreSQL + FalkorDB, freshly seeded and trained demo) | **36 passed, 1 skipped** (screenshot spec) — 32 V1–V7 + 4 V8. Note: the V1–V7 specs change the demo data (receipts, issues, approvals); running the whole suite a third time on the same data made 3 specs fail that expect suture 2-0 to still be at risk — re-seed before a full run (`python -m app.seed --reset && python -m app.ml.train`), as for V7 |
| Migration on an empty DB | upgrade → check (no drift) → downgrade -1 → upgrade → check (no drift) ✔ |
| Migration on a copy of the V7 demo DB | upgrade (5 users → 5 memberships, 14,586 movements and 84 audit rows kept, 16 triggers) → check → downgrade → upgrade → check ✔ |
| Downgrade of V8 multi-hospital data (copy) | 14 users → 13 (the platform-admin account, which cannot exist before V8, is removed), hospitals / movements / hospital audit rows kept; the group admin keeps one hospital; re-upgrade puts the hospitals into "Default organization" ✔ |
| Sunrise demo data vs V7 | row-level fingerprint of a V7-code seed and a V8-code seed, run on the same day, identical (checked on 23 and 24 Sep 2026; only wall-clock timestamps of "today" events differ, as in V7) |

Test-infrastructure changes to V1–V7 tests: the `world` fixture now creates organizations (required by the schema), and
three calendar-sensitive V2B/V5 tests pin the business date with a new `frozen_day` fixture. Those three tests also fail
on the unmodified `v7.0.0` code when run on 24 Sep 2026 (verified in a `v7.0.0` worktree): their synthetic data follow
calendar weekdays, and the fixed seeds were calibrated on another weekday. It is a pre-existing test fragility, not a V8
regression. Likewise the demo's V3 model choice depends on the day (the synthetic ledger ends "yesterday"): on 24 Sep the
cover rule is served for Sunrise (the V7 code seeds identical data that day, and training is deterministic), so the V3 E2E test now checks the explanation of
whichever model is served.

## 14. Performance observations

Measured on this development container (2 CPUs), medians of 7 requests after a warm-up, demo data — indicative only.
The V7 numbers were measured on 23 Sep with V7 code on different demo data and possibly different container resources,
so this is not a controlled A/B:

| Endpoint | V7 (23 Sep) | V8 (24 Sep) |
|---|---|---|
| `GET /auth/me` | 5.4 ms | 9.4 ms (now lists memberships and organizations) |
| `GET /dashboard/summary` | 67.7 ms | 57.1 ms |
| `GET /inventory` | 15.8 ms | 15.1 ms |
| `GET /stockout-risks` | 54.4 ms | 31.0 ms |
| `GET /graph/items/{id}/explain` | 41.4 ms | 39.1 ms |
| assistant "Why is Suture 2-0 at risk?" | 91.9 ms | 65.1 ms |
| organization overview, CareNet (2 hospitals) | — | 65 ms |
| organization overview, 8 synthetic hospitals (18,875 movements) | — | 138 ms |

The per-request tenant check is one indexed query (membership joined with hospital and organization). No N+1 was
introduced: the organization overview uses grouped queries plus one V4 scorecard per hospital.

## 15. Deployment limitations

* **Docker: NOT VERIFIED.** `docker build` fails in the development environment: Docker Hub returns 403 / "Forbidden" for
  `python:3.11-slim`. The stack was run directly (uvicorn, Next.js standalone, PostgreSQL 16, FalkorDB).
* **Neo4j: NOT VERIFIED locally.** The graph implementation was tested using FalkorDB, an openCypher-compatible graph
  engine. Neo4j is configured as the production graph store and is covered by CI configuration, but Neo4j execution could
  not be locally verified because the required Neo4j image/download endpoints returned HTTP 403 in the development
  environment.
* **CI: NOT VERIFIED.** The GitHub Actions workflow was not executed (no remote run from this environment).
* **LLM mode: NOT VERIFIED with a real model** (unchanged from V7): no model could be downloaded or reached; the loop is
  tested with a scripted provider.
* **Compliance: not assessed.** Technical controls only; no HIPAA / GDPR / ISO certification or audit.

## 16. Known limitations

See `multi-hospital.md` §16 — notably: the active hospital is per account (other tabs reload on 409), organization
reporting reads stored snapshots, supplier comparison matches by code, cross-hospital question detection is rule-based
(tools cannot reach other hospitals regardless), rate limits are in-process, no delete API for hospitals/organizations, no
billing / SSO / integrations. The migrated demo database keeps an empty "Default organization" created by the migration.

## 17. What V9 could address

Real integrations (ERP / HIS / purchasing systems, supplier EDI) with tenant-scoped connectors and credentials per
hospital; scheduled per-hospital jobs (nightly retraining, risk refresh, graph sync) with explicit tenant context;
per-tab hospital context if needed; a shared rate-limit store; SSO if a customer requires it; data export per hospital.
