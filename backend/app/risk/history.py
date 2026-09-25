"""Per-item daily history rebuilt from the V1 ledger and the V2B procedure schedule.

For every active item and business day d (data_start … data_end):
    consumed      units issued − returned (all departments), as recorded
    usable_end    usable stock at the end of d = batches still usable on d+1 (expired stock excluded)
    expiring_14   part of usable_end that expires within the next 14 days
    received      units received on d
    stockout      the item was unavailable on d: the ledger balance hit 0 during the day, the day started with
                  no usable stock
    kit           units implied by non-cancelled scheduled procedures × active kit mappings on d
Future `kit` (after data_end) comes from SCHEDULED rows of active procedure types only (as in V2B).
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import business_today
from app.ml.data import MAX_HISTORY_DAYS, _local_date
from app.models import (
    Consumable,
    MovementType,
    ProcedureItemMapping,
    ProcedureSchedule,
    ProcedureStatus,
    ProcedureType,
    StockBatch,
    StockMovement,
    SupplierProduct,
)
from app.risk.projection import Batch

COLUMNS = ["consumed", "usable_end", "expiring_14", "received", "stockout", "kit"]
EXPIRY_WINDOW = 14


@dataclass
class ItemHistory:
    consumable_id: int
    sku: str
    name: str
    unit: str
    reorder_level: int
    max_level: int | None
    lead_time_days: int | None
    moq: int
    department_id: int | None  # department consuming most of this item
    frame: pd.DataFrame  # index: dates; COLUMNS
    kit_future: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))  # date → kit units after data_end
    shelf_life_days: float | None = None  # median expiry − receipt of this item's batches
    batches: list[Batch] = field(default_factory=list)  # current usable batches (for serving)


@dataclass
class LedgerHistory:
    data_start: date | None
    data_end: date
    schedule_end: date | None  # last date with a future SCHEDULED procedure (None: no future schedule)
    items: dict[int, ItemHistory]


def _lead_times(db: Session, hospital_id: int, ids: list[int]) -> dict[int, tuple[int, int]]:
    """Preferred supplier's (lead time, MOQ) per item; otherwise the shortest offer."""
    out: dict[int, tuple[int, int, bool]] = {}
    q = select(SupplierProduct).join(Consumable).where(Consumable.hospital_id == hospital_id,
                                                       SupplierProduct.consumable_id.in_(ids))
    for sp in db.scalars(q):
        cur = out.get(sp.consumable_id)
        cand = (sp.lead_time_days, sp.moq, bool(sp.is_preferred))
        if cur is None or (cand[2] and not cur[2]) or (cand[2] == cur[2] and cand[0] < cur[0]):
            out[sp.consumable_id] = cand
    return {k: (v[0], v[1]) for k, v in out.items()}


def kit_expected(db: Session, hospital_id: int, ids: list[int], data_end: date,
                 until: date | None = None) -> tuple[dict[int, dict[date, float]], date | None]:
    """Units per item and date implied by the procedure schedule × active kit mappings."""
    q = (
        select(ProcedureSchedule.scheduled_date, ProcedureSchedule.count, ProcedureSchedule.status,
               ProcedureType.is_active, ProcedureItemMapping.consumable_id, ProcedureItemMapping.quantity_per_procedure)
        .join(ProcedureType, ProcedureType.id == ProcedureSchedule.procedure_type_id)
        .join(ProcedureItemMapping, ProcedureItemMapping.procedure_type_id == ProcedureSchedule.procedure_type_id)
        .where(ProcedureSchedule.hospital_id == hospital_id, ProcedureSchedule.status != ProcedureStatus.CANCELLED,
               ProcedureItemMapping.is_active.is_(True), ProcedureItemMapping.consumable_id.in_(ids))
    )
    if until is not None:
        q = q.where(ProcedureSchedule.scheduled_date <= until)
    out: dict[int, dict[date, float]] = defaultdict(lambda: defaultdict(float))
    schedule_end = None
    for d, count, status, type_active, cid, qty in db.execute(q):
        if d > data_end and (status != ProcedureStatus.SCHEDULED or not type_active):
            continue  # future demand: planned rows of active procedure types only
        out[cid][d] += float(count) * float(qty)
        if d > data_end:
            schedule_end = d if schedule_end is None or d > schedule_end else schedule_end
    return out, schedule_end


def load_history(db: Session, hospital_id: int, data_end: date | None = None,
                 consumable_ids: list[int] | None = None, lookback_days: int | None = None) -> LedgerHistory:
    """Rebuild daily item histories up to and including `data_end` (default: yesterday)."""
    today = business_today()
    data_end = data_end or (today - timedelta(days=1))
    item_q = select(Consumable).where(Consumable.hospital_id == hospital_id, Consumable.is_active.is_(True))
    if consumable_ids is not None:
        item_q = item_q.where(Consumable.id.in_(consumable_ids))
    items = {c.id: c for c in db.scalars(item_q)}
    ids = list(items)
    if not ids:
        return LedgerHistory(None, data_end, None, {})

    batch_rows = db.execute(select(StockBatch.id, StockBatch.consumable_id, StockBatch.expiry_date, StockBatch.quantity,
                                   StockBatch.received_at).where(StockBatch.consumable_id.in_(ids))).all()
    expiry = {b.id: b.expiry_date for b in batch_rows}
    shelf: dict[int, list[float]] = defaultdict(list)
    current: dict[int, list[Batch]] = defaultdict(list)
    for b in batch_rows:
        if b.expiry_date is not None and b.received_at is not None:
            shelf[b.consumable_id].append((b.expiry_date - _local_date(b.received_at)).days)
        if b.quantity > 0 and (b.expiry_date is None or b.expiry_date >= today):
            current[b.consumable_id].append(Batch(float(b.quantity), b.expiry_date))

    mq = select(StockMovement.created_at, StockMovement.id, StockMovement.consumable_id, StockMovement.batch_id,
                StockMovement.department_id, StockMovement.movement_type, StockMovement.quantity,
                StockMovement.balance_after).where(StockMovement.hospital_id == hospital_id,
                                                   StockMovement.consumable_id.in_(ids))
    rows = db.execute(mq.order_by(StockMovement.created_at, StockMovement.id)).all()
    leads = _lead_times(db, hospital_id, ids)

    if rows:
        df = pd.DataFrame(rows, columns=["ts", "id", "item", "batch", "dept", "type", "qty", "balance"])
        df["date"] = pd.to_datetime([_local_date(t) for t in df["ts"]])
        df = df[df["date"] <= pd.Timestamp(data_end)]
    else:
        df = pd.DataFrame(columns=["ts", "id", "item", "batch", "dept", "type", "qty", "balance", "date"])
    if df.empty:
        data_start = None
        dates = pd.DatetimeIndex([])
    else:
        start = df["date"].min()
        if lookback_days is not None:
            start = max(start, pd.Timestamp(data_end) - pd.Timedelta(days=lookback_days - 1))
        start = max(start, pd.Timestamp(data_end) - pd.Timedelta(days=MAX_HISTORY_DAYS - 1))
        dates = pd.date_range(start, pd.Timestamp(data_end), freq="D")
        data_start = dates[0].date()

    kits, schedule_end = kit_expected(db, hospital_id, ids, data_end)
    out: dict[int, ItemHistory] = {}
    for cid, c in items.items():
        m = df[df["item"] == cid] if not df.empty else df
        frame = _item_frame(m, dates, expiry)
        kit = kits.get(cid, {})
        if len(dates):
            frame["kit"] = [kit.get(d.date(), 0.0) for d in dates]
        future = pd.Series({pd.Timestamp(d): v for d, v in kit.items() if d > data_end}, dtype=float).sort_index()
        dept = None
        use = m[m["type"].isin([MovementType.ISSUE, MovementType.RETURN]) & m["dept"].notna()] if not m.empty else m
        if not use.empty:
            dept = int((-use.groupby("dept")["qty"].sum()).idxmax())
        lt, moq = leads.get(cid, (None, 1))
        out[cid] = ItemHistory(
            consumable_id=cid, sku=c.sku, name=c.name, unit=c.unit, reorder_level=int(c.reorder_level or 0),
            max_level=c.max_level, lead_time_days=lt, moq=int(moq or 1), department_id=dept, frame=frame,
            kit_future=future, shelf_life_days=float(np.median(shelf[cid])) if shelf.get(cid) else None,
            batches=current.get(cid, []),
        )
    return LedgerHistory(data_start, data_end, schedule_end, out)


def _item_frame(m: pd.DataFrame, dates: pd.DatetimeIndex, expiry: dict[int, date | None]) -> pd.DataFrame:
    frame = pd.DataFrame(0.0, index=dates, columns=COLUMNS)
    if not len(dates):
        return frame
    frame["stockout"] = False
    if m.empty:
        frame["stockout"] = True  # never stocked
        return frame
    use = m[m["type"].isin([MovementType.ISSUE, MovementType.RETURN])]
    frame["consumed"] = (-use.groupby("date")["qty"].sum()).reindex(dates).fillna(0.0)
    rec = m[m["type"] == MovementType.RECEIPT]
    frame["received"] = rec.groupby("date")["qty"].sum().reindex(dates).fillna(0.0)

    # batch quantities at the end of each day (movements before the window are included via cumsum)
    per = m.groupby(["date", "batch"])["qty"].sum().unstack("batch").fillna(0.0).cumsum()
    per = per.reindex(per.index.union(dates)).ffill().fillna(0.0).reindex(dates)
    d_next = (dates + pd.Timedelta(days=1)).date
    usable = np.zeros(len(dates))
    expiring = np.zeros(len(dates))
    for b in per.columns:
        exp = expiry.get(int(b))
        q = per[b].to_numpy()
        if exp is None:
            usable += q
            continue
        ok = np.array([exp >= dn for dn in d_next])  # still usable tomorrow
        soon = np.array([exp < dn + timedelta(days=EXPIRY_WINDOW) for dn in d_next])
        usable += np.where(ok, q, 0.0)
        expiring += np.where(ok & soon, q, 0.0)
    frame["usable_end"] = np.clip(usable, 0, None)
    frame["expiring_14"] = np.clip(expiring, 0, None)

    # intraday: ledger balance touched zero
    bal_min = m.groupby("date")["balance"].min().reindex(dates)
    start_usable = frame["usable_end"].shift(1)
    # (stock that merely expires tonight shows up as "no usable stock at the start" of the next day)
    frame["stockout"] = ((bal_min <= 0).fillna(False) | (start_usable <= 0).fillna(False)).astype(bool)
    return frame
