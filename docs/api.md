# API (Versions 1–6)

Interactive docs: `http://localhost:8000/api/docs` (use **Authorize** → `/api/auth/token`).
All endpoints except login/refresh/logout/health require auth (HttpOnly cookie or `Authorization: Bearer`).

| Method | Path | Permission | Purpose |
|---|---|---|---|
| POST | `/api/auth/login` | — | JSON login; sets `access_token`, `refresh_token`, `mf_session` cookies |
| POST | `/api/auth/token` | — | OAuth2 form login (Swagger) |
| POST | `/api/auth/refresh` | refresh cookie | Rotate tokens |
| POST | `/api/auth/logout` | — | Revoke refresh tokens, clear cookies |
| GET | `/api/auth/me` | any | Current user, hospital, permissions |
| POST | `/api/auth/change-password` | any | Change own password (revokes other sessions) |
| GET/PATCH | `/api/hospitals/current` | read / `hospital:manage` | Hospital profile & policy |
| GET/POST/PATCH | `/api/departments[/{id}]` | read / `departments:manage` | |
| GET/POST/PATCH | `/api/users[/{id}]` | `users:manage` | |
| GET/POST/PATCH | `/api/categories[/{id}]` | read / `catalog:manage` | |
| GET/POST/PATCH | `/api/consumables[/{id}]` | read / `catalog:manage` | Item catalogue |
| GET | `/api/inventory` | read | Stock per item: usable, expired, value, status (`?status=LOW`) |
| GET | `/api/inventory/{consumable_id}` | read | Detail: batches, suppliers, movements, 30-day series, days of cover |
| GET | `/api/inventory/batches?consumable_id=` | read | Batches |
| GET | `/api/inventory/movements` | read | Ledger; filters: item, department, supplier, type, dates, search; paginated |
| POST | `/api/inventory/receive` | `stock:receive` | New batch + RECEIPT. V4: optional `supplier_order_id` records the delivery against a supplier order |
| POST | `/api/inventory/issue` | `stock:issue` or own dept | FEFO ISSUE, may span batches |
| POST | `/api/inventory/return` | `stock:issue` or own dept | RETURN to a batch (≤ outstanding for that dept) |
| POST | `/api/inventory/wastage` | `stock:receive` | WASTAGE from a batch (reason required) |
| POST | `/api/inventory/adjust` | `stock:receive` | Set batch to counted qty (reason required) |
| GET/POST/PATCH | `/api/suppliers[/{id}]` | read / `suppliers:manage` | |
| GET/POST/PATCH/DELETE | `/api/suppliers/{id}/products[/{pid}]` | read / `suppliers:manage` | Supplier catalogue |
| GET | `/api/alerts` | read | `?status=active|OPEN|ACKNOWLEDGED|RESOLVED|all`, type, severity |
| POST | `/api/alerts/{id}/acknowledge` · `/resolve` | `alerts:manage` | |
| POST | `/api/alerts/evaluate` | `alerts:manage` | Re-run the rule engine hospital-wide |
| GET | `/api/dashboard/summary` | read | Supply health, values, alerts, 30-day series, department consumption |
| GET | `/api/audit-logs` | `audit:read` | Paginated audit trail |
| GET | `/api/forecasts?days=14` | read | V2 · Forecast for every item (active model): total, range, recent rate, change, stock cover, backtest WAPE |
| GET | `/api/forecasts/{item_id}?days=14` | read | V2 · Item forecast (1–30 days): totals for 7/14/30, daily series, 60-day history, drivers, explanation, backtest |
| GET | `/api/forecasts/models` | read | V2 · Model registry (newest first) |
| GET | `/api/forecasts/models/{id}` | read | V2 · Model + its run's candidates + per-item holdout errors |
| POST | `/api/forecasts/train` | `forecasts:train` | V2 · Train baselines + XGBoost (+ V2B when procedure data exists), evaluate, activate best, store forecasts. Response adds `procedure_model` |
| GET | `/api/forecasts/{item_id}/procedure-impact?days=14` | read | V2B · Scheduled procedures using the item, kit demand, V2A vs V2B totals |
| GET | `/api/procedures/types` | read | V2B · Procedure types (`?include_inactive=`), with `mapping_count`, `upcoming_count` |
| POST · PATCH | `/api/procedures/types[/{id}]` | `procedures:manage` or own dept | V2B · Create / edit / (de)activate |
| GET | `/api/procedures/schedule` | read | V2B · Filters `date_from`, `date_to`, `department_id`, `procedure_type_id`, `status=active\|SCHEDULED\|COMPLETED\|CANCELLED\|all`; paginated |
| POST · PATCH | `/api/procedures/schedule[/{id}]` | `procedures:manage` or own dept | V2B · One row per (type, dept, date); count 1–500; past ⇒ COMPLETED, future ⇒ SCHEDULED |
| POST | `/api/procedures/schedule/{id}/cancel` | `procedures:manage` or own dept | V2B · Cancel (reason optional); COMPLETED rows can't be cancelled (409) |
| GET | `/api/procedures/summary?days=14` | read | V2B · Upcoming scheduled counts by type, schedule horizon, synthetic row count |
| GET | `/api/procedures/mappings` | read | V2B · Procedure → item kit quantities (`?procedure_type_id=&consumable_id=&include_inactive=`) |
| POST · PATCH | `/api/procedures/mappings[/{id}]` | `procedures:manage` or own dept | V2B · quantity > 0 and ≤ 10 000; duplicate (type, item) → 409; inactive item → 422 |
| GET | `/api/stockout-risks?level=` | read | V3 · Current risk for every item: probability, level, stockout date, days left, 14-day shortage, lead time, order-by, main reason, counts. Recomputed lazily when older than today / the active models |
| GET | `/api/stockout-risks/{item_id}` | read | V3 · Detail: reasons, SHAP factors, 30-day projection, last 30 days of risk history |
| GET | `/api/stockout-risks/models` · `/models/{id}` | read | V3 · Risk-model registry; a version with its run's three scorers, backtest metrics, events, PR curve, calibration |
| POST | `/api/stockout-risks/train` | `risk:train` | V3 · Simulate → train → backtest on the ledger → select → score all items → alerts |
| POST | `/api/stockout-risks/refresh` | `risk:train` | V3 · Recompute risk and risk alerts for all items (no training) |
| GET | `/api/supplier-orders?status=active\|overdue\|OPEN\|PARTIAL\|RECEIVED\|CANCELLED\|all&supplier_id=&consumable_id=` | read | V4 · Supplier order log (paginated) |
| POST | `/api/supplier-orders` | `suppliers:manage` | V4 · Record an order placed with a supplier (defaults: today, catalogue price, expected = quoted lead time). Tracking only |
| POST | `/api/supplier-orders/{id}/cancel` | `suppliers:manage` | V4 · OPEN → cancelled; PARTIAL → closed short |
| GET | `/api/supplier-intelligence/scorecards?window_days=365` | read | V4 · Reliability score + components per supplier, hospital-wide rates, the score formula |
| GET | `/api/supplier-intelligence/suppliers/{id}` | read | V4 · Supplier performance: components, monthly trend, lead-time histogram, per-item metrics + price history, recent orders |
| GET | `/api/supplier-intelligence/items/{item_id}` | read | V4 · Supplier options for an item with the V3 deadline: P(delivered in time) per supplier, in-transit orders, findings |
| GET | `/api/supplier-intelligence/at-risk` | read | V4 · V3 medium/high-risk items with their best supplier option |
| GET | `/api/supplier-intelligence/open-orders` | read | V4 · Open orders with predicted arrival, P(late), P(before stockout) |
| GET | `/api/supplier-intelligence/evaluation` | read | V4 · Lead-time and delay prediction backtest |
| GET | `/api/health` | — | Liveness + DB check |

Errors: `{"detail": "…"}` (string) or FastAPI validation arrays (422). 404 is returned for objects that belong to
another hospital. Conflicts (duplicate SKU/code) return 409.

Example — issue 50 pairs of gloves to Orthopaedics:

```http
POST /api/inventory/issue
{"consumable_id": 2, "quantity": 50, "department_id": 1, "reference": "IND-0923-ORTHO"}
```
```json
{"movements": [
   {"movement_type": "ISSUE", "quantity": -30, "batch": {"lot_number": "EARLY"}, "balance_after": 150},
   {"movement_type": "ISSUE", "quantity": -20, "batch": {"lot_number": "LATE"},  "balance_after": 130}],
 "usable_stock": 130, "status": "OK"}
```

Example — 14-day forecast for surgical gloves:

```http
GET /api/forecasts/2?days=14
```
```json
{
  "item": "Surgical gloves, sterile, size 7",
  "horizon_days": 14,
  "predicted_demand": 939,
  "lower": 811, "upper": 1067,
  "model_version": "xgb_v1",
  "model_type": "xgboost",
  "data_through": "2026-09-22",
  "horizons": [{"days": 7, "predicted": 465.8}, {"days": 14, "predicted": 938.9}, {"days": 30, "predicted": 2042.0}],
  "drivers": [{"feature": "trend_7_14", "label": "Recent trend (7-day vs 14-day)", "units": 21.7}, "..."],
  "explanation": ["Expected demand over the next 14 days: about 939 pair (≈67.1/day).", "..."],
  "backtest": {"wape": 0.133, "mae": 7.8, "rmse": 8.7, "bias": 0.13}
}
```
`404` when no model has been trained yet; `422` for `days` outside 1–30.

## V2B additions to the forecast response

All V2A fields are unchanged (tested: `test_forecast_api_v2b.py`). New fields — real response from the demo:

```http
GET /api/forecasts/754?days=14
```
```json
{
  "item": "Absorbable suture, polyglactin 1-0",
  "predicted_demand": 305, "lower": 285, "upper": 325,
  "model_version": "v2b_xgb_v1", "model_type": "xgboost_procedure",
  "forecast_source": "procedure_aware",
  "scheduled_procedures": 117,
  "procedure_driven_demand": 279.0,
  "usable_stock": 378, "expected_shortage": 0, "stock_covers_horizon": true, "days_of_stock_remaining": 17,
  "procedure_impact": {
    "scheduled_procedures": 117, "expected_quantity": 279.0,
    "v2a_forecast": 278.0, "v2b_forecast": 305.2, "procedure_effect": 27.2,
    "departments": ["General Surgery OT", "Obstetrics & Gynaecology OT", "Orthopaedics OT"],
    "types": [{"code": "TKR", "name": "Total knee replacement", "count": 29, "quantity_per_procedure": 3.0, "expected_quantity": 87.0}, "..."]
  },
  "model_comparison": {
    "v2a_model": "xgb_v1", "v2a_wape": 0.190, "v2b_model": "v2b_xgb_v1", "v2b_wape": 0.168,
    "improved": true, "active_source": "procedure_aware",
    "summary": "V2B improved WAPE from 19.0% to 16.8%. The procedure-aware model is active.",
    "schedule_through": "2026-10-22", "schedule_changed_since_training": false
  },
  "explanation": ["...", "Demand is driven mainly by scheduled procedures: 117 relevant procedures need about 279 foil by the configured kits (91% of the forecast).",
                  "Using the procedure schedule makes this forecast 27 foil higher than the consumption-only model (V2A: 278).", "..."]
}
```

`forecast_source` is `procedure_aware` (V2B served), `consumption` (V2A XGBoost) or `baseline`. With no procedure
data: `forecast_source = "consumption"`, `scheduled_procedures = 0`, `model_comparison.v2b_trained = false`.
The kit numbers are synthetic demo values, not clinical standards.

## V3 — stockout risk

`risk:train` is held by admin, procurement manager and inventory manager. Real response from the demo:

```http
GET /api/stockout-risks?level=HIGH
```
```json
{
  "model": {"name": "stockout_xgb_v1", "model_type": "xgboost_classifier", "warn_threshold": 0.179, "high_threshold": 0.5, "...": "..."},
  "forecast_model": "v2b_xgb_v1", "as_of": "2026-09-23", "horizon_days": 14,
  "counts": {"high": 5, "medium": 7, "low": 30, "out_of_stock": 4, "total": 42},
  "items": [{
    "name": "Oscillating saw blade", "risk_level": "HIGH", "probability": 0.6353,
    "usable_stock": 15, "forecast_14": 28.0, "days_of_stock_remaining": 8, "expected_stockout_date": "2026-10-01",
    "shortage_quantity": 13, "lead_time_days": 7, "order_by_date": "2026-09-24", "can_replenish_in_time": true,
    "main_reason": "Main factors: little cover beyond supplier lead time + long supplier lead time + high procedure demand."
  }]
}
```
`STOCKOUT_RISK` is a new alert type (`/api/alerts?alert_type=STOCKOUT_RISK`); existing alert types are unchanged.
With no forecast or risk model the list is empty and `message` says which model to train.

## V4 — supplier options for an at-risk item (real demo response, abridged)

```http
GET /api/supplier-intelligence/items/{absorbable suture 2-0}
```
```json
{
  "risk": {"risk_level": "MEDIUM", "expected_stockout_date": "2026-09-27", "usable_stock": 80},
  "deadline_days": 4,
  "options": [
    {"supplier": "Cauvery Pharma & Surgicals", "unit_price": 294.13, "price_vs_cheapest": 0.0216, "quoted_lead_time_days": 4,
     "typical_lead_time_days": 4.0, "p90_lead_time_days": 4.0, "on_time_rate": 0.93, "reliability_score": 91,
     "evidence_basis": "item", "in_time_k": 14, "in_time_n": 15, "p_in_time": 0.906, "verdict": "likely"},
    {"supplier": "OrthoPrime Consumables", "unit_price": 287.90, "price_vs_cheapest": 0.0, "quoted_lead_time_days": 7,
     "typical_lead_time_days": 7.0, "p90_lead_time_days": 8.9, "evidence_basis": "supplier",
     "in_time_k": 0, "in_time_n": 96, "p_in_time": 0.005, "verdict": "unlikely"}
  ],
  "open_orders": [{"reference": "PO-00110", "supplier": "Cauvery Pharma & Surgicals", "overdue_days": 2}],
  "summary": ["Projected stockout in 4 days ...", "The cheapest supplier (OrthoPrime Consumables) is unlikely to deliver in time ...",
              "Information for procurement review — no order has been placed. ..."]
}
```

## V5 — procurement intelligence (recommend-only)

| Method | Path | Permission | Purpose |
|---|---|---|---|
| GET | `/api/procurement/settings` | read | Cost-model parameters, defaults, per-parameter explanation, formula |
| PUT | `/api/procurement/settings` | `procurement:configure` | Save parameters (validated ranges; audited with before → after) |
| GET | `/api/procurement/needs-attention` | read | Items needing a decision this review cycle: V5.1 numbers, in-transit (outstanding and expected), P(stockout) without a new order, reasons, pending recommendation |
| GET | `/api/procurement/items/{id}/plan` | read | Live plan: replenishment (step by step + 30-day projection with / without in-transit), in-transit orders with P(in time), every scenario (lines, costs, metrics, k-of-n evidence, tags), solver status, explanation. Stores nothing |
| POST | `/api/procurement/items/{id}/what-if` | read | `{"delays":[{"supplier_id":163,"days":2}],"demand_change_pct":0}` → before/after per scenario (P(stockout), shortage, cost, arrival) + summary. Stores nothing |
| POST | `/api/procurement/recommendations/generate` | `procurement:recommend` | `{}` (items needing attention) or `{"item_ids":[…]}` → PENDING recommendations; CP-SAT across items (budget); older PENDING → SUPERSEDED. Creates no orders |
| GET | `/api/procurement/recommendations?status=PENDING` | read | Queue / history (`PENDING`, `APPROVED`, `REJECTED`, `SUPERSEDED`, `decided`, `all`) |
| GET | `/api/procurement/recommendations/{id}` | read | Detail incl. all scenarios, cost breakdown, replenishment, in-transit, settings snapshot, solver, final evaluation |
| POST | `/api/procurement/recommendations/{id}/approve` | `procurement:approve` | `{}` = as recommended; `{"lines":[{"supplier_id":…,"quantity":…,"unit_price":null}],"reason":"…"}` = modified (reason required, MOQ + catalogue enforced). Records supplier order(s) with `recommendation_id` — nothing is sent anywhere. 409 if already decided or generated on an earlier day |
| POST | `/api/procurement/recommendations/{id}/reject` | `procurement:approve` | `{"reason":"…"}` (required) |
| GET | `/api/procurement/evaluation` | read | Arrival-probability backtest (expanding window) vs "the quote is certain": Brier, calibration bins |

`GET /api/supplier-orders` rows gain `recommendation_id` (additive).

Scenario object (real demo response for suture 2-0, abridged):

```json
{"key": "split-175-166-176-132", "kind": "split", "label": "166 foil from CPS + 132 foil from OPI", "tags": ["recommended", "split"], "quantity": 298, "purchase_value": 86828.38, "costs": {"purchase": 87328.38, "stockout": 12159.02, "holding": 351.19, "expiry": 0.0, "carried_forward": 50272.87, "total": 49565.72}, "metrics": {"p_stockout": 0.456, "expected_shortage": 8.4, "arrival_date": "2026-09-27", "last_arrival_date": "2026-09-30", "p_arrive_by_need": 0.908}, "lines": [{"code": "CPS", "quantity": 166, "unit_price": 294.13, "window_k": 14, "window_n": 14, "p_arrive_by_need": 0.917, "arrival_basis": "item"}, {"code": "OPI", "quantity": 132, "unit_price": 287.9, "window_k": 0, "window_n": 93, "p_arrive_by_need": 0.0, "arrival_basis": "supplier"}]}
```

## V6 — operational knowledge graph

Graph reads re-project from PostgreSQL when it changed since the last sync. With the graph store down every endpoint
below answers **503** (`"The knowledge graph is unavailable (…). Inventory, forecasting, … are unaffected"`).

| Method | Path | Permission | Purpose |
|---|---|---|---|
| GET | `/api/graph/status` | read | Backend, reachability, is the graph behind PostgreSQL (and which parts changed), last successful sync, last 10 runs. Never syncs |
| POST | `/api/graph/sync` | `graph:sync` | Rebuild the projection now; returns the run with expected vs found counts and `verified` (audited) |
| GET | `/api/graph/schema` | read | Labels and relationship patterns with counts and meaning |
| GET | `/api/graph/items/{id}/explain` | read | V6.3 explanation chain: `summary`, `steps[]` (kind, title, lines, nodes, via relationship), `graph` (layered nodes + edges), `cypher` |
| GET | `/api/graph/impact/suppliers/{id}` | read | V6.4 supplier unavailable: items (alternatives, severity, risk), procedures, departments, open orders, pending recommendations, example chains |
| GET | `/api/graph/impact/items/{id}` | read | V6.4 item shortage: procedures (kit qty, scheduled, covered by stock), departments, suppliers |
| GET | `/api/graph/queries` | read | Predefined questions with parameters and Cypher |
| POST | `/api/graph/queries/{name}` | read | `{"item_id": …}` / `{"supplier_id": …}` → `columns`, `rows`, `cypher` |

Responses wrap results as `{"synced_at", "sync_run_id", "data"}` so the UI can say which projection it read.

## V7 — AI operations assistant (read-only)

| Method | Path | Permission | Purpose |
|---|---|---|---|
| GET | `/api/assistant/status` | read | `provider` / `mode` (`deterministic` by default, `llm` when configured), `model`, `llm_configured`, `max_steps`, `tools[]`, `examples[]`, `capabilities[]` |
| POST | `/api/assistant/ask` | read | `{"question": "…" (1–500 chars), "conversation_id": null}` → answer + evidence; 404 for someone else's conversation, 429 above the per-user rate limit |
| GET | `/api/assistant/conversations` | read | The caller's own conversations (`id`, `title`, `message_count`, timestamps) |
| GET | `/api/assistant/conversations/{id}` | read | One of them with every message (answer JSON, tool calls, provider, grounding) |

Ask response (real demo response, abridged):

```json
{"conversation_id": 3, "message_id": 6, "question": "Why is Suture 2-0 at risk?", "mode": "deterministic", "provider": "deterministic",
 "model": null, "intent": "why_risk", "grounded": true, "fallback_reason": null, "latency_ms": 165,
 "answer": {"title": "Why Absorbable suture, polyglactin 2-0 is at risk",
            "summary": "Absorbable suture, polyglactin 2-0 is at MEDIUM stockout risk (17% within 14 days). Without a delivery it runs out on 2026-09-27 (4 days of stock), 179 foil short over 14 days. …",
            "points": ["…"], "notes": [], "links": [{"label": "Stockout risk", "href": "/stockout-risks?item=1091"}],
            "follow_ups": ["Which suppliers can cover it within 4 days?", "…"]},
 "evidence": [{"tool": "item_status", "label": "Item status", "args": {"item_id": 1091}, "ok": true, "facts": ["…"], "error": null, "ms": 89},
              {"tool": "graph_explain", "label": "Knowledge graph", "args": {"item_id": 1091}, "ok": true, "facts": ["…"], "error": null, "ms": 64}]}
```

`grounded` describes the answer shown: an LLM answer is shown only if every number in it occurs in this turn's tool
results; otherwise the deterministic answer is shown and `fallback_reason` says why. See `docs/assistant.md`.

## V8 — multi-hospital SaaS

Every hospital endpoint above now runs in the caller's **active hospital**, verified on each request (active
membership in an active hospital of an active organization; access token bound to that hospital — a token issued before a
switch answers 401; `X-MedFlow-Hospital` header, when sent, must match — otherwise 409 `hospital_changed: …`). Without an
active hospital, hospital endpoints answer 403 "No hospital selected". Ids of other hospitals answer 404. Existing
request/response shapes are unchanged; additive fields: `GET /auth/me` (`hospital` may be null; `memberships`,
`admin_organizations`, `organization`, `is_platform_admin`), `HospitalOut.organization_id/status`,
`UserOut.membership_id/membership_status`. `/users` now lists / creates / updates the **members of the active hospital**
(role, department, active = the membership's).

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/hospitals` | any account | hospitals I belong to or administer (`my_role`, `can_switch`, `can_admin`) |
| POST | `/api/hospitals/{id}/switch` | member | make it the active hospital (new access token, audit `hospital.switch`); 404 without a usable membership |
| GET / PATCH | `/api/hospitals/{id}` | hospital admin (active hospital) · org admin · platform admin | detail with departments; profile, `status` (org / platform admin only) |
| GET / POST | `/api/hospitals/{id}/members` | same | members; add a new account or an existing account of the same organization |
| PATCH | `/api/hospitals/{id}/members/{user_id}` | same | role / department / active **in that hospital**; name & password only for single-hospital accounts |
| GET / POST | `/api/organizations` | org admin (own) · platform admin (all; create) | organizations with hospital / member / admin counts |
| GET / PATCH | `/api/organizations/{id}` | org admin · platform admin (`status`) | |
| GET / POST | `/api/organizations/{id}/hospitals` | org admin · platform admin | list; **onboard** (hospital + administrator + departments + procurement settings) |
| GET | `/api/organizations/{id}/overview` | org admin | per-hospital aggregates, totals, contributing hospitals, supplier comparison, notes |
| GET / POST / DELETE | `/api/organizations/{id}/admins[/{user_id}]` | org admin · platform admin | organization administrators |
| GET | `/api/organizations/{id}/audit-logs` | org admin | audit rows of the organization and its hospitals |
| GET | `/api/platform/audit-logs` | platform admin | platform- and organization-level events (no hospital audit) |
| POST | `/api/imports/{departments\|suppliers\|items\|opening_stock}` | per kind: departments / suppliers / catalog / stock:receive | `{"csv": "...", "dry_run": true}` → rows, valid, created, line-numbered errors; all-or-nothing into the active hospital |

Organization and platform admins receive **no hospital operational data** through these endpoints (the overview returns
aggregates of stored results; platform admins get 404 on it).

## V9 — integrations & data exchange

Hospital-scoped like everything else (active hospital; foreign ids → 404). `run` = `integrations:run` (admin, procurement,
inventory); `manage` = `integrations:manage` (admin). Details: `docs/integrations.md`.

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/integrations/entities` | run | the 8 entities with their MedFlow fields (name, type, required, description) |
| GET | `/api/integrations/overview` | run | monitoring: per source health, last run / success / failure / error, freshness, 7-day counts, open reconciliation, latest run per entity; totals |
| GET / POST | `/api/integrations/sources` | run / manage | list; create (`name`, `system_type`, `connector` upload \| api_push \| rest_pull, `entities`, `config`, `enabled`) — config validated, secrets refused, rest_pull host must be allow-listed |
| GET / PATCH | `/api/integrations/sources/{source_id}` | run / manage | detail; rename, entities, config, enable / disable |
| POST | `/api/integrations/sources/{source_id}/test` | run | connection test (rest_pull: `health_path`; others: key status) |
| GET | `/api/integrations/sources/{source_id}/mappings` | run | effective mapping per entity (`field_map` MedFlow → source field, `defaults`, `date_format`, `customized`) |
| PUT / DELETE | `/api/integrations/sources/{source_id}/mappings/{entity}` | manage | set / reset one entity's mapping |
| GET / POST | `/api/integrations/sources/{source_id}/credentials` | manage | API keys (api_push); POST returns `api_key` **once** |
| POST | `/api/integrations/credentials/{credential_id}/revoke` | manage | revoke a key |
| POST | `/api/integrations/sources/{source_id}/upload` | run | multipart `file` (.csv / .xlsx), `entity`, `dry_run` → run with rejected records |
| POST | `/api/integrations/sources/{source_id}/sync` | run | rest_pull now: `{"entities": null, "full": false, "dry_run": false}` → one run per entity |
| GET | `/api/integrations/runs` | run | paginated history (`source_id`, `entity`, `status` filters) |
| GET | `/api/integrations/runs/{run_id}` | run | run + rejected records (row, key, raw, reasons, status) |
| POST | `/api/integrations/runs/{run_id}/retry` | run | re-process the run's rejected records as a new linked run |
| GET | `/api/integrations/reconciliation` | run | issues (`status` OPEN \| RESOLVED \| ALL, `source_id`) incl. `medflow_now` |
| POST | `/api/integrations/reconciliation/{issue_id}/resolve` | run (+ `stock:receive` for `adjust`) | `{"action": "adjust" \| "external_wrong" \| "accept", "note": "…"}` |
| GET | `/api/ingest/v1/status` | **API key** | which source / entities the key may send, limits |
| POST | `/api/ingest/v1/{entity}` | **API key** | `{"records": [...], "dry_run": false}` → run id, status, counts, rejected records (401 / 403 / 404 / 413 / 429 as documented) |
| GET | `/api/reference-erp/{hospital_code}/{resource}` | simulator bearer token | **simulated** ERP feed for demo hospitals (off by default → 404) |

Sync run shape (abridged example):

```json
{"id": 12, "source_name": "Reference ERP (simulated)", "entity": "consumption", "mode": "initial", "trigger": "manual",
 "status": "PARTIAL", "records_received": 2, "records_created": 1, "records_updated": 0, "records_unchanged": 0,
 "records_rejected": 1, "error_summary": {"fatal": null, "reasons": {"unknown_item": 1}, "info": {}},
 "checkpoint_before": null, "checkpoint_after": "2026-09-24T10:02:11+00:00", "triggered_by": {"id": 2, "full_name": "Procurement_Manager"}}
```

## V10 — real hospital pilot & business validation

Hospital-scoped (active hospital; foreign ids → 404). Read = any member; `pilots:manage` = admin; `pilots:contribute` =
admin, procurement, inventory, department manager. Details: `docs/v10-pilot.md`.

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET / POST | `/api/pilots` | read / manage | list; create (name, description, baseline_start/end, pilot_start/end, owner_id, notes, department_ids, user_ids, source_ids — extra fields such as `hospital_id` are rejected) |
| POST | `/api/pilots/sandbox` | manage | demo pilot on synthetic data (demo hospitals only; 409 otherwise) |
| GET | `/api/pilots/active` | read | the active pilot or `null` |
| GET / PATCH | `/api/pilots/{pilot_id}` | read / manage | detail; update incl. `status` (planned → active → paused ↔ active → completed / cancelled; one active pilot per hospital) |
| POST | `/api/pilots/{pilot_id}/external-factors` | manage | `{"factor", "note", "period"}` |
| GET | `/api/pilots/{pilot_id}/dashboard` | read | pilot-period metrics + issue counts + readiness summary + classification label |
| GET | `/api/pilots/{pilot_id}/metrics?period=pilot\|baseline` · `/baseline` | read | metrics of one period: `{key, label, section, value, unit, n, min_n, sufficient, formula, source, note}` + integration reliability |
| GET | `/api/pilots/{pilot_id}/comparison` | read | descriptive baseline vs pilot rows (difference, pp, relative change, "insufficient_data"), causality statement, external factors |
| GET | `/api/pilots/{pilot_id}/decisions` | read | decision funnel per V5 recommendation (viewed, outcome, decider, hours to decision, reason) |
| GET / POST | `/api/pilots/{pilot_id}/issues` | read / contribute | issues (category, severity, title, source, source_ref, impact, assignee) |
| PATCH | `/api/pilot-issues/{pilot_issue_id}` | contribute | status (open / investigating / resolved / ignored — resolution note required), assignee, severity |
| GET / POST | `/api/pilots/{pilot_id}/feedback` | read / contribute | useful / somewhat useful / not useful + reasons + comment (active or paused pilots) |
| POST | `/api/pilot-tracking/recommendations/{rec_id}/view` | read | records that a recommendation was opened |
| GET · PUT | `/api/pilots/{pilot_id}/readiness` · `/readiness/{item_key}` | read · manage | checklist with automatic evidence; confirm manual items |
| GET | `/api/pilots/{pilot_id}/report[?format=markdown]` | read | the pilot report (JSON + Markdown) |
| POST / GET | `/api/pilots/{pilot_id}/report/snapshots` · GET `/api/pilot-reports/{snapshot_id}` | manage / read | immutable saved reports |
| POST | `/api/pilots/{pilot_id}/sync` | integrations:run | syncs the pilot's enabled REST sources through the V9 engine |
| GET | `/api/organizations/{org_id}/pilots` | organization admin | status of pilots in the organization's hospitals (no metrics) |
