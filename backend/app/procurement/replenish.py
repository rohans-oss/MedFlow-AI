"""V5.1 — replenishment calculation (deterministic, from expected values; shown step by step).

    projected available(t) = usable stock projected FEFO with forecast demand (V3.1, expiry-aware)
                             + Σ in-transit quantity × P(arrived by day t)        ← V5.2: weighted, never certain
    cover period           = expected lead time L + review period R
    safety stock           = z × √((L + R) × σ_d² + μ_d² × σ_L²)
                             σ_d: daily forecast error of the served model; σ_L: spread of this supplier's lead times
    reorder date           = first day projected available falls below the safety stock, minus L
    required quantity      = demand over the cover period + safety stock − usable stock expected to be used
                             − expected in-transit arrivals within the cover period         (≥ 0, whole units)
    MOQ-adjusted quantity  = max(required, supplier MOQ)                                     (0 if nothing required)
    expiry cap             = units a new batch can be used before it expires = μ_d × shelf life − stock ahead of it
    storage cap            = item max level − projected available on arrival
"""

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from app.procurement.arrival import Arrival
from app.risk.projection import Batch, project


@dataclass
class TransitLine:
    order_id: int
    reference: str
    supplier_id: int
    supplier: str
    outstanding: int
    expected_date: date
    arrival: Arrival


@dataclass
class Replenishment:
    lead_time: float
    lead_time_sd: float
    cover_days: int
    avg_daily: float
    demand_cover: float
    sigma_daily: float
    safety_stock: float
    usable_stock: float
    expiring_before_use: float
    expected_incoming: float
    reorder_point: float
    order_by_offset: int | None  # days from today (negative = overdue); None = not within the horizon
    required: int
    moq: int
    moq_adjusted: int
    expiry_cap: int | None
    storage_cap: int | None
    physical: list[float] = field(default_factory=list)  # projected available without any delivery
    with_transit: list[float] = field(default_factory=list)  # + in-transit weighted by arrival probability
    need_by_offset: int | None = None  # first day projected available (with in-transit) runs out
    warnings: list[str] = field(default_factory=list)


def projected(batches: list[Batch], mu: np.ndarray, today: date, transit: list[TransitLine]) -> tuple[list[float], list[float], float]:
    proj = project(batches, list(mu), today)
    phys, cum_unmet = [], 0.0
    for d in proj.days:
        cum_unmet += d.unmet
        phys.append(d.stock_end - cum_unmet)  # negative = expected shortfall
    wt = [p + sum(t.outstanding * t.arrival.cdf(k) for t in transit) for k, p in enumerate(phys)]
    return phys, wt, proj.expired()


def calculate(batches: list[Batch], mu: np.ndarray, sigma_d: float, today: date, transit: list[TransitLine],
              lead_time: float, lead_time_sd: float, review_days: int, z: float, moq: int,
              shelf_life: float | None, max_level: int | None) -> Replenishment:
    H = len(mu)
    L = max(float(lead_time), 0.0)
    cover = int(min(math.ceil(L) + review_days, H))
    avg = float(np.mean(mu)) if len(mu) else 0.0
    demand_cover = float(np.sum(mu[:cover]))
    ss = z * math.sqrt(cover * sigma_d ** 2 + (avg ** 2) * (lead_time_sd ** 2))
    phys, wt, _ = projected(batches, mu, today, transit)
    usable = float(sum(b.quantity for b in batches))
    exp_cover = project(batches, list(mu[:cover]), today).expired() if cover else 0.0
    incoming = sum(t.outstanding * t.arrival.cdf(cover - 1) for t in transit) if cover else 0.0
    required = max(0, math.ceil(demand_cover + ss - (usable - exp_cover) - incoming - 1e-9))
    below = next((k for k, v in enumerate(wt) if v < ss), None)
    order_by = None if below is None else below - int(math.ceil(L))
    need_by = next((k for k, v in enumerate(wt) if v < -1e-9), None)  # first day demand can't be met (as V3)
    moq_adj = max(required, moq) if required > 0 else 0
    k = int(math.ceil(L))
    at_arrival = (max(wt[min(k - 1, H - 1)], 0.0) if k >= 1 else usable) if H else 0.0  # stock ahead of a new batch
    exp_cap = None if shelf_life is None else max(int(avg * shelf_life - at_arrival), 0)
    st_cap = None if not max_level else max(int(max_level - at_arrival), 0)
    warnings = []
    if exp_cap is not None and moq_adj > exp_cap:
        warnings.append(f"The quantity ({moq_adj:,}) is more than can be used before a new batch expires "
                        f"(about {exp_cap:,} at {avg:.1f}/day over a {shelf_life:.0f}-day shelf life).")
    if st_cap is not None and moq_adj > st_cap:
        warnings.append(f"The quantity ({moq_adj:,}) would take stock above the item's max level ({max_level:,}).")
    if moq_adj > required > 0:
        warnings.append(f"Rounded up from {required:,} to the supplier minimum order of {moq:,}.")
    return Replenishment(
        lead_time=round(L, 1), lead_time_sd=round(lead_time_sd, 2), cover_days=cover, avg_daily=round(avg, 2),
        demand_cover=round(demand_cover, 1), sigma_daily=round(sigma_d, 2), safety_stock=round(ss, 1),
        usable_stock=usable, expiring_before_use=round(exp_cover, 1), expected_incoming=round(incoming, 1),
        reorder_point=round(avg * L + ss, 1), order_by_offset=order_by, required=required, moq=moq, moq_adjusted=moq_adj,
        expiry_cap=exp_cap, storage_cap=st_cap, physical=[round(v, 1) for v in phys], with_transit=[round(v, 1) for v in wt],
        need_by_offset=need_by, warnings=warnings,
    )


def offset_date(today: date, k: int | None) -> date | None:
    return None if k is None else today + timedelta(days=k)
