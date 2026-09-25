# Architecture (Versions 1–10)

```text
Browser ──► Next.js (3000) ──/api/* rewrite──► FastAPI (8000) ──► PostgreSQL
            pages, proxy.ts route guard        routers → services → ORM
            TanStack Query cache               auth, RBAC, ledger, alerts, audit
```

## Backend layers

| Layer | Location | Responsibility |
|---|---|---|
| Routers | `app/api/*.py` | HTTP, validation (Pydantic), permission checks, hospital scoping |
| Services | `app/services/stock.py` | The only code that changes batch quantities; writes movements |
| | `app/services/alerts.py` | Rule engine; evaluate per item after each movement or hospital-wide on demand |
| | `app/services/audit.py` | Adds audit rows inside the same transaction as the change |
| Models | `app/models/__init__.py` | SQLAlchemy 2 typed models |
| Core | `app/core/` | Settings, JWT/bcrypt, permission matrix, rate limiter |
| ML (V2) | `app/ml/` | Consumption panel, features, procedure data + features (V2B), baselines, XGBoost (V2A/V2B), metrics, training pipeline, CLI |
| Supplier intelligence (V4) | `app/supplier_intel/`, `services/supplier_orders.py` | Order log, OTIF metrics & score, lead-time/delay prediction + backtest, item comparison against the V3 deadline |
| Procurement (V5) | `app/procurement/`, `services/procurement.py` | Replenishment, arrival distributions, Monte Carlo, cost model, OR-Tools, approval |
| Knowledge graph (V6) | `app/graph/` | Projection, sync, Cypher queries (Neo4j / FalkorDB executors) |
| Tenancy (V8) | `app/services/tenancy.py`, `app/api/tenancy.py`, `app/db/tenant_guard.py`, `app/services/org_reporting.py`, `app/services/imports.py` | Memberships, verified active hospital, switching, organization / platform administration, onboarding, organization reporting, CSV import, ORM + PostgreSQL tenant guards |
| AI assistant (V7) | `app/assistant/` | Read-only tools over the V1–V6 endpoint functions, deterministic planner + composer, optional LLM loop with guardrails, conversations + audit |
| Risk (V3) | `app/risk/` | Ledger history, FEFO projection, point-in-time risk features, replenishment simulator, classifier + rules, backtest metrics, serving engine (snapshots, reasons) |

Every write endpoint follows: authorise → load hospital-owned objects (404 across tenants) →
service call → alert evaluation → audit row → single commit.

### Concurrency

Stock operations take a `SELECT … FOR UPDATE` lock on the consumable row (and on the batches during
FEFO picking), so two simultaneous issues of the same item serialise instead of over-issuing.

## Frontend

- App Router, all app pages are client components backed by TanStack Query (`queryKey` prefixes:
  `inventory`, `movements`, `dashboard`, `alerts`, `suppliers` …). Stock mutations invalidate all stock-related keys.
- `lib/api.ts` — fetch wrapper; on 401 it calls `/api/auth/refresh` once (de-duplicated), retries, else redirects to login.
- `proxy.ts` — Next 16's replacement for middleware: redirects to `/login` when the `mf_session` hint cookie is
  absent. It is a UX guard only; the API authorises every request.
- UI permissions come from `/api/auth/me` → `permissions[]`; buttons are hidden, and the API enforces the same matrix.

## Where later versions plug in

| Version | Hook |
|---|---|
| V2 forecasting | **Built (2A + 2B)** — `app/ml/` reads ISSUE/RETURN movements; V2B also reads `procedure_*` tables (`app/api/procedures.py`); see `docs/ml-pipeline.md` |
| V3 stockout | **Built** — `app/risk/`; `STOCKOUT_RISK` alerts added next to (not replacing) the V1 rules, same lifecycle; risk refreshed on every stock movement |
| V4 suppliers | **Built** — `supplier_orders` / `supplier_deliveries` (receipts linked via `supplier_order_id`) → OTIF reliability, lead-time/delay prediction, item comparison against the V3 stockout date |
| V5 procurement | **Built** — `app/procurement/` (replenishment, arrival distributions, Monte Carlo, cost model, OR-Tools CP-SAT) + `services/procurement.py` (recommendations, approval → V4 supplier orders); recommend-only, no external purchasing system |
| V6 graph | **Built** — `app/graph/`: PostgreSQL → Neo4j projection (one way, verified), explanation chains, impact analysis, predefined Cypher queries; graph down ⇒ only the Knowledge graph page is affected. See `docs/knowledge-graph.md` |
| V7 assistant | **Built** — `app/assistant/`: tools call the existing router functions directly (same numbers, same hospital scoping); no write tools; deterministic planner by default, optional LLM via environment with grounding check and fallback. See `docs/assistant.md` |
| V10 pilot & business validation | **Built** — `app/pilots/`: pilots with baseline and pilot periods; metrics computed on demand through the existing functions (V3 `load_history`, V2 `forecast_metrics` + `load_panel`, V3.4 `confusion`/`prf`, V4 `summarize`, V5 recommendation records, V9 sync runs / reconciliation); descriptive comparison; issues, feedback, recommendation views, readiness, reports. No new model, no algorithm change. See `docs/v10-pilot.md` |
| V9 integrations | **Built** — `app/integrations/`: connectors (upload, api_push with hashed keys + rate limit, rest_pull with retries / pagination / checkpoints, scheduled `cli run-due`) → mapping → validation → idempotency (`external_refs`) → the existing stock-ledger / supplier-order / catalogue services in per-record savepoints; `sync_runs` audit trail, rejected-record retry, reconciliation issues resolved by people; local reference ERP simulator (off by default). No V2–V7 algorithm changed. See `docs/integrations.md` |
| V8 multi-hospital SaaS | **Built** — organizations → hospitals → memberships; request context verified in `deps.get_current_user` (membership, token binding, stale-tab header); ORM guard + PostgreSQL triggers; V1–V7 code unchanged because it already scoped by `user.hospital_id`, now a verified cache of the active membership. See `docs/multi-hospital.md` |
