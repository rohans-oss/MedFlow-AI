"""V3.1 — deterministic stock projection (no ML).

Given usable batches today and a daily demand forecast, walk forward day by day:
  1. batches whose expiry date has passed are removed (lost to expiry),
  2. the day's demand is taken FEFO (earliest expiry first),
  3. demand that cannot be met is `unmet` — the first such day is the expected stockout date.

No deliveries are assumed (purchase orders arrive in V5), so the result answers
"when do we run out if nothing new arrives?". Replenishment timing is judged separately against lead time.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta


@dataclass
class Batch:
    quantity: float
    expiry_date: date | None  # None = does not expire


@dataclass
class ProjectionDay:
    date: date
    demand: float
    stock_end: float
    unmet: float
    expired: float


@dataclass
class Projection:
    start: date
    days: list[ProjectionDay] = field(default_factory=list)

    @property
    def stockout_offset(self) -> int | None:
        """0-based index of the first day with unmet demand (None: stock lasts the whole projection)."""
        for i, d in enumerate(self.days):
            if d.unmet > 1e-9:
                return i
        return None

    @property
    def stockout_date(self) -> date | None:
        i = self.stockout_offset
        return None if i is None else self.days[i].date

    @property
    def days_of_stock_remaining(self) -> int | None:
        """Number of whole days fully covered from `start` (None: covers the whole projection)."""
        return self.stockout_offset

    def shortage(self, horizon: int) -> float:
        return sum(d.unmet for d in self.days[:horizon])

    def expired(self, horizon: int | None = None) -> float:
        return sum(d.expired for d in (self.days if horizon is None else self.days[:horizon]))

    def demand(self, horizon: int) -> float:
        return sum(d.demand for d in self.days[:horizon])


def project(batches: list[Batch], demand: list[float], start: date) -> Projection:
    """Project usable stock from `start` (day 0) for len(demand) days. A batch is usable on its expiry date."""
    stock = sorted(([b.expiry_date, float(b.quantity)] for b in batches if b.quantity > 0),
                   key=lambda b: (b[0] is None, b[0] or date.max))
    out = Projection(start=start)
    for k, dem in enumerate(demand):
        day = start + timedelta(days=k)
        expired = 0.0
        while stock and stock[0][0] is not None and stock[0][0] < day:
            expired += stock.pop(0)[1]
        need = max(float(dem), 0.0)
        for b in stock:
            take = min(b[1], need)
            b[1] -= take
            need -= take
            if need <= 1e-9:
                break
        stock = [b for b in stock if b[1] > 1e-9]
        out.days.append(ProjectionDay(day, float(dem), sum(b[1] for b in stock), need if need > 1e-9 else 0.0, expired))
    return out


@dataclass
class Replenishment:
    lead_time_days: int | None
    order_by_date: date | None  # latest order date for delivery to arrive before the projected stockout
    earliest_arrival: date | None  # if ordered today
    can_replenish_in_time: bool | None  # None when no stockout is projected or no supplier is known
    order_overdue: bool


def replenishment(projection: Projection, today: date, lead_time_days: int | None) -> Replenishment:
    """Could an order placed today arrive before the projected stockout? (Deliveries arrive before the day's demand.)"""
    so = projection.stockout_date
    if lead_time_days is None:
        return Replenishment(None, None, None, None, False)
    arrival = today + timedelta(days=lead_time_days)
    if so is None:
        return Replenishment(lead_time_days, None, arrival, None, False)
    order_by = so - timedelta(days=lead_time_days)
    return Replenishment(lead_time_days, order_by, arrival, arrival <= so, order_by < today)
