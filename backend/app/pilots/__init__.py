"""V10 — real hospital pilot & business validation.

A pilot measures the EXISTING system (V1–V9) in one hospital over a baseline period and a pilot period. Every metric
is computed on demand from data MedFlow already stores, through the functions that already define it:

    stock position, stockout days / events     app.risk.history.load_history        (V3 ledger reconstruction)
    forecast error                             app.ml.metrics.forecast_metrics      (V2 evaluation)
                                               + app.ml.data.load_panel (censored actuals)
    warning precision / recall                 app.risk.metrics.confusion / prf     (V3.4 evaluation)
    supplier OTIF, delay, fill, cancellations  app.supplier_intel.metrics.summarize (V4)
    recommendations and decisions              procurement_recommendations          (V5, unchanged)
    data quality, integration reliability      sync_runs / reconciliation_issues    (V9)

Nothing here trains a model, changes an algorithm or writes operational data. Results are descriptive: a baseline vs
pilot comparison does not establish causality, and results from demo hospitals are labelled synthetic.
"""
