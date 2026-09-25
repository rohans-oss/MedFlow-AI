# Product Roadmap

| Version | Goal | Status |
|---|---|---|
| V0 | Research & validation | **Drafts done** (`docs/research`). Interviews still to do — exit criteria in `docs/research/README.md`. |
| V1 | Working inventory MVP | **Done** (`v1.0.0`) |
| V2A | Consumption forecasting (baselines, XGBoost, evaluation, registry, API, UI) | **Done** (`v2.0.0`) |
| V2B | Procedure-aware forecasting (procedure types, schedule, item mappings, V2B candidate, UI) | **Done** (`v2.1.0`) |
| V3 | Stockout intelligence (probability, date, shortage, reasons, risk alerts, evaluation) | **Done** (`v3.0.0`) |
| V4 | Supplier intelligence (order log, OTIF reliability score, lead-time & delay prediction, item-level comparison linked to V3) | **Done** (`v4.0.0`) |
| V5 | Procurement intelligence & optimisation (replenishment, in-transit-aware feasibility, scenarios with a configurable expected-cost model, OR-Tools, what-if, human approval) | **Done** (`v5.0.0`) |
| V6 | Operational knowledge graph (Neo4j projection of PostgreSQL: explanation chains, impact analysis, graph search) | **Done** (`v6.0.0`) |
| V7 | AI operations assistant (read-only tool calling over V1–V6: deterministic planner by default, optional LLM with grounding check, audit) | **Done** (`v7.0.0`) |
| V8 | Multi-hospital SaaS (organizations, memberships, verified hospital context & switching, tenant guards in ORM and PostgreSQL, organization / platform administration, onboarding, CSV import, organization reporting) | **Done** (`v8.0.0`) |
| V9 | Real hospital integrations & data exchange (integration framework per hospital, CSV/Excel upload, API push with hashed keys + rate limit, REST pull with retries / pagination / checkpoints, scheduled feeds, field mapping, validation, idempotency, retry, reconciliation, monitoring, audit trail, local reference ERP simulator — no real hospital system connected or claimed) | **Done** (`v9.0.0`) |
| V10 | Real hospital pilot & business validation (pilots with baseline and pilot periods, metrics through the existing V1–V9 functions, descriptive comparison, decisions, feedback, issues, readiness, reports, synthetic sandbox — no real hospital pilot yet) | **Current — done** (`v10.0.0`) |
| Next | A real pilot with one hospital using V10 (permission, tested data connection, real baseline, legal/privacy review). No new software version is planned until it produces observed data. | Not started |

## V10 acceptance checklist

- [x] Pilot management and lifecycle (planned / active / paused / completed / cancelled), one hospital per pilot, participants, data sources
- [x] Configurable baseline and pilot periods, never overlapping (API + database constraint)
- [x] Metrics through existing functions: inventory, stockouts, forecasting (V2), stockout prediction (V3.4), suppliers (V4), procurement decisions (V5), adoption, data quality and integration reliability (V9)
- [x] Descriptive baseline vs pilot comparison with pp / relative change, per-30-day normalisation, "Insufficient data", external factors, causality statement
- [x] Decision tracking (generated / viewed / approved / modified / rejected / expired) — no orders created
- [x] Feedback, issue tracking (no automatic inventory changes), readiness checklist, report + snapshots
- [x] Pilot sandbox on synthetic demo data, labelled everywhere; no fake hospitals, no business claims
- [x] Hospital / organization isolation (V8 sweep extended), tests, E2E, docs

## V9 acceptance checklist

- [x] Integration framework: sources per hospital, enable / disable, connection status + test, last successful / failed sync, sync history
- [x] CSV / Excel import for inventory, items, suppliers, consumption, purchase orders, deliveries (+ departments, supplier catalogue); dry run
- [x] REST API ingestion: per-source API keys stored as hashes (shown once, revocable), hospital-specific configuration, validation, per-key rate limiting, error handling
- [x] REST pull connector: outbound token by environment-variable name, host allowlist, retries with back-off, pagination, incremental checkpoints; scheduled feeds (`cli run-due`, compose scheduler)
- [x] Data mapping per source and entity (e.g. material_code → item.code, po_number → supplier order reference), defaults, date formats
- [x] Validation: missing fields, invalid dates, unknown items / suppliers / departments / POs, duplicates, negative quantities, inactive departments, out-of-order and insufficient-stock transactions
- [x] Sync engine: initial / incremental, retry of rejected records, idempotency (`external_refs`), checkpoints, per-record savepoints
- [x] Reconciliation: stock-count differences vs the ledger at the snapshot time → issues resolved by a person (adjust / external wrong / accept), auto-close when matched later
- [x] Monitoring dashboard: connected systems, health, records processed / rejected, errors, last sync, data freshness
- [x] Audit trail: every run with hospital, source, started / completed, received / created / updated / rejected, status, error summary
- [x] No fake integration: local reference ERP simulator, labelled simulated, off by default, demo hospitals only; limitation stated in the UI and docs
- [x] No V2–V7 algorithm changed; data flows into them through the existing services
- [x] Tests (18 V9 + isolation sweep extended to the 16 new id routes), E2E spec, docs

## V1 acceptance checklist

- [x] Login, refresh, logout, change password; 5 roles with enforced permissions
- [x] Hospital profile & expiry policy; departments; categories; users (admin)
- [x] Consumable catalogue with reorder/max levels
- [x] Batches with lot and expiry; usable vs expired stock
- [x] Receipts, FEFO issues, returns, wastage, count adjustments — immutable ledger with balances
- [x] Suppliers with catalogue (price, lead time, MOQ, preferred)
- [x] Dashboard: supply health, value, expiry exposure, alerts, 30-day consumption, departments, recent movements
- [x] Alerts: out of stock, low stock, expired, expiring soon; acknowledge/resolve; auto-resolve; escalation
- [x] Audit log
- [x] Realistic demo hospital (synthetic, labelled)
- [x] Backend tests (42), E2E tests (5), CI, Docker Compose

## V2A acceptance checklist

- [x] Baselines: historical average, 7-day moving average
- [x] XGBoost with lag 1/7/14, 7/14-day rolling mean, volatility, trend, day of week, item, department
- [x] Stock-out days censored (not treated as zero demand)
- [x] Holdout evaluation on the last 14 days: MAE, RMSE, WAPE, bias — overall and per item
- [x] Model registry: version, training date, data window, dataset hash, params, metrics, feature importance, artifact
- [x] `GET /api/forecasts/{item_id}?days=14` returning item, horizon, predicted demand, model version (+ range, drivers, explanation)
- [x] Forecasts page: item selector, history → forecast chart, next 7/14/30 days, drivers, backtest, model evaluation
- [x] Retrain from UI/CLI/entrypoint; stale-model flag
- [x] Tests: features/no-leakage, metrics, baselines, model regression vs baseline, determinism, censoring, pipeline, API

## V2B acceptance checklist

- [x] Tables: procedure types, schedule (SCHEDULED / COMPLETED / CANCELLED), procedure → item mappings; Alembic migration up/down/up
- [x] CRUD API + Procedures page; RBAC (admin all, department manager own department, others read-only); audit rows
- [x] Synthetic procedure generator, labelled synthetic in DB and UI
- [x] Features: procedure count, expected quantity, type count, department count, demand share (+ 7-day delta)
- [x] Mandatory no-leakage test; future schedule cannot change the backtest
- [x] Same-holdout comparison of hist / MA-7 / V2A / V2B (MAE, RMSE, WAPE, bias); rolling folds; V2B never forced
- [x] Fallback to V2A: no procedure data, beyond the known schedule, V2B not better
- [x] Forecast API extended (V2A contract unchanged): source, procedure impact, comparison, explanation, stock cover
- [x] Forecast UI: comparison, procedure impact, V2A line + kit bars, stock vs forecast, fold table
- [x] Tests: backend 90 (SQLite + Postgres), E2E 12 + screenshots

## V3 acceptance checklist

- [x] V3.1 deterministic projection: usable stock (expiry-aware, FEFO) vs served V2 forecast → days left, stockout date, shortage, lead time, order-by, can a delivery arrive in time
- [x] V3.2 XGBoost stockout-probability model on point-in-time features (stock, 7/14/30-day demand, recent consumption, trend, volatility, procedure demand, lead time, reorder level, cover, expiry, department, stockout history); leakage test
- [x] Training data from a labelled synthetic replenishment simulator parameterised from pre-backtest ledger data
- [x] Risk levels (recall-target and precision thresholds + lead-time floor), plain-English reasons, SHAP factors
- [x] V3.3 Stockout risk page (high/medium/low, probability, date, shortage, days left, reason, projection chart), dashboard card, STOCKOUT_RISK alerts, live refresh on stock movements
- [x] V3.4 backtest on the ledger: precision, recall, F1, PR-AUC, FP, FN, Brier, calibration, event recall, lead-time-aware recall — vs V1 reorder rule and V3.1 cover rule; serving guard
- [x] Tests: backend 107 (SQLite + Postgres), E2E 16

## V4 acceptance checklist

- [x] Supplier order log (orders, deliveries, cancel / close short); receive against an order; audit rows; RBAC
- [x] Synthetic order history (12 months + simulated period, labelled synthetic) — V1–V3 results verified unchanged
- [x] Performance per supplier and supplier × item: orders, on time, late, days late, lead time (median / p90 vs quoted), cancellations, fill rate, price stability & trend, monthly trend
- [x] Reliability score = measured OTIF rate with small-sample shrinkage, 95 % interval, grade; formula shown in the UI
- [x] Item comparison: price vs cheapest, MOQ, quoted / typical / worst-case delivery, reliability, P(delivered before the V3 projected stockout); "cheapest isn't safest" finding
- [x] In-transit orders with predicted arrival; SUPPLIER_DELAY alerts
- [x] Lead-time and delay prediction with a no-look-ahead temporal backtest vs quoted / hospital-wide baselines
- [x] No autonomous purchasing: information for procurement review only
- [x] Tests: backend 125 (SQLite + Postgres), E2E 20

## V5 acceptance checklist

- [x] V5.1 replenishment: usable stock, forecast demand, safety stock (service level, forecast error, lead-time spread), projected inventory, reorder date, required quantity, MOQ adjustment, expiry and storage caps
- [x] V5.2 feasibility: per-supplier arrival distributions from V4 history; in-transit orders counted at their historical arrival probability (conditional on elapsed days), never as certain; "no evidence" stated
- [x] V5.3 scenarios (no order, supplier × quantity, split) evaluated by Monte Carlo on common random numbers; configurable expected-cost model (purchase + stockout + holding + expiry − carried forward), no hidden weights; OR-Tools CP-SAT selection with optional budget
- [x] Explanations from numbers ("₹… more than the cheapest … arrived within the window in k of n orders"); not "AI recommends"
- [x] What-if simulator (supplier delay, demand change) on the same scenarios and paths
- [x] V5.4 approval queue: approve / modify (reason) / reject (reason), stale-recommendation guard, audit; approval records V4 supplier orders only
- [x] Procurement page: Needs attention, Recommendations, Scenarios, What-if, Approval queue (+ Cost model)
- [x] Arrival-probability backtest (no look-ahead) vs trusting the quote
- [x] No LLM, no auto-purchase, no external purchasing system
- [x] Tests: backend 140 (SQLite + Postgres), E2E 24

## V6 acceptance checklist

- [x] V6.1 graph model: Hospital, Department, Category, Item, Procedure, Supplier, Batch, Forecast, StockoutRisk, SupplierOrder, ProcurementRecommendation; relationships carry evidence (usage, kit quantities, schedule counts, prices, k-of-n delivery history)
- [x] V6.2 PostgreSQL stays the source of truth: one-way projection, fingerprint staleness detection + automatic re-sync, generation-based rebuild, count verification, sync-run audit table; graph down ⇒ 503 on graph endpoints only, core MedFlow unaffected (tested)
- [x] V6.3 explanation chain: item → risk → forecast → scheduled procedures → departments → suppliers & delivery history → orders in transit → procurement option, with a layered neighbourhood drawing and the Cypher
- [x] V6.4 impact analysis: supplier unavailable (sole-source classification, procedures, departments, risks, orders, recommendations, chains) and item shortage
- [x] V6.5 graph search: 9 predefined questions, Cypher shown
- [x] Hospital isolation in a shared graph; no patient data; no LLM; no Graph ML
- [x] Neo4j in docker compose and CI. The graph implementation was tested using FalkorDB, an openCypher-compatible graph engine. Neo4j is configured as the production graph store and is covered by CI configuration, but Neo4j execution could not be locally verified because the required Neo4j image/download endpoints returned HTTP 403 in the development environment.
- [x] Tests: backend 150 (SQLite + Postgres, graph tests on FalkorDB), E2E 28

## V7 acceptance checklist (AI operations assistant)

- [x] 20 read-only tools wrapping the existing V1–V6 endpoint functions (same numbers as the pages, same hospital scoping); no write tool
- [x] Deterministic planner (default, no LLM, no API keys): intents + entity resolution + conversation memory ("it"), answers composed only from tool facts; clarifying answers for ambiguous / unknown names
- [x] All example questions answered on the demo (why at risk, suppliers within N days, supplier unavailable, procedures affected, why the optimizer chose X, compare with cheapest, high-risk single-source, what to review today, most delays, run out next week, driving demand, supplier N days late)
- [x] Write requests refused before any tool or model runs; approvals stay with people (tested: nothing created)
- [x] Optional LLM via environment only (`openai_compatible` — e.g. a local Ollama / llama.cpp / vLLM — or `anthropic`): tool whitelist, argument validation, step limit, structured JSON output, grounding check, deterministic fallback with the reason shown
- [x] Conversations + messages stored with tool calls, provider, model, grounding and latency; audit row per question; conversations private to their user; per-user rate limit
- [x] AI assistant page: examples, answer card, evidence panel (every tool call with arguments and facts), links, follow-ups, conversation list
- [x] Graph down ⇒ graph-backed answers say so; the other tools still answer (tested)
- [ ] **Not done here:** no real LLM was executed (model downloads and model servers unreachable in the development environment); LLM answer quality is unevaluated. The loop is tested with a scripted provider, the adapters with mocked HTTP.
- [x] Tests: backend 168 (SQLite + Postgres, graph tests on FalkorDB), E2E 32

## V8 acceptance checklist (multi-hospital SaaS)

- [x] Organizations, hospitals (`organization_id`, status), explicit hospital memberships (role, department, status), organization admins, platform admins
- [x] Hospital context server-verified on every request (membership, hospital & organization status, token bound to the hospital, stale-tab header); hospital switching
- [x] Isolation tested for inventory, departments, procedures, forecasts, risk, suppliers, orders, procurement, alerts, audit, graph, assistant and conversations — including every id-taking route × 3 roles and body-id attacks
- [x] Database guards: ORM tenant guard (all databases) + PostgreSQL triggers (15 tables) + active-hospital pointer trigger; per-hospital uniqueness
- [x] Hospital-specific procurement settings (tested)
- [x] Graph sync / queries / impact per hospital; PostgreSQL stays the source of truth; graph failure still isolated
- [x] Assistant: all 20 tools stay in the active hospital; cross-hospital questions refused before any tool/model; V7 read-only behaviour unchanged
- [x] Hospital, organization and platform administration; onboarding; hospital-scoped CSV import; organization reporting (explicit per-hospital aggregates)
- [x] Demo: Sunrise (unchanged) + Lakeview (same group, different data) + Harbor (other organization); group admin and platform admin accounts
- [x] Migration upgrade → downgrade → upgrade on an empty DB and on a copy of the V7 demo DB
- [x] Tests: see `docs/v8-multi-hospital.md` for exact counts
- [ ] Not done by design: billing, subscriptions, SSO, real integrations

## Known V1 limitations

- Single central store per hospital (no sub-store transfers yet).
- No purchase orders — receipts are recorded directly (POs come with V4/V5).
- Reorder level is static and manually set (V2/V3 make it data-driven).
- Rate limiter is per-process.
