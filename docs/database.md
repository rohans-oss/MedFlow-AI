# Database (Versions 1–10)

PostgreSQL 16, managed by Alembic (`backend/alembic/versions`). Money is `NUMERIC(12,2)`; quantities are integers
in the item's base unit; timestamps are `timestamptz` (UTC). "Today" for expiry is computed in `TIMEZONE`
(default `Asia/Kolkata`).

```text
hospitals ─┬─< departments
           ├─< users >── departments (optional; required for department managers)
           ├─< consumable_categories ─< consumables
           ├─< suppliers ─< supplier_products >─ consumables
           ├─< stock_movements >── consumables, stock_batches, departments, suppliers, users
           ├─< alerts >── consumables, stock_batches
           └─< audit_logs >── users
consumables ─< stock_batches >── suppliers
```

| Table | Notes |
|---|---|
| `hospitals` | `expiry_warning_days` (default 60) drives EXPIRING_SOON; `is_demo` flags synthetic data |
| `users` | bcrypt hash; `role`; `token_version` (bump = revoke all refresh tokens) |
| `consumables` | unique `(hospital_id, sku)`; `reorder_level`, optional `max_level` |
| `stock_batches` | `lot_number`, `expiry_date`, `quantity` (current), `initial_quantity`, `unit_cost` |
| `stock_movements` | append-only; signed `quantity`; `balance_after` = item on-hand after the movement; indexed `(consumable_id, created_at)` |
| `supplier_products` | unique `(supplier_id, consumable_id)`; price, lead time, MOQ, at most one `is_preferred` per item (enforced in API) |
| `alerts` | `alert_type`, `severity`, `status` (OPEN/ACKNOWLEDGED/RESOLVED); key = `(consumable_id, alert_type, batch_id)` among active alerts |
| `audit_logs` | action, entity, JSON details (field-level from→to diffs), IP |

**V2 tables** (migration `207788448e4f`):

| Table | Notes |
|---|---|
| `model_versions` | one row per candidate per training run (`run_id`); name `xgb_vN` / `ma7_vN` / `hist_avg_vN`; data + holdout windows; `dataset_hash`; features; params; `metrics` JSON (mae, rmse, wape, bias, n_points); `feature_importance`; `artifact` (XGBoost booster JSON); one `is_active` per hospital |
| `model_item_metrics` | per (model version, item): holdout totals, MAE, RMSE, WAPE, bias, residual σ, daily holdout series, SHAP drivers |
| `forecasts` | per (model version, item, date): `horizon` (1–30) and `predicted` units |

**Invariant (tested):** for every item, ordered by time, `balance_after[n] = balance_after[n-1] + quantity[n]`,
and the sum of batch quantities equals the last `balance_after`.

**V2B tables** (migration `c67619e5a251`; all carry `hospital_id`, `is_synthetic`, timestamps; **counts only — no patient data**):

| Table | Notes |
|---|---|
| `procedure_types` | `code` unique per hospital, `name`, `department_id`, `description`, `avg_duration_minutes`, `is_active` |
| `procedure_item_mappings` | `procedure_type_id`, `consumable_id`, `quantity_per_procedure NUMERIC(10,2)`, `notes`, `is_active`; unique `(procedure_type_id, consumable_id)` |
| `procedure_schedules` | `procedure_type_id`, `department_id`, `scheduled_date`, `count`, `status` (SCHEDULED/COMPLETED/CANCELLED), `notes`, `created_by_id`; index `(hospital_id, scheduled_date)`; **partial unique index** `(procedure_type_id, department_id, scheduled_date) WHERE status <> 'CANCELLED'` |

V2B models reuse `model_versions` (`model_type = xgboost_procedure`, name `v2b_xgb_vN`; `params.procedure_data`,
`params.validation_folds`, `params.consistent_improvement`).

**V3 tables** (migration `62cc820f507c`):

| Table | Notes |
|---|---|
| `risk_model_versions` | one row per scorer per training run (`stockout_xgb_vN`, `cover_rule_vN`, `reorder_rule_vN`); horizon, data and backtest windows, rows/positives trained on (simulated), dataset hash, features, params (simulation settings, thresholds rule, cover-rule rates), `metrics` (backtest, backtest_with_floor, validation), `evaluation` (events, PR curve, calibration, selection reason), feature importance, warn/high thresholds, booster, one `is_active` per hospital |
| `stockout_predictions` | append-only risk snapshot per item: usable stock, 7/14/30-day demand, days left, stockout date, 14-day shortage, expiring units, lead time, order-by, can-replenish flag, probability, level, source model, reasons, SHAP drivers, 30-day projection; FKs to the risk and forecast model versions; newest row per item = current risk |

`alerts.alert_type` gains the value `STOCKOUT_RISK` (a string column — no schema change).

**V4 tables** (migration `edd9c4eaa152`):

| Table | Notes |
|---|---|
| `supplier_orders` | one item per order: supplier, item, unique `reference` per hospital, `ordered_date`, `quoted_lead_time_days`, `expected_date`, `quantity_ordered`, `unit_price`, `status` (OPEN / PARTIAL / RECEIVED / CANCELLED), `quantity_received`, `first_delivery_date`, `completed_date`, `cancelled_date`, `close_reason`, `is_synthetic`, `created_by_id` |
| `supplier_deliveries` | per delivery: order, `received_date`, `quantity`, `unit_price`, `stock_movement_id` + `batch_id` when it went through the ledger (null for imported history), `is_synthetic` |

Reliability metrics are computed from these rows on request (no stored score that could drift from its evidence).
`alerts.alert_type` gains `SUPPLIER_DELAY`.

**V5 tables** (migration `fe7e6ae943d8`):

| Table | Notes |
|---|---|
| `procurement_settings` | one row per hospital (defaults until first saved): `service_level`, `review_period_days`, `horizon_days`, `stockout_cost_multiplier`, `holding_cost_rate`, `disposal_cost_pct`, `order_cost`, `allow_split`, `budget_limit`, `simulations`, `updated_by_id` |
| `procurement_recommendations` | one per item per generation run (`run_id`): `status` (PENDING / APPROVED / REJECTED / SUPERSEDED), `as_of`, risk link and level, need-by / order-by dates, recommended `scenario_key` + `lines`, `quantity`, `purchase_value`, `expected_cost`; JSON snapshots for audit: `cost_breakdown`, `metrics`, `scenarios` (all evaluated), `replenishment`, `in_transit`, `explanation`, `settings_snapshot`, `solver`; decision: `decided_by_id`, `decided_at`, `decision_reason`, `modified`, `final_lines`, `final_evaluation` |
| `supplier_orders.recommendation_id` | new nullable FK: the order was recorded by approving that recommendation |

Not built: multi-line purchase orders, supplier capacity, contracts / volume discounts.

**V6 table** (migration `13f7948e8cf3`):

| Table | Notes |
|---|---|
| `graph_sync_runs` | one per PostgreSQL → graph projection: `status` (RUNNING / SUCCESS / FAILED), `trigger` (manual / auto / train), `backend`, `fingerprint` (data version it was built from), `node_counts` / `edge_counts` (projection) vs `graph_node_counts` / `graph_edge_counts` (found after writing), `verified`, `removed_nodes`, `duration_ms`, `error`, `triggered_by_id` |

The graph itself (Neo4j) holds no data of record: it is rebuilt from PostgreSQL (`docs/knowledge-graph.md`).

## V7 — AI assistant (migration `181a85264e64`)

| Table | Notes |
|---|---|
| `assistant_conversations` | `hospital_id`, `user_id` (private to that user), `title`, `context` (JSON: item / supplier being discussed, for "it"), `created_at`, `updated_at` |
| `assistant_messages` | `conversation_id`, `role` (user / assistant), `content`, `answer` (JSON), `tool_calls` (JSON: tool, args, ok, facts, error, ms), `provider`, `model`, `intent`, `grounded`, `fallback_reason`, `latency_ms`, `created_at` |

Each question also writes an `audit_logs` row (`assistant.ask`). No patient data: questions are about supplies.

## V8 — multi-hospital tenancy (migration `623279e318d6`)

| Table / change | Notes |
|---|---|
| `organizations` | `name`, `code` (unique), `status` ACTIVE / SUSPENDED, `is_demo` |
| `hospitals` + `organization_id` (NOT NULL, RESTRICT), `status` | existing hospitals → "Default organization" |
| `hospital_memberships` | `user_id`, `hospital_id`, `role`, `department_id`, `status`, `created_by_id`; unique (user, hospital) — **the source of hospital access**; one row per existing user created by the migration |
| `organization_memberships` | organization admins (`role` = organization_admin, `status`) |
| `users` | `hospital_id` / `role` now nullable = the **active** hospital context (cache of an active membership; FK SET NULL instead of CASCADE); `is_platform_admin` |
| `audit_logs` | `hospital_id` nullable, `organization_id` added (backfilled): hospital, organization or platform events |
| PostgreSQL triggers | `medflow_tenant_guard()` on 15 tables — a row may only reference hospital-owned rows of its own hospital; deferred constraint trigger `trg_active_hospital_guard` on `users.hospital_id` |

Per-hospital uniqueness is unchanged and intentional: `(hospital_id, sku)`, `(hospital_id, code)`, … never global.
Details: `docs/multi-hospital.md`.

## V9 — integrations & data exchange (migration `5684eca6bc5c`)

Purely additive; every table carries `hospital_id` (V8 ORM guard + PostgreSQL `medflow_tenant_guard` triggers on the 7
tables that reference other hospital-owned rows).

| Table | Notes |
|---|---|
| `integration_sources` | one hospital system: `name` (unique per hospital), `system_type`, `connector` (upload / api_push / rest_pull), `enabled`, `entities` JSON, non-secret `config` JSON, `is_simulated`, `last_run_at` / `last_success_at` / `last_failure_at` / `last_error`, `service_user_id` (the source's inactive service account) |
| `integration_credentials` | inbound API keys: `key_prefix` (unique, public part), `key_hash` (HMAC-SHA256 with a server pepper), `last_used_at`, `revoked_at` — the key itself is never stored |
| `integration_mappings` | per source + entity (unique): `field_map` (MedFlow field → source field), `defaults`, `date_format` |
| `sync_runs` | the audit trail: `source_id`, `entity`, `mode` (initial / incremental / retry / dry_run), `trigger` (upload / api / schedule / manual / retry), `status`, `started_at`, `completed_at`, `records_received/created/updated/unchanged/rejected`, `error_summary` JSON, `checkpoint_before/after`, `file_name`, `file_sha256`, `parent_run_id`, `triggered_by_id` |
| `sync_records` | rejected records: `run_id`, `row_number`, `external_id`, `raw` JSON, `errors` JSON, `status` REJECTED / RETRIED, `retried_in_run_id` |
| `external_refs` | idempotency: unique (source, entity, external_id) → `record_hash`, `target_type`, `target_id`, first / last run |
| `sync_checkpoints` | unique (source, entity) → `cursor` (largest `updated_at` received), `run_id` |
| `reconciliation_issues` | `consumable_id`, `as_of`, `external_quantity`, `medflow_quantity` (ledger balance at the snapshot time), `difference` (external − MedFlow), `status` OPEN / RESOLVED, `resolution` (adjusted / external_wrong / accepted / matched_later), `note`, `resolved_by_id`, `resolved_at` |

Imported business data lives in the **existing** tables (`stock_movements` with `performed_by_id` = the source's service
account and `reference` = the external id, `stock_batches`, `supplier_orders.reference` = PO number, `supplier_deliveries`,
`consumables`, `suppliers`, `supplier_products`, `departments`). Details: `docs/integrations.md`.

## V10 — pilot & business validation (migration `b8c6b2c0cc67`)

Purely additive; hospital-owned (V8 ORM guard + 7 new PostgreSQL trigger specs). Metrics are **not stored** — they are
computed on demand from the V1–V9 tables; only people's inputs and saved reports are stored.

| Table | Notes |
|---|---|
| `pilots` | hospital, organization, name (unique per hospital), description, `status` (planned / active / paused / completed / cancelled), `data_classification` (observed / synthetic — synthetic for demo hospitals), owner, `baseline_start/end`, `pilot_start/end` (planned end), `actual_end`, notes, `external_factors` JSON; CHECK constraints: baseline ordered, pilot ordered, **baseline ends before the pilot starts** |
| `pilot_participants` | a department **or** a user (CHECK exactly one) |
| `pilot_data_sources` | links to V9 `integration_sources` |
| `pilot_issues` | category, severity, title, description, source / source_ref, impact, status, assignee, creator, resolution, resolver, resolved_at |
| `pilot_feedback` | target type, optional recommendation, rating, reasons JSON, comment, user |
| `recommendation_views` | who opened which V5 recommendation, when |
| `pilot_readiness` | a person's confirmation of a manual checklist item (+ note, who, when) |
| `pilot_report_snapshots` | immutable saved report (JSON + Markdown + classification) |
