"""Synthetic replenishment simulator used to TRAIN the stockout-risk model.

Why: a hospital's own ledger rarely contains enough stockouts to learn from (the demo hospital has ~10 episodes in
90 days). The simulator replays each item many times under its own configuration — demand level and weekday profile,
procedure-kit demand, reorder level, max level, supplier lead time, MOQ, shelf life — while injecting controlled
events (supplier delays, missed reorders, demand spikes, short-dated deliveries). Every simulated day produces the
same columns as `history.ItemHistory.frame`, so features are computed by the same code.

Parameters are estimated only from the first `param_days` days of the ledger — before the backtest window — so the
evaluation on the hospital's real history is not contaminated by what happened later.

All simulated data is SYNTHETIC. It is used for training only, never stored as stock or shown as history.
"""

import math
import random
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.risk.history import EXPIRY_WINDOW, ItemHistory

SIM_START = pd.Timestamp("2000-01-03")  # arbitrary (a Monday); simulated dates never touch real ones


@dataclass
class SimConfig:
    replicates: int = 3  # minimum replicates per item for training (validation uses a third, at least one)
    min_rows: int = 20_000  # small hospitals get more replicates, up to max_replicates
    max_replicates: int = 40
    days: int = 240  # recorded days per replicate (after burn-in)
    burn_in: int = 30
    p_on_time: tuple[float, float] = (0.60, 0.95)  # supplier on-time probability range (per replicate)
    delay_days: tuple[int, int] = (2, 7)
    p_missed_reorder: float = 0.06  # procurement lapse when the reorder point is hit
    missed_days: tuple[int, int] = (3, 10)
    p_spike: float = 1 / 40  # daily chance a demand spike starts
    spike: tuple[float, float] = (1.3, 2.0)
    spike_days: tuple[int, int] = (5, 20)
    p_short_dated: float = 0.08  # delivery arrives with little shelf life left
    reorder_factor: tuple[float, float] = (0.6, 1.3)  # reorder policy tightness varies per replicate
    level_factor: tuple[float, float] = (0.8, 1.25)


@dataclass
class ItemParams:
    level: float  # non-procedure daily demand
    weekday: np.ndarray  # 7 multiplicative factors
    kit_weekday: np.ndarray  # 7 mean kit units per weekday
    dispersion: float  # gamma shape for extra-Poisson noise (large = Poisson)
    reorder_level: float
    max_level: float
    lead_time: int
    moq: int
    shelf_life: float


def estimate_params(item: ItemHistory, param_days: int) -> ItemParams | None:
    f = item.frame.iloc[:param_days]
    if len(f) < 14:
        return None
    ok = ~f["stockout"]
    nonproc = (f["consumed"] - f["kit"]).clip(lower=0)[ok]
    if nonproc.empty:
        return None
    level = max(float(nonproc.mean()), 0.05)
    wd = nonproc.groupby(nonproc.index.dayofweek).mean()
    weekday = np.array([float(wd.get(w, level)) / level if level > 0 else 1.0 for w in range(7)]).clip(0.3, 2.5)
    kit = f["kit"][ok]
    kwd = kit.groupby(kit.index.dayofweek).mean()
    kit_weekday = np.array([float(kwd.get(w, 0.0)) for w in range(7)])
    var = float(nonproc.var()) if len(nonproc) > 2 else level
    dispersion = level ** 2 / (var - level) if var > level * 1.05 else 50.0
    reorder = float(item.reorder_level) if item.reorder_level > 0 else level * 7
    max_level = float(item.max_level) if item.max_level else reorder * 3.5
    return ItemParams(level, weekday, kit_weekday, max(min(dispersion, 50.0), 0.5), reorder, max(max_level, reorder + 1),
                      int(item.lead_time_days or 5), max(int(item.moq or 1), 1), float(item.shelf_life_days or 730))


def _poisson(rng: np.random.Generator, lam: float) -> int:
    return int(rng.poisson(max(lam, 0.0))) if lam > 0 else 0


def simulate_item(p: ItemParams, cfg: SimConfig, seed: int) -> pd.DataFrame:
    """One replicate: daily frame with history.COLUMNS (index: synthetic dates)."""
    rng = np.random.default_rng(seed)
    pyr = random.Random(seed)
    n = cfg.burn_in + cfg.days + 31  # +31: known procedure plan / labels after the last origin
    level = p.level * pyr.uniform(*cfg.level_factor)
    rf = pyr.uniform(*cfg.reorder_factor)
    reorder, max_level = p.reorder_level * rf, max(p.max_level * rf, p.reorder_level * rf + 1)
    p_on_time = pyr.uniform(*cfg.p_on_time)
    usage = pyr.uniform(0.75, 1.25)  # actual kit usage vs configured kit

    dates = SIM_START + pd.to_timedelta(np.arange(n), unit="D")
    batches: list[list[float]] = [[pyr.uniform(reorder, max_level), pyr.uniform(0.3, 1.0) * p.shelf_life]]  # [qty, expiry day]
    pending: tuple[int, float, float] | None = None  # (arrival day, qty, shelf)
    lapse_until = -1
    spike_mult, spike_until = 1.0, -1
    out = np.zeros((n, 6))  # consumed, usable_end, expiring_14, received, stockout, kit
    prev_usable = sum(b[0] for b in batches)
    for d in range(n):
        wd = dates[d].dayofweek
        # expiry: batches past their date are lost
        batches = [b for b in batches if b[1] >= d]
        received = 0.0
        if pending and pending[0] <= d:
            received = pending[1]
            batches.append([pending[1], d + pending[2]])
            batches.sort(key=lambda b: b[1])
            pending = None
        if d > spike_until and pyr.random() < cfg.p_spike:
            spike_mult, spike_until = pyr.uniform(*cfg.spike), d + pyr.randint(*cfg.spike_days)
        mult = spike_mult if d <= spike_until else 1.0
        lam = level * p.weekday[wd] * mult
        g = rng.gamma(p.dispersion, 1 / p.dispersion) if p.dispersion < 50 else 1.0
        kit_plan = _poisson(rng, p.kit_weekday[wd] * mult) if p.kit_weekday[wd] > 0 else 0
        demand = _poisson(rng, lam * g) + (_poisson(rng, kit_plan * usage) if kit_plan else 0)

        start_usable = sum(b[0] for b in batches)
        need = float(demand)
        for b in batches:
            take = min(b[0], need)
            b[0] -= take
            need -= take
            if need <= 0:
                break
        batches = [b for b in batches if b[0] > 1e-9]
        usable = sum(b[0] for b in batches)
        consumed = demand - need
        stockout = prev_usable <= 0 or start_usable <= 0 or usable <= 0
        expiring = sum(b[0] for b in batches if d + 1 <= b[1] < d + 1 + EXPIRY_WINDOW)
        out[d] = (consumed, usable, expiring, received, float(stockout), kit_plan)
        prev_usable = usable

        # replenishment policy (end of day)
        if usable <= reorder and pending is None:
            if d <= lapse_until:
                pass
            elif pyr.random() < cfg.p_missed_reorder:
                lapse_until = d + pyr.randint(*cfg.missed_days)
            else:
                delay = 0 if pyr.random() < p_on_time else pyr.randint(*cfg.delay_days)
                qty = max(max_level - usable, p.moq)
                shelf = p.shelf_life * (pyr.uniform(0.05, 0.2) if pyr.random() < cfg.p_short_dated else pyr.uniform(0.6, 1.0))
                pending = (d + p.lead_time + delay, math.ceil(qty), shelf)

    frame = pd.DataFrame(out, index=dates, columns=["consumed", "usable_end", "expiring_14", "received", "stockout", "kit"])
    frame["stockout"] = frame["stockout"].astype(bool)
    return frame
