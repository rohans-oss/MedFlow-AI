"""V5 — procurement intelligence & optimisation.

    V1 stock + expiry + MOQ ─┐
    V2 served forecast ──────┤
    V3 risk snapshot ────────┼─► V5.1 replenishment (safety stock, reorder date, required / MOQ / expiry-capped qty)
    V4 order history ────────┤   V5.2 feasibility (arrival distributions per supplier; in-transit orders weighted
    open supplier orders ────┘         by their historical arrival probability — never assumed certain)
                                 V5.3 scenarios → Monte Carlo evaluation → configurable expected-cost model → OR-Tools
                                 V5.4 recommendation → human approval → recorded supplier order (never sent anywhere)

The procurement package recommends; it never purchases. No LLM: every explanation is built from computed numbers.
"""
