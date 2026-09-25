"""V5.2 — when will a delivery arrive? Empirical arrival distributions from the V4 order log.

A new order placed today with supplier S for item I arrives on day t (0 = today) with the probability observed in S's
past orders for I (≥ MIN_SAMPLES outcomes), else S's orders for any item, else the hospital-wide delay versus the
quoted lead time applied to S's quote. Cancelled orders count as never arriving. An order already in transit for e
days is judged only against past orders that had not arrived after e days (conditional distribution).

Small samples: as V4's reliability score, a supplier × item distribution is shrunk towards the supplier's (and a
supplier's towards the hospital-wide one) with 5 pseudo-orders, plus one pseudo-order that never arrives:
pmf = (n × own + 5 × level above) ÷ (n + 5 + 1). No history makes a delivery certain (20 of 20 on time → 96 %).
The raw "k of n" evidence is always reported next to the probability.

Deliveries arrive before the day's demand (as in V3). Nothing here assumes an order is certain to arrive.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from app.supplier_intel.metrics import PRIOR_STRENGTH, OrderRec

MIN_SAMPLES = 5  # outcomes needed before a supplier × item (or supplier) distribution is used


@dataclass
class Arrival:
    """P(arrival on day t) for t = 0 … H−1 (relative to today) plus P(later than the horizon or never)."""

    pmf: np.ndarray
    p_beyond: float
    basis: str  # "item" | "supplier" | "hospital" | "quoted" | "no_evidence"
    n: int  # outcomes behind the distribution
    samples: list[float | None] = field(default_factory=list)  # arrival day per comparable past order (None = never)

    @property
    def horizon(self) -> int:
        return len(self.pmf)

    def cdf(self, t: int) -> float:
        """P(arrived by the start of day t, i.e. usable on day t)."""
        if t < 0:
            return 0.0
        return float(self.pmf[: min(t, self.horizon - 1) + 1].sum())

    def quantile(self, q: float) -> int | None:
        """Day by which a fraction q of deliveries has arrived (None when that never happens within the horizon)."""
        c = np.cumsum(self.pmf)
        idx = np.nonzero(c >= q - 1e-9)[0]
        return int(idx[0]) if len(idx) else None

    def evidence_within(self, days: int) -> tuple[int, int]:
        """k of n comparable past orders arrived within `days` (from the samples, not the smoothed pmf)."""
        n = len(self.samples)
        k = sum(1 for s in self.samples if s is not None and s <= days)
        return k, n

    def shifted(self, days: int) -> "Arrival":
        """What-if: every delivery `days` later."""
        if days <= 0:
            return self
        h = self.horizon
        pmf = np.zeros(h)
        if days < h:
            pmf[days:] = self.pmf[: h - days]
        moved = float(self.pmf.sum() - pmf.sum())
        samples = [None if s is None else s + days for s in self.samples]
        return Arrival(pmf, self.p_beyond + moved, self.basis, self.n, samples)


def _pmf(samples: list[float | None], horizon: int) -> np.ndarray:
    pmf = np.zeros(horizon)
    for s in samples:
        if s is not None and s < horizon:
            pmf[max(int(round(s)), 0)] += 1.0
    return pmf / len(samples) if samples else pmf


def _from_samples(samples: list[float | None], horizon: int, basis: str,
                  prior: list[float | None] | None = None) -> Arrival:
    """Empirical distribution, shrunk towards `prior` (the level above) with PRIOR_STRENGTH pseudo-orders — as V4's
    reliability score — plus one pseudo-order that never arrives, so no history makes a delivery certain."""
    n = len(samples)
    if n == 0:
        return Arrival(np.zeros(horizon), 1.0, "no_evidence", 0, [])
    m = PRIOR_STRENGTH if prior else 0
    pmf = (n * _pmf(samples, horizon) + (m * _pmf(prior, horizon) if prior else 0)) / (n + m + 1)
    return Arrival(pmf, float(1.0 - pmf.sum()), basis, n, list(samples))


def outcome(r: OrderRec) -> float | None | bool:
    """Lead time of a past order; None = never arrived (cancelled); False = outcome not known yet (skip)."""
    if r.first_delivery is not None:
        return float(r.lead_time)
    if r.status == "CANCELLED":
        return None
    return False


def _outcomes(recs: list[OrderRec]) -> list[float | None]:
    return [o for o in (outcome(r) for r in recs) if o is not False]


def _hospital(recs: list[OrderRec], quoted: int) -> list[float | None]:
    """Hospital-wide delay versus quote, applied to this quote."""
    return [None if o is None else max(quoted + o - r.quoted_lead, 0) for r in recs for o in [outcome(r)] if o is not False]


def new_order(recs: list[OrderRec], supplier_id: int, item_id: int, quoted: int, horizon: int,
              hospital_recs: list[OrderRec] | None = None) -> Arrival:
    """Arrival distribution for an order placed today."""
    hosp = _hospital(hospital_recs if hospital_recs is not None else recs, quoted)
    hosp = hosp if len(hosp) >= MIN_SAMPLES else None
    sup = _outcomes([r for r in recs if r.supplier_id == supplier_id])
    item = _outcomes([r for r in recs if r.supplier_id == supplier_id and r.consumable_id == item_id])
    if len(item) >= MIN_SAMPLES:
        return _from_samples(item, horizon, "item", sup if len(sup) >= MIN_SAMPLES else hosp)
    if len(sup) >= MIN_SAMPLES:
        return _from_samples(sup, horizon, "supplier", hosp)
    if hosp:
        return _from_samples(hosp, horizon, "hospital")
    pmf = np.zeros(horizon)
    if quoted < horizon:
        pmf[quoted] = 1.0
    return Arrival(pmf, float(1 - pmf.sum()), "quoted", 0, [])


def in_transit(recs: list[OrderRec], supplier_id: int, item_id: int, ordered: date, today: date, quoted: int,
               horizon: int) -> Arrival:
    """Arrival distribution for an order placed on `ordered` that has not (fully) arrived by today.

    Conditional on "not arrived after e days": only past orders that took longer than e (or never came) are
    comparable, shifted by e. With no comparable past order there is no evidence — it is not counted as arriving.
    """
    e = (today - ordered).days

    def cond(outs: list[float | None]) -> list[float | None]:
        return [None if o is None else o - e for o in outs if o is None or o > e]

    hosp_all = _hospital(recs, quoted)
    hosp = cond(hosp_all) if len(hosp_all) >= MIN_SAMPLES else []
    sup_all = _outcomes([r for r in recs if r.supplier_id == supplier_id])
    sup = cond(sup_all)
    item_all = _outcomes([r for r in recs if r.supplier_id == supplier_id and r.consumable_id == item_id])
    if len(item_all) >= MIN_SAMPLES:
        if not cond(item_all):
            return Arrival(np.zeros(horizon), 1.0, "no_evidence", 0, [])
        return _from_samples(cond(item_all), horizon, "item", sup if len(sup_all) >= MIN_SAMPLES and sup else hosp or None)
    if len(sup_all) >= MIN_SAMPLES:
        if not sup:
            return Arrival(np.zeros(horizon), 1.0, "no_evidence", 0, [])
        return _from_samples(sup, horizon, "supplier", hosp or None)
    if hosp:
        return _from_samples(hosp, horizon, "hospital")
    return Arrival(np.zeros(horizon), 1.0, "no_evidence", 0, [])


def lead_time_stats(a: Arrival) -> tuple[float | None, float | None]:
    """Mean and standard deviation of the arrival day among deliveries that arrive (for safety stock)."""
    vals = [s for s in a.samples if s is not None]
    if not vals:
        m = a.quantile(0.5)
        return (None if m is None else float(m)), 0.0
    return float(np.mean(vals)), float(np.std(vals))


def backtest(recs: list[OrderRec], warmup_days: int = 90, extra_days: tuple[int, ...] = (0, 2)) -> dict:
    """Are the arrival probabilities calibrated? Expanding window, no look-ahead: for every order placed after the
    warm-up whose outcome is known, build its distribution from orders whose outcome was known BEFORE it was placed,
    and score P(arrived within quoted + k days) against what happened. Baseline: "the quote is certain" (P = 1)."""
    done = [r for r in recs if outcome(r) is not False]
    if not done:
        return {"n": 0}
    start = min(r.ordered for r in recs) + timedelta(days=warmup_days)

    def known_at(x: OrderRec, day: date) -> bool:
        end = x.first_delivery if x.first_delivery is not None else x.completed
        return end is not None and end < day

    p_model, p_quote, y_all = [], [], []
    for r in sorted((r for r in done if r.ordered >= start), key=lambda r: r.ordered):
        hist = [x for x in recs if x.id != r.id and known_at(x, r.ordered)]
        a = new_order(hist, r.supplier_id, r.consumable_id, r.quoted_lead, 90)
        lt = outcome(r)
        for k in extra_days:
            d = r.quoted_lead + k
            p_model.append(a.cdf(d))
            p_quote.append(1.0)
            y_all.append(1.0 if lt is not None and lt <= d else 0.0)
    if not y_all:
        return {"n": 0}
    pm, pq, y = np.array(p_model), np.array(p_quote), np.array(y_all)
    bins = []
    for lo, hi in ((0, 0.5), (0.5, 0.8), (0.8, 0.9), (0.9, 0.95), (0.95, 1.01)):
        m = (pm >= lo) & (pm < hi)
        if m.any():
            bins.append({"from": lo, "to": min(hi, 1.0), "n": int(m.sum()), "mean_predicted": round(float(pm[m].mean()), 3),
                         "observed": round(float(y[m].mean()), 3)})
    return {"n": int(len(y)), "orders": int(len(y) // len(extra_days)), "windows": [f"quoted + {k} days" for k in extra_days],
            "observed_rate": round(float(y.mean()), 3), "mean_predicted": round(float(pm.mean()), 3),
            "brier_model": round(float(np.mean((pm - y) ** 2)), 4), "brier_quote_certain": round(float(np.mean((pq - y) ** 2)), 4),
            "calibration": bins}
