# ML pipeline — V2A (consumption), V2B (procedure-aware), V3 (stockout risk), V4 (supplier intelligence), V5 (procurement)

Code: `backend/app/ml/`. Runs inside the FastAPI service. Training ≈ 12–20 s on the demo hospital
(42 items, item×department series, 90 days) including V2B and its rolling validation folds. V2B is described in
[its own section below](#version-2b--procedure-aware-forecasting); everything above it is unchanged V2A behaviour.

```text
stock_movements (ISSUE − RETURN, by department, business-day)
      │  data.load_panel()      + censoring: stocked-out item-days → NaN
      ▼
panel: dates × (item, department)            item totals: dates × item
      │
      ├── split: train = all but last 14 days · holdout = last 14 days
      │
      ├── historical_average     flat mean of known days
      ├── moving_average_7       flat mean of last 7 known days
      └── xgboost                global model, recursive 1-day-ahead
                 │
                 ▼  forecast 14 days per series → sum to item → score vs actual (censored days excluded)
      metrics: MAE, RMSE, WAPE, bias (overall + per item)
                 │
                 ▼  serve the lowest WAPE (XGBoost wins ties)
      refit selected model on ALL data → forecast 30 days → store
```

## Data

- **Target**: units consumed per (item, department, day) = issued − returned.
- **Censoring**: a day is censored for an item if its on-hand balance hit 0 during the day or the day started at 0.
  Consumption on those days reflects availability, not demand, so it is NaN in features (XGBoost handles missing
  values) and excluded from targets and metrics.
- Today (a partial day) is excluded; `data_end` = yesterday.

## Features (XGBoost)

| Feature | Meaning |
|---|---|
| `lag_1` | previous-day consumption |
| `lag_7` | same weekday last week |
| `lag_14` | same weekday two weeks ago |
| `roll_mean_7` | 7-day rolling average (days before *d*) |
| `roll_mean_14` | 14-day rolling average |
| `roll_std_7` | 7-day volatility |
| `trend_7_14` | recent trend: 7-day minus 14-day average |
| `day_of_week` | 0 = Monday |
| `department` | categorical |
| `item` | categorical |

Each series is scaled by its own training mean so one global model serves gloves (≈60/day) and saw blades
(≈1/day). Predictions are rescaled and clipped at 0. Multi-day forecasts are **recursive**: predict day 1,
append, recompute features, predict day 2, …

Parameters: `eta 0.05, max_depth 5, min_child_weight 3, subsample 0.8, colsample 0.9, 300 rounds, hist,
seed 42` (see `xgb_model.PARAMS`).

## Metrics (holdout, item-level daily totals)

- **MAE** — mean absolute error per item-day (units)
- **RMSE** — penalises large misses
- **WAPE** = Σ|error| ÷ Σ actual — the selection metric (scale-free; safe with zero days, unlike MAPE)
- **Bias** = Σ(predicted − actual) ÷ Σ actual — positive means over-forecasting

Demo result (synthetic data, seed of 2026-09-23, holdout 09-09 … 09-22):

| Candidate | MAE | RMSE | WAPE | Bias |
|---|---|---|---|---|
| Historical average (`hist_avg_v1`) | 13.98 | 25.69 | 23.2 % | +10.1 % |
| 7-day moving average (`ma7_v1`) | 13.71 | 25.19 | 22.7 % | +4.0 % |
| XGBoost V2A (`xgb_v1`) | 11.48 | 21.49 | 19.0 % | +3.3 % |
| **Procedure-aware XGBoost V2B (`v2b_xgb_v1`)** — served | **10.14** | **20.53** | **16.8 %** | +2.0 % |

(The V2.0 release reported XGBoost ≈ 11 % WAPE. That was on the *previous* synthetic generator; the V2B generator
issues part of the demand as procedure kits, which makes daily consumption lumpier, so every model's error is
higher. Numbers from different generators are not comparable.) Sparse, slow-moving
items (≤ 1/day) have high per-item WAPE — expected for count data; don't over-read them.

## Registry & storage

| Table | Contents |
|---|---|
| `model_versions` | one row per candidate per run: name (`xgb_v3`), type, trained_at/by, data & holdout windows, dataset hash, features, params, metrics, feature importance, booster (XGBoost), `is_active` |
| `model_item_metrics` | per item: actual/predicted totals, MAE, RMSE, WAPE, bias, residual σ, daily holdout series, SHAP drivers (7/14/30 days) |
| `forecasts` | 30 daily predictions per item for the active model |

## Explanations

- **Drivers**: XGBoost SHAP values (`pred_contribs=True`) summed over departments and horizon days, in units.
  Base level + contributions = forecast (exact before clipping; tested).
- **Sentences**: generated from stored numbers only (forecast vs recent average, top ± drivers, backtest WAPE,
  stock cover).
- **Range**: heuristic ≈ 80 %: `1.28·σ_daily·√h + |mean holdout error|·h`. Labelled approximate in the API and UI.

## Running

```bash
python -m app.ml.train                 # all hospitals
python -m app.ml.train --if-missing    # used by the Docker entrypoint
POST /api/forecasts/train              # admin, procurement, inventory roles
```

Retrain after new stock data; the UI flags a model as stale when newer data exists.

## Known limitations

- 90 days of **synthetic** history; no yearly seasonality possible; real accuracy is unknown until pilot data.
- Censoring uses on-hand balance including expired stock (usable-stock history isn't stored).
- Recursive forecasting compounds error over 30 days; direct multi-horizon models are a later option.
- Intervals are heuristic, not calibrated quantiles.

---

# Version 2B — procedure-aware forecasting

`Scheduled procedures × configured kit quantity → expected item demand → extra features → V2B candidate`.
V2B **extends** V2A; it never replaces it. When there is no usable procedure data, V2B is not trained and V2A
behaves exactly as before.

## Data (`app/ml/procedures.py`)

| Table | Used as |
|---|---|
| `procedure_types` | procedure kind, department, active flag |
| `procedure_item_mappings` | active rows: `quantity_per_procedure` of an item per procedure (from the DB — never hard-coded) |
| `procedure_schedules` | per (type, department, date): `count`, `status` |

- **History** (≤ `data_end`): the non-cancelled row per slot (COMPLETED, or SCHEDULED if never closed).
- **Future** (> `data_end`): `SCHEDULED` rows of **active** types only.
- **CANCELLED rows never count.** Inactive mappings never count.
- `schedule_end` = last date with a future schedule row. Days after it are **unknown** (not "zero procedures"):
  the V2B forecast for those days falls back to the V2A forecast (`params.procedure_data.falls_back_to_v2a_from`).
- **Fingerprint**: sha256 over mappings and schedule rows. The V2B `dataset_hash` = sha256(consumption hash +
  fingerprint), so it changes when consumption, schedule or mappings change. The forecast API compares the live
  fingerprint with the trained one → `schedule_changed_since_training`.

## Features (added to the V2A features for the V2B candidate only)

| Feature | Level | Meaning for day *d* |
|---|---|---|
| `procedure_count` | series (item × dept) | procedures in that department on *d* that use the item |
| `procedure_expected_quantity` | series | Σ count × kit qty on *d*, divided by the series scale |
| `procedure_type_count` | item | distinct procedure types using the item on *d* |
| `procedure_department_count` | item | departments with such procedures on *d* |
| `procedure_demand_share` | series | expected quantity ÷ 14-day mean consumption (capped at 1) |
| `procedure_expected_delta_7` | series | expected quantity minus its mean over the previous 7 days |

**No leakage.** The only same-day input is the *schedule* for *d*, which is known before *d* (it is a plan).
Consumption-derived parts use strictly earlier days. Tests:
`test_ml_procedures.py::test_no_future_procedure_leakage_into_historical_features` (mandatory leakage test) and `test_future_schedule_does_not_change_backtest`
(adding future schedule rows cannot change holdout metrics).

## Selection — V2B must earn its place

1. V2B is trained only if `has_signal`: ≥ 14 history days with a positive expected quantity. Otherwise
   `procedure_model.skipped_reason` explains why and V2A is served.
2. All candidates are scored on the same 14-day holdout (MAE, RMSE, WAPE, bias).
3. **Rolling validation**: V2A and V2B are also scored on up to 2 earlier 14-day windows (3 folds total,
   each trained only on data before the window).
4. V2B is eligible only if it beats V2A on **every** fold (`consistent_improvement`).
5. Lowest WAPE wins; ties go to xgboost → ma7 → hist → v2b, so **V2B has to be strictly better**.
6. Model names: `v2b_xgb_vN`. `notes` on each row say why it was / was not selected.

Demo (seeded synthetic data): V2A → V2B per fold 19.0 → 16.8 %, 19.2 → 16.8 %, 20.4 → 19.8 % ⇒ V2B served.
V2B feature importance (gain share): `procedure_expected_delta_7` 21 %, `procedure_expected_quantity` 15 %,
`item` 13 %, `day_of_week` 10 %.

Why the unanimous rule: on a 2-series toy where procedures are **pure noise** (no effect on consumption), V2B still
won a single 14-day holdout in many seeds. With the unanimous-fold rule the false-positive rate in that toy fell to
roughly 3 in 10 seeds on 84 days of data. It is lower on longer histories, but it is **not zero** — see limitations.
Tests: `test_v2a_stays_active_when_v2b_is_worse`, `test_v2b_lucky_on_holdout_but_inconsistent_is_not_served`,
`test_no_procedure_data_falls_back_to_v2a`.

## Explanations & API additions

`GET /api/forecasts/{id}` keeps every V2A field and adds `forecast_source`, `scheduled_procedures`,
`procedure_driven_demand`, `procedure_impact` (types, counts, kit quantities, V2A vs V2B totals, SHAP units from
procedure features), `model_comparison`, `comparison_daily` (V2A line for the chart), `expected_shortage`,
`stock_covers_horizon`, `days_of_stock_remaining`. Explanation sentences are built from these numbers — no LLM.
For an item no scheduled procedure uses, the V2A/V2B gap is described as a model difference, not a schedule effect.

**Stored forecasts only change on retraining.** Editing the schedule updates `procedure_impact` (live) and flags
"retrain", but the predicted numbers stay those of the trained model.

## Synthetic demo data (`app/seed.py`)

9 synthetic procedure types across Orthopaedics, General Surgery, Obstetrics & Gynaecology and OPD, 72 kit
mappings (notes: "Synthetic kit quantity … not a clinical standard"), weekday volume patterns, weekly department
load factors, ~6 % cancellations, a joint-replacement "camp" (×2.5 TKR/THR on days 3–9 of the future window), a
hidden per-(procedure, item) factor of 0.75–1.25 between kit and actual use, and 30 days of future schedule.
Everything is flagged `is_synthetic` and labelled in the UI. **It is not real hospital data and not medically
validated.** The generator is deterministic (fixed seeds, sorted iteration).

## V2B limitations

- The demo shows V2B helping because the generator makes consumption depend on procedures. Whether procedures
  improve forecasts in a real hospital is unknown until pilot data exists.
- False positives remain possible (see noise experiment above); more history and more folds reduce them.
- Kit quantities are taken as configured; learning them from history (ratio of consumption to procedures) is not
  implemented.
- Emergency (unscheduled) procedures are only visible after the fact, as COMPLETED rows.
- The schedule horizon limits V2B: beyond `schedule_end` the forecast is V2A.
- Alerts were not changed; shortage vs forecast is shown on the forecast page only (alerting on forecasts belongs
  to V3 stockout intelligence).

---

# Version 3 — stockout risk intelligence

**Question answered:** *which items are likely to run out, when, and why?*
Code: `backend/app/risk/`. One product pipeline, not a separate ML system:

```text
V1 inventory (usable batches, expiry, ledger) ─┐
V2A/V2B served demand forecast (30 days) ──────┼─► V3.1 projection ─► days left · stockout date · 14-day shortage
supplier lead time (preferred supplier)  ──────┘                    · expiring units · order-by date · can a delivery arrive in time?
point-in-time features ─► V3.2 risk model ─► probability ─► risk level (thresholds + lead-time floor) ─► reasons ─► alerts / dashboard
```

## V3.1 — deterministic projection (`projection.py`)

Day by day from today: drop batches past their expiry, take the day's forecast demand FEFO, record unmet demand.
Outputs `days_of_stock_remaining`, `expected_stockout_date`, `shortage_quantity` (unmet demand over 14 days),
`expiring_quantity`, and with the preferred supplier's lead time: `order_by_date = stockout − lead time`,
`can_replenish_in_time = today + lead time ≤ stockout`. **No deliveries are assumed** — purchase orders and
orders in transit are not tracked until V5. Today's already-issued units are subtracted from today's forecast.

## V3.2 — risk model

**Label:** the item is in stock at the end of day *t* and is out of stock on at least one of the next 14 days.
A stockout day = the ledger balance touched 0 that day or the day started with no usable (non-expired) stock.

**Features** (`features.py`, 23; all from data ≤ *t*, plus the procedure plan, which is known in advance):
usable stock; 7/14/30-day demand estimate; stock ÷ demand for each; 7- and 28-day consumption; trend; volatility;
procedure-driven demand and share; supplier lead time; stock ÷ reorder level; projected days of cover;
cover − lead time; stock expiring within 14 days (units and share); stockout days in the last 60; days since last
stockout; days since last delivery; main department. The demand estimate in the features is point-in-time
(non-procedure level × weekday profile + scheduled kits) because the V2 model cannot be re-trained for every
historical day without leakage; the displayed date/shortage use the served V2 forecast.
Leakage test: `test_risk.py::test_features_use_only_data_up_to_the_origin`.

**Training data — simulated, labelled synthetic** (`simulate.py`). The demo ledger has only 10 stockout episodes
(9 items, all in the last 30 days) — too few to learn from. The simulator replays each item with its own demand
level, weekday profile, procedure kits, reorder/max level, lead time, MOQ and shelf life, and injects supplier
delays (on-time 60–95 %, 2–7 days late), missed reorders (6 %), demand spikes (×1.3–2.0), short-dated deliveries
and varied reorder discipline (×0.6–1.3). **Its parameters come only from the first 28 days of the ledger**, before
the backtest window. Small hospitals get more replicates (≥ 20 000 rows). Settings were fixed before looking at the
backtest and were not tuned on it.

**Model:** XGBoost binary classifier (depth 4, 300 rounds, η 0.05, seed 42), stored as `stockout_xgb_vN` in
`risk_model_versions` with its booster, features, params, dataset hash and feature importance.

**Thresholds** (chosen on a separate simulated validation replicate — never on the backtest):
MEDIUM ("warn") = highest probability that still catches 80 % of validation stockouts (recall target: missing a
stockout is worse than an extra warning); HIGH = where validation precision reaches 60 % (at least 0.5).
An earlier F2-optimal rule was dropped because it depends on the base rate, which differs between simulation
(~20 %) and ledger (~6.5 %).

**Lead-time floor** (deterministic safety net): at least MEDIUM when the order-by date is within 2 days, HIGH when
even an order placed today arrives after the projected stockout. Added because the model learns that items near the
reorder point are usually restocked (it cannot see orders in transit), which showed LOW risk for an item projected
to run out in 4 days with a 4-day lead time. Its cost is measured in the backtest (below). An item already out of
stock is HIGH with probability 1 and keeps the V1 OUT_OF_STOCK alert.

## V3.4 — evaluation: could it have predicted the stockouts that happened?

Backtest on the hospital's **own ledger**: for every item and day from day 28 (as-of dates 22 Jul – 8 Sep for the
demo), compute features as of that day and compare with what happened in the next 14 days. 2 085 in-stock
item-days, 135 positive, 10 stockout events. Same rows for every scorer. Demo result (synthetic data):

| Scorer | Precision | Recall | F1 | PR-AUC | FP | FN | Brier | Events warned | …≥ lead time ahead |
|---|---|---|---|---|---|---|---|---|---|
| **XGBoost + lead-time floor (what users see)** | 0.25 | **0.73** | 0.37 | 0.51 | 302 | **37** | 0.046 | 10/10 | **10/10** |
| XGBoost probability only | 0.27 | 0.63 | 0.37 | **0.51** | 235 | 50 | 0.046 | 10/10 | 9/10 |
| Cover rule (V3.1, bucketed probability) | 0.17 | 0.90 | 0.28 | 0.35 | 603 | 13 | 0.054 | 10/10 | 10/10 |
| Reorder-level rule (V1 LOW_STOCK) | 0.21 | 0.57 | 0.31 | 0.40 | 287 | 58 | — | 10/10 | 10/10 |

Reading it honestly:
- XGBoost ranks risk best (PR-AUC 0.51 vs 0.40 for the V1 rule) and beats the V1 rule on precision *and* recall.
- Row-level recall (0.63) is below the 80 % target set on simulated data: the ledger differs from the simulation.
- Event-level metrics don't separate the scorers on this demo — every scorer warned about all 10 events at some
  point; with 10 events that is not strong evidence of anything.

> **Audit note (2026-09-24, `docs/v0-v10-audit.md`).** The table above is one seed date. The demo ledger is generated
> relative to the day it is seeded, so these numbers change from day to day. Re-measured on the demo seeded 2026-09-24
> (as-of 23 Jul – 9 Sep, 2 086 item-days, 98 positive, **7 events**): XGBoost PR-AUC **0.236**, cover rule **0.261**, V1 reorder
> rule 0.254 — so on that day XGBoost did **not** rank best and the selection rule correctly served the **cover rule**
> (precision 0.12, recall 0.93, lead-time-aware event recall 7/7). With 7–10 events per seed, the ranking of the scorers is
> not stable; the bullet "XGBoost ranks risk best" holds for the seed above only.
- Precision is low (~1 in 4 warnings is followed by a stockout within 14 days); in the demo, many warned items were
  restocked in time by the simulated purchasing process.
- Calibration on the ledger: close in the lowest bin (predicted 5 % → observed 3 %), somewhat over-confident in
  0.2–0.4 (26 % → 18 %) and under-confident above 0.4 (48 % → 59 %; the top bins hold only 14 item-days).

**Serving guard:** the XGBoost model is served unless its backtest PR-AUC is below the cover rule's, in which case
the cover rule is served (tested with an anti-model). This uses the backtest for a safety check, so the served
model's backtest numbers are slightly optimistic — noted, not hidden. Metrics, events, PR curves and calibration
are stored per model version (`metrics.backtest`, `metrics.backtest_with_floor`, `metrics.validation`, `evaluation`).

## Serving & alerts

`risk/engine.py` appends a `stockout_predictions` snapshot per item: after every stock movement of that item
(inside the alert engine), after forecast or risk training, and lazily on the first read of a new day. The alert
engine raises `STOCKOUT_RISK` (MEDIUM → MEDIUM, HIGH → HIGH, HIGH that cannot be replenished in time → CRITICAL)
for in-stock items with the same dedupe / escalate / auto-resolve lifecycle; V1 alert types are unchanged.

## V3 limitations

- Trained on simulated histories; the evaluation rests on 10 real (synthetic-demo) stockout events.
- Orders in transit are unknown (V5), so the projection assumes no deliveries and the lead-time floor may flag items
  whose delivery is already on its way.
- Supplier lead time is the configured value; actual delivery reliability is V4 (the simulator assumes a range).
- The procedure plan is assumed known 14–30 days ahead, as in V2B.
- Prediction snapshots accumulate (≈ 1 row per item per movement / day); no pruning yet.

---

# Version 4 — supplier intelligence & reliability

**Question answered:** *given the predicted demand and stockout risk, which suppliers are reliable enough to help
the hospital avoid the problem?* Code: `backend/app/supplier_intel/` (+ `services/supplier_orders.py`).
V4 adds evidence and analysis; it does **not** change forecasting (V2) or the stockout engine (V3), and it places no
orders (recommendations are V5).

```text
supplier order log (orders + deliveries) ─► metrics ─► reliability score (OTIF) + components   (scorecards, per item)
                                         └► predict ─► lead time (median / 90th pct), P(late)  (+ temporal backtest)
V3 expected stockout date ──────────────────────────► can each supplier deliver in time? (its own history)
```

## Evidence: the supplier order log

`supplier_orders` (order date, quoted lead time, expected date, quantity, price, status) and `supplier_deliveries`
(date, quantity, price, linked RECEIPT movement). Orders are recorded from the UI/API; deliveries are recorded by
receiving stock against an order (`POST /inventory/receive` with `supplier_order_id`). Cancelling an open order or
closing a partly delivered one short counts against the supplier.

**Demo data (synthetic):** the simulation's own reorders are logged as orders and linked to their receipts (107);
items that ran out have a supplier backorder (4, overdue); 3 orders are in transit; plus 12 months of imported-style
history for every supplier × item (preferred supplier on the replenishment cycle, alternatives occasionally) with
supplier-specific delays, cancellations, short deliveries and price changes derived from each fictional supplier's
on-time probability. It uses a separate random stream and creates no stock movements, so **V1–V3 results are
unchanged** (verified: V2A 19.04 %, V2B 16.82 % WAPE; V3 PR-AUC 0.5075, same precision/recall/FP/FN).

## Reliability score — one measured rate, not a weighted blend

| Term | Definition |
|---|---|
| decided order | received (fully or closed short), cancelled, or still open past its expected date |
| on time | first delivery on or before the expected date (order date + quoted lead time) |
| in full | quantity received ≥ quantity ordered |
| **OTIF** | delivered in full by the expected date |
| **score** | `100 × (OTIF orders + 5 × prior) ÷ (decided orders + 5)`, prior = hospital-wide OTIF (same window) |
| interval | Wilson 95 % on the raw OTIF rate; < 5 decided orders = "limited evidence" |
| grade | A ≥ 90 · B ≥ 80 · C ≥ 65 · D < 65 |

Stored/shown components (never weighted into the score): orders, open, overdue, received, cancelled; on-time and
late rate; average days late; lead time median / mean / 90th percentile / std vs quoted; cancellation rate; quantity
fill rate and in-full rate; price stability (1 − share of consecutive orders whose price moved > 2 %) and price
trend; monthly OTIF / on-time; lead-time histogram. Item-level scores shrink towards the supplier's own rate.

Demo scorecards (last 365 days, synthetic): hospital-wide 751 orders, OTIF 84.9 %, on time 91.9 %.

| Supplier | Score | Decided | OTIF (95 %) | On time | Lead time quoted · median · p90 | Cancelled | Fill | Price stability |
|---|---|---|---|---|---|---|---|---|
| Karnataka Surgical (KSD) | **93.5 A** | 175 | 93.7 % (89–96) | 98.2 % | 3 · 3 · 3 | 2.9 % | 99.4 % | 96 % |
| Silicon City Lab (SCL) | 91.1 A | 116 | 91.4 % (85–95) | 94.7 % | 3 · 3 · 3 | 1.7 % | 100 % | 96 % |
| Deccan Healthcare (DHT) | 84.4 B | 211 | 84.4 % (79–89) | 92.2 % | 5 · 5 · 5 | 3.3 % | 99.4 % | 94 % |
| Cauvery Pharma (CPS) | 82.2 B | 78 | 82.1 % (72–89) | 88.0 % | 4 · 4 · 4 | 3.9 % | 99.9 % | 91 % |
| OrthoPrime (OPI) | 78.0 C | 94 | 77.7 % (68–85) | 87.1 % | 7 · 7 · 8.9 | 1.1 % | 98.2 % | 87 % |
| Nandi Medisupplies (NMS, cheapest) | 69.1 C | 75 | 68.0 % (57–77) | 81.4 % | 2 · 2 · 5.1 | 6.7 % | 99.8 % | 84 % |

## Item-specific comparison and the V3 link

For an item, every supplier that lists it: catalogue price (and % above the cheapest), MOQ, quoted / typical (median)
/ worst-case (90th pct) delivery time, on-time, OTIF score — for this item if it has ≥ 5 decided orders, otherwise
the supplier overall (labelled). With a V3 projected stockout in *D* days, each supplier gets
**P(delivered within D days)** = past orders delivered within D days ÷ past orders (cancelled = never arrived; open
orders count once their outcome is known), smoothed `(k + 0.5)/(n + 1)` and shown as *k of n*; "likely" ≥ 80 %,
"uncertain" 50–80 %, "unlikely" < 50 %, "no history" when n = 0. Orders already in transit get a predicted arrival
(median of past lead times longer than the time already elapsed) and P(arrives before the stockout).

Demo example: *Absorbable suture 2-0* runs out in 4 days. The cheapest supplier (OrthoPrime, ₹287.90) delivered
within 4 days in 0 of 96 orders — unlikely; Cauvery (₹294.13, +2 %) in 14 of 15 for this item — likely. Its open
order from Cauvery is already 2 days overdue.

Wording is deliberate: "based on historical delivery performance, X can meet the required delivery window" and
"information for procurement review — no order has been placed". Nothing is ordered automatically.

## Lead-time & delay prediction — evaluation

Expanding-window backtest: every delivered order after a 90-day warm-up (721 orders, 23 Sep 2025 – 23 Sep 2026)
is predicted from orders whose outcome was known on its order date (test:
`test_backtest_uses_only_outcomes_known_on_the_order_date`). 7.5 % of test orders arrived late.

| Lead-time predictor | MAE (days) | Bias | Within ±1 day | Covered by predicted p90 |
|---|---|---|---|---|
| Quoted lead time | 0.35 | −0.13 | 94.0 % | 92.5 % |
| Supplier history | 0.35 | −0.13 | 94.0 % | **93.9 %** |
| Supplier × item history | 0.37 | −0.15 | 93.9 % | 92.5 % |

| Delay predictor (P(late)) | Brier | ROC-AUC | Log loss |
|---|---|---|---|
| Hospital-wide rate | 0.070 | 0.44 | 0.268 |
| **Supplier rate** | **0.069** | **0.63** | **0.261** |
| Supplier × item rate | 0.072 | 0.57 | 0.289 |

Honest reading: 92 % of deliveries arrive on the quoted day, so history adds nothing to the *typical* lead time; its
value is in the tail (p90 coverage +1.4 points) and in *who* is late — supplier history ranks late deliveries better
than chance (AUC 0.63) but only modestly, and per-item rates are too sparse to help. The suppliers were generated
with different reliabilities, so this shows the method working on synthetic data, not real supplier behaviour.

## Alerts

`SUPPLIER_DELAY`: an open/partial order past its expected date (one alert per item, worst order in the message;
HIGH when ≥ 3 days overdue or stock is at/below the reorder level). Auto-resolves when the order is received or
cancelled. Evaluated on order changes, stock movements, and the daily refresh.

## V4 limitations

- Demo order history is synthetic; reliability on real suppliers is unknown until real orders are recorded.
- One order line per item (multi-line purchase orders, approvals and quantities are V5).
- Quality issues (rejected deliveries) are not tracked yet; "in full" counts accepted quantity only.
- V3's projection still assumes no deliveries; V4 shows in-transit orders beside it. V5 combines them in its own
  projection (weighted by arrival probability) without changing V3.
- Supplier × item evidence is thin; the item view falls back to the supplier overall when an item has < 5 orders.

---

# Version 5 — procurement intelligence & optimisation

**Question answered:** *what should we buy, how much, from whom, and by when?* Code: `backend/app/procurement/`
(+ `services/procurement.py`, `api/procurement.py`). V5 **recommends — it never purchases**: every order needs a person's
approval, and an approved recommendation is recorded in MedFlow's V4 order log only (no purchasing system, nothing sent to
a supplier). No LLM: every explanation is assembled from computed numbers. XGBoost stays where it was (V2 demand, V3 risk);
V5 adds replenishment maths, arrival distributions, simulation and an OR-Tools selection.

```text
V1 batches + expiry, MOQ, prices ─┐
V2 served forecast (+ its error) ─┤─► V5.1 replenishment: safety stock, reorder date, required / MOQ / expiry-capped qty
V3 risk snapshot ─────────────────┤
V4 order history ─────────────────┼─► V5.2 arrival distributions: new orders per supplier; in-transit orders conditional
open supplier orders ─────────────┘         on days already elapsed → projected available = physical + Σ incoming × P(arrived)
                                      V5.3 scenarios (no order · supplier × 3 quantities · split) → Monte Carlo (same paths)
                                           → expected cost = purchase + stockout + holding + expiry − carried forward
                                           → OR-Tools CP-SAT picks one scenario per item (optional budget across items)
                                      what-if: supplier +k days / demand ±% → same scenarios, same paths, recomputed
                                      V5.4 PENDING recommendation → approve / modify (reason) / reject (reason) → order recorded
```

## V5.1 — replenishment (`replenish.py`, deterministic)

| Quantity | Definition |
|---|---|
| projected available(t) | V3.1 FEFO projection of usable batches against the served forecast (expiry-aware), **+ Σ in-transit quantity × P(arrived by t)**; negative = expected shortfall |
| cover period | expected lead time *L* of the supplier (from its delivery history) + review period *R* (setting, default 7 days) |
| safety stock | *z* × √((L + R) σ_d² + μ_d² σ_L²) — *z* from the service level (default 95 %), σ_d = the served model's daily holdout error for the item, σ_L = spread of the supplier's actual lead times |
| reorder date | first day projected available falls below safety stock, minus *L* |
| required quantity | demand over the cover period + safety stock − (usable − expiring before use) − expected in-transit arrivals |
| MOQ-adjusted | max(required, supplier MOQ) when anything is required; the rounding is shown |
| expiry cap / storage cap | μ_d × shelf life − stock ahead of the new batch / item max level − projected stock on arrival; exceeding either is a warning |

Needs attention = order-by date before the next review, V3 medium/high risk, out of stock, or an overdue order.

## V5.2 — supplier feasibility & arrival probabilities (`arrival.py`)

For a new order the distribution of the arrival day comes from the supplier's past orders **for this item** (≥ 5 outcomes),
else the supplier's orders for any item, else the hospital-wide delay-versus-quote applied to this supplier's quote.
Cancelled orders never arrive. As V4's score, an item distribution is shrunk towards the supplier's and a supplier's
towards the hospital's with 5 pseudo-orders, and one pseudo-order that never arrives keeps every probability below
certainty (20 of 20 on time → 96 %). **Orders in transit** are judged only against past orders that had not arrived after
the same number of days; if none took that long there is no evidence and the order is **not counted** (said explicitly).
The raw "k of n" is always shown next to the probability.

**Are the probabilities trustworthy?** `GET /procurement/evaluation` — expanding window, no look-ahead: every order placed
after a 90-day warm-up is scored with a distribution built only from orders completed before it was placed, for
P(delivered within the quote) and P(within quote + 2 days). Demo (synthetic, 744 orders, 1,488 cases):

| | Mean predicted | Observed | Brier |
|---|---|---|---|
| V5 arrival distributions | 86.1 % | 91.1 % | **0.0851** |
| "the quote is certain" | 100 % | 91.1 % | 0.0887 |

Slightly better than trusting the quote, and **conservative** (under-predicts on-time delivery by ~5 points, mostly in the
50–90 % band) — by design it errs towards not counting stock that may not come. Disclosure: the first version shrank
towards "never arrives" only and was *worse* than trusting the quote (Brier 0.0914, predicted 83.8 %); it was changed to
shrink towards the level above, the same convention as V4, after seeing that result. No parameter was tuned to the
backtest beyond that one structural change.

## V5.3 — scenarios, cost model, optimiser (`engine.py`, `simulate.py`, `costs.py`, `optimize.py`)

**Scenarios per item:** no new order; for every active supplier that lists the item: the V5.1 quantity, the quantity without
safety stock, one extra review period of cover (capped by expiry / storage); and — when the cheapest supplier is not the one
most likely to arrive in time — a **split**: the reliable supplier bridges until the cheap one's 90th-percentile delivery,
the cheap one supplies the rest.

**Monte Carlo (500 paths, configurable):** demand = max(0, forecast + σ_d·z), arrival days drawn from each distribution;
stock walked day by day exactly like V3.1 (deliveries before demand, expiry removed, FEFO). **Common random numbers:** every
scenario of an item sees the same demand paths and the same draw per supplier and per in-transit order; an order's effect is
its difference to "no order" on the same paths. Horizon = the reference supplier's cover period (until an order at the next
review could arrive). Seed = item + date, so results are reproducible within a day.

**Expected total cost** (all parameters in `procurement_settings`, editable on the Cost model tab, formula shown):

```text
purchase        Σ quantity × unit price + fixed cost per order line (₹250)
+ stockout      E[units short] × reference price × stockout multiplier (5)
+ holding       E[extra unit-days in stock, in and after the horizon] × reference price × 25 %/year ÷ 365
+ expiry/waste  E[extra units expiring before use] × unit price × (1 + disposal 10 %)
− carried fwd   (quantity − E[extra demand served in the horizon]) × unit price   ← stock left serves later demand
```

**OR-Tools CP-SAT** chooses exactly one scenario per item minimising the summed expected cost; with a budget (setting) the
constraint Σ purchase ≤ budget couples the items and the solver trades expected cost against cash across them (the
explanation states what the budget changed). One worker, fixed seed → deterministic.

**Explanations** (numbers only), e.g. on the demo: *"Buying 313 pair from RELY costs ₹1,252 more than the same quantity from
the cheapest supplier (CHEAP at ₹38.00/pair), but RELY has historically arrived within the required 3-day window in 20 of 20
orders (CHEAP: 9 of 20)"* (test world); suture 2-0 (demo): *"CPS arrived within 4 days in 14 of 14 past orders; OPI arrived
within 4 days in 0 of 93"*, split recommended, overdue PO-00110 not counted (no past CPS order took that long).

**Demo run (synthetic, 13 items needing attention, model-based — not a validation):**

| Policy (same simulated paths) | Expected total cost | Expected shortage (units) | Purchase value |
|---|---|---|---|
| V5 recommendation | ₹2.51 lakh | 331 | ₹2.54 lakh |
| Cheapest supplier at its V5.1 quantity | ₹3.28 lakh | 422 | ₹2.30 lakh |
| No new order | ₹6.04 lakh | 1,198 | — |

These numbers come from the same model that made the recommendation, so they show the trade-off it makes, not that it
works in a real hospital. Three recommendations are "no new order" (stock and in-transit orders cover the period).

## What-if (`engine.whatif`)

Supplier *S* +k days shifts *S*'s arrival distributions (new orders and its in-transit orders); demand ±% scales the
forecast. The same scenarios are re-evaluated on the same paths and horizon, so every change is caused by the what-if.
It also re-plans from scratch (quantities recalculated) to show whether the recommendation would change. Nothing stored.

## V5.4 — human approval (`services/procurement.py`)

Generate → PENDING (older PENDING for the item → SUPERSEDED) → **approve** (as recommended) / **modify** (supplier,
quantity, price; reason required; MOQ and catalogue enforced; the changed plan is re-evaluated and stored) / **reject**
(reason required). Only same-day recommendations can be approved. Approving records supplier order(s) with
`recommendation_id`, note "Not sent to the supplier by MedFlow", audit rows for the decision and each order.

## V5 limitations

- All data is synthetic; costs (stockout multiplier, holding rate…) are placeholders to be set by each hospital.
- Scenario quantities are a small grid, not a continuous optimum; CP-SAT optimises the choice among them (and the budget).
- Demand errors are independent day to day (no demand shocks); partial deliveries are treated as full deliveries.
- No historical backtest of V5 decisions (would need replaying procurement decisions); only the arrival probabilities are
  backtested. The policy comparison above is model-based.
- Expiry of new stock uses the item's median shelf life; FEFO order among batches uses nominal expiry.
- Results depend on the time of day (today's issues reduce today's remaining demand) and change daily.
- No supplier capacity limits, volume discounts or storage volumes beyond the item max level.
