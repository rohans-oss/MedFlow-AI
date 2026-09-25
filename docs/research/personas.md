# User Personas (draft — replace with real interview composites)

These map directly to the Version 1 roles in the application.

## 1. Ramesh — Store / Inventory Manager (`inventory_manager`)

- 12 years in hospital stores, owns the central store and sub-stores.
- **Jobs:** receive deliveries (GRN), issue stock to departments, run cycle counts, track expiry.
- **Pains:** issues not recorded in time → system stock ≠ shelf stock; expiry discovered too late;
  blamed when OT runs short.
- **Needs from V1:** fast receive/issue entry, per-batch expiry, low-stock and expiry alerts,
  a movement history that proves what happened.

## 2. Priya — Procurement Manager (`procurement_manager`)

- Manages ~40 suppliers, raises POs, negotiates prices.
- **Jobs:** decide what/when/how much to order, pick supplier, chase late deliveries.
- **Pains:** reorders triggered by phone calls, not data; no record of which supplier is reliable;
  premium emergency purchases.
- **Needs from V1:** supplier catalogue with prices, lead times and MOQs; list of items at/below
  reorder level; consumption history per item.

## 3. Dr. Kavitha — Department Manager, Orthopaedics OT (`department_manager`)

- OT in-charge; cares about cases not getting cancelled.
- **Jobs:** raise indents, check that items for scheduled cases are available.
- **Pains:** finds out an item is missing on the day of surgery.
- **Needs from V1:** read visibility of stock for department items; alerts on items her OT uses.

## 4. Suresh — Hospital Administrator (`admin`)

- COO / admin head. Sets up users, departments, approves policies.
- **Needs from V1:** user and role management, audit trail, a one-screen supply health view.

## 5. Finance / Management viewer (`viewer`)

- Read-only: stock value, alert counts, trends.
