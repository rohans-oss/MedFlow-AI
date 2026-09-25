"""V5.3 — the expected-cost model. Every parameter is stored per hospital (`procurement_settings`) and editable by
procurement; nothing is weighted inside the code.

For a scenario (a set of order lines; "no order" is a scenario too), over the evaluation horizon H (the reference
supplier's expected lead time + review period — until an order placed at the next review could arrive):

    purchase        Σ quantity × unit price  +  fixed cost per order line
    stockout        E[units short within H] × stockout cost per unit
                    stockout cost per unit = item reference price × stockout multiplier
    holding         E[extra unit-days in stock] × reference price × annual holding rate ÷ 365
                    (within H from the simulation; after H until the stock is used, at the forecast rate)
    expiry / waste  E[extra units that expire before use] × unit price × (1 + disposal %)
    − carried fwd   (quantity − E[extra demand served within H]) × unit price
                    (stock not used within H serves later demand, so this order is not charged for it here —
                     unless it expires unused, in which case its value is charged under expiry / waste)

    total expected cost = purchase + stockout + holding + expiry/waste − carried forward

"Extra" = difference to the "no order" scenario on the SAME simulated paths, so the effect of an order does not
depend on which batch first-expiry-first-out happens to take units from.
The item reference price (catalogue median, else item unit cost) values stockouts and holding so a more expensive
supplier does not make a shortage look more costly.
"""

from dataclasses import dataclass
from statistics import NormalDist

DEFAULTS = {
    "service_level": 0.95,
    "review_period_days": 7,
    "horizon_days": 30,
    "stockout_cost_multiplier": 5.0,
    "holding_cost_rate": 0.25,
    "disposal_cost_pct": 0.10,
    "order_cost": 250.0,
    "allow_split": True,
    "budget_limit": None,
    "simulations": 500,
}

EXPLAIN = {
    "service_level": "Target probability of not running out during lead time + review period (sets the safety stock z).",
    "review_period_days": "How often procurement reviews and places orders; an order must cover lead time + this period.",
    "horizon_days": "Days over which each scenario is simulated (max 30 = length of the stored forecast).",
    "stockout_cost_multiplier": "Cost of one unit short, as a multiple of the item's reference price (emergency buying, "
                                "substitutions, postponed procedures).",
    "holding_cost_rate": "Annual cost of holding stock as a share of its value (capital, storage, handling).",
    "disposal_cost_pct": "Extra cost of disposing of an expired unit, as a share of its price (on top of the lost value).",
    "order_cost": "Fixed administrative/delivery cost per order line (₹); makes split orders pay for themselves.",
    "allow_split": "Allow scenarios that split the quantity between two suppliers.",
    "budget_limit": "Optional cap on the total purchase value of one recommendation run (₹); the optimiser respects it.",
    "simulations": "Monte Carlo paths per item (more = smoother estimates, slower).",
}


@dataclass
class CostModel:
    service_level: float = 0.95
    review_period_days: int = 7
    horizon_days: int = 30
    stockout_cost_multiplier: float = 5.0
    holding_cost_rate: float = 0.25
    disposal_cost_pct: float = 0.10
    order_cost: float = 250.0
    allow_split: bool = True
    budget_limit: float | None = None
    simulations: int = 500

    @property
    def z(self) -> float:
        return NormalDist().inv_cdf(min(max(self.service_level, 0.5), 0.9999))

    @classmethod
    def from_settings(cls, s) -> "CostModel":
        if s is None:
            return cls()
        return cls(**{k: (float(getattr(s, k)) if isinstance(DEFAULTS[k], float) and getattr(s, k) is not None
                          else getattr(s, k)) for k in DEFAULTS})

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in DEFAULTS}
