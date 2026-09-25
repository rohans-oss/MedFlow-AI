"""V4 — supplier performance metrics from order evidence (pure functions, no database).

Definitions (documented in docs/ml-pipeline.md, V4 section):

    decided order    an order whose outcome is known on `today`: received (fully or closed short), cancelled, or
                     still open/partial but already past its expected date (known to be late)
    on time          first delivery on or before the expected date (ordered + quoted lead time)
    in full          quantity received ≥ quantity ordered
    OTIF             delivered in full by the expected date — the reliability score is the OTIF rate
    lead time        first delivery date − order date (days)
    fill rate        Σ received ÷ Σ ordered over closed, non-cancelled orders
    price stability  1 − share of consecutive orders (same supplier and item) whose price moved by more than 2 %

Reliability score = OTIF rate, shrunk towards a prior for small samples:
    score = 100 × (OTIF successes + M × prior) ÷ (decided orders + M),   M = 5
with a Wilson 95 % interval on the raw rate. No weights are invented: the score is one measured rate.
"""

import math
from dataclasses import dataclass
from datetime import date

import numpy as np

PRIOR_STRENGTH = 5  # pseudo-orders pulling small samples towards the prior
MIN_ORDERS = 5  # below this the score is shown as "limited evidence"
PRICE_CHANGE = 0.02  # a price move larger than 2 % counts as a change
GRADES = [(90, "A"), (80, "B"), (65, "C"), (0, "D")]


@dataclass
class OrderRec:
    id: int
    supplier_id: int
    consumable_id: int
    ordered: date
    expected: date
    quoted_lead: int
    qty_ordered: int
    qty_received: int
    status: str  # OPEN | PARTIAL | RECEIVED | CANCELLED
    first_delivery: date | None
    completed: date | None
    unit_price: float

    @property
    def lead_time(self) -> int | None:
        return None if self.first_delivery is None else (self.first_delivery - self.ordered).days

    def overdue(self, today: date) -> bool:
        return self.status in ("OPEN", "PARTIAL") and today > self.expected

    def decided(self, today: date) -> bool:
        return self.status in ("RECEIVED", "CANCELLED") or self.overdue(today)

    def otif(self, today: date) -> bool | None:
        if not self.decided(today):
            return None
        return (self.status == "RECEIVED" and self.qty_received >= self.qty_ordered
                and self.completed is not None and self.completed <= self.expected)

    def on_time(self, today: date) -> bool | None:
        """First delivery on or before the expected date (None: undecided or cancelled before delivery)."""
        if self.first_delivery is not None:
            return self.first_delivery <= self.expected
        if self.status == "CANCELLED":
            return None
        return False if self.overdue(today) else None


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if n == 0:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def shrink(k: int, n: int, prior: float, m: int = PRIOR_STRENGTH) -> float:
    return (k + m * prior) / (n + m)


def grade(score: float | None) -> str | None:
    if score is None:
        return None
    return next(g for lo, g in GRADES if score >= lo)


def _q(values: list[float], q: float) -> float | None:
    return round(float(np.quantile(values, q)), 1) if values else None


def price_stats(orders: list[OrderRec]) -> dict:
    pairs = changes = 0
    trend = []
    by_item: dict[int, list[OrderRec]] = {}
    for o in orders:
        by_item.setdefault(o.consumable_id, []).append(o)
    for recs in by_item.values():
        recs = sorted(recs, key=lambda o: (o.ordered, o.id))
        for a, b in zip(recs, recs[1:], strict=False):
            if a.unit_price > 0:
                pairs += 1
                changes += abs(b.unit_price / a.unit_price - 1) > PRICE_CHANGE
        if len(recs) >= 2 and recs[0].unit_price > 0:
            trend.append(recs[-1].unit_price / recs[0].unit_price - 1)
    return {
        "price_pairs": pairs,
        "price_changes": changes,
        "price_stability": None if pairs == 0 else 1 - changes / pairs,
        "price_change_pct": float(np.mean(trend)) if trend else None,
    }


def summarize(orders: list[OrderRec], today: date, prior: float | None = None) -> dict:
    """All components for one group of orders (a supplier, or a supplier × item)."""
    decided = [o for o in orders if o.decided(today)]
    otif_k = sum(bool(o.otif(today)) for o in decided)
    n = len(decided)
    ot = [o.on_time(today) for o in orders]
    ot = [x for x in ot if x is not None]
    delivered = [o for o in orders if o.first_delivery is not None]
    lts = [float(o.lead_time) for o in delivered]
    late_days = [(o.first_delivery - o.expected).days for o in delivered if o.first_delivery > o.expected]
    closed = [o for o in orders if o.status in ("RECEIVED", "CANCELLED")]
    cancelled = [o for o in closed if o.status == "CANCELLED"]
    received = [o for o in closed if o.status == "RECEIVED"]
    raw = otif_k / n if n else None
    p0 = prior if prior is not None else (raw if raw is not None else 0.0)
    score = 100 * shrink(otif_k, n, p0) if (n or prior is not None) else None
    lo, hi = wilson(otif_k, n)
    quoted = [o.quoted_lead for o in orders]
    return {
        "orders": len(orders),
        "open": sum(o.status in ("OPEN", "PARTIAL") and not o.overdue(today) for o in orders),
        "overdue": sum(o.overdue(today) for o in orders),
        "received": len(received),
        "cancelled": len(cancelled),
        "decided": n,
        "otif_successes": otif_k,
        "otif_rate": raw,
        "otif_ci_low": lo,
        "otif_ci_high": hi,
        "prior": p0,
        "reliability_score": None if score is None else round(score, 1),
        "grade": grade(score),
        "limited_evidence": n < MIN_ORDERS,
        "on_time_rate": (sum(ot) / len(ot)) if ot else None,
        "late_rate": (1 - sum(ot) / len(ot)) if ot else None,
        "avg_days_late": float(np.mean(late_days)) if late_days else None,
        "lead_time_median": _q(lts, 0.5),
        "lead_time_mean": float(np.mean(lts)) if lts else None,
        "lead_time_p90": _q(lts, 0.9),
        "lead_time_std": float(np.std(lts)) if len(lts) > 1 else None,
        "quoted_lead_time": int(np.median(quoted)) if quoted else None,
        "cancellation_rate": (len(cancelled) / len(closed)) if closed else None,
        "fill_rate": (sum(o.qty_received for o in received) / sum(o.qty_ordered for o in received)) if received else None,
        "in_full_rate": (sum(o.qty_received >= o.qty_ordered for o in received) / len(received)) if received else None,
        **price_stats(orders),
    }


def p_within(orders: list[OrderRec], days: int, today: date, elapsed: int = 0) -> dict:
    """Evidence that an order arrives within `days` of being placed (conditional on not having arrived after
    `elapsed` days). Cancelled orders count as never arriving; open orders count only once their outcome for
    this deadline is known. Returns k/n and a smoothed probability (k + 0.5) / (n + 1) (None when n = 0)."""
    k = n = 0
    for o in orders:
        if o.status == "CANCELLED" and o.first_delivery is None:
            n += 1
            continue
        lt = o.lead_time
        if lt is None:
            age = (today - o.ordered).days
            if age > days and age > elapsed:
                n += 1  # still not delivered after `days`: a known miss
            continue
        if lt <= elapsed:
            continue  # would already have arrived — not comparable to an order still in transit
        n += 1
        k += lt <= days
    # no comparable past order → no evidence (never report the smoothing prior as if it were a measurement)
    return {"k": k, "n": n, "p": (k + 0.5) / (n + 1) if n else None, "raw": (k / n) if n else None}


def monthly(orders: list[OrderRec], today: date) -> list[dict]:
    out: dict[str, list[OrderRec]] = {}
    for o in orders:
        out.setdefault(o.ordered.strftime("%Y-%m"), []).append(o)
    rows = []
    for m in sorted(out):
        recs = out[m]
        dec = [o for o in recs if o.decided(today)]
        ot = [x for x in (o.on_time(today) for o in recs) if x is not None]
        rows.append({"month": m, "orders": len(recs), "decided": len(dec),
                     "otif_rate": (sum(bool(o.otif(today)) for o in dec) / len(dec)) if dec else None,
                     "on_time_rate": (sum(ot) / len(ot)) if ot else None,
                     "avg_price": float(np.mean([o.unit_price for o in recs]))})
    return rows


def lead_time_histogram(orders: list[OrderRec], max_days: int = 21) -> list[dict]:
    counts: dict[int, int] = {}
    for o in orders:
        if o.lead_time is not None:
            d = min(o.lead_time, max_days)
            counts[d] = counts.get(d, 0) + 1
    return [{"days": d, "orders": counts.get(d, 0)} for d in range(0, max(counts, default=0) + 1)]
