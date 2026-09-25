# MedFlow AI — Hospital Supply Intelligence Platform

> Operations and procurement software for hospital consumables. **Not a clinical or diagnostic system.**

**Current release: Version 10 — Real hospital pilot & business validation**, on top of V1 inventory, V2 demand forecasting, V3 stockout risk, V4 supplier intelligence, V5 procurement optimisation, V6 operational knowledge graph, V7 AI operations assistant, V8 multi-hospital SaaS and V9 integrations & data exchange.
V1 success condition: *a user can manage a realistic hospital inventory from the web application.*
V2 success condition: *the system can forecast future consumable demand and explain the important inputs.*
V3 goal: *predict shortages before they happen — which items, when, how short, and why.*
V4 goal: *procurement managers can understand supplier risk before placing an order.*
V5 goal: *what should we buy, how much, from whom, and by when — recommended, never purchased automatically.*
V6 goal: *make the relationships between items, procedures, departments, suppliers, forecasts, risks and procurement decisions explicit and queryable.*
V7 principle: *the AI does not make the operational calculations — it queries and explains the results V1–V6 already produce.*
V8 goal: *many hospitals in one application — and Hospital A can never see, query, modify, forecast, explain or infer Hospital B's data.*
V9 goal: *receive and check the operational data a hospital's own systems already hold — better data into the existing V1–V8 systems, without changing their algorithms.*
V10 goal: *be able to run a real hospital pilot and measure — honestly, against a baseline — whether MedFlow's existing decision support is useful.* **No real hospital pilot has taken place yet; all pilot results so far are synthetic/demo results.**

| | |
|---|---|
| ✅ V0 Research & validation | Draft problem statement, personas, workflow map, pain points, MVP spec, interview guide → [`docs/research`](docs/research/README.md) (hypotheses — **not yet validated with interviews**) |
| ✅ V1 Inventory MVP | Auth + roles, hospital, departments, categories, consumables, batches with expiry, stock ledger (receive / issue FEFO / return / wastage / count), suppliers + catalogue, dashboard, rule-based alerts, audit log |
| ✅ V2A Consumption forecasting | Baselines (7-day moving average, historical average) vs global XGBoost; stock-out censoring; holdout MAE / RMSE / WAPE / bias; model registry; stored 30-day forecasts; `GET /api/forecasts/{item_id}?days=14`; Forecasts page with history → forecast chart, 7/14/30-day totals, SHAP drivers, backtest → [`docs/ml-pipeline.md`](docs/ml-pipeline.md) |
| ✅ V2B Procedure-aware forecasting | Procedure types, schedule and procedure → item mappings (DB + Procedures page, RBAC); procedure features added to a second XGBoost candidate; served **only** if it beats V2A on the holdout and every rolling validation window, otherwise V2A stays; falls back to V2A with no procedure data or beyond the known schedule; procedure impact, V2A vs V2B comparison and stock vs forecast on the Forecasts page → [`docs/ml-pipeline.md`](docs/ml-pipeline.md#version-2b--procedure-aware-forecasting) |
| ✅ V3 Stockout risk | For every item: **probability of a stockout within 14 days, expected stockout date, 14-day shortage and a plain-English reason** — V1 stock + expiry and the V2 forecast projected day by day against supplier lead time (V3.1), an XGBoost risk model trained on labelled synthetic replenishment histories (V3.2), a Stockout risk page + dashboard card + `STOCKOUT_RISK` alerts (V3.3), and a backtest on the hospital's own ledger vs the V1 reorder rule (V3.4) → [`docs/ml-pipeline.md`](docs/ml-pipeline.md#version-3--stockout-risk-intelligence) |
| ✅ V4 Supplier intelligence | Supplier **order log** (record orders, receive against them, cancel / close short) → per supplier and per item: on time, days late, lead time (median / 90th pct vs quoted), cancellations, fill rate, price stability — and a **reliability score that is the measured on-time-in-full (OTIF) rate** (small-sample shrinkage, 95 % range, formula shown). For each V3 at-risk item: **can each supplier deliver before the projected stockout, based on its own history** ("14 of 15 past orders"), in-transit orders, overdue-order alerts, and a lead-time / delay prediction backtest. Nothing is ordered automatically → [`docs/ml-pipeline.md`](docs/ml-pipeline.md#version-4--supplier-intelligence--reliability) |
| ✅ V5 Procurement intelligence | For every item needing a decision: **replenishment maths** (safety stock, reorder date, required / MOQ / expiry-capped quantity), **in-transit orders weighted by their historical arrival probability**, **scenarios** (no order, supplier × quantity, split) simulated on the same demand/delivery paths and costed with a **configurable expected-cost model** (purchase + stockout + holding + expiry − carried forward), chosen with **OR-Tools CP-SAT** (optional budget), explained with numbers; **what-if** simulator; **approval queue** (approve / modify with reason / reject) — an approval records the order in MedFlow, nothing is sent to a supplier. No LLM → [`docs/ml-pipeline.md`](docs/ml-pipeline.md#version-5--procurement-intelligence--optimisation) |
| ✅ V6 Operational knowledge graph | PostgreSQL projected (one way, verified, auto re-synced) into **Neo4j**: 11 node types, relationships carrying evidence. **Explain a risk** as a traversal (item → risk → forecast → scheduled procedures → departments → suppliers & delivery history → orders → procurement option), **impact analysis** (supplier unavailable / item shortage), **graph search** (9 predefined questions, Cypher shown). If Neo4j is down only that page is affected. No LLM, no Graph ML → [`docs/knowledge-graph.md`](docs/knowledge-graph.md) |
| ✅ V7 AI operations assistant | Ask in plain English — *Why is Suture 2-0 at risk? Which suppliers can cover it within 4 days? What happens if CPS becomes unavailable? Why did the optimizer choose CPS instead of OPI? What should I review today?* — and get an answer built **only from MedFlow's own tools** (20 read-only tools wrapping the V1–V6 endpoints), with the evidence (every tool call, its arguments and facts) under each answer. **Default: a deterministic planner — no LLM, no API keys.** Optional LLM (a local model or a hosted provider, configured only via environment) behind guardrails: tool whitelist, argument validation, step limit, structured output and a **grounding check** (every number must come from the tool results, else the deterministic answer is used). Read-only: write requests are refused; approvals stay with people. Conversations + audit log → [`docs/assistant.md`](docs/assistant.md) |
| ✅ V8 Multi-hospital SaaS | **Organizations → hospitals → memberships** (one account, several hospitals, a role per hospital). The active hospital is **verified on every request** (membership, token bound to the hospital, stale-tab check) and switched from the header; isolation enforced in the routers, an ORM guard and **PostgreSQL triggers**, and tested across every id-taking route, the ML pipelines, the knowledge graph and the assistant (which refuses questions about other hospitals). Hospital, organization and platform administration, onboarding, hospital-scoped CSV import, organization reporting (per-hospital aggregates, never pooled). No billing / SSO / integrations → [`docs/multi-hospital.md`](docs/multi-hospital.md) |
| ✅ V9 Integrations & data exchange | Per-hospital **integration sources** with three connectors — **CSV / Excel upload**, **API push** (`POST /api/ingest/v1/{entity}` with per-source API keys stored as hashes, rate-limited) and **REST pull** (retries, pagination, incremental checkpoints, scheduled feeds) — then **field mapping** (e.g. `material_code → item.code`), **validation** (missing fields, bad dates, unknown items / suppliers / departments, duplicates, negative quantities…), **idempotency** and **retry of rejected records**, all applied through the existing stock ledger and supplier-order log (consumption → V2, stock → V3, orders / deliveries → V4 / V5). Stock counts that disagree with the ledger become **reconciliation issues** a person resolves. **Monitoring** (health, freshness, processed / rejected) and a full **audit trail** of every run. Tested end to end against a local, clearly labelled **reference ERP simulator** — no real hospital system is connected → [`docs/integrations.md`](docs/integrations.md) |
| ✅ V10 Real hospital pilot & business validation — Completed | **Pilots** per hospital with a **baseline period** and a **pilot period** (lifecycle planned → active → paused → completed / cancelled, departments, users, V9 data sources). Metrics computed on demand through the **existing** V1–V9 functions: inventory accuracy (V9 reconciliation), stockouts (V3 ledger reconstruction), forecast error (V2 evaluation), warning precision / recall / lead time (V3.4), supplier OTIF / delay / fill (V4), recommendation decisions — viewed, approved, modified, rejected, expired, time to decision (V5), adoption, a transparent **data-quality score** and integration reliability (V9). **Descriptive baseline vs pilot comparison** (pp + relative change, "Insufficient data" below minimum samples, external factors, no causal claims), **issue tracking**, **feedback**, a **readiness checklist**, a **pilot report** (Markdown + snapshots) and a **pilot sandbox** on the demo hospitals' synthetic data, labelled *DEMO / SYNTHETIC DATA — NOT REAL HOSPITAL DATA* → [`docs/v10-pilot.md`](docs/v10-pilot.md) |

Git tags: `v1.0.0` (V1 only), `v2.0.0` (V1 + V2A), `v2.1.0` (V1 + V2A + V2B), `v3.0.0` (V1 + V2 + V3), `v4.0.0` (V1–V4), `v5.0.0` (V1–V5), `v6.0.0` (V1–V6), `v7.0.0` (V1–V7), `v8.0.0` (V1–V8), `v9.0.0` (V1–V9) and `v10.0.0` (V1–V10). `git checkout v1.0.0` gives you the V1 code.

![Organization view — per-hospital aggregates and the same supplier side by side](docs/screenshots/organization-overview.png)

## Quick start (Docker)

Nothing runs until you start it on your own machine — `localhost:3000` shows "refused to connect" until then.

1. Install **Docker Desktop** (https://www.docker.com/products/docker-desktop/) and start it. Wait until it shows *Engine running*.
2. Unzip the project and open the `medflow-ai` folder.
3. Start it:
   - **Windows:** double-click `start-windows.bat`
   - **macOS / Linux:** `./start.sh`
   - or manually: `copy .env.example .env` (Windows) / `cp .env.example .env`, then `docker compose up --build`
4. Open http://localhost:3000 — the first build takes 3–5 minutes; the page is refused until all three containers are up.

- API docs (Swagger): http://localhost:8000/api/docs
- Stop: `docker compose down` (add `-v` to also delete the database)

**Troubleshooting**

| Symptom | Fix |
|---|---|
| `localhost refused to connect` | The stack isn't running yet. Run `docker compose ps` — all three services should be `running`/`healthy`. Still building? wait. |
| `docker: command not found` / `error during connect` | Docker Desktop isn't installed or isn't running. |
| `port is already allocated` (3000 or 8000) | Another app uses the port. Stop it, or change the left side of `ports:` in `docker-compose.yml` (e.g. `"3001:3000"`). |
| backend keeps restarting | `docker compose logs backend` |

The first start runs migrations, seeds three fictional hospitals and trains their models: **Sunrise Multispecialty Hospital (Demo)**
(180 beds, 8 departments, 42 consumables, 6 suppliers, 90 days of simulated stock movements) and **Lakeview Community
Hospital (Demo)** (120 beds, obstetric-heavy, low glove use, poor CPS deliveries) in the *CareNet Hospitals Group (Demo)*,
and **Harbor Clinic & Nursing Home (Demo)** in a separate organization.
All demo data is synthetic and labelled as such in the UI.

| Demo login (password `Demo@1234`) | Role | Can |
|---|---|---|
| `admin@sunrise.demo` | Administrator | everything, users, audit log |
| `procurement@sunrise.demo` | Procurement manager | suppliers & catalogue, items, alerts, retrain forecasts, generate / **approve** procurement recommendations, cost model |
| `inventory@sunrise.demo` | Inventory manager | receive / issue / wastage / counts, items, alerts, retrain forecasts, generate procurement recommendations (not approve) |
| `ortho@sunrise.demo` | Department manager (Ortho OT) | read all, issue/return for own department |
| `viewer@sunrise.demo` | Viewer | read-only |
| `admin@lakeview.demo`, `procurement@lakeview.demo`, … | same roles in Lakeview | Lakeview only |
| `admin@carenet.demo` | Organization admin of CareNet + hospital admin of Sunrise **and** Lakeview | switch hospitals (header), Organization page |
| `platform@medflow.demo` | Platform admin (no hospital) | organizations and their admins only — no hospital data |

V9: administrators configure integration sources and API keys; administrators, procurement and inventory managers see
Integrations (monitoring, uploads, syncs, retries, reconciliation). Each demo hospital has three pre-configured sources —
*Reference ERP (simulated)*, *Stores spreadsheet (CSV / Excel)* and *Procurement system (API push)* — and nothing
imported yet: open **Integrations → Sources & mapping → Reference ERP (simulated) → Sync now** to see the whole flow.

## Local development (without Docker)

Requirements: Python 3.11+, Node 22+, PostgreSQL 16.

```bash
# 1. Database
createuser medflow -P          # password: medflow
createdb medflow -O medflow

# 2. API
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
alembic upgrade head
python -m app.seed             # demo data (use --reset to recreate)
python -m app.ml.train         # V2: train baselines + XGBoost, store forecasts
uvicorn app.main:app --reload  # http://localhost:8000/api/docs
# V9 demo ERP (simulated): REFERENCE_ERP_ENABLED=true REFERENCE_ERP_TOKEN=dev-token uvicorn app.main:app --reload
# scheduled feeds:         python -m app.integrations.cli run-due   (cron / every few minutes)

# 3. Web
cd ../frontend
npm install
npm run dev                    # http://localhost:3000  (proxies /api → :8000)
```

## Tests

```bash
cd backend && pytest                       # V1–V10 incl. ML, tenant isolation, integrations and pilots (counts: docs/v0-v10-audit.md); graph-store tests need TEST_GRAPH_URL, SQLite in-memory by default
TEST_DATABASE_URL=postgresql+psycopg://medflow:medflow@localhost:5432/medflow_test pytest   # against Postgres

cd frontend && npm run lint && npm run typecheck
npx playwright install chromium && npm run test:e2e   # needs the stack running with demo data
```

CI (`.github/workflows/ci.yml`) runs ruff, migrations (`upgrade → check → downgrade`), pytest on Postgres,
ESLint, TypeScript, the production build, and the Playwright suite.

## Repository layout

```text
medflow-ai/
├── backend/            FastAPI + SQLAlchemy 2 + Alembic
│   ├── app/api/        routers (auth, org, catalog, inventory, suppliers, alerts, dashboard/audit, forecasts, procedures, stockout-risks, supplier-orders, supplier-intelligence)
│   ├── app/services/   stock ledger, alert engine, audit
│   ├── app/ml/         V2 forecasting: data panel, features, procedure data (V2B), baselines, XGBoost, metrics, pipeline, train CLI
│   ├── app/risk/       V3 stockout risk: projection, ledger history, features, simulator, classifier, backtest, engine
│   ├── app/supplier_intel/  V4 supplier intelligence: OTIF metrics, lead-time/delay prediction, item comparison
│   ├── app/procurement/     V5 replenishment, arrival distributions, simulation, cost model, OR-Tools
│   ├── app/graph/           V6 knowledge-graph projection, sync, Cypher queries
│   ├── app/assistant/       V7 read-only tools, deterministic planner, optional LLM loop, conversations
│   ├── app/services/tenancy.py, app/api/tenancy.py, app/db/tenant_guard.py   V8 memberships, switching, admin, tenant guards
│   ├── app/models/     ORM models
│   ├── app/seed.py     demo hospital simulation
│   ├── alembic/        migrations
│   └── tests/          pytest (auth, RBAC, ledger/FEFO, alerts, dashboard, seed, ML, forecast API)
├── frontend/           Next.js 16 (App Router) + TypeScript + Tailwind v4 + TanStack Query + RHF/Zod + Recharts
│   ├── app/            pages: login, dashboard, inventory, forecasts, procedures, stockout-risks, supplier-intelligence, procurement, knowledge-graph, assistant, organization, platform, movements, suppliers, alerts, settings, audit-logs
│   ├── components/     UI primitives (shadcn-style), dialogs/forms, charts
│   └── e2e/            Playwright
├── docs/               architecture, database, API, ML pipeline, security, roadmap, research (V0)
├── CLAUDE.md           rules for AI coding agents working on this repo
└── docker-compose.yml  postgres + backend + frontend
```

## Key design decisions

- **Stock is a ledger, not a number.** Quantities live on batches; every change writes an immutable
  `stock_movements` row with `balance_after`. Corrections are new movements.
- **FEFO issuing.** Issues pick non-expired batches first-expiry-first-out and may span batches; expired stock is
  reported separately and can never be issued.
- **Explainable alerts.** Every alert message states the numbers behind it; alerts dedupe, escalate
  (re-opening if acknowledged), and auto-resolve when the condition clears.
- **First-party auth cookies.** The browser only talks to the Next.js origin, which proxies `/api/*` to FastAPI —
  HttpOnly `SameSite=Lax` cookies, no CORS in production.
- **Tenant-ready.** Every table is scoped by `hospital_id`, so Version 8 multi-tenancy is an extension.

See [`docs/architecture.md`](docs/architecture.md) for more.

## Forecasting (V2A) in one paragraph

Daily consumption per item × department is rebuilt from the stock ledger; days when an item was stocked out are
treated as *unknown*, not zero. Three candidates — historical average, 7-day moving average and a global XGBoost
model (lags 1/7/14, 7/14-day rolling means, volatility, trend, day of week, item, department) — are trained on the
same window and scored on the last 14 days. The lowest-WAPE model is refit on all data and its 30-day forecasts are
stored with the model version, metrics and SHAP drivers. Details and limitations: [`docs/ml-pipeline.md`](docs/ml-pipeline.md).

## Procedure-aware forecasting (V2B) in one paragraph

Scheduled procedures × configured kit quantities (both stored in the database and editable on the Procedures page)
give an expected demand per item and day. Six procedure features are added to a second XGBoost candidate
(`v2b_xgb_vN`) that is scored on the same 14-day holdout and on earlier rolling windows. It is served only when it
beats V2A on all of them; otherwise V2A stays active. On the seeded **synthetic** demo data: historical average
23.2 % WAPE, 7-day MA 22.7 %, V2A XGBoost 19.0 %, V2B 16.8 % (better on 3/3 windows; seed of 2026-09-23 — on the
demo seeded 2026-09-24: 23.3 %, 23.9 %, 18.6 %, 17.2 %, V2B served). The demo generator makes
consumption depend on procedures, so this shows the mechanism works — not that it will help in a real hospital.
All procedure data in the demo is synthetic and not medically validated.

## Stockout risk (V3) in one paragraph

For each item, usable (non-expired) batches are projected day by day against the served V2 forecast, first-expiry
first, with no deliveries assumed: that gives days of stock left, the expected stockout date, the 14-day shortage and
— with the preferred supplier's lead time — the latest order date. An XGBoost classifier gives the probability of a
stockout within 14 days from 23 point-in-time features; because a single hospital's history has few stockouts, it is
trained on **labelled synthetic** replenishment histories built from each item's own settings, and **evaluated on the
hospital's real ledger**. A deterministic lead-time rule keeps an item from showing LOW when an order placed now would
arrive too late. On the demo (10 stockout events): XGBoost PR-AUC 0.51 vs 0.40 for the V1 reorder-level rule; with the
lead-time rule it warned early enough to reorder for all 10 events, at precision ≈ 25 %. Synthetic data — not a claim
about real hospitals. (Measured on one seed date; the demo is generated relative to the seed date and the ranking changes between
days — on the demo seeded 2026-09-24 XGBoost's PR-AUC was 0.24 vs 0.26 for the cover rule, which was therefore served. See
[`docs/v0-v10-audit.md`](docs/v0-v10-audit.md).)

## Supplier intelligence (V4) in one paragraph

Every order placed with a supplier is logged with its quoted lead time; receiving stock against it records what
actually arrived and when. From that evidence each supplier (and supplier × item) gets on-time rate, days late,
actual lead time vs quoted, cancellations, fill rate and price stability, and a reliability score that *is* the
on-time-in-full rate — pulled towards the hospital average for small samples, with a 95 % range. For an item V3
expects to run out in *D* days, every supplier is shown with the share of its past orders that arrived within *D*
days: e.g. suture 2-0 runs out in 4 days; the cheapest supplier delivered within 4 days in 0 of 96 orders, the
supplier 2 % dearer in 14 of 15. It never places an order. Demo order history is synthetic.

## Procurement intelligence (V5) in one paragraph

For each item that needs a decision this review cycle, V5 works out how much to order (demand over lead time + review
period + safety stock − usable stock − in-transit orders *counted at their historical probability of arriving*, rounded to
the supplier's MOQ, checked against shelf life and storage), builds scenarios per supplier and quantity (plus a split
between a reliable and a cheap supplier), simulates each on the same 500 demand/delivery paths, and costs them with a
formula whose every parameter is on the Cost model tab. OR-Tools CP-SAT picks the lowest expected cost per item (and
respects a budget across items). On the demo, suture 2-0: the cheapest supplier delivered within the 4-day window in 0 of
93 orders, CPS in 14 of 14 — V5 recommends CPS bridging the gap plus the cheap supplier for the rest. Across the 13 items
needing attention, the recommendations carry an expected cost of ₹2.51 lakh vs ₹3.28 lakh for "cheapest supplier" and
₹6.04 lakh for "no order" — the model's own estimate on synthetic data, not a validation (seed date of the V5 release; on the demo seeded
2026-09-24 the run produced 15 recommendations, 10 with an order, purchase value ₹3.01 lakh). Arrival probabilities were
backtested without look-ahead: Brier 0.085 vs 0.089 for trusting the quote, and conservative (86 % predicted vs 91 %
on time). A person approves, modifies (with a reason) or rejects every recommendation; approval records the order in
MedFlow and sends nothing to anyone. The V5 recommendations demonstrate the optimisation behaviour on synthetic data;
they are not evidence of real-world savings. The stockout multiplier (5×) and holding rate (25 %/year) are placeholders
each hospital must set — both are editable on the Cost model tab.

## Knowledge graph (V6) in one paragraph

MedFlow's PostgreSQL data is projected into an operational graph (Neo4j): hospital → departments → procedures → items
← suppliers, with batches, the served forecast, the latest stockout risk, supplier orders and procurement
recommendations attached, and the evidence on the relationships (units used per department, kit quantity per procedure
and scheduled counts, prices, "14 of 15 orders within the window"). PostgreSQL remains the source of truth: the graph is
rebuilt from it, verified by counts, and re-synced automatically when the data changed; if the graph store is down only
the Knowledge graph page stops working. Three things it answers that tables don't answer easily: *why* an item is at
risk (a traversal from risk to forecast to the scheduled procedures and departments driving it to the suppliers who can
or can't deliver in time), *what depends on* a supplier or an item, and predefined structural questions such as
"high-risk items with only one supplier". The graph implementation was tested using FalkorDB, an openCypher-compatible graph engine. Neo4j is configured as the production graph store and is covered by CI configuration, but Neo4j execution could not be locally verified because the required Neo4j image/download endpoints returned HTTP 403 in the development environment.

## AI operations assistant (V7) in one paragraph

The assistant answers operational questions by calling MedFlow's own functions — the same ones behind the Stockout risk,
Forecasts, Supplier intelligence, Procurement and Knowledge graph pages — and explaining what they return. It does not
calculate, estimate or buy anything: there is no write tool, imperative requests ("approve…", "order…") are refused, and
approvals stay on the Procurement page. By default a deterministic planner maps the question to tools (with conversation
memory, so "it" refers to the item just discussed) and composes the answer from the tools' facts: no LLM, no keys. An LLM
can be switched on through environment variables only (a local Ollama / llama.cpp / vLLM server with no key, or a hosted
provider with a key kept out of Git). Its answer is shown only if it used MedFlow tools, returned the structured format and
every number in it appears in the tool results; otherwise the deterministic answer is shown with the reason. Every answer
shows its evidence and is stored with its tool calls and an audit-log row. **No real LLM was executed while building V7**
(model downloads and servers were unreachable in the development environment). The LLM path is tested with a scripted
provider and mocked HTTP; answer quality with a real model is not evaluated.

## Multi-hospital SaaS (V8) in one paragraph

One account can belong to several hospitals, with a different role in each; hospitals belong to organizations. Every
request runs in the account's **active hospital**, which the server re-verifies each time (an active membership in an
active hospital of an active organization, an access token bound to that hospital, and a header that stops a stale browser
tab from acting on a different hospital). All V1–V7 code already filtered by the caller's hospital, so it is unchanged; V8
adds the membership model, switching, administration (hospital, organization, platform), onboarding with CSV import, an
organization overview built from each hospital's own results, and defence in depth: an ORM guard and PostgreSQL triggers
that reject any row linking two hospitals. The tests call every id-taking endpoint of hospital A with hospital B's ids,
train two demo hospitals in one database and check that neither's data enters the other's models, graph or assistant
answers. The assistant refuses questions about other hospitals before running anything. These are technical controls;
no formal compliance (HIPAA, GDPR, ISO) has been assessed.

## Integrations & data exchange (V9) in one paragraph

MedFlow supports a validated integration framework and controlled data-import/API connectors. Production hospital-system
integrations require access to the corresponding hospital/vendor systems and have not been claimed unless actually tested.
Each hospital configures its own sources (Integrations page): files uploaded by people, data pushed by the hospital's
system with an API key, or data MedFlow pulls from the hospital's REST API on a schedule. Every record is mapped to
MedFlow's fields, validated, de-duplicated and applied through the same services a person uses (FEFO issue, receipt
against a purchase order, …), so forecasts, risk, supplier reliability and procurement simply see more real data. Nothing
is corrected silently: rejected records are kept with their reasons for retry, and stock counts that disagree with the
ledger wait for a person's decision. The demo's "Reference ERP (simulated)" source talks to a simulator built into MedFlow
(off by default on a server; the demo `.env.example` switches it on); it is labelled simulated everywhere.

## Pilots & business validation (V10) in one paragraph

A hospital administrator creates a pilot (or, in a demo hospital, the pilot sandbox), picks a baseline period and a pilot
period, the participating departments and people, and the V9 data sources. MedFlow then measures both periods with the
calculations it already has — nothing is re-modelled — and shows every number with its formula and sample size, or
"Insufficient data". People record what they do with recommendations (viewed / approved / modified / rejected), give
short feedback, log issues (which never change stock) and work through a readiness checklist. The pilot report states
its limitations and ends with a factual, non-causal conclusion; for demo hospitals it says "No real-world outcome claim
can be made yet." MedFlow has **not** been piloted in a real hospital; the next step is a real pilot, not another version.

## Out of scope so far (by design)

Billing / subscriptions / SSO; outbound connections to purchasing systems (MedFlow still recommends and records — it never
sends orders); HL7 / FHIR / EDI adapters; causal impact evaluation (a real pilot comes first). See [`docs/product-roadmap.md`](docs/product-roadmap.md).
