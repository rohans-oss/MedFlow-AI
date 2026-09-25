"""Version 4 — supplier intelligence & reliability.

    supplier order log (orders, deliveries)  ─► metrics  (OTIF score, on-time, lead time, cancellations, fill, price)
                                              ─► predict  (lead-time & delay predictions + temporal backtest)
    V3 stockout risk (expected stockout date) ─► service  (item-level supplier comparison: can each supplier deliver
                                                           before the projected stockout, based on its own history?)
Information for procurement review only — nothing is ordered automatically (V5).
"""
