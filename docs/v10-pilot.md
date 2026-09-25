```text
V10 STATUS: COMPLETE
VERSION: v10.0.0
BASE: v9.0.0
BRANCH: feature/v10-real-hospital-pilot
```

# MedFlow AI — V10: Real hospital pilot & business validation

> **Real-hospital status:** no real hospital or clinic has taken part in a MedFlow pilot. Every pilot result produced
> while building V10 is a **synthetic/demo result** from the demo hospitals' simulated data. **No real-world outcome claim
> can be made yet.** MedFlow supports a validated integration framework and controlled data-import/API connectors.
> Production hospital-system integrations require access to the corresponding hospital/vendor systems and have not been
> claimed unless actually tested.

## 1. Purpose

V10 adds no new intelligence. It lets MedFlow run a time-boxed **pilot** in one hospital and measure, against a
**baseline period**, whether the existing system (V1–V9) is useful: data quality, inventory accuracy, stockouts, forecast
error, stockout-warning performance, supplier reliability, what people do with procurement recommendations, adoption,
feedback, and the problems met on the way. The question it answers:

> Does MedFlow provide useful operational decision support when used with real hospital operational data and real
> hospital workflows?

It answers it **descriptively**: a before/after comparison shows what changed between two periods, not that MedFlow
caused the change. Results from demo hospitals are always labelled *Synthetic/demo result*; results from other hospitals
are labelled *Observed pilot result*.

## 2. Architecture

```text
V1 ledger ─┐  V2 forecasts + holdouts ─┐  V3 risk snapshots ─┐  V4 order log ─┐  V5 recommendations ─┐  V9 sync runs + reconciliation ─┐
           ▼                           ▼                     ▼                ▼                      ▼                                 ▼
      app/pilots/metrics.py  — one function per section, each calling the function that already defines the number
           │   V3 load_history (stock, usable, expiring, stockout days)   V2 forecast_metrics + load_panel (censored actuals)
           │   V3.4 confusion / prf (warnings)   V4 summarize (OTIF, delay, fill, cancellations, price)   V9 freshness
           ▼
   Period(baseline) ──► metrics ──┐
   Period(pilot)    ──► metrics ──┴─► comparison.py (descriptive, pp + relative, per-30-day normalisation, "Insufficient data")
                                          │
   people: issues · feedback · recommendation views · readiness confirmations · external factors
                                          ▼
                           report.py (JSON + Markdown; labelled; factual conclusion) → saved snapshots
```

Code: `backend/app/pilots/` (`metrics.py`, `comparison.py`, `service.py`, `readiness.py`, `report.py`), API
`backend/app/api/pilots.py`, schemas `backend/app/schemas/pilots.py`, pages `frontend/app/(app)/pilots/` (list,
`[id]` dashboard, `[id]/metrics`, `[id]/issues`, `[id]/readiness`, `[id]/report`), shared UI `frontend/components/pilots.tsx`,
and a small hook on the V5 Procurement page (records "viewed" and asks for feedback during an active pilot).

**Nothing in V1–V9 was changed** except additive wiring: two permissions, the router registration, the navigation entry,
the version numbers and the Procurement page hook. No forecasting, risk, supplier, optimisation, graph, assistant,
tenancy or integration logic was modified (see §15).

## 3. Pilot lifecycle

`planned → active → paused ↔ active → completed | cancelled` (terminal). One active pilot per hospital. A pilot belongs to
exactly **one hospital** (the creator's active hospital — the request body cannot name one) and records its organization,
owner, description, notes, participating departments and users (active members only), linked V9 data sources, baseline
dates, pilot start / planned end / actual end, external factors, and timestamps. Completed or cancelled pilots keep
their data; only notes/description can still be edited. Every change is audited (`pilot.*`).

## 4. Baseline methodology

* Administrators set both periods. The baseline must end **before** the pilot starts (API check + PostgreSQL CHECK
  constraint) — baseline and pilot data are never mixed.
* **Ledger metrics** (stock, stockouts, forecasts, warnings) use complete business days only: up to
  `min(period end, yesterday)`. **Activity metrics** (syncs, decisions, views, feedback, users) run up to now. A running
  pilot is flagged "partial" and its report says the results are provisional.
* Periods of different length: accumulating counts are compared **per 30 days** (and labelled so); positions (stock at
  period end) and rates are compared as they are.
* Percentages are compared as **percentage points** and **relative change**.
* A value is shown only above a minimum sample (`MIN` in `metrics.py`: 5 counted items, 14 forecast item-days, 20 warning
  item-days, 5 decided supplier orders, 3 decisions, 20 integration records, 7 ledger days). Below it the UI and report
  say **"Insufficient data"** and show n and the minimum.
* External factors (seasonal demand, department / supplier / data-source / inventory-policy / staffing change, other) can
  be recorded against either period and appear in the comparison and the report.

## 5. Metrics (definitions)

Every metric carries its formula (with the actual numbers where useful) and its source, visible on hover in the UI.

| Section | Metric | Definition (source) |
|---|---|---|
| Inventory | Inventory accuracy | items whose hospital count matched MedFlow (no reconciliation issue open during the period) ÷ items counted by the hospital system in the period (V9 inventory snapshots + reconciliation) |
| | Stock discrepancies, units in discrepancy | reconciliation issues open during the period; Σ \|count − ledger\| (V9) |
| | Usable / expired / expiring-14 stock, value | at the end of the last complete day (V3 `load_history`; expired = ledger balance − usable; value at today's unit cost) |
| Stockouts | Stockout item-days, events, affected items | V3 definition: balance reached 0, or no usable stock at the start of the day; an event = first day of an episode (V3 `load_history`) |
| | Estimated unmet demand | stockout days × the item's mean consumption on non-stockout days in the period (**estimate**, labelled) |
| | Stockout events with no order in the pipeline | events with no supplier order open on the first stockout day (V4 order log) |
| Forecasting | WAPE, MAE, RMSE, bias (live) | V2 `forecast_metrics` on stored forecasts of the served model (most recent served version per day) vs censored actuals (V2 `load_panel`) |
| | WAPE, bias (holdout backtest) | the same on the served models' stored out-of-sample backtest days in the period |
| | Coverage | item-days with a stored forecast ÷ active item-days |
| Stockout prediction | Precision, recall, TP / FP / FN | V3 snapshots (MEDIUM/HIGH = warning) per item-day vs a stockout within 14 days (V3.4 `confusion`, `prf`); snapshots whose 14-day window is not complete are excluded and counted in a note |
| | Events warned, median lead time | stockout events V3 was monitoring that had a warning 1–14 days before; median days from first warning to stockout |
| Suppliers | OTIF, on-time, average delay, fill rate, cancellation rate, price changes | V4 `summarize` over orders placed in the period |
| Procurement | generated, viewed, approved, modified, rejected, expired, rates, median hours to decision | V5 `procurement_recommendations` (status, `modified`, `decided_at`, reason, final lines) + V10 `recommendation_views`; expired = superseded or from an earlier day and never decided |
| | Emergency purchases | supplier orders not created from a recommendation, placed on a stockout day of the item or the day after (seed history excluded) |
| Adoption | active users, recommendation views, decisions, assistant questions, feedback, share rated useful | audit log, views, decisions, pilot feedback |
| Data quality | received, accepted and rejected on first pass, recovered on retry, score, reasons, reconciliation, stale sources | V9 `sync_runs` of the pilot's sources (dry runs excluded; retries counted separately) |

**Data-quality score** = records accepted on the first attempt ÷ records received — a plain acceptance rate with the
two numbers shown; no weights and no AI score. Rejection reasons (duplicate, missing field, unknown item / supplier /
department, invalid date, negative quantity) are shown next to it.

## 6. Decision tracking

For each V5 recommendation generated in a period: generated → viewed (count, first view) → approved / modified / rejected
/ expired / pending, decider, decision time, reason and — for modifications — the original and the final lines, all read
from the unchanged V5 record. Views are recorded when a person opens a recommendation's explanation on the Procurement
page. Human approval remains the only way an order is recorded; V10 creates no orders.

## 7. Feedback

During an active or paused pilot, contributors rate a workflow, forecast, stockout risk, supplier view, integration or a
specific recommendation as *useful / somewhat useful / not useful*, with optional structured reasons (correct
recommendation, wrong quantity, wrong supplier, data problem, timing problem, missing context, other) and a comment.
Summaries by rating, reason and target feed adoption metrics and the report. It is deliberately not a review system.

## 8. Issue tracking

Categories: inventory mismatch, unknown item / supplier, incorrect mapping, duplicate record, missing data, incorrect
quantity, integration failure, prediction problem, recommendation problem, user workflow problem, other. Each issue
records pilot, hospital, category, severity, title, description, source (manual, sync run, reconciliation, feedback) with
an ownership-checked reference (e.g. `sync_run:12`), impact, creator, assignee (an active member), status
(open / investigating / resolved / ignored), resolution (required to resolve or ignore), resolver and time. **Issues never
change inventory, orders or any operational data.**

## 9. Report

`GET /api/pilots/{id}/report` (JSON + Markdown download; saved as immutable snapshots). Sections: 1 overview, 2 data
quality with integration reliability per source, 3 inventory, 4 forecasting with the served models, 5 stockouts and
prediction performance, 6 suppliers, 7 procurement, 8 adoption, 9 issues, 10 limitations, 11 conclusion. The limitations
are generated from the data: sample size (which metrics were insufficient), duration and whether the pilot is still
running, data quality, missing systems or failing sources, departments not participating, recorded external factors,
that most metrics are hospital-wide, synthetic data, and the lack of causal evidence. The conclusion lists only measured
differences in neutral words ("X was A in the baseline and B in the pilot"); for demo hospitals it starts with **"No
real-world outcome claim can be made yet."** No sentence of the form "MedFlow reduced / saved / improved …" is generated
(tested).

## 10. Pilot sandbox

In a demo hospital, **Pilots → Pilot sandbox (synthetic)** creates "Sandbox pilot — DEMO / SYNTHETIC DATA": baseline =
the 60th to 31st day before today, a 60-day pilot that started 30 days ago, all active departments, the admin /
procurement / inventory / department-manager members, and every V9 source of the hospital (including the simulated
reference ERP). It uses the existing Sunrise / Lakeview synthetic data; no new synthetic data is generated and the seed is
unchanged. Workflow: create → dates → link V9 source → *Sync REST sources (V9)* → validate (rejections) → reconcile
(V9 reconciliation) → metrics → decisions and feedback → issues → report. The UI shows a violet "DEMO / SYNTHETIC DATA —
NOT REAL HOSPITAL DATA" banner on every pilot page of a demo hospital.

## 11. Real hospital readiness

`GET /api/pilots/{id}/readiness` — 20 items in five groups (Integration, Data, Operations, Pilot, Governance). Items with
data evidence are checked automatically (source connected, initial / incremental sync, no unknown-code rejections in the
latest runs, inventory compared with no open reconciliation issue, consumption synced, a decided recommendation, a
resolved reconciliation issue, dates, participants, audit rows); items that need a person (mapping verified, roles
verified, retention documented, access reviewed, **no patient data required**) are confirmed by an admin with a note.
It states what has been prepared — **not** that a real hospital completed it.

## 12. Privacy and data limits

Pilots use item codes, inventory, consumption, suppliers, purchase orders, deliveries, departments and procedure counts.
V10 adds no patient names, IDs, records, diagnoses or clinical notes, and no free-text field is meant for them (the
readiness checklist asks for explicit confirmation). MedFlow claims no HIPAA, GDPR, ISO 27001 or other certification; a
real deployment needs appropriate legal, security and privacy review.

## 13. Implementation summary

**Files created:** `backend/app/pilots/{__init__,metrics,comparison,service,readiness,report}.py`, `backend/app/api/pilots.py`,
`backend/app/schemas/pilots.py`, `backend/alembic/versions/20260924_b8c6b2c0cc67_v10_real_hospital_pilot_and_business_.py`,
`backend/tests/test_pilots.py`, `frontend/app/(app)/pilots/page.tsx`, `frontend/app/(app)/pilots/[id]/{page,metrics/page,issues/page,readiness/page,report/page}.tsx`,
`frontend/components/pilots.tsx`, `frontend/e2e/v9z-v10-pilots.spec.ts`, `docs/v10-pilot.md`.

**Files changed (additive):** `backend/app/models/__init__.py` (8 models), `backend/app/core/permissions.py` (+2
permissions), `backend/app/main.py` (router, version 10.0.0), `backend/tests/test_multi_hospital.py` (B-hospital pilot
data + 4 path parameters in the isolation sweep), `frontend/lib/types.ts`, `frontend/hooks/use-me.ts`,
`frontend/components/app-shell.tsx` (nav + footer), `frontend/app/(app)/procurement/page.tsx` (view + feedback hook),
`frontend/package.json` / `package-lock.json` (10.0.0), README, CLAUDE.md and `docs/{api,database,architecture,security,deployment,product-roadmap}.md`.

**Database:** migration `b8c6b2c0cc67` — 8 tables (`pilots`, `pilot_participants`, `pilot_data_sources`, `pilot_issues`,
`pilot_feedback`, `recommendation_views`, `pilot_readiness`, `pilot_report_snapshots`), CHECK constraints for the period
order and the participant kind, indexes, and 7 PostgreSQL tenant-guard trigger specs (30 triggers in total). Metric
values are not stored; there is no `pilot_metrics` table because every metric is derivable from existing tables.

**APIs:** 25 operations (`docs/api.md`, V10 section). **Permissions:** `pilots:manage` (admin), `pilots:contribute`
(admin, procurement, inventory, department manager); reading needs `read`.

**Frontend:** Pilots list (create, sandbox), dashboard (status & lifecycle, V9 sync, eight metric sections with formulas,
integration reliability, decision funnel, feedback), baseline vs pilot (comparison + external factors), issues,
readiness, report (download Markdown, snapshots).

## 14. Tests and results (exact)

| Check | Result |
|---|---|
| Backend, SQLite + FalkorDB | **228 passed, 1 skipped** (the PostgreSQL-trigger test) — 215 V1–V9 + 14 V10 |
| Backend, PostgreSQL 16 + FalkorDB | **229 passed, 0 skipped** |
| New V10 backend tests (`tests/test_pilots.py`) | 14 — creation/validation/lifecycle/permissions; hand-derived ledger → exact stockout days/events, emergency stockouts, usable stock and value, shortage estimate, OTIF / delay / fill; forecast WAPE / MAE / bias from stored forecasts (unserved model ignored) + holdout; warning precision/recall/TP/FP/FN, excluded incomplete windows, events warned and lead time; decision funnel and rates; V9 data → data-quality score, rejection reasons, inventory accuracy, discrepancies, source reliability; no sources; future periods / empty data → insufficient, never zero; comparison (raw vs per-30-days, pp, relative, insufficient, external factors); issues (validation, ownership-checked references, resolution required, no inventory change); feedback rules; readiness auto + manual; report (sections, labels, banned phrases, Markdown, snapshots); demo → synthetic + sandbox; hospital and organization isolation; assistant; period boundaries |
| V8 isolation sweep | now 88 (method, path) pairs × 3 roles, incl. the 22 new V10 routes, all 404/403/422 with no leak; list/search sweep covers `/api/pilots` and `/api/pilots/active` |
| Ruff · `npm run lint` · `npm run typecheck` · `npm run build` | clean · clean · clean · success |
| E2E (Playwright, freshly seeded + trained demo, PostgreSQL + FalkorDB, reference ERP simulator on) | **44 passed, 1 skipped** (screenshot spec) — 41 V1–V9 + 3 V10 (`v9z-v10-pilots.spec.ts`: create pilot with dates, department, user and the simulated ERP → start → V9 sync from the pilot → data-quality and metric tiles → feedback → baseline vs pilot + external factor → issue logged and resolved → readiness evidence + manual confirmation → report with synthetic banner, “No real-world outcome claim can be made yet.” and saved snapshot; recommendation view + feedback on the Procurement page; sandbox, viewer read-only, other hospital 404). An earlier full run failed only because a stale Next.js server from a previous build was still bound to port 3000; after stopping it and re-seeding, the full suite passed |
| Migration | empty DB: upgrade → check → downgrade -1 → upgrade → check, no drift, 30 guard triggers; copy of the development database after the E2E run (2 pilots, 1 issue, 2 feedback, 1 view, 29,670 movements, 18 sync runs, 57 recommendations): downgrade -1 drops only the pilot tables (movements, sync runs, recommendations unchanged) → upgrade → check, no drift, 30 triggers |
| Sunrise seed | unchanged (V10 adds nothing to the seed) |

## 15. Regression — V9 and earlier unchanged

`git diff v9.0.0 -- backend/app/{ml,risk,supplier_intel,procurement,graph,assistant,integrations,services,db}` is
**empty**, as is the diff of `seed.py`, `api/deps.py`, `api/auth.py`, `core/security.py` and every earlier migration.
The complete V1–V9 test suite passes unchanged apart from the two additive isolation-sweep edits listed above.

## 16. Sandbox / demo results (synthetic/demo results — not observed)

Sunrise (demo hospital), sandbox dates: baseline = 60th–31st day before 24 Sep 2026, pilot = the 30 days since (running,
29 complete ledger days), after the full E2E run (which synced the simulated reference ERP twice and decided three
recommendations). Selected rows of the comparison — **synthetic/demo results, not observed**:

| Metric | Baseline | Pilot | Note |
|---|---:|---:|---|
| Stockout item-days / events / items | 0 / 0 / 0 | 28 / 7 / 7 | the seed's scenarios put its stockouts and near-expiry batches in the last weeks of the 90-day history — the difference reflects the simulator's design, not MedFlow |
| Estimated unmet demand on stockout days | 0 | 419.5 units | estimate |
| OTIF / on-time | 93.0% / 93.0% | 88.1% / 88.4% | −4.9 pp / −4.7 pp (synthetic V4 order history) |
| Inventory accuracy | Insufficient data | 95.3% | no ERP counts existed in the baseline → no comparison |
| Data-quality score | Insufficient data | 97.7% | 3 unknown-material and 3 negative-quantity records: the simulator's deliberate invalid postings |
| Forecast WAPE (live) | Insufficient data | Insufficient data | forecasts are stored only for days after training (training ran today) |
| Forecast WAPE (holdout) | Insufficient data | 17.2% | the served V2B model's backtest days fall in the pilot period only |
| Warning precision / recall | Insufficient data | Insufficient data | V3 snapshots exist only from training day onwards |
| Approval / modification / rejection rate | Insufficient data | 33% / 33% / 33% (n = 3) | three E2E decisions; below-minimum rows are flagged in the report |

Every report of these pilots starts with "DEMO / SYNTHETIC DATA — NOT REAL HOSPITAL DATA" and its conclusion begins "No
real-world outcome claim can be made yet." (verified by tests and E2E).

## 17. Performance (indicative)

Development container (2 CPUs), Sunrise demo hospital (42 items, ~15,000 ledger movements, 90 days), medians of 3
requests, indicative only:

| Endpoint | Time |
|---|---|
| `GET /pilots/{id}/dashboard` (pilot-period metrics + readiness) | 1.8 s |
| `GET /pilots/{id}/comparison` (both periods) | 3.6 s |
| `GET /pilots/{id}/report` (both periods + Markdown) | 3.6 s |

Most of the time is the V3 ledger reconstruction (`load_history`) and the V2 panel, run once per period. Not optimised
(no caching) — acceptable for a pilot dashboard; larger hospitals were not measured.

## 18. Limitations and known issues

* **No real hospital pilot has taken place.** All V10 results so far are synthetic/demo results.
* Before/after comparison is descriptive; there is no control group, randomisation or statistical test, so no causal
  claim is possible. Short periods and small samples are common — watch the "Insufficient data" markers.
* Most metrics are **hospital-wide** (all items and departments); participants scope people and the report, not the
  ledger calculations.
* Live forecast accuracy needs forecasts stored *before* the days they predict: MedFlow stores forecasts only at training
  time, so a pilot measures live forecasts only for days after a training run (the demo retrains on start; earlier days
  have holdout backtests only). Likewise V3 snapshots exist only from when the risk engine ran, so warning metrics for
  past periods of the demo are "Insufficient data".
* Inventory accuracy needs counts from a V9 inventory feed; hospitals that only count in MedFlow (V1 counts record
  adjustments, not matches) cannot measure it.
* Unmet demand on stockout days is an estimate; inventory value uses today's unit cost.
* "Viewed" is recorded when a recommendation's explanation is opened on the Procurement page; views through other screens
  or the API are not counted.
* Metrics are computed on each request (a full report takes a few seconds on the demo hospital); no caching yet.
* Docker still not verified here (Docker Hub returns "Forbidden"); Neo4j not verified locally (see below); CI not run.

The graph implementation was tested using FalkorDB, an openCypher-compatible graph engine. Neo4j is configured as the
production graph store and is covered by CI configuration, but Neo4j execution could not be locally verified because the
required Neo4j image/download endpoints returned HTTP 403 in the development environment.

## 19. Next stage

A **real pilot** with one hospital using this release — with its written permission, a V9 connection or regular exports
tested against its own system, a baseline of real data, and a legal/privacy review — is the only step that can turn
these measurements into evidence. No further software version is proposed until that pilot has produced observed data.
