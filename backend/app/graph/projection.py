"""V6.1 — the graph model, projected from PostgreSQL (read-only; nothing here writes to PostgreSQL).

Nodes (every node: key, hospital_id, name)                 Relationships (evidence on the relationship)
  Hospital                                                  Hospital   -HAS_DEPARTMENT-> Department
  Department                                                Hospital   -HAS_ITEM->       Item
  Category                                                  Hospital   -HAS_SUPPLIER->   Supplier
  Item            (stock position)                          Item       -IN_CATEGORY->    Category
  Procedure       (procedure type + schedule counts)        Department -USES->           Item        issued units (90 d)
  Supplier        (V4 reliability)                          Department -PERFORMS->       Procedure   scheduled / completed
  Batch           (usable or expired, quantity > 0)         Procedure  -USES_ITEM->      Item        kit qty, units next 14 d
  Forecast        (served V2 model, next 7/14/30 d)         Supplier   -SUPPLIES->       Item        price, MOQ, lead time,
  StockoutRisk    (latest V3 snapshot)                                                               delivery evidence (k of n)
  SupplierOrder   (V4 log: last 365 d or still open)        Item       -HAS_BATCH->      Batch
  ProcurementRecommendation (V5: pending + decided 90 d)    Batch      -FROM_SUPPLIER->  Supplier
                                                            Item       -HAS_FORECAST->   Forecast
                                                            Item       -HAS_RISK->       StockoutRisk
                                                            Supplier   -HAS_ORDER->      SupplierOrder
                                                            SupplierOrder -FOR_ITEM->    Item
                                                            ProcurementRecommendation -FOR_ITEM-> Item
                                                            ProcurementRecommendation -USES_SUPPLIER-> Supplier
                                                            ProcurementRecommendation -RECORDED_AS-> SupplierOrder

Keys are "<hospital>:<Label>:<postgres id>" so a node always points back to its PostgreSQL row. Property values are
flat (strings, numbers, booleans, lists of strings) — dates as ISO strings — so every graph backend stores them alike.
Counts only: no patient data exists in MedFlow, and none is projected.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import business_today
from app.ml.data import _local_date
from app.models import (
    ACTIVE_ORDER_STATUSES,
    Consumable,
    ConsumableCategory,
    Department,
    Forecast,
    Hospital,
    ModelItemMetric,
    ModelVersion,
    MovementType,
    ProcedureItemMapping,
    ProcedureSchedule,
    ProcedureStatus,
    ProcedureType,
    ProcurementRecommendation,
    RecommendationStatus,
    StockBatch,
    StockMovement,
    StockoutPrediction,
    Supplier,
    SupplierOrder,
    SupplierProduct,
)
from app.supplier_intel.metrics import MIN_ORDERS, OrderRec, p_within, summarize

LABELS = ["Hospital", "Department", "Category", "Item", "Procedure", "Supplier", "Batch", "Forecast", "StockoutRisk",
          "SupplierOrder", "ProcurementRecommendation"]

# (type, from label, to label, meaning) — the schema shown on the Knowledge graph page
RELATIONSHIPS = [
    ("HAS_DEPARTMENT", "Hospital", "Department", "department of the hospital"),
    ("HAS_ITEM", "Hospital", "Item", "active catalogue item"),
    ("HAS_SUPPLIER", "Hospital", "Supplier", "supplier of the hospital"),
    ("IN_CATEGORY", "Item", "Category", "item category"),
    ("USES", "Department", "Item", "units issued to the department in the last 90 days (net of returns)"),
    ("PERFORMS", "Department", "Procedure", "procedure type run by the department; scheduled next 14 days, completed last 90"),
    ("USES_ITEM", "Procedure", "Item", "kit quantity per procedure (active mapping) and units implied by the next 14 days"),
    ("SUPPLIES", "Supplier", "Item", "catalogue price, MOQ, quoted lead time; V4 delivery evidence for this item"),
    ("HAS_BATCH", "Item", "Batch", "batch with stock (usable or expired)"),
    ("FROM_SUPPLIER", "Batch", "Supplier", "supplier the batch was received from"),
    ("HAS_FORECAST", "Item", "Forecast", "served V2 demand forecast"),
    ("HAS_RISK", "Item", "StockoutRisk", "latest V3 stockout-risk snapshot"),
    ("HAS_ORDER", "Supplier", "SupplierOrder", "order placed with the supplier (V4 log)"),
    ("FOR_ITEM", "SupplierOrder", "Item", "item ordered"),
    ("FOR_ITEM", "ProcurementRecommendation", "Item", "item the V5 recommendation is for"),
    ("USES_SUPPLIER", "ProcurementRecommendation", "Supplier", "supplier line of the recommendation (quantity, price)"),
    ("RECORDED_AS", "ProcurementRecommendation", "SupplierOrder", "order recorded when the recommendation was approved"),
]

WINDOW_ORDERS = 365
WINDOW_USAGE = 90
WINDOW_SCHEDULE = 14
WINDOW_RECS = 90


@dataclass
class Projection:
    hospital_id: int
    nodes: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))
    edges: dict[tuple[str, str, str], list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))

    def key(self, label: str, pk: int | str) -> str:
        return f"{self.hospital_id}:{label}:{pk}"

    def node(self, label: str, pk: int | str, **props) -> str:
        k = self.key(label, pk)
        self.nodes[label].append({"key": k, "props": {"pg_id": pk, **{a: _flat(v) for a, v in props.items()}}})
        return k

    def edge(self, rel: str, a_label: str, a_pk, b_label: str, b_pk, **props) -> None:
        self.edges[(rel, a_label, b_label)].append(
            {"a": self.key(a_label, a_pk), "b": self.key(b_label, b_pk), "props": {k: _flat(v) for k, v in props.items()}})

    def node_counts(self) -> dict[str, int]:
        return {lb: len(self.nodes.get(lb, [])) for lb in LABELS}

    def edge_counts(self) -> dict[str, int]:
        out: dict[str, int] = defaultdict(int)
        for (rel, a, b), rows in self.edges.items():
            out[f"{a}-{rel}->{b}"] += len(rows)
        return dict(out)


def _flat(v: Any) -> Any:
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float):
        return round(v, 4)
    if hasattr(v, "is_finite"):  # Decimal
        return float(v)
    if isinstance(v, list | tuple):
        return [str(x) for x in v] or None  # (an empty list would be typeless in some stores)
    return v


def _rec(o: SupplierOrder) -> OrderRec:
    return OrderRec(o.id, o.supplier_id, o.consumable_id, o.ordered_date, o.expected_date, o.quoted_lead_time_days,
                    o.quantity_ordered, o.quantity_received, o.status, o.first_delivery_date, o.completed_date,
                    float(o.unit_price))


def build(db: Session, hospital_id: int) -> Projection:
    """Project one hospital's operational data into nodes and relationships."""
    from app.risk import engine as risk_engine  # V3 read-only

    today = business_today()
    p = Projection(hospital_id)
    h = db.get(Hospital, hospital_id)
    p.node("Hospital", h.id, name=h.name, code=h.code, city=h.city, is_demo=bool(getattr(h, "is_demo", False)))

    depts = list(db.scalars(select(Department).where(Department.hospital_id == hospital_id).order_by(Department.id)))
    for d in depts:
        p.node("Department", d.id, name=d.name, code=d.code, is_active=d.is_active)
        p.edge("HAS_DEPARTMENT", "Hospital", h.id, "Department", d.id)

    for c in db.scalars(select(ConsumableCategory).where(ConsumableCategory.hospital_id == hospital_id)):
        p.node("Category", c.id, name=c.name)

    items = {c.id: c for c in db.scalars(select(Consumable).where(Consumable.hospital_id == hospital_id,
                                                                 Consumable.is_active.is_(True)).order_by(Consumable.id))}
    ids = list(items)
    usable: dict[int, int] = defaultdict(int)
    expired: dict[int, int] = defaultdict(int)
    batches = list(db.scalars(select(StockBatch).where(StockBatch.consumable_id.in_(ids), StockBatch.quantity > 0)
                              .order_by(StockBatch.id))) if ids else []
    for b in batches:
        ok = b.expiry_date is None or b.expiry_date >= today
        (usable if ok else expired)[b.consumable_id] += b.quantity

    risks = risk_engine.latest(db, hospital_id, ids) if ids else {}
    for cid, c in items.items():
        r = risks.get(cid)
        p.node("Item", cid, name=c.name, sku=c.sku, unit=c.unit, reorder_level=c.reorder_level, max_level=c.max_level,
               unit_cost=c.unit_cost, usable_stock=usable.get(cid, 0), expired_stock=expired.get(cid, 0),
               risk_level=r.risk_level if r else None, risk_probability=r.probability if r else None)
        p.edge("HAS_ITEM", "Hospital", h.id, "Item", cid)
        if c.category_id:
            p.edge("IN_CATEGORY", "Item", cid, "Category", c.category_id)

    for b in batches:
        ok = b.expiry_date is None or b.expiry_date >= today
        p.node("Batch", b.id, name=b.lot_number, lot=b.lot_number, quantity=b.quantity, expiry_date=b.expiry_date,
               status="usable" if ok else "expired", received_date=_local_date(b.received_at) if b.received_at else None)
        p.edge("HAS_BATCH", "Item", b.consumable_id, "Batch", b.id)
        if b.supplier_id:
            p.edge("FROM_SUPPLIER", "Batch", b.id, "Supplier", b.supplier_id)

    # ---------------- suppliers + SUPPLIES with V4 delivery evidence
    since = today - timedelta(days=WINDOW_ORDERS)
    orders = list(db.scalars(select(SupplierOrder).where(SupplierOrder.hospital_id == hospital_id)
                             .order_by(SupplierOrder.id)))
    window_orders = [o for o in orders if o.ordered_date >= since or o.status in ACTIVE_ORDER_STATUSES]
    recs = [_rec(o) for o in orders if o.ordered_date >= since]
    hosp = summarize(recs, today)
    prior = hosp["otif_rate"] if hosp["otif_rate"] is not None else 0.8
    by_sup: dict[int, list[OrderRec]] = defaultdict(list)
    for r in recs:
        by_sup[r.supplier_id].append(r)
    sups = list(db.scalars(select(Supplier).where(Supplier.hospital_id == hospital_id).order_by(Supplier.id)))
    sup_rate: dict[int, float] = {}
    for s in sups:
        m = summarize(by_sup[s.id], today, prior) if by_sup.get(s.id) else None
        sup_rate[s.id] = m["otif_rate"] if m and m["otif_rate"] is not None else prior
        p.node("Supplier", s.id, name=s.name, code=s.code, city=s.city, is_active=s.is_active,
               default_lead_time_days=s.default_lead_time_days,
               reliability_score=m["reliability_score"] if m else None, grade=m["grade"] if m else None,
               otif_rate=m["otif_rate"] if m else None, on_time_rate=m["on_time_rate"] if m else None,
               orders=m["orders"] if m else 0, cancellation_rate=m["cancellation_rate"] if m else None,
               lead_time_median=m["lead_time_median"] if m else None, lead_time_p90=m["lead_time_p90"] if m else None)
        p.edge("HAS_SUPPLIER", "Hospital", h.id, "Supplier", s.id)

    deadlines: dict[int, int] = {}
    for cid, r in risks.items():
        if r.as_of == today and r.expected_stockout_date is not None and r.usable_stock > 0:
            deadlines[cid] = (r.expected_stockout_date - today).days
    for sp in db.scalars(select(SupplierProduct).join(Supplier).where(Supplier.hospital_id == hospital_id,
                                                                       SupplierProduct.consumable_id.in_(ids))
                         .order_by(SupplierProduct.id)) if ids else []:
        item_recs = [r for r in by_sup.get(sp.supplier_id, []) if r.consumable_id == sp.consumable_id]
        im = summarize(item_recs, today, sup_rate.get(sp.supplier_id, prior)) if item_recs else None
        use_item = im is not None and im["decided"] >= MIN_ORDERS
        basis = item_recs if use_item else by_sup.get(sp.supplier_id, [])
        dl = deadlines.get(sp.consumable_id)
        ev = p_within(basis, dl, today) if dl is not None and basis else None
        p.edge("SUPPLIES", "Supplier", sp.supplier_id, "Item", sp.consumable_id,
               unit_price=sp.unit_price, moq=sp.moq, quoted_lead_time_days=sp.lead_time_days, is_preferred=sp.is_preferred,
               item_orders=len(item_recs), item_on_time_rate=im["on_time_rate"] if im else None,
               item_otif_rate=im["otif_rate"] if im else None,
               lead_time_median=im["lead_time_median"] if im else None, lead_time_p90=im["lead_time_p90"] if im else None,
               evidence_basis="item" if use_item else ("supplier" if basis else "none"),
               window_days=dl, window_k=ev["k"] if ev else None, window_n=ev["n"] if ev else None)

    # ---------------- procedures (counts only)
    ptypes = list(db.scalars(select(ProcedureType).where(ProcedureType.hospital_id == hospital_id)
                             .order_by(ProcedureType.id)))
    sched: dict[int, int] = defaultdict(int)
    done: dict[int, int] = defaultdict(int)
    rows = db.execute(select(ProcedureSchedule.procedure_type_id, ProcedureSchedule.status,
                             ProcedureSchedule.scheduled_date, ProcedureSchedule.count)
                      .where(ProcedureSchedule.hospital_id == hospital_id,
                             ProcedureSchedule.scheduled_date >= today - timedelta(days=WINDOW_USAGE),
                             ProcedureSchedule.scheduled_date < today + timedelta(days=WINDOW_SCHEDULE))).all()
    for tid, st, d, n in rows:
        if st == ProcedureStatus.SCHEDULED and d >= today:
            sched[tid] += n
        elif st == ProcedureStatus.COMPLETED and d < today:
            done[tid] += n
    for t in ptypes:
        p.node("Procedure", t.id, name=t.name, code=t.code, is_active=t.is_active, is_synthetic=t.is_synthetic,
               department=t.department.name if t.department else None,
               scheduled_next_14=sched.get(t.id, 0) if t.is_active else 0, completed_last_90=done.get(t.id, 0))
        p.edge("PERFORMS", "Department", t.department_id, "Procedure", t.id,
               scheduled_next_14=sched.get(t.id, 0) if t.is_active else 0, completed_last_90=done.get(t.id, 0))
    for m in db.scalars(select(ProcedureItemMapping).where(ProcedureItemMapping.hospital_id == hospital_id,
                                                           ProcedureItemMapping.is_active.is_(True))
                        .order_by(ProcedureItemMapping.id)):
        if m.consumable_id not in items:
            continue
        n14 = sched.get(m.procedure_type_id, 0) if m.procedure_type.is_active else 0
        p.edge("USES_ITEM", "Procedure", m.procedure_type_id, "Item", m.consumable_id,
               quantity_per_procedure=m.quantity_per_procedure,
               units_next_14=round(float(m.quantity_per_procedure) * n14, 1), is_synthetic=m.is_synthetic)

    # ---------------- department consumption (net issues, 90 days)
    if ids:
        since_ts = today - timedelta(days=WINDOW_USAGE)
        use = db.execute(select(StockMovement.department_id, StockMovement.consumable_id,
                                func.sum(StockMovement.quantity), func.max(StockMovement.created_at))
                         .where(StockMovement.hospital_id == hospital_id, StockMovement.consumable_id.in_(ids),
                                StockMovement.department_id.is_not(None),
                                StockMovement.movement_type.in_([MovementType.ISSUE, MovementType.RETURN]),
                                StockMovement.created_at >= since_ts)
                         .group_by(StockMovement.department_id, StockMovement.consumable_id)).all()
        totals: dict[int, float] = defaultdict(float)
        for _d, cid, q, _t in use:
            totals[cid] += max(-float(q), 0.0)
        for did, cid, q, last in sorted(use, key=lambda r: (r[0], r[1])):
            units = max(-float(q), 0.0)
            if units <= 0:
                continue
            p.edge("USES", "Department", did, "Item", cid, units_90=round(units),
                   share=units / totals[cid] if totals[cid] else None, last_used=_local_date(last) if last else None)

    # ---------------- forecast (served V2 model)
    _, fc = risk_engine.active_models(db, hospital_id)
    if fc is None:
        fc = db.scalar(select(ModelVersion).where(ModelVersion.hospital_id == hospital_id, ModelVersion.is_active.is_(True)))
    if fc is not None and ids:
        sums: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
        for cid, d, v in db.execute(select(Forecast.consumable_id, Forecast.forecast_date, Forecast.predicted)
                                    .where(Forecast.model_version_id == fc.id, Forecast.forecast_date >= today,
                                           Forecast.forecast_date < today + timedelta(days=30))):
            k = (d - today).days
            for i, hz in enumerate((7, 14, 30)):
                if k < hz:
                    sums[cid][i] += float(v)
        wape = {m.consumable_id: m.wape for m in db.scalars(select(ModelItemMetric).where(
            ModelItemMetric.model_version_id == fc.id))}
        src = "V2B procedure-aware" if "v2b" in (fc.name or "") else "V2A"
        for cid, (f7, f14, f30) in sums.items():
            if cid not in items:
                continue
            p.node("Forecast", f"{fc.id}-{cid}", name=f"{fc.name} · {items[cid].sku}", model=fc.name, source=src,
                   data_end=fc.data_end, forecast_7=round(f7, 1), forecast_14=round(f14, 1), forecast_30=round(f30, 1),
                   item_wape=wape.get(cid))
            p.edge("HAS_FORECAST", "Item", cid, "Forecast", f"{fc.id}-{cid}")

    # ---------------- stockout risk (latest V3 snapshot)
    for cid, r in risks.items():
        if cid not in items:
            continue
        p.node("StockoutRisk", r.id, name=f"{r.risk_level} · {items[cid].sku}", level=r.risk_level,
               probability=r.probability, as_of=r.as_of, usable_stock=r.usable_stock,
               days_of_stock_remaining=r.days_of_stock_remaining, expected_stockout_date=r.expected_stockout_date,
               shortage_14=r.shortage_quantity, forecast_14=r.forecast_14, lead_time_days=r.lead_time_days,
               order_by_date=r.order_by_date, source=r.probability_source, reasons=list(r.reasons or [])[:6])
        p.edge("HAS_RISK", "Item", cid, "StockoutRisk", r.id)

    # ---------------- supplier orders (V4)
    for o in window_orders:
        if o.consumable_id not in items:
            continue
        overdue = (today - o.expected_date).days if o.status in ACTIVE_ORDER_STATUSES and today > o.expected_date else 0
        late = None if o.first_delivery_date is None else max((o.first_delivery_date - o.expected_date).days, 0)
        p.node("SupplierOrder", o.id, name=o.reference, reference=o.reference, status=o.status, ordered_date=o.ordered_date,
               expected_date=o.expected_date, quantity_ordered=o.quantity_ordered, quantity_received=o.quantity_received,
               outstanding=o.quantity_ordered - o.quantity_received if o.status in ACTIVE_ORDER_STATUSES else 0,
               unit_price=o.unit_price, first_delivery_date=o.first_delivery_date, days_late=late, overdue_days=overdue,
               is_open=o.status in ACTIVE_ORDER_STATUSES, is_synthetic=o.is_synthetic)
        p.edge("HAS_ORDER", "Supplier", o.supplier_id, "SupplierOrder", o.id)
        p.edge("FOR_ITEM", "SupplierOrder", o.id, "Item", o.consumable_id)
    projected_orders = {o.id for o in window_orders if o.consumable_id in items}

    # ---------------- procurement recommendations (V5)
    rsince = today - timedelta(days=WINDOW_RECS)
    recq = select(ProcurementRecommendation).where(ProcurementRecommendation.hospital_id == hospital_id,
                                                   ProcurementRecommendation.status != RecommendationStatus.SUPERSEDED,
                                                   ProcurementRecommendation.as_of >= rsince)
    all_orders = {o.id: o for o in orders}
    rec_orders: dict[int, list[int]] = defaultdict(list)
    for o in orders:
        if o.recommendation_id:
            rec_orders[o.recommendation_id].append(o.id)
    for r in db.scalars(recq.order_by(ProcurementRecommendation.id)):
        if r.consumable_id not in items:
            continue
        none = next((s for s in r.scenarios if s.get("kind") == "none"), None)
        p.node("ProcurementRecommendation", r.id, name=f"#{r.id} · {items[r.consumable_id].sku}", status=r.status,
               as_of=r.as_of, quantity=r.quantity, purchase_value=r.purchase_value, expected_cost=r.expected_cost,
               p_stockout=(r.metrics or {}).get("p_stockout"),
               no_order_p_stockout=none["metrics"]["p_stockout"] if none else None,
               headline=(r.explanation or [""])[0], modified=r.modified, decision_reason=r.decision_reason,
               order_by_date=r.order_by_date, need_by_date=r.need_by_date)
        p.edge("FOR_ITEM", "ProcurementRecommendation", r.id, "Item", r.consumable_id)
        for ln in (r.final_lines if r.status == RecommendationStatus.APPROVED and r.final_lines is not None else r.lines):
            p.edge("USES_SUPPLIER", "ProcurementRecommendation", r.id, "Supplier", ln["supplier_id"],
                   quantity=ln["quantity"], unit_price=ln["unit_price"])
        for oid in rec_orders.get(r.id, []):
            if oid in projected_orders and oid in all_orders:
                p.edge("RECORDED_AS", "ProcurementRecommendation", r.id, "SupplierOrder", oid)
    return p


def fingerprint(db: Session, hospital_id: int) -> dict[str, str]:
    """A cheap summary of everything the projection reads; if it changes, the graph is behind PostgreSQL."""
    today = business_today()

    def agg(q) -> str:
        row = db.execute(q).one()
        return "|".join("" if v is None else str(v) for v in row)

    items = select(Consumable.id).where(Consumable.hospital_id == hospital_id)
    return {
        "day": today.isoformat(),
        "departments": agg(select(func.count(Department.id), func.max(Department.updated_at))
                           .where(Department.hospital_id == hospital_id)),
        "categories": agg(select(func.count(ConsumableCategory.id), func.max(ConsumableCategory.updated_at))
                          .where(ConsumableCategory.hospital_id == hospital_id)),
        "items": agg(select(func.count(Consumable.id), func.max(Consumable.updated_at))
                     .where(Consumable.hospital_id == hospital_id)),
        "batches": agg(select(func.count(StockBatch.id), func.max(StockBatch.updated_at))
                       .where(StockBatch.consumable_id.in_(items))),
        "movements": agg(select(func.count(StockMovement.id), func.max(StockMovement.id))
                         .where(StockMovement.hospital_id == hospital_id)),
        "suppliers": agg(select(func.count(Supplier.id), func.max(Supplier.updated_at))
                         .where(Supplier.hospital_id == hospital_id)),
        "catalogue": agg(select(func.count(SupplierProduct.id), func.max(SupplierProduct.updated_at))
                         .where(SupplierProduct.consumable_id.in_(items))),
        "orders": agg(select(func.count(SupplierOrder.id), func.max(SupplierOrder.updated_at))
                      .where(SupplierOrder.hospital_id == hospital_id)),
        "procedures": agg(select(func.count(ProcedureType.id), func.max(ProcedureType.updated_at))
                          .where(ProcedureType.hospital_id == hospital_id)),
        "mappings": agg(select(func.count(ProcedureItemMapping.id), func.max(ProcedureItemMapping.updated_at))
                        .where(ProcedureItemMapping.hospital_id == hospital_id)),
        "schedule": agg(select(func.count(ProcedureSchedule.id), func.max(ProcedureSchedule.updated_at))
                        .where(ProcedureSchedule.hospital_id == hospital_id)),
        "risk": agg(select(func.max(StockoutPrediction.id)).where(StockoutPrediction.hospital_id == hospital_id)),
        "forecast": agg(select(func.max(ModelVersion.id)).where(ModelVersion.hospital_id == hospital_id,
                                                                ModelVersion.is_active.is_(True))),
        "recommendations": agg(select(func.count(ProcurementRecommendation.id), func.max(ProcurementRecommendation.id),
                                      func.max(ProcurementRecommendation.decided_at))
                               .where(ProcurementRecommendation.hospital_id == hospital_id)),
    }
