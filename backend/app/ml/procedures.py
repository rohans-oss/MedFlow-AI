"""V2B — procedure data for forecasting.

    procedure_schedules (non-cancelled)  ×  procedure_item_mappings (active)  →  expected kit usage
    per (date, item, department)

Everything comes from the database; nothing about specific procedures is hard-coded here.

Timing rules (no leakage):
  * the features for day d use procedures dated d (a schedule is known before the day) and earlier days only;
  * history (≤ data_end) uses the non-cancelled row of each slot — normally COMPLETED;
  * future days use SCHEDULED rows of ACTIVE procedure types; CANCELLED rows never count;
  * days after the last scheduled date are *unknown*, not "zero procedures" (the pipeline falls back to V2A there).
"""

import hashlib
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Department, ProcedureItemMapping, ProcedureSchedule, ProcedureStatus, ProcedureType

# Base frames supplied to the feature builder; share and delta are derived there (they need consumption history).
PROC_BASE = ["procedure_count", "procedure_expected_quantity", "procedure_type_count", "procedure_department_count"]
PROC_FEATURES = [*PROC_BASE, "procedure_demand_share", "procedure_expected_delta_7"]
PROC_FEATURE_LABELS = {
    "procedure_count": "Scheduled procedures (department)",
    "procedure_expected_quantity": "Scheduled procedure demand (kit)",
    "procedure_type_count": "Procedure types involved",
    "procedure_department_count": "Departments with procedures",
    "procedure_demand_share": "Procedure share of demand",
    "procedure_expected_delta_7": "Procedure load vs last 7 days",
}


@dataclass
class ProcedureData:
    rows: pd.DataFrame  # long: date, type_id, type_code, type_name, dept_id, dept_name, item, count, qty, expected
    data_end: date
    schedule_end: date | None  # last date with a non-cancelled entry (None = no procedures at all)
    fingerprint: str
    n_schedule_rows: int
    n_mappings: int

    def is_empty(self) -> bool:
        return self.rows.empty

    def frames(self, index: pd.DatetimeIndex, series: pd.DataFrame, scale: np.ndarray | None) -> dict[str, pd.DataFrame]:
        """Base procedure feature frames aligned to (index × series). Expected quantity is divided by `scale`."""
        n = len(series)
        out = {k: pd.DataFrame(0.0, index=index, columns=range(n)) for k in PROC_BASE}
        if self.rows.empty or n == 0:
            return out
        r = self.rows[self.rows["date"].isin(index)]
        if r.empty:
            return out
        key = pd.MultiIndex.from_frame(series[["consumable_id", "department_id"]])
        by_series = r.groupby(["date", "item", "dept_id"])[["count", "expected"]].sum()
        for col, name in (("count", "procedure_count"), ("expected", "procedure_expected_quantity")):
            wide = by_series[col].unstack(["item", "dept_id"])
            wide = wide.reindex(index=index, columns=key).fillna(0.0)
            out[name] = pd.DataFrame(wide.to_numpy(dtype=float), index=index, columns=range(n))
        positive = r[r["count"] > 0]
        per_item_types = positive.groupby(["date", "item"])["type_id"].nunique().unstack("item")
        per_item_depts = positive.groupby(["date", "item"])["dept_id"].nunique().unstack("item")
        items = series["consumable_id"].to_numpy()
        for frame, name in ((per_item_types, "procedure_type_count"), (per_item_depts, "procedure_department_count")):
            f = frame.reindex(index=index, columns=np.unique(items)).fillna(0.0)
            out[name] = pd.DataFrame(f[items].to_numpy(dtype=float), index=index, columns=range(n))
        if scale is not None:
            out["procedure_expected_quantity"] = out["procedure_expected_quantity"] / scale
        return out

    def has_signal(self, until: date) -> bool:
        """At least two weeks of history with procedure-driven expected usage."""
        if self.rows.empty:
            return False
        hist = self.rows[(self.rows["date"] <= pd.Timestamp(until)) & (self.rows["expected"] > 0)]
        return hist["date"].nunique() >= 14


def load_procedure_data(db: Session, hospital_id: int, data_end: date, horizon: int) -> ProcedureData:
    """Procedure-driven expected usage from the first recorded procedure up to data_end + horizon."""
    last_day = data_end + timedelta(days=horizon)
    mappings = db.execute(
        select(ProcedureItemMapping.procedure_type_id, ProcedureItemMapping.consumable_id,
               ProcedureItemMapping.quantity_per_procedure)
        .where(ProcedureItemMapping.hospital_id == hospital_id, ProcedureItemMapping.is_active.is_(True))
        .order_by(ProcedureItemMapping.procedure_type_id, ProcedureItemMapping.consumable_id)
    ).all()
    sched = db.execute(
        select(ProcedureSchedule.scheduled_date, ProcedureSchedule.procedure_type_id, ProcedureType.code,
               ProcedureType.name, ProcedureType.is_active, ProcedureSchedule.department_id, Department.name,
               ProcedureSchedule.count, ProcedureSchedule.status)
        .join(ProcedureType, ProcedureType.id == ProcedureSchedule.procedure_type_id)
        .join(Department, Department.id == ProcedureSchedule.department_id)
        .where(ProcedureSchedule.hospital_id == hospital_id, ProcedureSchedule.status != ProcedureStatus.CANCELLED,
               ProcedureSchedule.scheduled_date <= last_day)
        .order_by(ProcedureSchedule.scheduled_date, ProcedureSchedule.procedure_type_id, ProcedureSchedule.department_id)
    ).all()
    # future rows only for active procedure types; history is fact regardless of today's active flag
    sched = [s for s in sched if s[0] <= data_end or s[4]]

    h = hashlib.sha256()
    for m in mappings:
        h.update(f"m{m[0]}:{m[1]}:{float(m[2])};".encode())
    for s in sched:
        h.update(f"s{s[0]}:{s[1]}:{s[5]}:{s[7]}:{s[8]};".encode())
    schedule_end = max((s[0] for s in sched), default=None)

    cols = ["date", "type_id", "type_code", "type_name", "dept_id", "dept_name", "item", "count", "qty", "expected"]
    if not sched or not mappings:
        return ProcedureData(pd.DataFrame(columns=cols), data_end, schedule_end, h.hexdigest(), len(sched), len(mappings))
    s_df = pd.DataFrame(sched, columns=["date", "type_id", "type_code", "type_name", "active", "dept_id", "dept_name",
                                        "count", "status"])
    m_df = pd.DataFrame(mappings, columns=["type_id", "item", "qty"])
    m_df["qty"] = m_df["qty"].astype(float)
    rows = s_df.merge(m_df, on="type_id", how="inner")  # procedures without mappings contribute nothing
    rows["date"] = pd.to_datetime(rows["date"])
    rows["count"] = rows["count"].astype(float)
    rows["expected"] = rows["count"] * rows["qty"]
    return ProcedureData(rows[cols].reset_index(drop=True), data_end, schedule_end, h.hexdigest(), len(sched), len(mappings))


def item_impact(proc: ProcedureData, item_id: int, start: date, days: int) -> dict:
    """Scheduled procedures relevant to one item in [start, start+days): counts, kit-expected units, breakdowns."""
    end = start + timedelta(days=days - 1)
    r = proc.rows
    if not r.empty:
        r = r[(r["item"] == item_id) & (r["date"] >= pd.Timestamp(start)) & (r["date"] <= pd.Timestamp(end))]
    if r is None or r.empty:
        return {"scheduled_procedures": 0, "expected_quantity": 0.0, "types": [], "departments": [], "daily": {}}
    types = (r.groupby(["type_id", "type_code", "type_name", "dept_name", "qty"])[["count", "expected"]].sum()
             .reset_index().sort_values("expected", ascending=False))
    daily = r.groupby("date")["expected"].sum()
    return {
        "scheduled_procedures": int(r["count"].sum()),
        "expected_quantity": round(float(r["expected"].sum()), 1),
        "types": [{"procedure_type_id": int(t.type_id), "code": t.type_code, "name": t.type_name, "department": t.dept_name,
                   "count": int(t["count"]), "quantity_per_procedure": float(t.qty), "expected_quantity": round(float(t.expected), 1)}
                  for _, t in types.iterrows()],
        "departments": sorted(r["dept_name"].unique().tolist()),
        "daily": {d.date().isoformat(): round(float(v), 1) for d, v in daily.items()},
    }
