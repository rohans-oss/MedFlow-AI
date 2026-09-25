"""V5.3 — Monte Carlo evaluation of an order plan (vectorised; pure numpy, no database).

Each simulated path draws
    daily demand   max(0, forecast_t + σ · z_t)       σ = the served model's daily holdout error for the item
    arrival day    for every in-transit order and every new order line, from its empirical arrival distribution
and walks the stock day by day exactly like the V3.1 projection: deliveries arrive before the day's demand, expired
batches are removed, demand is taken first-expiry-first-out, demand that cannot be met is a shortage.

Common random numbers: all scenarios for an item share the same demand paths and the same arrival draw per supplier
(and per in-transit order), so differences between scenarios come from the decision, not from sampling noise.
"""

from dataclasses import dataclass

import numpy as np

from app.procurement.arrival import Arrival

NEVER = 10**6
MAX_TAIL_DAYS = 365  # holding beyond the horizon is counted for at most a year


@dataclass
class Supply:
    qty: float
    arrival: Arrival | None  # None = on hand now
    expiry_day: float | None  # on hand: days from today until the expiry date; incoming: shelf life after arrival
    key: str  # random stream (common random numbers): "sup:<id>" for new lines, "order:<id>" for in-transit
    new: bool = False  # part of the decision being evaluated


@dataclass
class Paths:
    """Shared randomness for one item: demand paths and uniforms per random stream."""

    demand: np.ndarray  # [S, H]
    uniforms: dict[str, np.ndarray]
    seed: int

    def u(self, key: str) -> np.ndarray:
        if key not in self.uniforms:
            rng = np.random.default_rng([self.seed, sum(ord(c) * (i + 1) for i, c in enumerate(key))])
            self.uniforms[key] = rng.random(self.demand.shape[0])
        return self.uniforms[key]


def make_paths(mu: np.ndarray, sigma: float, sims: int, seed: int, demand_factor: float = 1.0) -> Paths:
    rng = np.random.default_rng(seed)
    z = rng.standard_normal((sims, len(mu)))
    demand = np.clip(mu[None, :] * demand_factor + sigma * z, 0.0, None)
    return Paths(demand, {}, seed)


def _arrival_days(a: Arrival, u: np.ndarray) -> np.ndarray:
    cdf = np.cumsum(a.pmf)
    days = np.searchsorted(cdf, u, side="right")  # u ≥ total mass → index H → never within horizon
    return np.where(days >= len(a.pmf), NEVER, days)


@dataclass
class SimResult:
    shortage: np.ndarray  # [S] units short over the horizon
    shortage_14: np.ndarray  # [S] units short over the first 14 days
    first_stockout: np.ndarray  # [S] day of the first shortage (NEVER if none)
    new_arrival: np.ndarray  # [S, L] arrival day per new line (NEVER = not within horizon)
    new_consumed: np.ndarray  # [S, L] units of each new line used within the horizon
    new_expired: np.ndarray  # [S, L] units of each new line expired within the horizon
    new_waste_after: np.ndarray  # [S, L] units left at the horizon that expire before they can be used
    new_left_usable: np.ndarray  # [S, L] units left at the horizon that will be used before expiring
    new_stock_days: np.ndarray  # [S, L] unit-days held within the horizon
    new_tail_days: np.ndarray  # [S, L] unit-days held after the horizon until use (approximate, see module doc)
    existing_expired: np.ndarray  # [S] on-hand + in-transit units expiring within the horizon
    # totals over ALL stock (on hand + in transit + new); a scenario is costed by its difference to "no order" on the
    # same paths, which is independent of which batch FEFO happens to take units from
    total_consumed: np.ndarray  # [S]
    total_waste: np.ndarray  # [S] expired within the horizon + left at the horizon but expiring before use
    total_hold_days: np.ndarray  # [S] unit-days held within the horizon + after it until use

    @property
    def p_stockout(self) -> float:
        return float(np.mean(self.shortage > 1e-6))

    @property
    def p_stockout_14(self) -> float:
        return float(np.mean(self.shortage_14 > 1e-6))


def simulate(on_hand: list[Supply], transit: list[Supply], new: list[Supply], paths: Paths,
             rate_after: float) -> SimResult:
    """Walk every path over the horizon. `rate_after` = expected daily demand after the horizon (for leftovers)."""
    demand = paths.demand
    S, H = demand.shape
    cols = on_hand + transit + new
    B = len(cols)
    qty = np.array([c.qty for c in cols], dtype=float)
    arrive = np.zeros((S, B), dtype=np.int64)
    expiry = np.full((S, B), np.inf)
    nominal = np.zeros(B)  # FEFO order: nominal expiry day (fixed per column)
    for j, c in enumerate(cols):
        if c.arrival is not None:
            arrive[:, j] = _arrival_days(c.arrival, paths.u(c.key))
            typical = c.arrival.quantile(0.5)
            base = typical if typical is not None else H
        else:
            base = 0
        if c.expiry_day is None:
            nominal[j] = np.inf
        elif c.arrival is None:
            expiry[:, j] = c.expiry_day
            nominal[j] = c.expiry_day
        else:
            expiry[:, j] = np.where(arrive[:, j] >= NEVER, np.inf, arrive[:, j] + c.expiry_day)
            nominal[j] = base + c.expiry_day
    order = np.argsort(nominal, kind="stable")
    qty, arrive, expiry = qty[order], arrive[:, order], expiry[:, order]
    is_new = np.array([cols[j].new for j in order])
    inv = np.empty(B, dtype=np.int64)
    inv[order] = np.arange(B)
    new_pos = [int(inv[len(on_hand) + len(transit) + k]) for k in range(len(new))]  # caller's order of new lines

    stock = np.zeros((S, B))
    consumed = np.zeros((S, B))
    expired = np.zeros((S, B))
    stock_days = np.zeros((S, B))
    unmet = np.zeros((S, H))
    for t in range(H):
        stock += (arrive == t) * qty[None, :]
        gone = (expiry < t) & (stock > 0)
        expired += np.where(gone, stock, 0.0)
        stock = np.where(gone, 0.0, stock)
        d = demand[:, t]
        cum = np.cumsum(stock, axis=1)
        take = np.clip(d[:, None] - (cum - stock), 0.0, stock)
        stock -= take
        consumed += take
        unmet[:, t] = np.clip(d - cum[:, -1], 0.0, None)
        stock_days += stock

    shortage = unmet.sum(axis=1)
    has = unmet > 1e-6
    first = np.where(has.any(axis=1), has.argmax(axis=1), NEVER)

    # after the horizon: leftovers are used FEFO at `rate_after`; units whose batch expires first are waste
    before = np.cumsum(stock, axis=1) - stock
    days_left = expiry - (H - 1)  # a unit is usable up to and including its expiry day
    if rate_after > 1e-9:
        usable_cap = np.clip(rate_after * days_left - before, 0.0, None)
        left_usable = np.minimum(stock, usable_cap)
        tail = left_usable * np.minimum((before + left_usable / 2.0) / rate_after, MAX_TAIL_DAYS)
    else:
        left_usable = np.where(np.isinf(expiry), stock, 0.0)
        tail = left_usable * MAX_TAIL_DAYS
    waste_after = stock - left_usable

    def pick(a: np.ndarray) -> np.ndarray:
        return a[:, new_pos] if new_pos else np.zeros((S, 0))

    existing = ~is_new
    return SimResult(
        shortage=shortage, shortage_14=unmet[:, :14].sum(axis=1), first_stockout=first,
        new_arrival=pick(arrive.astype(float)), new_consumed=pick(consumed), new_expired=pick(expired),
        new_waste_after=pick(waste_after), new_left_usable=pick(left_usable), new_stock_days=pick(stock_days),
        new_tail_days=pick(tail), existing_expired=expired[:, existing].sum(axis=1) if existing.any() else np.zeros(S),
        total_consumed=consumed.sum(axis=1), total_waste=expired.sum(axis=1) + waste_after.sum(axis=1),
        total_hold_days=stock_days.sum(axis=1) + tail.sum(axis=1),
    )
