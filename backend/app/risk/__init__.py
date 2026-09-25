"""Version 3 — stockout risk intelligence.

V1 inventory (batches, ledger) + V2A/V2B demand forecast + supplier lead time + expiry
    → deterministic projection (V3.1)  → probability model (V3.2)  → risk level, date, shortage, reasons.

Modules:
    projection  pure FEFO stock projection against a daily demand forecast (V3.1)
    history     per-item daily history rebuilt from the ledger (usable stock, stockouts, kits, expiry)
    features    point-in-time risk features + labels, shared by ledger history and simulation
    simulate    synthetic replenishment simulator used to train the risk model (labelled synthetic)
    metrics     precision / recall / F1 / PR-AUC / Brier / calibration / lead-time-aware event recall
    model       XGBoost classifier + rule scorers
    pipeline    training, ledger backtest, model registry
    engine      scoring of current items → snapshots, reasons, alerts input
"""
