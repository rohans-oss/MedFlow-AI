"""Alert engine. Deterministic and explainable: every alert message states the numbers behind it.

V1 rules: OUT_OF_STOCK, LOW_STOCK (reorder level), EXPIRED, EXPIRING_SOON — unchanged.
V3 adds STOCKOUT_RISK from the current stockout-risk snapshot (MEDIUM/HIGH risk items that are still in stock).
V4 adds SUPPLIER_DELAY for open supplier orders past their expected delivery date.
Same lifecycle for all types: dedupe per (item, type, batch), escalate, auto-resolve when the condition clears.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import business_today
from app.db.base import utcnow
from app.models import (
    ACTIVE_ALERT_STATUSES,
    ACTIVE_ORDER_STATUSES,
    Alert,
    AlertSeverity,
    AlertStatus,
    AlertType,
    Consumable,
    Hospital,
    RiskLevel,
    StockBatch,
    StockoutPrediction,
    SupplierOrder,
)
from app.services.stock import StockLevel, stock_levels


@dataclass
class Condition:
    alert_type: AlertType
    severity: AlertSeverity
    batch_id: int | None
    title: str
    message: str

    @property
    def key(self) -> tuple[str, int | None]:
        return (self.alert_type, self.batch_id)


def _risk_condition(item: Consumable, level: StockLevel, pred: StockoutPrediction | None, today: date) -> Condition | None:
    """V3: forecast-driven stockout risk for items still in stock (out-of-stock items have the V1 alert)."""
    if pred is None or pred.as_of != today or level.usable <= 0 or pred.risk_level == RiskLevel.LOW:
        return None
    unit = item.unit
    high = pred.risk_level == RiskLevel.HIGH
    sev = (AlertSeverity.CRITICAL if high and pred.can_replenish_in_time is False
           else AlertSeverity.HIGH if high else AlertSeverity.MEDIUM)
    parts = [f"{pred.probability:.0%} probability of a stockout within {pred.horizon_days} days."]
    if pred.expected_stockout_date is not None:
        n = pred.days_of_stock_remaining
        parts.append(f"Usable stock {pred.usable_stock:,} {unit} lasts about {n} day{'' if n == 1 else 's'} "
                     f"(to {pred.expected_stockout_date.isoformat()}) at forecast demand.")
    if pred.shortage_quantity > 0:
        parts.append(f"About {pred.shortage_quantity:,} {unit} short over {pred.horizon_days} days without a delivery.")
    if pred.can_replenish_in_time is False:
        parts.append(f"An order placed today ({pred.lead_time_days}-day lead time) would arrive after the projected stockout.")
    elif pred.order_by_date is not None:
        parts.append(f"Order by {pred.order_by_date.isoformat()} (lead time {pred.lead_time_days} days).")
    return Condition(AlertType.STOCKOUT_RISK, sev, None, f"{'High' if high else 'Medium'} stockout risk: {item.name}",
                     " ".join(parts))


def _delay_condition(item: Consumable, level: StockLevel, late: list[SupplierOrder], today: date) -> Condition | None:
    """V4: open supplier orders past their expected delivery date (one alert per item, worst order first)."""
    if not late:
        return None
    worst = max(late, key=lambda o: (today - o.expected_date).days)
    days = (today - worst.expected_date).days
    outstanding = worst.quantity_ordered - worst.quantity_received
    sev = AlertSeverity.HIGH if days >= 3 or level.usable <= item.reorder_level else AlertSeverity.MEDIUM
    extra = f" ({len(late) - 1} more overdue order{'s' if len(late) > 2 else ''})" if len(late) > 1 else ""
    return Condition(
        AlertType.SUPPLIER_DELAY, sev, None, f"Supplier delivery overdue: {item.name}",
        f"Order {worst.reference} from {worst.supplier.name} for {outstanding:,} {item.unit} was expected on "
        f"{worst.expected_date.isoformat()} ({days} day{'s' if days != 1 else ''} ago){extra}. "
        f"Usable stock now {level.usable:,} {item.unit}.",
    )


def _conditions(item: Consumable, level: StockLevel, batches: list[StockBatch], today: date, warn_days: int) -> list[Condition]:
    if not item.is_active:
        return []
    out: list[Condition] = []
    u, unit = level.usable, item.unit
    if u <= 0:
        extra = f" ({level.expired} {unit} on hand are expired and cannot be issued)" if level.expired else ""
        out.append(Condition(
            AlertType.OUT_OF_STOCK, AlertSeverity.CRITICAL, None,
            f"{item.name} is out of stock",
            f"Usable stock is 0 {unit}; reorder level is {item.reorder_level} {unit}{extra}.",
        ))
    elif u <= item.reorder_level:
        sev = AlertSeverity.HIGH if u <= item.reorder_level / 2 else AlertSeverity.MEDIUM
        pct = round(100 * u / item.reorder_level) if item.reorder_level else 0
        out.append(Condition(
            AlertType.LOW_STOCK, sev, None,
            f"{item.name} is below reorder level",
            f"Usable stock {u} {unit} is at {pct}% of the reorder level ({item.reorder_level} {unit}).",
        ))
    for b in batches:
        if b.quantity <= 0 or b.expiry_date is None:
            continue
        days = (b.expiry_date - today).days
        if days < 0:
            out.append(Condition(
                AlertType.EXPIRED, AlertSeverity.HIGH, b.id,
                f"Expired batch: {item.name} lot {b.lot_number}",
                f"{b.quantity} {unit} expired on {b.expiry_date.isoformat()} ({-days} days ago). "
                f"Record wastage to remove it from stock.",
            ))
        elif days <= warn_days:
            sev = AlertSeverity.HIGH if days <= 14 else AlertSeverity.MEDIUM
            out.append(Condition(
                AlertType.EXPIRING_SOON, sev, b.id,
                f"Expiring soon: {item.name} lot {b.lot_number}",
                f"{b.quantity} {unit} expire on {b.expiry_date.isoformat()} (in {days} days). Issue this lot first.",
            ))
    return out


def evaluate(db: Session, hospital_id: int, consumable_ids: list[int] | None = None,
             refresh_risk: bool = True) -> dict[str, int]:
    """Create/update/auto-resolve alerts so the active set matches current conditions.

    With `refresh_risk`, the V3 stockout risk of these items is recomputed first (no-op until models are trained).
    """
    from app.risk import engine as risk_engine  # local import: the risk engine depends on the ML stack

    if refresh_risk:
        risk_engine.refresh(db, hospital_id, consumable_ids)
    hospital = db.get(Hospital, hospital_id)
    today = business_today()
    q = select(Consumable).where(Consumable.hospital_id == hospital_id)
    if consumable_ids is not None:
        q = q.where(Consumable.id.in_(consumable_ids))
    items = db.scalars(q).all()
    ids = [i.id for i in items]
    levels = stock_levels(db, hospital_id, ids)
    risk = risk_engine.latest(db, hospital_id, ids)
    late: dict[int, list[SupplierOrder]] = {}
    for o in db.scalars(select(SupplierOrder).where(SupplierOrder.hospital_id == hospital_id,
                                                    SupplierOrder.consumable_id.in_(ids),
                                                    SupplierOrder.status.in_(ACTIVE_ORDER_STATUSES),
                                                    SupplierOrder.expected_date < today)):
        late.setdefault(o.consumable_id, []).append(o)

    batches_by_item: dict[int, list[StockBatch]] = {}
    for b in db.scalars(select(StockBatch).where(StockBatch.consumable_id.in_(ids), StockBatch.quantity > 0)):
        batches_by_item.setdefault(b.consumable_id, []).append(b)

    active: dict[int, dict[tuple, Alert]] = {}
    for a in db.scalars(
        select(Alert).where(
            Alert.hospital_id == hospital_id,
            Alert.consumable_id.in_(ids),
            Alert.status.in_(ACTIVE_ALERT_STATUSES),
        )
    ):
        active.setdefault(a.consumable_id, {})[(a.alert_type, a.batch_id)] = a

    stats = {"created": 0, "updated": 0, "resolved": 0}
    now = utcnow()
    for item in items:
        lvl = levels.get(item.id, StockLevel())
        conds = _conditions(item, lvl, batches_by_item.get(item.id, []), today, hospital.expiry_warning_days)
        rc = _risk_condition(item, lvl, risk.get(item.id), today) if item.is_active else None
        if rc is not None:
            conds.append(rc)
        dc = _delay_condition(item, lvl, late.get(item.id, []), today) if item.is_active else None
        if dc is not None:
            conds.append(dc)
        existing = active.get(item.id, {})
        wanted = {c.key for c in conds}
        for c in conds:
            a = existing.get(c.key)
            if a is None:
                db.add(Alert(
                    hospital_id=hospital_id, consumable_id=item.id, batch_id=c.batch_id,
                    alert_type=c.alert_type, severity=c.severity, status=AlertStatus.OPEN,
                    title=c.title, message=c.message,
                ))
                stats["created"] += 1
            elif a.message != c.message or a.severity != c.severity:
                escalated = _rank(c.severity) > _rank(a.severity)
                a.message, a.severity, a.title = c.message, c.severity, c.title
                if escalated and a.status == AlertStatus.ACKNOWLEDGED:
                    a.status = AlertStatus.OPEN  # re-open on escalation so it is seen again
                stats["updated"] += 1
        for key, a in existing.items():
            if key not in wanted:
                a.status = AlertStatus.RESOLVED
                a.resolved_at = now
                a.resolved_by_id = None  # auto-resolved by the engine
                stats["resolved"] += 1
    db.flush()
    return stats


_RANKS = {AlertSeverity.LOW: 0, AlertSeverity.MEDIUM: 1, AlertSeverity.HIGH: 2, AlertSeverity.CRITICAL: 3}


def _rank(sev: str) -> int:
    return _RANKS.get(AlertSeverity(sev), 0)


def expiring_window(hospital: Hospital) -> tuple[date, date]:
    today = business_today()
    return today, today + timedelta(days=hospital.expiry_warning_days)
