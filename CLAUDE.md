# CLAUDE.md — MedFlow AI

Instructions for Claude (or any AI coding agent) working in this repository. Read this fully before changing code.

## 1. What this product is

MedFlow AI is **hospital supply-intelligence software**: inventory, stock ledger, suppliers, alerts and demand
forecasting for hospital consumables. It is **operations/procurement software — never a clinical or diagnostic
system**. It stores **no patient data**. All demo data is **synthetic** and must stay labelled as such.

The master plan lives outside the repo; the roadmap is in `docs/product-roadmap.md`. Build **one version at a time**
and **never implement features from a later version** unless the user explicitly asks.

## 2. What is already built

| Version | Status | Git tag |
|---|---|---|
| V0 Research | Drafts in `docs/research/` (hypotheses, not validated) | — |
| V1 Inventory MVP | Done: auth + 5 roles, hospital, departments, categories, consumables, batches + expiry, stock ledger (receive / FEFO issue / return / wastage / count), suppliers + catalogue, dashboard, rule-based alerts, audit log | `v1.0.0` |
| **V2A Consumption forecasting** | **Done**: daily consumption panel with stock-out censoring, baselines (MA-7, historical average), global XGBoost with recursive multi-step forecast, holdout evaluation (MAE/RMSE/WAPE/bias), model registry, stored forecasts, forecast API, Forecasts page, explanations | `v2.0.0` |
| **V2B Procedure-aware forecasting** | **Done**: procedure types / schedule / item mappings (tables, API, Procedures page, RBAC), synthetic procedure generator, procedure features, V2B XGBoost candidate with rolling-fold gating, V2A fallback, procedure impact + model comparison + stock cover in forecast API/UI — see §10 | `v2.1.0` |
| **V3 Stockout risk intelligence** | **Done**: V3.1 FEFO projection vs served forecast + lead time; V3.2 XGBoost risk model trained on synthetic replenishment histories; risk levels + lead-time floor + reasons; V3.3 Stockout risk page, dashboard card, `STOCKOUT_RISK` alerts; V3.4 ledger backtest vs V1/V3.1 rules — see §10b | `v3.0.0` |
| **V4 Supplier intelligence** | **Done**: supplier order log (orders, deliveries, receive-against-order, cancel/close short), OTIF reliability score + components per supplier and item, lead-time & delay prediction with temporal backtest, item comparison against the V3 stockout date, SUPPLIER_DELAY alerts — see §10c | `v4.0.0` |
| **V5 Procurement intelligence & optimisation** | **Done**: V5.1 replenishment (safety stock, reorder date, required / MOQ / expiry-capped qty); V5.2 supplier feasibility + in-transit orders weighted by historical arrival probability; V5.3 scenarios → Monte Carlo → configurable expected-cost model → OR-Tools CP-SAT; what-if simulator; V5.4 human approval (approve / modify / reject) that records V4 supplier orders — see §10d | `v5.0.0` |
| **V6 Operational knowledge graph** | **Done**: V6.1 graph model (11 node labels, 16 relationship types in 17 patterns) projected from PostgreSQL; V6.2 one-way sync with fingerprint staleness detection, generation-based rebuild and count verification, graceful degradation; V6.3 explanation chains; V6.4 supplier / item impact analysis; V6.5 predefined graph queries; Knowledge graph page — see §10e | `v6.0.0` |
| **V7 AI operations assistant** | **Done**: 20 read-only tools over the V1–V6 endpoint functions; deterministic planner + composer (default, no LLM, no keys); optional LLM (environment only) with tool whitelist, argument validation, step limit, structured output, grounding check and deterministic fallback; conversations, evidence and audit; AI assistant page — see §10f | `v7.0.0` |
| **V8 Multi-hospital SaaS** | **Done**: organizations, hospital memberships (role per hospital), verified active hospital + switching, organization / platform admins, ORM tenant guard + PostgreSQL triggers, onboarding, hospital-scoped CSV import, organization reporting — see §10g | `v8.0.0` |
| **V9 Integrations & data exchange** | **Done**: per-hospital integration sources; CSV/Excel upload, API push (hashed keys, rate limit), REST pull (retries, pagination, checkpoints, scheduled `run-due`); mapping, validation, idempotency, retry, reconciliation, monitoring, audit trail; local reference ERP simulator (labelled, off by default) — see §10h | `v9.0.0` |
| **V10 Real hospital pilot & business validation** | **Done (current)**: pilots with baseline + pilot periods, lifecycle, participants, V9 sources; metrics through the existing V1–V9 functions; descriptive comparison; decisions, feedback, issues, readiness, reports; synthetic sandbox — no real pilot yet — see §10i | `v10.0.0` |

## 3. Architecture

```text
Browser ─► Next.js 16 (frontend/, :3000) ─/api/* rewrite─► FastAPI (backend/, :8000) ─► PostgreSQL 16 (source of truth)
                                                                                        └─► Neo4j 5 (V6 projection only)
                                                            ├─ app/api        routers (HTTP, validation, permissions)
                                                            ├─ app/services   stock ledger, alert engine, audit
                                                            ├─ app/ml         V2 forecasting pipeline (in-process)
                                                            ├─ app/risk       V3 stockout risk (projection, simulator, classifier, engine)
                                                            ├─ app/supplier_intel  V4 supplier metrics, predictions, item comparison
                                                            ├─ app/procurement V5 replenishment, arrival distributions, simulation, OR-Tools, explanations
                                                            ├─ app/graph       V6 projection, sync, Cypher queries (Neo4j / FalkorDB executors)
                                                            ├─ app/assistant   V7 read-only tools, planner, optional LLM loop, conversations
                                                            ├─ app/services/tenancy.py + app/db/tenant_guard.py  V8 tenant context and guards
                                                            ├─ app/integrations V9 connectors, mapping, validation, sync engine, reconciliation, reference ERP simulator
                                                            ├─ app/pilots      V10 pilots: period metrics via existing functions, comparison, readiness, report
                                                            └─ app/models     SQLAlchemy 2 models (Alembic migrations)
```

- Stack: Python 3.11, FastAPI, SQLAlchemy 2 (typed `Mapped[...]`), Pydantic v2, Alembic, PostgreSQL 16;
  numpy/pandas/xgboost-cpu for ML; OR-Tools (CP-SAT) for V5 scenario selection; Neo4j 5 (official `neo4j` driver) for the V6 graph. Frontend: Next.js 16 App Router, React 19, TypeScript, Tailwind v4,
  TanStack Query, React Hook Form + Zod v4, Recharts, Radix primitives (shadcn-style components hand-written in
  `components/ui/`).
- **Next.js 16 differs from older versions**: `middleware.ts` is now `proxy.ts`; read
  `frontend/node_modules/next/dist/docs/` before using unfamiliar Next APIs (see `frontend/AGENTS.md`).
- Auth: JWT access + refresh in HttpOnly cookies; the browser only talks to the Next origin (rewrite to API).
- ML runs inside the FastAPI process (training is a few seconds). Separate workers only when needed.

## 4. Files you must not modify without an explicit request

- `backend/alembic/versions/*` — **never edit an existing migration**. Schema changes = a new revision.
- `backend/app/services/stock.py` ledger semantics (FEFO, append-only movements, `balance_after`).
- `backend/app/core/security.py`, `backend/app/api/auth.py`, `backend/app/core/permissions.py` existing entries
  (you may **add** permissions; do not weaken existing ones).
- The V1 API contracts in `docs/api.md` (paths, request/response shapes). Add fields; don't rename or remove.
- `backend/app/seed.py` simulation logic (it drives demo data **and** ML behaviour). Changing it changes model
  metrics — only with a reason, and re-check `tests/test_ml.py` and `tests/test_ml_procedures.py`. Keep it
  deterministic: fixed seeds and **sorted** iteration (never iterate a `set` of strings when drawing random numbers —
  hash randomisation changes the order between runs).
- `backend/app/ml/pipeline.py` V2B selection rules (`select_best`, `consistent_improvement`, `PRIORITY`): V2B must be
  strictly better than V2A on every validation fold. Do not loosen this to make V2B "win".
- `frontend/AGENTS.md` (generated by Next).

## 5. Coding rules

- Small, incremental changes; keep V1 and V2 behaviour working at every step. Run the tests before and after.
- Backend: routers stay thin (auth → load hospital-owned objects via `get_owned` → service → audit → one commit).
  Business logic in `services/` or `ml/`. Type hints everywhere. `ruff check app tests` must pass (line length 140).
- Frontend: client pages use TanStack Query; mutations go through `useAction` (toast + invalidation).
  Types in `lib/types.ts` mirror Pydantic schemas. `npm run lint` and `npm run typecheck` must pass.
  Charts follow the dataviz rules already used: one y-axis, validated palette slots (`#2a78d6`, `#eb6834`),
  legend for ≥2 series, text in text colours, tooltips on every chart.
- No new dependencies without a clear reason; pin versions in `requirements.txt` / `package-lock.json`.
- Never commit secrets. Config comes from environment variables (`app/core/config.py`, `.env.example`).

## 6. Database rules

- PostgreSQL is the system of record. Every tenant-owned row is scoped by `hospital_id` (directly or via parent);
  **every query filters by the caller's hospital**; foreign IDs return 404.
- Stock movements are **append-only**; corrections are new movements. Quantities are integers in base units.
- Money: `NUMERIC(12,2)`. Timestamps: `timestamptz` in UTC; "today" = business timezone (`TIMEZONE`, Asia/Kolkata).
- Schema change → update the model → `alembic revision --autogenerate -m "..."` → review the file →
  `alembic upgrade head && alembic check && alembic downgrade -1 && alembic upgrade head`.
- Model tables (`model_versions`, `model_item_metrics`, `forecasts`) are write-once per training run.

## 7. API rules

- All routes under `/api`, registered in `app/main.py`. Pydantic request/response models for everything.
- Permissions via `Depends(require(PERM))`; read endpoints need `read`, writes need a specific permission.
- Every state change writes an audit row (`services/audit.record`) in the same transaction.
- Errors: `HTTPException` with a human-readable `detail`; 404 for missing/foreign, 409 conflicts, 422 validation.
- Forecast endpoints (`/api/forecasts…`) **read stored forecasts of the active model** — they never train
  implicitly. Training happens only via `POST /api/forecasts/train`, `python -m app.ml.train`, or the entrypoint.

## 8. ML rules

- **No leakage**: a feature for day *d* uses data strictly before *d* (`tests/test_ml.py::test_features_use_only_the_past`).
- **Censoring**: days when an item was stocked out are NaN — excluded from targets and evaluation; never treat
  them as zero demand.
- **Always compare against baselines** on the same holdout (last `TEST_DAYS` = 14 days). The served model is the
  lowest-WAPE candidate; it may be a baseline — that is correct behaviour, not a bug.
- Store for every model version: name, type, training date, data window, holdout window, dataset hash, features,
  params, MAE, RMSE, WAPE, bias, per-item errors, and (XGBoost) the serialized booster + feature importance.
- Explanations come from **model output** (XGBoost SHAP contributions via `pred_contribs`) and stored metrics —
  never invented text. Intervals are labelled approximate.
- Determinism: fixed seeds; `test_xgboost_is_deterministic` must pass.
- Do not add deep learning (LSTM/TFT) until there is real data. Do not claim accuracy on real hospitals.

## 9. Testing requirements

| Layer | Command | Must stay green |
|---|---|---|
| Backend (SQLite) | `cd backend && pytest` | 235 tests (graph-store tests skipped without `TEST_GRAPH_URL`; the PostgreSQL-trigger test skipped on SQLite) |
| Backend (Postgres) | `TEST_DATABASE_URL=postgresql+psycopg://medflow:medflow@localhost:5432/medflow_test pytest` | same |
| Graph store | add `TEST_GRAPH_URL=bolt://localhost:7687 TEST_GRAPH_PASSWORD=…` (Neo4j) or `TEST_GRAPH_URL=redis://localhost:6379` (FalkorDB) | 235; none skipped on PostgreSQL |
| Lint | `ruff check app tests` · `npm run lint` · `npm run typecheck` | clean |
| Migrations | `alembic upgrade head && alembic check` | no drift |
| E2E | stack running with seed + trained models + a graph store, API started with `REFERENCE_ERP_ENABLED=true REFERENCE_ERP_TOKEN=…` → `npx playwright test` | 45 tests incl. the 390 px layout check (+1 screenshot spec, skipped unless `SCREENSHOTS=1`) |

New feature ⇒ new tests: API contract + permissions + hospital isolation (V8: `tests/test_multi_hospital.py` checks every id route automatically; calendar-sensitive fixtures use `frozen_day`); ML: data validation, feature tests,
a model regression test against baselines, and inference tests.

## 10. V2B — procedure-aware forecasting (built, `v2.1.0`)

`Scheduled procedures × kit quantity → expected demand → procedure features → V2B candidate`. Details:
`docs/ml-pipeline.md` (V2B section).

- Tables (migration `c67619e5a251`): `procedure_types`, `procedure_item_mappings`, `procedure_schedules`
  (SCHEDULED / COMPLETED / CANCELLED; partial unique index on (type, dept, date) for non-cancelled rows).
  **Counts only — never patient data.** Mappings come from the DB; never hard-code kit quantities in ML code.
- API `app/api/procedures.py`; permissions `procedures:manage` (admin) and `procedures:manage:own` (department
  manager, own department). Everyone else reads.
- ML `app/ml/procedures.py` (+ `features._procedure_features`, `XGBForecaster(proc=...)`, pipeline V2B branch).
  Rules that must hold:
  - Cancelled rows and inactive types/mappings never count; future = SCHEDULED rows only.
  - Beyond the last scheduled date the V2B forecast falls back to V2A (unknown ≠ zero procedures).
  - No procedure signal ⇒ V2B not trained, V2A served, nothing errors.
  - V2B served only if it beats V2A on the holdout **and** every rolling fold; ties keep V2A.
  - Leakage test `test_no_future_procedure_leakage_into_historical_features` must pass.
  - Explanations only from stored/computed numbers (no LLM). Don't describe a V2A/V2B gap as a schedule effect for
    items no scheduled procedure uses.
- Forecast API: all V2A fields unchanged; V2B fields are additive. Editing the schedule updates
  `procedure_impact` live and flags "retrain"; stored forecasts change only on retraining.
- Demo procedure data is synthetic (`is_synthetic`), labelled in the UI, not medically validated.
- Next.js rewrite proxy timeout is raised to 180 s (`experimental.proxyTimeout`) because training can exceed 30 s.

## 10b. V3 — stockout risk intelligence (built, `v3.0.0`)

`V1 stock + expiry → V2 served forecast → V3.1 projection (+ lead time) → V3.2 probability → level + reasons →
alerts/dashboard`. Details: `docs/ml-pipeline.md` (V3 section). Code: `backend/app/risk/`.

- Tables (migration `62cc820f507c`): `risk_model_versions`, `stockout_predictions` (append-only snapshots; newest per
  item = current risk). Permission `risk:train` (admin, procurement, inventory). API `app/api/risk.py`.
- Rules that must hold:
  - Features use data ≤ origin only (+ the procedure plan): `test_features_use_only_data_up_to_the_origin`.
  - The risk model is trained on **simulated** histories (labelled synthetic) whose parameters come only from the
    ledger's first 28 days; it is **evaluated** on the real ledger. Never tune simulator settings or thresholds on the
    backtest; thresholds come from the simulated validation replicate.
  - Always compare with the V1 reorder rule and the V3.1 cover rule on the same rows; the ML model is not served if
    its backtest PR-AUC is below the cover rule's.
  - The lead-time floor (MEDIUM when order-by ≤ 2 days away, HIGH when an order placed now arrives too late) must
    stay, and its cost stays measured (`metrics.backtest_with_floor`).
  - Report precision, recall, F1, PR-AUC, FP, FN, Brier/calibration, event and lead-time-aware recall — never
    accuracy alone. False negatives matter more than false positives.
  - Reasons/factors come from computed numbers; a "main factor" phrase is used only when the feature value supports it.
  - V1 alert types are unchanged; `STOCKOUT_RISK` is additive. Out-of-stock items keep the V1 OUT_OF_STOCK alert.
- Risk is refreshed after every stock movement (`alerts.evaluate` → `risk.engine.refresh`), after training, and
  lazily on the first read of a new day. Endpoints never train implicitly.
- V3's projection still assumes no deliveries: in-transit orders now exist in the V4 order log and are shown beside the
  risk; V5 (Procurement) builds its own in-transit-aware projection and leaves this one unchanged. Supplier delays in V3's simulator are still assumed, not measured.

## 10c. V4 — supplier intelligence (built, `v4.0.0`)

`supplier order log → OTIF reliability + components → lead-time / delay prediction → can each supplier deliver
before the V3 projected stockout?` Details: `docs/ml-pipeline.md` (V4 section). Code: `backend/app/supplier_intel/`,
`backend/app/services/supplier_orders.py`, API `app/api/supplier_intel.py`.

- Tables (migration `edd9c4eaa152`): `supplier_orders` (one item per order), `supplier_deliveries` (linked to the
  RECEIPT movement when received through the ledger). Receive against an order via `POST /inventory/receive` with
  `supplier_order_id` (additive; V1 receive unchanged). Recording/cancelling orders needs `suppliers:manage`.
- Rules that must hold:
  - The reliability score **is** the OTIF rate (shrunk with M = 5 towards the hospital rate), with a Wilson interval
    and the formula shown. Do not introduce invented weights; other components explain, they don't score.
  - No evidence → no number: suppliers without orders show "no orders"; P(in time) with n = 0 is "no history",
    never the smoothing prior.
  - Predictions only use orders whose outcome was known at the time (`test_backtest_uses_only_outcomes_known_on_the_order_date`);
    always compare with the quoted lead time and the hospital-wide late rate.
  - V4 reads V3 (latest stockout snapshot); it must not change V2 forecasts or the V3 engine.
  - **No autonomous purchasing**: V4 informs ("can meet the delivery window based on history"), never orders or
    recommends quantities. Reading intelligence endpoints never creates orders (tested).
  - The seed's V4 history uses its own random stream and creates no stock movements: V1–V3 demo results must stay
    identical (re-check V2/V3 metrics after touching `_seed_order_history`).
- `SUPPLIER_DELAY` alerts for orders past their expected date (additive alert type, same lifecycle).

## 10d. V5 — procurement intelligence & optimisation (built, `v5.0.0`)

`V1 stock/expiry/MOQ + V2 forecast + V3 risk + V4 order history → V5.1 replenishment → V5.2 arrival distributions
(new and in-transit orders) → V5.3 scenarios × Monte Carlo × expected-cost model → OR-Tools CP-SAT → V5.4 human
approval → recorded V4 supplier order`. Details: `docs/ml-pipeline.md` (V5 section). Code: `backend/app/procurement/`,
`backend/app/services/procurement.py`, API `app/api/procurement.py`, page `frontend/app/(app)/procurement/`.

- Tables (migration `fe7e6ae943d8`): `procurement_settings` (one row per hospital; the cost model), `procurement_recommendations`
  (scenarios, costs, settings and solver snapshotted as JSON for audit), `supplier_orders.recommendation_id` (additive).
  Permissions: `procurement:recommend` (admin, procurement, inventory), `procurement:approve` and `procurement:configure`
  (admin, procurement). Everyone reads.
- Rules that must hold:
  - **Recommend-only.** Generating, planning, what-if and reading never create orders (tested). Only an approval by a person
    with `procurement:approve` records supplier orders — in MedFlow's own V4 log; nothing is sent to a supplier or purchasing
    system. Stale recommendations (from an earlier day) cannot be approved. Modifying needs a reason; rejecting needs a reason.
  - **No hidden weights.** Every cost parameter lives in `procurement_settings` and is shown with the formula in the UI
    (`costs.py`). Don't add constants that change the ranking without exposing them.
  - **In-transit orders are never certain.** They count at their historical arrival probability (`arrival.py`: conditional on
    days already elapsed; shrunk towards the level above with V4's 5 pseudo-orders plus one pseudo-order that never arrives).
    With no comparable history they are not counted and the explanation says so.
  - **Not just the cheapest.** Scenarios are ranked by expected total cost; explanations cite k-of-n delivery evidence and
    money differences from computed numbers (no LLM).
  - Scenarios are costed by their difference to "no order" on the same simulated paths (common random numbers) — keep this;
    per-batch attribution is wrong under FEFO.
  - V5 reads V1–V4; it must not change V2 forecasts, the V3 engine or V4 metrics. V3's projection still ignores in-transit
    orders by design; V5's projection is the in-transit-aware one.
  - Arrival-probability calibration is reported (`/procurement/evaluation`, expanding window, no look-ahead) against
    "the quote is certain". Don't tune the shrinkage on that backtest.
- `python -m app.ml.train` ends by generating PENDING recommendations for the demo (nothing approved).

## 10e. V6 — operational knowledge graph (built, `v6.0.0`)

`PostgreSQL → projection (app/graph/projection.py) → graph store (Neo4j; FalkorDB for dev/tests) → explanation chains,
impact analysis, predefined queries`. Details: `docs/knowledge-graph.md`. Code: `backend/app/graph/`, API
`app/api/graph.py`, page `frontend/app/(app)/knowledge-graph/`.

- It is an **operational supply graph** (items, procedures, departments, suppliers, forecasts, risks, orders, procurement
  decisions) — not a biomedical knowledge graph, and no Graph ML / graph algorithms yet.
- Table (migration `13f7948e8cf3`): `graph_sync_runs` (fingerprint, expected vs found counts, verified, error).
  Permission `graph:sync` (admin, procurement, inventory). Config: `GRAPH_BACKEND` (neo4j | falkordb | disabled),
  `GRAPH_URL`, `GRAPH_USER`, `GRAPH_PASSWORD` (environment only — never committed), `GRAPH_NAME`.
- Rules that must hold:
  - **PostgreSQL is the source of truth.** The graph is derived: sync is one-way, idempotent (MERGE on
    `<hospital>:<Label>:<pg id>` keys, generation stamp, stale nodes/relationships deleted) and verified by counting what is
    in the graph against the projection. Never write business data to the graph, never read business decisions from it.
  - **Degrade, don't break.** No V1–V5 code path imports `app.graph`. With the graph store down, graph endpoints answer
    503 ("…unaffected") and everything else works (tested). A failed sync is recorded, never raised into a caller.
  - **One Cypher dialect.** Queries are written once in the openCypher subset both Neo4j and FalkorDB run; return plain
    values (`properties(n)`, scalars, maps), never driver node objects. No APOC, no `COUNT {}` subqueries.
  - **Hospital isolation.** Every query anchors on a hospital-scoped key or filters `hospital_id`; ownership of item /
    supplier ids is checked in PostgreSQL first (foreign → 404). Tested with two hospitals in one graph.
  - Explanations are sentences built from numbers on nodes/relationships, shown with the Cypher that produced them. No LLM
    (that is V7).
  - Reads re-sync automatically when the PostgreSQL fingerprint changed; `python -m app.ml.train` and
    `python -m app.graph.cli sync|check` sync explicitly.

**Graph store verification (use this wording):** The graph implementation was tested using FalkorDB, an openCypher-compatible graph engine. Neo4j is configured as the production graph store and is covered by CI configuration, but Neo4j execution could not be locally verified because the required Neo4j image/download endpoints returned HTTP 403 in the development environment.

## 10f. V7 — AI operations assistant (built, `v7.0.0`)

`question → (write request? refuse) → deterministic planner or optional LLM → read-only MedFlow tools → answer from tool
facts → stored with its evidence + audit row`. Details: `docs/assistant.md`. Code: `backend/app/assistant/`, API
`app/api/assistant.py`, page `frontend/app/(app)/assistant/`.

- Tables (migration `181a85264e64`): `assistant_conversations` (private to their user; `context` = conversation memory),
  `assistant_messages` (answer, tool calls, provider, model, intent, grounded, fallback reason, latency). Every question
  also writes an `assistant.ask` audit row. Permission: `read`. Config: `ASSISTANT_PROVIDER` (none | openai_compatible |
  anthropic), `ASSISTANT_BASE_URL`, `ASSISTANT_MODEL`, `ASSISTANT_API_KEY` (environment only — never committed),
  `ASSISTANT_TIMEOUT_SECONDS`, `ASSISTANT_MAX_STEPS`, `ASSISTANT_RATE_LIMIT_PER_MINUTE`.
- Rules that must hold:
  - **The assistant does not calculate.** Tools call the existing endpoint functions (`risk.item_risk`,
    `supplier_intel.item_options`, `procurement.item_plan`, `graph.explain`, …) with the caller's user and session; the answer
    repeats their numbers and explanation sentences. Don't add scoring, forecasting or estimation to `app/assistant/`.
  - **Read-only.** No write tool in `tools.TOOLS`; imperative write requests are refused before any tool or model runs; tested
    that asking never creates orders, recommendations or movements. Approval stays with people (V5).
  - **Default is deterministic, with no LLM and no keys.** An LLM is optional and configured only through the environment. Its answer is used
    only if it called at least one tool, returned the structured JSON, and every number in it occurs in this turn's tool
    results (`llm.grounded`); otherwise the planner answers and the reason is stored and shown.
  - Tool results are data: the system prompt says so; tool arguments are validated against the JSON schema (`llm.validate_args`).
  - **Hospital isolation.** Entity resolution only searches the caller's hospital; tools re-check ownership (404);
    conversations are private to their user.
  - Graph tools degrade: with the graph store down the answer notes it and the other tools still answer.
  - Be honest about LLM status: no real LLM has been executed in development (downloads blocked); tests use `ScriptedProvider`
    and mocked HTTP for the adapters.

## 10g. V8 — multi-hospital SaaS (built, `v8.0.0`)

`Platform → Organization → Hospital → memberships → everything operational`. Details: `docs/multi-hospital.md`,
release report `docs/v8-multi-hospital.md`. Code: `app/services/tenancy.py`, `app/api/tenancy.py`, `app/api/deps.py`
(request context), `app/db/tenant_guard.py`, `app/services/org_reporting.py`, `app/services/imports.py`; pages
`frontend/app/(app)/organization/`, `frontend/app/(app)/platform/`, header `components/hospital-switcher.tsx`.

- Tables (migration `623279e318d6`): `organizations`, `hospital_memberships`, `organization_memberships`;
  `hospitals.organization_id/status`; `users.is_platform_admin`, `users.hospital_id/role` nullable; `audit_logs.organization_id`.
- Rules that must hold:
  - **Memberships grant hospital access; nothing else does.** `users.hospital_id/role/department_id` is the ACTIVE context —
    a cache of an active membership. Change it only via `tenancy.switch`, `tenancy.ensure_context`,
    `tenancy.sync_pointer_after_membership_change` (or seed). Never assign it from client input.
  - Every request goes through `deps.get_current_user` (token hospital binding, `tenancy.verify_request_context`, the
    `X-MedFlow-Hospital` stale-tab check) and hospital endpoints through `require(...)` (needs an active hospital). Never
    add a hospital endpoint without `require`.
  - Keep **every query filtered by the caller's hospital** and `get_owned` for ids (404 for other hospitals). The
    systematic test `test_every_route_with_an_id_rejects_another_hospitals_ids` covers new id routes automatically — add
    the new path parameter's hospital-B value to `_path_params`.
  - Do not remove or bypass the ORM guard / PostgreSQL triggers (`tenant_guard.py`). New hospital-owned tables are covered
    automatically by the ORM guard; add them to a new migration's trigger list.
  - Organization and platform admins get no operational hospital data unless they hold a membership.
  - Organization reporting uses explicit aggregate queries over authorized hospitals and shows per-hospital rows; never pool
    supplier metrics across hospitals.
  - Uniqueness stays per hospital (`(hospital_id, sku)` etc.), never global.
  - Assistant: tools have no hospital parameter; cross-hospital questions are refused in `planner.plan` before tools/LLM.
  - Background jobs take an explicit hospital (`ml.train`, `graph.cli` loop hospitals; `alerts.evaluate(db, hid)`).
  - No billing / subscriptions / SSO / fake enterprise features.
- The Sunrise demo profile (`seed.SUNRISE`) must keep producing the V1–V7 data exactly (same seeds, same order).

## 10h. V9 — integrations & data exchange (built, `v9.0.0`)

`hospital system → connector (upload | api_push | rest_pull) → mapping → validation → idempotency → EXISTING services →
PostgreSQL → V2–V7 unchanged`. Details: `docs/integrations.md`, release report `docs/v9-integrations.md`. Code:
`backend/app/integrations/`, API `app/api/integrations.py` (+ `ingest_router`, API-key auth), page
`frontend/app/(app)/integrations/`.

- Tables (migration `5684eca6bc5c`): `integration_sources`, `integration_credentials`, `integration_mappings`, `sync_runs`,
  `sync_records`, `external_refs`, `sync_checkpoints`, `reconciliation_issues` (all hospital-owned; tenant triggers).
  Permissions `integrations:run` (admin, procurement, inventory) and `integrations:manage` (admin).
- Rules that must hold:
  - **No algorithm changes.** Imported data goes through `stock.issue` / `stock.receive` / `stock.adjust`,
    `supplier_orders.create_order` / `record_delivery` / `cancel_or_close` and catalogue upserts. Never write movements,
    batches or orders directly from `app/integrations/`, never retrain implicitly.
  - **Append-only ledger.** Transactions older than the item's latest movement are rejected (`out_of_order`); a re-sent
    transaction with different content is rejected (`changed_transaction`). Corrections are new transactions.
  - **Never auto-correct stock.** Snapshots create reconciliation issues; only a person resolves them (adjust needs
    `stock:receive`). Opening balances only for items with no ledger at all.
  - **Idempotent and auditable.** Every run is a `sync_runs` row + audit row; rejected records are kept for retry; dry runs
    roll back everything but the run record. Each record is applied in its own SAVEPOINT.
  - **Secrets.** API keys only as HMAC hashes (shown once); outbound tokens only via env-var NAMES (`MEDFLOW_INTEGRATION_*`);
    `rest_pull` hosts must be on `INTEGRATION_ALLOWED_HOSTS` (SSRF). Never store a secret in `config`.
  - **Tenancy.** A key writes only into its source's hospital; code lookups are per hospital; new id routes are in the
    V8 sweep (`_path_params`: source_id, run_id, credential_id, issue_id, entity, hospital_code, resource).
  - **No fake integration.** The reference ERP simulator (`app/integrations/reference_erp.py`) is off by default, token
    protected, demo hospitals only, labelled simulated. Do not describe MedFlow as connected to a hospital system unless it
    was actually tested with that system. Required wording: "MedFlow supports a validated integration framework and
    controlled data-import/API connectors. Production hospital-system integrations require access to the corresponding
    hospital/vendor systems and have not been claimed unless actually tested."
  - No scraping or screen automation of other systems; no outbound orders to purchasing systems (V5 stays recommend-only).
- Demo: `seed_all` adds three sources per CareNet hospital (configuration only — Sunrise data unchanged, fingerprint-checked).

## 10i. V10 — real hospital pilot & business validation (built, `v10.0.0`)

`pilot (one hospital) → baseline period + pilot period → metrics computed on demand through the EXISTING functions →
descriptive comparison → people's decisions, feedback, issues, readiness → report`. Details and release report:
`docs/v10-pilot.md`. Code: `backend/app/pilots/`, API `app/api/pilots.py`, pages `frontend/app/(app)/pilots/`.

- Tables (migration `b8c6b2c0cc67`): `pilots`, `pilot_participants`, `pilot_data_sources`, `pilot_issues`, `pilot_feedback`,
  `recommendation_views`, `pilot_readiness`, `pilot_report_snapshots` (hospital-owned, tenant triggers). Permissions
  `pilots:manage` (admin) and `pilots:contribute` (admin, procurement, inventory, department manager); reading = `read`.
- Rules that must hold:
  - **Measure, don't model.** Every metric calls the function that already defines it (V3 `load_history`, V2
    `forecast_metrics` + `load_panel`, V3.4 `confusion`/`prf`, V4 `summarize`, V5 records, V9 runs). Never add a model, a
    second implementation of a calculation, or a stored metric table.
  - **Honest numbers.** Show the formula and n; below the minimum sample return "Insufficient data" (never 0). Baseline and
    pilot never overlap (API + CHECK). Comparisons are descriptive (pp + relative, per-30-day normalisation labelled); no
    "better/worse", no causal wording.
  - **Synthetic vs observed.** Pilots in demo hospitals are `synthetic`; the UI and report say "DEMO / SYNTHETIC DATA —
    NOT REAL HOSPITAL DATA", "Synthetic/demo result" and "No real-world outcome claim can be made yet." Never write
    savings / reduction / improvement claims or "validated in real hospitals" unless a real pilot produced the data.
  - Issues and feedback never change operational data; decisions stay with V5's human approval.
  - New id routes are in the V8 sweep (`_path_params`: pilot_id, pilot_issue_id, snapshot_id, item_key).
- No real hospital pilot has taken place. The next step is a real pilot, not a new software version.

## 11. What NOT to implement (until the user asks for that version)

Graph ML / graph algorithms (only with a real use case), billing / subscriptions / SSO (not planned),
HL7 / FHIR / EDI adapters and vendor-specific ERP connectors (only with a real system to test against). No autonomous procurement (V5 recommends; people approve), no outbound connection to purchasing systems, no LLM in V5, no write tools or autonomous actions for the V7 assistant, no Kubernetes/microservices, no patient data.

## 12. Working incrementally without breaking V1

1. `git status` clean → create a branch.
2. Run the full test suite first; note the baseline.
3. Make the smallest vertical slice (model → migration → service → API → UI → tests); run tests after each step.
4. Re-seed and re-train locally (`python -m app.seed --reset && python -m app.ml.train`) and click through the
   V1 flows (receive/issue, alerts) as well as the new feature.
5. Update docs (`docs/*.md`, README, this file's §2) and bump versions only when the version is complete.
