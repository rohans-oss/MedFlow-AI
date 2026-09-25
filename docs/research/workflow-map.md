# Workflow Map (draft current-state vs. V1 target)

## Current state (hypothesis)

```text
Supplier ──delivery──> Central Store ──indent/issue──> Department (OT/ICU/Ward) ──> Consumed
   ▲                       │  GRN on paper/HIS           │ often recorded late
   │                       │                             │
   └──── PO (phone/email) ─┴── "shelf looks low" ◄───────┘ nurse calls store when short
```

1. **Receive:** Delivery arrives → store checks invoice vs. PO → GRN entry (sometimes days later).
2. **Store:** Items shelved; batch/expiry not always captured.
3. **Indent:** Department raises indent (paper/HIS) → store issues → issue entry.
4. **Consume:** Department uses items; returns and wastage rarely logged.
5. **Reorder:** Triggered by visual check or complaint → procurement calls supplier.
6. **Expiry:** Manual periodic check; write-off discovered at audit.

## V1 target workflow (what the software must support)

```text
Receive stock (batch, expiry, supplier, cost)  ──> stock_movements (RECEIPT)
Issue to department (FEFO batch picking)        ──> stock_movements (ISSUE)
Return / wastage / adjustment after count       ──> stock_movements (RETURN / WASTAGE / ADJUSTMENT)
                     │
                     ▼
Alert engine (after every movement + on demand)
  - OUT_OF_STOCK / LOW_STOCK (on hand ≤ reorder level)
  - EXPIRING_SOON (≤ 60 days) / EXPIRED batches
                     │
                     ▼
Dashboard: supply health, open alerts, recent movements, consumption by department
Audit log: who did what, when
```

## Questions to confirm in interviews

- Is stock held centrally, per department sub-store, or both?
- Who records issues and when? Is FEFO enforced?
- What triggers a reorder today, and who approves?
- Is batch/expiry captured at GRN?
