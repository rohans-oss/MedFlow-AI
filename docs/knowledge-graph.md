# V6 — Operational supply knowledge graph

**Goal:** make the relationships MedFlow already knows — items, procedures, departments, suppliers, forecasts, risks,
orders and procurement decisions — explicit and queryable, so questions like *"why is suture 2-0 at risk?"* or *"what
happens if supplier CPS becomes unavailable?"* are answered by traversing relationships instead of hand-joining tables.

It is deliberately **not** a biomedical knowledge graph and **not** Graph ML. Prediction stays with XGBoost (V2/V3),
optimisation with OR-Tools (V5). Graph algorithms or Graph ML come later only if there is a real use case. No LLM.

```text
PostgreSQL (source of truth)
   │  app/graph/projection.py  — read-only projection per hospital
   ▼
Graph store: Neo4j 5 (docker compose)  ·  FalkorDB (openCypher engine used for development/tests here)
   │  app/graph/queries.py     — one set of openCypher queries for both
   ▼
Explain a risk (V6.3) · Impact analysis (V6.4) · Graph search (V6.5) · Sync & schema (V6.2)
```

## V6.1 — Graph model

| Node | From | Key properties |
|---|---|---|
| Hospital | `hospitals` | name, code |
| Department | `departments` | name, code |
| Category | `consumable_categories` | name |
| Item | active `consumables` + batches + V3 | sku, unit, reorder level, usable / expired stock, risk level |
| Procedure | `procedure_types` + schedule | scheduled next 14 days, completed last 90 (counts only) |
| Supplier | `suppliers` + V4 metrics | reliability score, grade, OTIF, on-time rate, lead-time median / p90 |
| Batch | `stock_batches` with quantity > 0 | lot, quantity, expiry, usable / expired |
| Forecast | served V2 model | forecast 7 / 14 / 30 days, model, source, item holdout WAPE |
| StockoutRisk | latest V3 snapshot | level, probability, stockout date, days left, 14-day shortage, reasons |
| SupplierOrder | V4 order log (365 days or open) | status, dates, outstanding, overdue days |
| ProcurementRecommendation | V5 (pending + decided 90 days) | status, quantity, purchase value, expected cost, P(stockout) with / without |

| Relationship | Evidence on the relationship |
|---|---|
| (Hospital)-[:HAS_DEPARTMENT / HAS_ITEM / HAS_SUPPLIER]→ | — |
| (Item)-[:IN_CATEGORY]→(Category) | — |
| (Department)-[:USES]→(Item) | units issued in 90 days (net of returns), share of the item's use, last used |
| (Department)-[:PERFORMS]→(Procedure) | scheduled next 14 days, completed last 90 |
| (Procedure)-[:USES_ITEM]→(Item) | kit quantity per procedure, units implied by the next 14 days |
| (Supplier)-[:SUPPLIES]→(Item) | price, MOQ, quoted lead time, preferred; V4 evidence for this item: on-time / OTIF, lead-time median / p90, **k of n past orders delivered within the item's current V3 window** |
| (Item)-[:HAS_BATCH]→(Batch)-[:FROM_SUPPLIER]→(Supplier) | — |
| (Item)-[:HAS_FORECAST]→(Forecast), (Item)-[:HAS_RISK]→(StockoutRisk) | — |
| (Supplier)-[:HAS_ORDER]→(SupplierOrder)-[:FOR_ITEM]→(Item) | — |
| (ProcurementRecommendation)-[:FOR_ITEM]→(Item), -[:USES_SUPPLIER]→(Supplier), -[:RECORDED_AS]→(SupplierOrder) | quantity, unit price |

Keys are `<hospital>:<Label>:<postgres id>`; every node and relationship carries `hospital_id`. Counts only — MedFlow
holds no patient data and none reaches the graph. Demo size: ~1,000 nodes, ~2,150 relationships.

## V6.2 — Synchronisation (PostgreSQL → graph, one way)

1. A cheap **fingerprint** of everything the projection reads (row counts and latest ids / `updated_at` of 15 tables, and
   the date) is compared with the last successful sync. Different → the graph is behind.
2. **Project** the hospital (~1.3 s on the demo) → **MERGE** nodes and relationships in batches of 500 with a new
   generation number → **delete** this hospital's nodes and relationships the generation didn't touch.
3. **Verify:** count every label and relationship pattern in the graph and compare with the projection; the run is
   SUCCESS only when they match. Every run (also failures) is a `graph_sync_runs` row.

Triggers: automatically on the first graph read after PostgreSQL changed, after `python -m app.ml.train`, the
**Sync now** button (`graph:sync`), and `python -m app.graph.cli sync|check`.

**If the graph store is down** only the Knowledge graph page is affected: graph endpoints answer 503 ("…inventory,
forecasting, stockout risk, supplier intelligence and procurement are unaffected"), a failed sync is recorded, and after a
failure the app waits 15 s before trying the store again so pages don't hang on timeouts. No V1–V5 module imports
`app.graph` (tested: inventory, dashboard, suppliers, V4 scorecards, V5 plans and recommendations all work with the store
down). Because the graph is derived, it can be deleted at any time and rebuilt with `python -m app.graph.cli sync`.

## V6.3 — "Why is this item at risk?" (explanation chain)

Six hops from the item, each rendered as a sentence built only from the returned numbers, plus a layered drawing of the
neighbourhood (suppliers → orders / recommendation → item, risk, forecast → procedures → departments). Demo, suture 2-0
(synthetic):

```text
Absorbable suture, polyglactin 2-0 — usable 80 foil (reorder level 152)
 → HAS_RISK      MEDIUM stockout risk 17 % within 14 days; runs out 2026-09-27 (4 days), 179 foil short over 14 days
 → HAS_FORECAST  163 / 294 / 616 foil over 7 / 14 / 30 days (V2B procedure-aware model, item holdout WAPE 40 %)
 → USES_ITEM     94 scheduled procedures in 14 days need 211 foil (72 % of the forecast):
                 Hernia repair 23 × 3 = 69 · Lap. cholecystectomy 28 × 2 = 56 · Caesarean 24 × 2 = 48 · Appendectomy 19 × 2 = 38
 → USES          General Surgery OT 67 % · Obstetrics & Gynaecology OT 28 % · Orthopaedics OT 5 % · Emergency 1 %
 → SUPPLIES      CPS ₹294.13: 14/15 past orders within 4 days · OPI ₹287.90: 0/96
 → FOR_ITEM      PO-00110 (CPS) 415 foil outstanding, 2 days overdue
 → FOR_ITEM      V5 recommendation (pending): 166 CPS + 132 OPI; P(stockout) 46 % with vs 100 % without
```

## V6.4 — Impact analysis

* **Supplier unavailable** — items it supplies, classified *critical* (no active alternative and already at risk), *high*
  (no alternative), *watch* (at risk, alternative exists), *low*; procedures that use those items (and which of them are
  sole-source); departments that use them; open orders that would not arrive; pending V5 recommendations that rely on
  it; example chains `Supplier → Item → Procedure → Department → Stockout risk`. Demo: CPS unavailable → 11 items lose a
  supplier, none sole-source, 4 already at medium/high risk, 2 open orders (489 units), 2 pending recommendations.
* **Item running short** — procedures using it (kit quantity, scheduled count, how many procedures current stock alone
  covers), departments depending on it directly or via procedures, suppliers. Demo: suture 2-0 → 4 procedure types need
  211 foil in 14 days against 80 usable (62 % of scheduled procedure demand uncovered by stock).

## V6.5 — Graph search (predefined questions)

| Question | Parameter |
|---|---|
| Which suppliers supply this item? | item |
| Which procedures use this item (and how heavily)? | item |
| Which departments depend on this item? | item |
| Which items are affected by this supplier? | supplier |
| Which high/medium-risk items have only one supplier? | — |
| Which scheduled procedures could be affected by an item shortage? | — |
| Which items have only one active supplier? | — |
| Which departments use the most high-risk items? | — |
| Which at-risk items are waiting on an overdue order? | — |

Each answer shows its Cypher. Free-text questions are V7's job: the AI assistant calls these queries (and the explain / impact endpoints) as
tools — see `docs/assistant.md`.

## Neo4j and FalkorDB — what was verified where

The graph implementation was tested using FalkorDB, an openCypher-compatible graph engine. Neo4j is configured as the production graph store and is covered by CI configuration, but Neo4j execution could not be locally verified because the required Neo4j image/download endpoints returned HTTP 403 in the development environment.

* **Production path:** Neo4j 5 Community in `docker-compose.yml` (password from `.env`), official `neo4j` Python driver
  (Bolt). Indexes on `key` and `hospital_id` per label are created on first sync.
* **What ran here:** all Cypher was executed on FalkorDB (run locally from the `falkordblite` wheel) by the backend tests
  (`TEST_GRAPH_URL=redis://…`), the Playwright suite and `python -m app.graph.cli check`. Queries use only the openCypher
  subset both engines implement.
* **Neo4j coverage:** CI (`.github/workflows/ci.yml`) starts a Neo4j service and runs the graph tests and
  `app.graph.cli check` against it; on your machine `docker compose up` then
  `docker compose exec backend python -m app.graph.cli check` does the same.

## V8 — several hospitals in one graph store

Unchanged design, now tested with several hospitals: sync, status, counts and verification are per hospital; syncing one
hospital never rewrites another's nodes; every query anchors on hospital-scoped keys; other hospitals' ids answer 404.

## Limitations

- Projection is a full per-hospital rebuild (~1–2 s on the demo); fine for one hospital, incremental sync later if needed.
- Staleness is detected per request (fingerprint), not streamed; the graph can lag PostgreSQL until the next graph read.
- Evidence on relationships is a snapshot (e.g. the delivery window follows the V3 stockout date at sync time).
- Procedure schedule and kit mappings in the demo are synthetic and not medically validated.
- No graph algorithms (centrality, community detection) and no Graph ML — by design for V6.
