"""Turn the stock ledger into a daily consumption panel.

Consumption for an (item, department) on a day = units ISSUED minus units RETURNED, in the business timezone.

Censoring: on a day when an item had no stock, recorded consumption is limited by availability, not demand.
Those item-days are marked `censored` and are excluded from training targets and from evaluation.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import business_today
from app.models import Consumable, MovementType, StockMovement

MAX_HISTORY_DAYS = 365


@dataclass
class Panel:
    """Daily consumption matrix: rows = dates (contiguous), columns = series (item, department)."""

    dates: pd.DatetimeIndex
    values: pd.DataFrame  # units consumed; NaN where the item was stocked out (censored)
    series: pd.DataFrame  # index = column position; columns: consumable_id, department_id
    item_actual: pd.DataFrame  # dates × consumable_id, total consumption (all departments), NaN if censored
    items: dict[int, dict] = field(default_factory=dict)  # consumable_id -> {name, sku, unit}

    @property
    def data_start(self) -> date:
        return self.dates[0].date()

    @property
    def data_end(self) -> date:
        return self.dates[-1].date()


def _local_date(ts) -> date:
    if ts.tzinfo is None:  # SQLite returns naive UTC
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(ZoneInfo(settings.TIMEZONE)).date()


def load_panel(db: Session, hospital_id: int, data_end: date | None = None, consumable_id: int | None = None) -> Panel:
    """Build the panel from movements up to and including `data_end` (default: yesterday).

    Today is excluded by default because it is a partial day.
    """
    data_end = data_end or (business_today() - timedelta(days=1))
    item_q = select(Consumable).where(Consumable.hospital_id == hospital_id, Consumable.is_active.is_(True))
    move_q = select(
        StockMovement.created_at, StockMovement.consumable_id, StockMovement.department_id,
        StockMovement.movement_type, StockMovement.quantity, StockMovement.balance_after, StockMovement.id,
    ).where(StockMovement.hospital_id == hospital_id)
    if consumable_id is not None:
        item_q = item_q.where(Consumable.id == consumable_id)
        move_q = move_q.where(StockMovement.consumable_id == consumable_id)
    items = {c.id: {"name": c.name, "sku": c.sku, "unit": c.unit} for c in db.scalars(item_q)}
    rows = db.execute(move_q.order_by(StockMovement.created_at, StockMovement.id)).all()

    empty = pd.DatetimeIndex([])
    if not rows:
        return Panel(empty, pd.DataFrame(), pd.DataFrame(columns=["consumable_id", "department_id"]), pd.DataFrame(), items)

    df = pd.DataFrame(rows, columns=["ts", "item", "dept", "type", "qty", "balance", "id"])
    df["date"] = pd.to_datetime([_local_date(t) for t in df["ts"]])
    df = df[df["date"] <= pd.Timestamp(data_end)]
    df = df[df["item"].isin(items)]
    if df.empty:
        return Panel(empty, pd.DataFrame(), pd.DataFrame(columns=["consumable_id", "department_id"]), pd.DataFrame(), items)

    start = max(df["date"].min(), pd.Timestamp(data_end) - pd.Timedelta(days=MAX_HISTORY_DAYS - 1))
    dates = pd.date_range(start, pd.Timestamp(data_end), freq="D")

    # --- consumption per (item, dept, day)
    use = df[df["type"].isin([MovementType.ISSUE, MovementType.RETURN]) & df["dept"].notna()].copy()
    use["units"] = -use["qty"]
    use["dept"] = use["dept"].astype(int)
    daily = use.groupby(["date", "item", "dept"])["units"].sum()
    if daily.empty:
        wide = pd.DataFrame(index=dates, columns=pd.MultiIndex.from_tuples([], names=["item", "dept"]), dtype=float)
    else:
        wide = daily.unstack(["item", "dept"]).reindex(dates).fillna(0.0)
        wide = wide.loc[:, (wide > 0).any(axis=0)]  # drop series that never consumed in the window

    # --- censoring per (item, day): stock hit zero during the day, or the day started at zero
    df_sorted = df.sort_values(["item", "ts", "id"])
    g = df_sorted.groupby(["item", "date"])["balance"]
    day_min = g.min().unstack("item").reindex(dates)
    day_end = g.last().unstack("item").reindex(dates)
    end_ffill = day_end.ffill()
    start_bal = end_ffill.shift(1)
    censored = (day_min <= 0) | ((day_min.isna()) & (end_ffill <= 0)) | (start_bal <= 0)
    censored = censored.fillna(False)

    series = pd.DataFrame(list(wide.columns), columns=["consumable_id", "department_id"]).astype(int)
    values = pd.DataFrame(wide.to_numpy(dtype=float), index=dates, columns=range(len(series)))
    for pos, item in enumerate(series["consumable_id"]):
        if item in censored.columns:
            values.loc[censored[item].to_numpy(dtype=bool), pos] = np.nan

    # item totals (all departments), NaN on censored days
    if daily.empty:
        totals = pd.DataFrame(0.0, index=dates, columns=sorted(items))
    else:
        totals = daily.groupby(["date", "item"]).sum().unstack("item").reindex(dates).fillna(0.0)
        totals = totals.reindex(columns=sorted(items), fill_value=0.0)
    for item in totals.columns:
        if item in censored.columns:
            totals.loc[censored[item].to_numpy(dtype=bool), item] = np.nan

    return Panel(dates=dates, values=values, series=series, item_actual=totals, items=items)
