"""V9 — real hospital integrations & data exchange.

Hospital systems (ERP, inventory, procurement, spreadsheets, REST APIs, scheduled feeds) → connector (upload / api_push /
rest_pull) → mapping (source field → MedFlow field) → validation → idempotency (external_refs) → the EXISTING MedFlow
services (stock ledger, supplier orders, catalogue) → PostgreSQL → V2–V7 read it as they always have.

Nothing here changes a forecasting, risk, supplier-intelligence or procurement algorithm: V9 only brings better data in.
Every exchange is a `sync_runs` row (the audit trail); rejected records are kept (`sync_records`) and can be retried;
stock counts that disagree with the ledger become reconciliation issues that a person resolves.
"""
