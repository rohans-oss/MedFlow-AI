# MVP Specification — Version 1 "Working Inventory MVP"

**Success condition:** a user can manage a realistic hospital inventory from the web application.
**Out of scope for V1:** forecasting, ML, stockout probability, procurement optimisation, Neo4j, LLM,
procedures module, purchase orders, multi-tenancy UI. (Schema keeps `hospital_id` so V8 is not a rewrite.)

## Features

| Area | V1 capability |
|---|---|
| Authentication | Email + password login, bcrypt hashing, JWT access (15 min) + refresh (7 days) in HTTP-only cookies, logout, `/me` |
| Roles | admin, procurement_manager, inventory_manager, department_manager, viewer (matrix below) |
| Hospital | View / edit hospital profile and inventory policy (expiry warning window) |
| Departments | CRUD; departments are the consumers stock is issued to |
| Consumables | Catalogue CRUD: SKU, name, category, unit, unit cost, reorder level, max level |
| Categories | CRUD |
| Inventory | Stock per item computed from batches: usable, expired, status, value, days since last movement |
| Batches | Lot number, expiry date, quantity, unit cost, supplier |
| Stock movements | RECEIPT, ISSUE (FEFO), RETURN, WASTAGE, ADJUSTMENT (count) — immutable ledger with balance after |
| Suppliers | CRUD + product catalogue (price, lead time, MOQ, preferred flag) |
| Dashboard | Supply health counts, stock value, open alerts, 30-day consumption trend, consumption by department, recent movements |
| Alerts | Rule engine: OUT_OF_STOCK, LOW_STOCK, EXPIRED, EXPIRING_SOON; dedup; auto-resolve; acknowledge/resolve |
| Audit | Every write is logged (actor, action, entity, details); admin can browse |

## Stock rules

- **Usable stock** = Σ quantity of batches that are not expired. Expired quantity is shown separately.
- **Status:** `OUT_OF_STOCK` (usable = 0) · `LOW` (usable ≤ reorder level) · `OVERSTOCK` (usable > max level, if set) · `OK`.
- **ISSUE** picks from non-expired batches in first-expiry-first-out order; can span batches; rejected if insufficient usable stock.
- **ADJUSTMENT** sets a batch to a counted quantity; reason required.
- **WASTAGE** removes quantity from a named batch; reason required (expired, damaged, contaminated…).
- Movements are append-only. Corrections are new movements, never edits.
- Quantities are integers in the item's base unit.

## Alert rules

| Type | Condition | Severity |
|---|---|---|
| OUT_OF_STOCK | usable = 0 and item active | CRITICAL |
| LOW_STOCK | 0 < usable ≤ reorder level | HIGH if ≤ 50 % of reorder level, else MEDIUM |
| EXPIRED | batch qty > 0 and expiry < today | HIGH |
| EXPIRING_SOON | batch qty > 0 and expiry ≤ today + warning window (default 60 d) | MEDIUM (HIGH if ≤ 14 d) |

Alerts are evaluated after each movement for the affected item and on demand for the whole hospital.
An open/acknowledged alert whose condition no longer holds is auto-resolved.

## Permission matrix

| Action | admin | procurement | inventory | department | viewer |
|---|:-:|:-:|:-:|:-:|:-:|
| Read everything except audit/users | ✓ | ✓ | ✓ | ✓ | ✓ |
| Manage users, hospital settings, departments | ✓ | | | | |
| Read audit log | ✓ | | | | |
| Manage categories & consumables | ✓ | ✓ | ✓ | | |
| Manage suppliers & supplier products | ✓ | ✓ | | | |
| Receive / wastage / adjust stock | ✓ | | ✓ | | |
| Issue / return stock | ✓ | | ✓ | own dept only | |
| Acknowledge / resolve alerts, run evaluation | ✓ | ✓ | ✓ | | |

## Demo data

A fictional 180-bed hospital ("Sunrise Multispecialty Hospital", Bengaluru) with 7 departments,
~40 consumables, 6 fictional suppliers and 90 days of generated movement history. **All demo data is
synthetic and labelled as such in the UI.**
