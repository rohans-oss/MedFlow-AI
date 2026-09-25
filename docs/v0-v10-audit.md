# MedFlow AI — V0–V10 system audit

Audit date: **2026-09-24** · branch `audit/v0-v10`, based on `master` = `v10.0.0` (`31a9a24`) · no new version, nothing pushed.

This audit **verifies** V0–V10. It is not a feature release. It fixes only genuine bugs, and each fix has a regression
test. Every result below comes from something that was **run** in this environment. Anything that could not be run is
marked **NOT VERIFIABLE**, and anything that works but with a stated gap is marked **PASS WITH LIMITATION**.

Statuses: **PASS** (tested; behaved as expected) · **PASS WITH LIMITATION** (tested; works, with a stated gap) ·
**FAIL** (tested; wrong) · **NOT VERIFIABLE** (could not be tested here).

---

## 1. Scorecard

| Area | Result | Key evidence |
|---|---|---|
| Environment & dependencies | PASS WITH LIMITATION | Python 3.11.15, Node 22.22.2, npm 10.9.7, PostgreSQL 16.13, FalkorDB on Redis 8.6.2; `pip check` and `npm ls` clean. The `neo4j` driver (pinned 6.3.1) was missing from the environment and was installed; it is imported lazily, so this was an environment gap, not a code bug. Docker images cannot be pulled (HTTP 403). |
| Migrations (empty DB, V7→V10, downgrade/upgrade on copies) | PASS | §3 |
| V0 research documents | PASS WITH LIMITATION | They are labelled "HYPOTHESES, NOT VALIDATED", but 0 interviews have been done (§4) |
| V1 inventory, ledger, FEFO, alerts, RBAC | PASS | Unit tests, E2E, and the live workflow (§5) |
| V2A consumption forecasting | PASS | Unit tests, E2E, stored holdout metrics (§9) |
| V2B procedure-aware forecasting | PASS | Unit tests, E2E, fold gating seen in the training log |
| V3 stockout risk | PASS WITH LIMITATION | Works; the served scorer falls back correctly. The docs' "XGBoost ranks best" only held on one seed date; wording corrected (bug D-1) |
| V4 supplier intelligence | PASS | Unit tests, E2E, live order → receipt → delivery |
| V5 procurement optimisation | PASS | Recommend-only; approval records orders; a reason is required to modify or reject; approving twice gives 409 |
| V6 knowledge graph | PASS WITH LIMITATION | FalkorDB only. Neo4j could not be run (see wording in §12) |
| V7 AI assistant | PASS WITH LIMITATION | Deterministic path verified live. No real LLM was run; the LLM path was tested only with a scripted provider and mocked HTTP |
| V8 multi-hospital isolation | PASS | Sweep of 3 × 88 id routes; live 404s; PostgreSQL triggers; no cross-hospital rows |
| V9 integrations | PASS WITH LIMITATION | Upload, push, pull, idempotency, reconciliation and the upload limits were verified. Tested only against the local reference ERP simulator; no real hospital system |
| V10 pilots | PASS WITH LIMITATION | Works end to end on synthetic data. No real pilot has been run, so no outcome can be measured |
| Complete V1 → V10 workflow | PASS | 60 live checks on one item, all passing (§5.2) after correcting 2 errors in the audit script |
| Frontend (every page, 7 roles, desktop + 390 px) | PASS after fix | Bug UI-1 (pages scrolled sideways at phone width) was fixed and has a regression test |
| API (170 operations) | PASS | §7 |
| Security | PASS after fix | Bug S-1 (the default JWT secret was accepted in production) was fixed and has a regression test |
| Data consistency | PASS | §5.2: across all 3 hospitals, Σ movements = Σ batches = last `balance_after` for every item, no negative batch, no cross-hospital movement or order; the API matches PostgreSQL for the traced item |
| ML (V2A, V2B, V3, V4, V5, V7 grounding) | PASS WITH LIMITATION | Metrics are honest and reproducible. They are all measured on synthetic data, and the V3 ranking is unstable between seed dates |
| Regression (SQLite, PostgreSQL, E2E, lint, typecheck, build) | PASS | §10 |
| Date-sensitive behaviour | PASS after fix | B-1 (leap-day 500 in the approval queue) and T-1 (calendar-dependent tests) fixed, with regression tests; §10.2 |
| Performance | PASS WITH LIMITATION | V10 comparison and report take about 3.3–3.5 s (§11) |
| Documentation | PASS after fix | Metric claims tied to one seed date have been dated (D-1) |
| Git / tags | PASS | Linear history, tags unmoved, no migration edited (§13) |
| CI | NOT VERIFIABLE | `.github/workflows/ci.yml` exists but was not executed; there is no CI runner and the code was not pushed |
| Real hospital, real LLM, Neo4j, Docker | NOT VERIFIABLE | Not available in this environment (§12) |

---

## 2. Bugs found

The audit followed the same steps for each bug: document it, reproduce it, find the root cause, apply the smallest fix,
add a regression test, and re-run.

### Critical
None found.

### High
None found.

### Medium

**S-1 — The development JWT secret was accepted in production.**
- **Reproduction:** `ENVIRONMENT=production` with no `JWT_SECRET`, or with the `.env.example` value `change-me`, started normally.
- **Why it matters:** anyone who reads the public default could sign valid access tokens. The same secret is also used to derive the V9 API-key pepper when `INTEGRATION_KEY_PEPPER` is empty.
- **Root cause:** `app/core/config.py` had a hard-coded default and no production check.
- **Fix:** a pydantic `model_validator` on `Settings`. With `ENVIRONMENT=production` it refuses the known placeholders, any value containing "change-me", and anything shorter than 32 characters. Development and test behaviour is unchanged.
- **Regression test:** `backend/tests/test_config_security.py` (5 tests). Against the old `config.py`: 4 failed, 1 passed. With the fix: 5 passed.

**D-1 — The V3 documentation stated a model ranking that depends on the seed date.**
- **What the docs said:** README and `docs/ml-pipeline.md` stated "XGBoost PR-AUC 0.51 vs 0.40", "XGBoost ranks risk best", and fixed V2 and V5 figures.
- **What was measured:** the demo ledger is generated relative to the day it is seeded. On the demo seeded 2026-09-24 there were 7 events (not 10), XGBoost PR-AUC was 0.236, the cover rule 0.261 and the V1 reorder rule 0.254. The selection rule therefore served the **cover rule**, which is correct code behaviour, but the docs no longer described it.
- **Root cause:** a figure measured on one day was presented as a general result.
- **Fix:** the figures are now dated, with the 2026-09-24 re-measurement added in README (V2B, V3 and V5 paragraphs) and `docs/ml-pipeline.md` (V3.4).
- **Code:** unchanged; the models were not retrained to get different numbers.
- **Regression check:** documentation-only. The behaviour is covered by the existing guard assertion in `tests/test_risk.py` ("served model follows the guard: ML unless its PR-AUC is below the cover rule's").

**B-1 — On 29 February the Procurement approval queue answered 500.**
- **Reproduction:** found by running the whole suite under `libfaketime` at 2028-02-29 (`tests/test_multi_hospital.py` → `GET /api/procurement/recommendations` → `ValueError: day is out of range for month`).
- **Root cause:** the pending-queue sort key used `r["as_of"].replace(year=9999)` for "no order-by date"; year 9999 is not a leap year. Any hospital with a PENDING "no order" recommendation would lose its approval queue for the whole day, once every four years.
- **Fix:** `date.max` (one line in `app/api/procurement.py`).
- **Regression test:** `tests/test_procurement.py::test_pending_queue_lists_a_no_order_recommendation_dated_29_february`. Before the fix: failed with the `ValueError`. After the fix: passes, with orders listed before "no order" as before.

**T-1 — Three tests depended on the calendar day and would have failed on some days (the first on 2026-09-29).**
- **What happened:** runs under `libfaketime` failed on some dates:
  - `test_risk.py::test_training_backtest_and_registry` failed on 2026-09-29, 2028-02-22 and 2028-02-29;
  - `test_ml_procedures.py::test_future_schedule_changes_v2b_forecast_and_falls_back_after_schedule` failed on 2028-02-29 and 2028-03-01;
  - `test_procurement.py::test_plan_does_not_simply_pick_the_cheapest_and_explains_with_numbers` failed on 2028-02-22, 2028-02-29 and 2028-03-01.
- **Product impact:** none. CI would have gone red on those days.
- **Root cause, first two tests:** their synthetic fixtures follow calendar weekdays (fixed seeds), but they did not use the project's `frozen_day` fixture.
- **Root cause, third test:** it already used `frozen_day`, but `frozen_day` pinned only `business_today`. Far from the calibrated day, a batch was stamped `received_at` = real now, long after the pinned "today", so its shelf life came out negative and every scenario could not order.
- **Fix, test-only:** the two tests use `frozen_day`, and `frozen_day` now shifts `utcnow` by the same number of days. **No assertion was changed, removed or loosened.**
- **Regression check:** the three tests pass on 7 faked dates (2026-09-24, 2026-09-29, 2026-12-31 20:30 UTC, 2027-06-15, 2028-02-22, 2028-02-29, 2028-03-01), and the whole suite passes on three faked dates (§10.2).

### Low

**UI-1 — At phone width (390 px) several pages scrolled sideways.**
- **Measured overflow:** dashboard 47 px, item detail 113 px, supplier detail 115 px.
- **Root cause:** a `Card` placed in a single-column CSS grid keeps `min-width: auto`, so a wide table inside it forced the card, and so the page, wider than the screen instead of scrolling inside its own `overflow-x-auto` wrapper.
- **Fix:** one class, `min-w-0`, on the shared `Card` primitive (`frontend/components/ui/primitives.tsx`).
- **Regression test:** `frontend/e2e/zz-audit-mobile-layout.spec.ts` checks dashboard, item, supplier, procurement and pilots at 390 px. It failed on the old build ("/dashboard overflows by 47px") and passes on the fixed build. A re-run of the mobile audit found 0 overflowing pages, and the full E2E suite passes.

### Observations (not treated as bugs)

- **LIKE wildcards:** `%` and `_` in search boxes act as wildcards (for example, a search for `%` lists all items). Values are bound parameters, and SQL-injection payloads matched 0 rows. No security impact.
- **Pages opened by URL without permission:** a platform admin, or a viewer, opening an operational page they cannot use gets an empty state, and the browser console logs "403". The navigation already hides these links, and the API correctly refuses.
- **Slow V10 pages:** the V10 comparison and report take about 3.3–3.5 s because they recompute the metrics on demand. This is by design, since V10 stores no metric table (§11).

---

## 3. Migration audit

| Check | Method | Result | Evidence |
|---|---|---|---|
| Empty DB → head | `alembic upgrade head` on the new DB `medflow_mig`, then `alembic check` | PASS | 11 revisions, no drift; 46 tables, 132 FKs, 161 indexes, 30 tenant triggers, 4 `ck_` constraints. Every `hospital_id` column has an FK and an index. The tables without tenant triggers are exactly those with no cross-hospital references |
| Seed on the migrated DB | `python -m app.seed` into `medflow_mig` | PASS | 3 hospitals, 20 users, 113 items, 29 660 movements; Σ movement qty = Σ batch qty = 108 215 |
| V7 → V10 with real V7 data | `v7.0.0` worktree: V7 seed and train into `medflow_v7`, then V10 `alembic upgrade head` (3 revisions) | PASS | 18 counts identical before and after (hospitals 1, users 5, items 42, batches 156, movements 14 636, Σqty 53 572 = Σbatch 53 572, alerts 26, model_versions 4, forecasts 2 520, schedules 870, predictions 42, orders 980, deliveries 989, recommendations 15). The upgrade added 5 memberships, a default organization and 30 triggers |
| V10 code on the migrated V7 data | TestClient against `medflow_v7` | PASS | Login, dashboard, inventory, forecasts, risk, recommendations, pilots, audit and graph status all return 200; an issue gives 201; the assistant answers from the V7-era model |
| Downgrade on a copy | copy of the upgraded V7 DB → downgrade to `181a85264e64` → upgrade to head | PASS | Data identical to the V7 counts after the downgrade and after the re-upgrade; `alembic check` clean |
| V10 demo DB, downgrade on a copy | copy `medflow_devcopy` → downgrade to `623279e318d6` (V8) → head | PASS | V9 and V10 tables dropped as expected; movements (29 670), hospitals and memberships kept; re-upgrade clean |
| Original demo DB untouched | no downgrade was ever run on `medflow` | PASS | Only copies were downgraded |
| Migration files unchanged since their release | `git diff --diff-filter=MD` between consecutive tags on `backend/alembic/versions` | PASS | 0 modified or deleted in every step; exactly 1 added per version |

## 4. V0 research documents

| Item | Result | Evidence |
|---|---|---|
| Documents exist (`docs/research/`: problem statement, personas, pain points, workflow map, interview guide, MVP spec) | PASS | 7 files |
| Assumptions are labelled as assumptions | PASS | README: "Status: HYPOTHESES, NOT VALIDATED"; each file says "(draft)", "(hypothesis)" or "0 interviews"; there is a kill criterion ("Interviewees say stockouts are rare…") |
| No unsupported statistics | PASS | No percentages or market figures apart from one rule threshold in `mvp-spec.md` |
| Real-world validation | PASS WITH LIMITATION | The interview checklist is still unticked: 0 of 5+ staff interviews, 0 of 2+ supplier interviews. Nothing in V0 is validated |

## 5. Functional audit per version

### 5.1 Version checks

| Version | Feature | Verification method | Expected | Actual | Result | Evidence | Issues found | Fix | Regression |
|---|---|---|---|---|---|---|---|---|---|
| V1 | Auth, 5 roles, RBAC | pytest; E2E `v1-*`; live viewer write | Viewer read-only | Viewer issue → 403, approve → 403; all 170 operations without a session → 401 (except public login) | PASS | `test_auth`, `test_rbac`; E2E 3 V1 specs; §7 | — | — | green |
| V1 | Receive, FEFO issue, append-only ledger | Live workflow + DB | FEFO from the earliest-expiring batch; `balance_after` = on-hand | Issue of 5 came from batch 1993 (earliest expiry); Σ movements = Σ batches = `balance_after` = 115 | PASS | §5.2 | — | — | green |
| V1 | Over-issue refused | Live | 4xx, ledger unchanged | 422, unchanged | PASS | §5.2 | — | — | — |
| V1 | Alerts, audit log | pytest; E2E; DB | Stock changes audited | 6 audit rows for the audit's stock actions | PASS | `test_alerts_dashboard`, E2E | — | — | green |
| V2A | Baselines vs XGBoost, holdout, registry | pytest `test_ml`; stored metrics | Lowest-WAPE model served; no leakage; deterministic | Sunrise: XGB 18.6 % WAPE vs MA-7 23.9 % vs historical average 23.3 % | PASS | §9 | — | — | green |
| V2B | Procedure-aware candidate with fold gating | pytest `test_ml_procedures`; training log | Served only if better on the holdout and every fold | Sunrise 0.186→0.172, 0.210→0.191, 0.248→0.239 → V2B served. Harbor: V2A served (V2B not better on all folds) | PASS | `/tmp` training log; `model_versions` | — | — | green |
| V2 | Reads never train | Live | No new model version | Model count unchanged after reads | PASS | §5.2 | — | — | — |
| V3 | Projection, risk model, lead-time floor, alerts | pytest `test_risk`; live | API = newest snapshot; refreshed after each movement | Snapshot written at the time of the movement; API level LOW = DB | PASS | §5.2 | — | — | green |
| V3 | Backtest honesty and scorer selection | Stored metrics | ML not served below the cover rule | XGB PR-AUC 0.236 < cover rule 0.261 → cover rule served | PASS WITH LIMITATION | §9 | D-1 (docs) | docs dated | docs only |
| V4 | Order log, receive against order, OTIF, delay model | pytest; E2E `v4-*`; live | Delivery linked to RECEIPT; order RECEIVED | Order 12531 RECEIVED 50; 1 delivery linked to movement 148345 | PASS | §5.2 | — | — | green |
| V5 | Recommend-only; approve / modify / reject | pytest; E2E `v5-*`; live | Plan, what-if and generate create no orders; approve records orders; reason required | 0 orders from plan, what-if or generate. Approving a pending recommendation recorded 2 OPEN orders (580 + 385) matching its lines. Modify without a reason → 422, reject without a reason → 422, second approve → 409 | PASS | §5.2 | — | — | green |
| V6 | Projection, sync, verification, explain, impact | pytest with FalkorDB; live | Re-sync on fingerprint change; verified | Sync run 24 verified; explain/impact 200 | PASS WITH LIMITATION | §12 (Neo4j) | — | — | green |
| V6 | Degrades when the store is down | Live, graph URL pointing at a closed port | Graph 503; everything else works | Explain 503 "…unavailable"; dashboard, risk, plan and assistant 200 | PASS | this audit | — | — | — |
| V7 | Read-only, grounded, private | pytest `test_assistant`; live | Write requests refused with no side effects; other users get 404 | "I can't approve, order, reject or change anything…"; orders, movements and recommendations unchanged; another hospital's user → 404; answer repeats V3's level and `cover_rule_v1` | PASS WITH LIMITATION | §5.2 | no real LLM | — | green |
| V8 | Tenant isolation | Sweep test (3 × 88 routes); PostgreSQL trigger test; live | Other hospital → 404 | 7 Sunrise ids → 404 for the Lakeview admin; issue → 404; org routes only for org admins (CareNet 200, Sunrise admin 404); platform admin → 403 on hospital data | PASS | `test_multi_hospital` | — | — | green |
| V9 | Upload, mapping, validation, idempotency | pytest `test_integrations`; E2E `v9-*`; live | Applied via the ledger; re-send unchanged; changed re-send rejected; dry run writes nothing | −3 issued (movement ISSUE −3 by the service user); re-upload → unchanged 1; changed → `changed_transaction`; dry run → ledger unchanged; older timestamp → `out_of_order` | PASS | §5.2 | — | — | green |
| V9 | Upload limits | Live | Bad type 4xx; > 10 MB → 413 | `../../etc/passwd.exe` → 422 "Upload a .csv or .xlsx file"; 11 MB → 413 | PASS | §5.2 | — | — | — |
| V9 | API push, REST pull, reconciliation | pytest; E2E (reference ERP) | Key hashed; rate limit; checkpoints | As expected | PASS WITH LIMITATION | simulator only | — | — | green |
| V10 | Pilot lifecycle, metrics, comparison, report | pytest `test_pilots`; E2E `v9z-v10-pilots`; live | Overlapping periods refused; synthetic labels; no outcome claims | Overlap → 422; `synthetic`; banner "DEMO / SYNTHETIC DATA — NOT REAL HOSPITAL DATA"; "No real-world outcome claim can be made yet."; no savings or improvement words; other hospital → 404 | PASS WITH LIMITATION | §5.2 | no real pilot | — | green |

### 5.2 Complete V1 → V10 workflow

The audit script `workflow.py` (kept in the audit scratchpad, not in the repo) follows **one item** (`SUT-STP-35`,
Skin stapler 35W, Sunrise) through every version on the running stack. The stack was PostgreSQL + FalkorDB, API with
the reference ERP simulator on, and the demo freshly seeded and trained. Each step is checked against PostgreSQL:

- **V1:** receive 120, then a FEFO issue of 5.
- **V2:** served forecast.
- **V3:** risk snapshot refreshed.
- **V4:** order 50, then receive against it.
- **V5:** plan and what-if, then generate, reject without a reason, viewer approve, approve, approve again.
- **V6:** graph re-sync and explain.
- **V7:** ask about the item; ask it to order; another hospital's user tries the same conversation.
- **V8:** the Lakeview admin tries 7 Sunrise ids and an issue.
- **V9:** CSV consumption upload, then re-upload, changed re-upload, dry run, bad file and 11 MB file.
- **V10:** pilot, overlap refusal, metrics, report, comparison, another hospital's access.
- **Global consistency checks** across all hospitals: Σ movements = Σ batches for every item; no negative batch; last `balance_after` = on-hand; no cross-hospital movement or supplier order.

**Result: 60 checks.** The first run had 56 passing. The 4 failures were errors in the audit script, not in MedFlow:
- **Wrong path.** The script called `/api/movements`; the route is `/api/inventory/movements`.
- **Wrong timestamp.** The script sent UTC wall-clock time without an offset, and MedFlow correctly reads naive times as hospital-local (Asia/Kolkata). The consumption therefore landed 5½ hours before the receipt just recorded, and MedFlow correctly rejected it as `out_of_order` ("the stock ledger is append-only").

After fixing the script and re-running those steps, **all 60 passed**.

## 6. Frontend audit

`browser-audit.mjs` (scratchpad) logs in as each role, opens every page, waits for network idle, and records
console errors, uncaught page errors, API 5xx responses, error text and horizontal overflow.

| Check | Result | Evidence |
|---|---|---|
| Desktop 1440 × 900: 25 routes × 7 roles | PASS | Roles: admin, procurement, inventory, department manager, viewer, organization admin, platform admin. Routes: 19 top-level URLs (18 pages + the recommendations tab) + item + supplier + 5 pilot tabs. 175 visits; 0 page errors, 0 API 5xx, 0 error screens, 0 redirects, 0 overflow. The 30 console "403" entries were all direct-URL visits by roles without access (28–29 by the platform admin, who has no hospital membership, plus 1 each by the viewer and the department manager on `/integrations`). The audit was re-run on the final build: 175 visits, 0 errors, 0 overflow; the navigation hides these links |
| Mobile 390 × 844 (admin, viewer) | PASS after fix | Before: 6 overflowing visits (UI-1). After: 52 visits, 0 overflow, 0 errors |
| Lint / typecheck / production build | PASS | `npm run lint`, `npm run typecheck` clean; `npm run build` exit 0 |
| Wording on screen | PASS | Synthetic-data banners, the integration wording and the V10 "no outcome claim" text appear where required (E2E asserts them) |

## 7. API audit

`api_audit.py` (scratchpad) reads all **170 operations** from the OpenAPI document.

| Check | Result | Evidence |
|---|---|---|
| No session → 401 for every operation | PASS | All non-public operations return 401. `/api/auth/token` is the public Swagger login. `/api/ingest/v1/{entity}` uses an API key: it returns 422 on an empty body and 401 "Invalid or revoked API key" for a valid body without a key |
| Admin: every GET with real ids | PASS | 87 GETs: 79 × 200 and 0 × 5xx. The rest were expected: org routes 404 for a hospital admin (200 for the CareNet org admin), platform audit 403 for a hospital admin (200 for the platform admin), `/inventory/batches` 422 without its required parameter (200 with it) |
| Viewer: every write operation | PASS | 0 × 5xx. Only 3 succeed, all by design: what-if (documented `read`, stores nothing), switch to own hospital, record a recommendation view |
| Response times | see §11 | |

## 8. Security audit

| Check | Result | Evidence |
|---|---|---|
| Secrets in tracked files or history | PASS | No key, token or private-key patterns in files or history; no `.env` or `.pem` ever committed. CI uses `ci-only-…` placeholders |
| Default JWT secret in production | FAIL → fixed | S-1 |
| Password hashes / key hashes in responses | PASS | No hash-like strings in `/users`, `/auth/me`, members, audit logs, sources or credentials. Credentials expose only `key_prefix`, label and dates. Stored key hashes are 64-hex HMACs. 0 audit rows contain passwords or raw keys |
| SQL injection | PASS | No SQL built from input (the Cypher f-strings use fixed schema labels only). Live payloads (`' OR 1=1 --`, `"; DROP TABLE …`) in search and filter fields → 200 with 0 matches; the tables are intact. Login injection → 422 |
| Command execution / unsafe deserialisation | PASS | No `subprocess`, `os.system`, `eval`, `exec`, `pickle` or `yaml.load` in `app/` |
| Upload: path traversal, type, size | PASS | The filename is truncated to 255 characters and never used as a path. Only .csv, .txt and .xlsx are accepted (`../../etc/passwd.exe` → 422). More than 10 MB → 413 (reads limit + 1 bytes) |
| SSRF (REST pull) | PASS | Host allowlist; unit tests |
| Rate limits | PASS | Login, assistant and ingest limits are covered by `test_login_rate_limited`, `test_rate_limit` and `test_ingest_rate_limit_and_request_size` |
| Compliance | — | No HIPAA, GDPR or ISO claim is made. The docs state that it has not been assessed |

## 9. ML audit

All figures are from the **synthetic** demo seeded on 2026-09-24. Models were trained only by the normal seed and train
process, never retrained to get different numbers.

| Model | Metric | Value | Comparison | Result |
|---|---|---|---|---|
| V2A XGBoost (Sunrise) | WAPE / MAE / RMSE / bias, 14-day holdout 09-10…09-23 | 18.6 % / 11.42 / 21.37 / +2.2 % | MA-7 23.9 %, historical average 23.3 % | PASS |
| V2B XGBoost (Sunrise, served) | WAPE / MAE / RMSE / bias | 17.2 % / 10.57 / 21.80 / +1.2 % | Better than V2A on the holdout and all folds (0.186→0.172, 0.210→0.191, 0.248→0.239). RMSE is slightly *higher* (21.80 vs 21.37); selection is by WAPE, as documented | PASS |
| V2 (Lakeview) | WAPE | V2B 11.1 % served (V2A 12.2 %) | | PASS |
| V2 (Harbor) | WAPE | V2A 18.7 % served (V2B 18.3 % on the holdout but not better on every fold → correctly not served) | | PASS |
| V2 reproducibility | Same code, same day | **The V7 code and the V10 code produce identical V2 and V3 metrics on the same seed date** (medflow_v7 vs medflow) | The Sunrise profile is unchanged by V8–V10 | PASS |
| V3 risk (Sunrise backtest, 2 086 item-days, 98 positive, 7 events) | PR-AUC / precision / recall / Brier | XGB 0.236 / 0.19 / 0.54 / 0.042; **cover rule (served)** 0.261 / 0.12 / 0.93 / 0.046; V1 reorder 0.254 / 0.14 / 0.51 | The ML model is correctly not served below the cover rule; lead-time-aware event recall 7/7 | PASS WITH LIMITATION (7 events; the ranking is unstable between seed dates, D-1) |
| V4 lead time / late delivery (727 test orders, 1-year window, 90-day warm-up) | MAE, within 1 day; Brier, ROC-AUC | Lead time: MAE 0.35 d, 94 % within 1 day. Late rate 7.3 %: supplier-rate Brier 0.0666 (ROC 0.64) vs hospital-rate 0.0679 (ROC 0.46) | Only outcomes known on the order date are used (unit test) | PASS WITH LIMITATION (small gain) |
| V5 arrival probability (1 500 predictions) | Brier; calibration | 0.0837 vs 0.0873 for "the quote is certain"; mean predicted 0.86 vs observed 0.913 (conservative) | | PASS |
| V7 grounding | Grounding check; deterministic answers | Grounded answers repeat tool numbers (`cover_rule_v1`, level, probability). LLM grounding was tested with `ScriptedProvider` only | | PASS WITH LIMITATION (no real LLM) |

The leakage tests (`test_features_use_only_the_past`, `test_no_future_procedure_leakage_into_historical_features`,
`test_features_use_only_data_up_to_the_origin`, `test_backtest_uses_only_outcomes_known_on_the_order_date`) and the
determinism test (`test_xgboost_is_deterministic`) pass in every run in §10.

## 10. Regression

### 10.1 Suites

| Suite | Before the fixes (baseline) | Final run (with fixes) |
|---|---|---|
| Backend, SQLite + FalkorDB | 228 passed, 1 skipped (PostgreSQL-trigger test) | **234 passed, 1 skipped** (235 tests; the skip is the PostgreSQL-trigger test) |
| Backend, PostgreSQL 16 + FalkorDB | 234 passed, 0 skipped (the run collected the 5 new S-1 tests) | **235 passed, 0 skipped** |
| E2E (Playwright, fresh seed + train, PostgreSQL + FalkorDB, reference ERP on) | 44 passed, 1 skipped (screenshot spec) | **45 passed, 1 skipped** (44 + the new 390 px layout spec); retraining took 54.1 s |
| `ruff check app tests` | clean | clean |
| `npm run lint` / `npm run typecheck` / `npm run build` | clean / clean / exit 0 | clean / clean / exit 0 |
| `alembic upgrade head && alembic check` | no drift | no drift |

The first E2E attempt in this audit ran at the same time as the PostgreSQL suite on a 2-CPU machine. There, "retrain
creates a new model version" exceeded its 150 s wait. Run alone, training took 59.6 s and all 44 specs passed. This is
a resource limit of the environment, not a code fault.

### 10.2 Date-sensitive behaviour

The whole SQLite suite was run under `libfaketime` at two dates:
- **2026-12-31 20:30 UTC.** The business date (Asia/Kolkata) is already **2027-01-01**, so the UTC date and the business date fall in different years.
- **2028-02-29 06:00 UTC.** This is a leap day.

| Faked clock | Before the fixes | After B-1 + T-1 |
|---|---|---|
| 2026-09-29 06:00 UTC (a Tuesday) | fails `test_training_backtest_and_registry` (the 3 T-1 tests run alone) | **234 passed, 1 skipped** |
| 2026-12-31 20:30 UTC (business day 2027-01-01) | 233 passed, 1 skipped | **234 passed, 1 skipped** |
| 2028-02-29 06:00 UTC (leap day) | **4 failed**, 229 passed, 1 skipped (B-1 + the 3 T-1 tests) | **234 passed, 1 skipped** |
| 2027-06-15, 2028-02-22, 2028-03-01 (the 3 T-1 tests only) | 2028-02-22: 2 failed; 2028-03-01: 2 failed | 3 passed on each |

The skip is the PostgreSQL-trigger test, which does not run on SQLite. The graph-store tests ran against FalkorDB.

Tests that depend on the weekday pin the business day with `frozen_day`. The seeded demo is generated relative to the
seed date, so demo metrics change from day to day (D-1).

## 11. Performance

The single API process runs on 2 CPUs. Times are single requests via `api_audit.py` after warm-up; page times are from
the browser audit.

| Endpoint / page | Time |
|---|---|
| Most GETs (inventory, risk, forecasts per item, dashboard summary, graph status) | 10–100 ms |
| `/api/forecasts` (all items), `/procurement/evaluation`, `/supplier-intelligence/evaluation`, `/graph/schema` | 0.24–0.61 s |
| `/api/pilots/{id}/metrics`, `/baseline`, `/dashboard` | 1.8–1.9 s |
| `/api/pilots/{id}/comparison`, `/report` | 3.3–3.5 s (the metrics for both periods are recomputed through the V1–V9 functions on every read) |
| Browser: slowest pages (pilot metrics and report) | 4.2–4.5 s to network idle |
| Forecast retraining (`POST /forecasts/train`, from the UI) | 59.6 s |
| Full seed + train of 3 demo hospitals | about 7 min |

**Limitation:** V10 reads are slow because nothing is cached. This is acceptable for a pilot report, but it would need
caching before heavy use.

## 12. Environment limitations

| Item | Status |
|---|---|
| Docker | NOT VERIFIABLE. Docker Hub is blocked (`python:3.11-slim … Forbidden`), so `docker compose` could not be built |
| Neo4j | NOT VERIFIABLE. The graph implementation was tested using FalkorDB, an openCypher-compatible graph engine. Neo4j is configured as the production graph store and is covered by CI configuration, but Neo4j execution could not be locally verified because the required Neo4j image/download endpoints returned HTTP 403 in the development environment. |
| CI | NOT VERIFIABLE. The workflow file exists but was not executed; nothing was pushed |
| Real LLM | NOT VERIFIABLE. No model or keys; tested with a scripted provider and mocked HTTP |
| Real hospital system | NOT VERIFIABLE. MedFlow supports a validated integration framework and controlled data-import/API connectors. Production hospital-system integrations require access to the corresponding hospital/vendor systems and have not been claimed unless actually tested. |
| Real hospital pilot / real data | NOT VERIFIABLE. All data is synthetic; no real-world outcome is claimed |

## 13. Git and tag integrity

| Check | Result | Evidence |
|---|---|---|
| Tags point where they were created | PASS | All 11 are annotated. v1.0.0 9481d62 · v2.0.0 6285598 · v2.1.0 b8a0ab2 · v3.0.0 109d707 · v4.0.0 110676c · v5.0.0 c435ad0 · v6.0.0 a70ece6 · v7.0.0 bfa6833 · v8.0.0 fb01839 · v9.0.0 7d5bd27 · v10.0.0 31a9a24 |
| Linear history | PASS | Each tag is an ancestor of the next |
| Each version's diff is that version's work | PASS | For example, v9.0.0..v10.0.0: 37 files, +4 482 / −30, 1 migration added, 0 modified |
| Repository integrity | PASS | `git fsck` clean |
| No history rewrite, no new tag, no push | PASS | The audit commit is on `audit/v0-v10` |

## 14. Verdict questions

| # | Question | Answer |
|---|---|---|
| 1 | Does the system install and start from scratch? | Yes, without Docker. An empty DB migrates, seeds, trains and serves. Docker itself could not be tested (blocked downloads) |
| 2 | Are migrations safe, with no data loss? | Yes. V7 → V10 kept every count; downgrades and upgrades on copies were lossless for the tables that stay; no migration was edited |
| 3 | Is V0 validated? | No. It is clearly labelled as hypotheses, and there have been 0 interviews |
| 4 | Does V1 inventory keep a correct ledger? | Yes. FEFO, append-only, Σ movements = Σ batches = last `balance_after` for every item in every hospital |
| 5 | Are V2A and V2B forecasts honest? | Yes. They are compared with baselines on the same holdout, there are no-leakage tests, and V2B is served only when it wins every fold (Harbor correctly stays on V2A) |
| 6 | Does V3 stockout risk work? | Yes, with a limitation. Its fallback to the cover rule worked when XGBoost ranked lower; the ranking is unstable across seed dates, and the docs now say so |
| 7 | Does V4 supplier intelligence work? | Yes. It is evidence-based, uses no look-ahead, and its gain over the hospital rate is small |
| 8 | Is V5 recommend-only, with human approval? | Yes. Nothing creates orders except an approval by a permitted person; reasons are enforced; double approval → 409 |
| 9 | Does V6 work and degrade safely? | Yes, on FalkorDB. Neo4j is untested here |
| 10 | Is V7 read-only and grounded? | Yes, on the deterministic path. No real LLM was run |
| 11 | Is one hospital's data isolated from another's? | Yes. Router, ORM guard and PostgreSQL triggers; a sweep of 3 × 88 id routes; live 404s; 0 cross-hospital rows |
| 12 | Are V9 integrations idempotent and safe? | Yes, against the simulator. Re-sends are unchanged, changed and out-of-order records are rejected, dry runs write nothing, and upload limits hold |
| 13 | Is V10 honest? | Yes. Synthetic labels, no outcome claims, descriptive comparison. No real pilot has been run |
| 14 | Does the complete V1 → V10 workflow work? | Yes. 60 of 60 live checks passed, plus 44 E2E specs |
| 15 | Were security problems found? | One, Medium: the default JWT secret was accepted in production. Fixed and tested. No critical or high issues. Separately, a leap-day crash (B-1) and calendar-dependent tests (T-1) were found and fixed |
| 16 | Is data consistent across modules? | Yes. The API matches PostgreSQL for the traced item; ledger invariants hold everywhere; no cross-hospital rows |
| 17 | Are the docs accurate? | Mostly. The seed-date-dependent metric claims (D-1) are now dated; the test counts are updated |
| 18 | Is MedFlow ready for a real hospital? | Ready for a **controlled pilot** on real data, not for production claims. Still needed: a real pilot, Neo4j and Docker verification, a CI run, a real LLM if one is to be used, a real integration, and a production secret (now enforced) |

## 15. Files changed by the audit

- `backend/app/core/config.py`: S-1 fix
- `backend/tests/test_config_security.py`: S-1 regression test (new)
- `backend/app/api/procurement.py`: B-1 fix
- `backend/tests/test_procurement.py`: B-1 regression test
- `backend/tests/conftest.py`, `backend/tests/test_risk.py`, `backend/tests/test_ml_procedures.py`: T-1 (calendar pinning only; no assertion changed)
- `frontend/components/ui/primitives.tsx`: UI-1 fix (`min-w-0` on `Card`)
- `frontend/e2e/zz-audit-mobile-layout.spec.ts`: UI-1 regression test (new)
- `README.md`, `docs/ml-pipeline.md`: D-1 (dated metric claims)
- `CLAUDE.md`: test counts
- `docs/v0-v10-audit.md`: this report
