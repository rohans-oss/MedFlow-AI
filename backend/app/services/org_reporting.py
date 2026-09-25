"""V8 — organization-level reporting: explicit aggregates over the hospitals of ONE organization.

Nothing here recomputes V1–V7 results; each hospital's figures are read from that hospital's own stored results
(latest V3 risk snapshot per item, active alerts, PENDING V5 recommendations, V4 scorecards on its own order log).
Every row says which hospital it came from, totals are plain sums over the listed hospitals, and supplier
performance is shown side by side per hospital — never pooled into one cross-hospital score.
"""

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import (
    ACTIVE_ALERT_STATUSES,
    Alert,
    Consumable,
    Hospital,
    HospitalMembership,
    Organization,
    ProcurementRecommendation,
    RecommendationStatus,
    StockoutPrediction,
    TenantStatus,
)
from app.supplier_intel import service as supplier_service


def _latest_risk(db: Session, hospital_ids: list[int]) -> dict[int, dict]:
    """Per hospital: counts of the newest V3 snapshot per active item (as stored — reporting never refreshes risk)."""
    latest = (select(func.max(StockoutPrediction.id).label("id"))
              .join(Consumable, Consumable.id == StockoutPrediction.consumable_id)
              .where(StockoutPrediction.hospital_id.in_(hospital_ids), Consumable.is_active.is_(True))
              .group_by(StockoutPrediction.consumable_id).subquery())
    rows = db.execute(select(StockoutPrediction.hospital_id, StockoutPrediction.risk_level,
                             func.count(), func.max(StockoutPrediction.as_of))
                      .join(latest, latest.c.id == StockoutPrediction.id)
                      .group_by(StockoutPrediction.hospital_id, StockoutPrediction.risk_level)).all()
    out: dict[int, dict] = {h: {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "as_of": None} for h in hospital_ids}
    for hid, level, n, as_of in rows:
        out[hid][level] = n
        cur = out[hid]["as_of"]
        out[hid]["as_of"] = as_of if cur is None or (as_of and as_of > cur) else cur
    return out


def _count_by(db: Session, col, where, hospital_ids: list[int]) -> dict[int, int]:
    rows = db.execute(select(col, func.count()).where(col.in_(hospital_ids), *where).group_by(col)).all()
    return {h: 0 for h in hospital_ids} | {h: n for h, n in rows}


def overview(db: Session, org: Organization) -> dict:
    hospitals = db.scalars(select(Hospital).where(Hospital.organization_id == org.id).order_by(Hospital.name)).all()
    ids = [h.id for h in hospitals]
    if not ids:
        return {"organization": org, "hospitals": [], "totals": {"hospitals": 0}, "contributing_hospitals": [],
                "supplier_comparison": [], "risk_as_of_range": None, "notes": []}
    risk = _latest_risk(db, ids)
    items = _count_by(db, Consumable.hospital_id, [Consumable.is_active.is_(True)], ids)
    alerts = _count_by(db, Alert.hospital_id, [Alert.status.in_(ACTIVE_ALERT_STATUSES)], ids)
    critical = _count_by(db, Alert.hospital_id, [Alert.status.in_(ACTIVE_ALERT_STATUSES), Alert.severity == "CRITICAL"], ids)
    members = _count_by(db, HospitalMembership.hospital_id, [HospitalMembership.status == TenantStatus.ACTIVE], ids)
    pend = {h: {"count": 0, "with_order": 0, "value": 0.0} for h in ids}
    for hid, n, with_order, value in db.execute(
            select(ProcurementRecommendation.hospital_id, func.count(),
                   func.sum(case((ProcurementRecommendation.quantity > 0, 1), else_=0)),
                   func.coalesce(func.sum(ProcurementRecommendation.purchase_value), 0))
            .where(ProcurementRecommendation.hospital_id.in_(ids),
                   ProcurementRecommendation.status == RecommendationStatus.PENDING)
            .group_by(ProcurementRecommendation.hospital_id)).all():
        pend[hid] = {"count": n, "with_order": int(with_order or 0), "value": float(value or 0)}

    rows, suppliers_by_code = [], {}
    for h in hospitals:
        sc = supplier_service.scorecards(db, h.id)
        hosp_otif = sc["hospital"]["otif_rate"]
        for s in sc["suppliers"]:
            m = s["metrics"]
            suppliers_by_code.setdefault(s["code"], {"code": s["code"], "names": set(), "by_hospital": []})
            suppliers_by_code[s["code"]]["names"].add(s["name"])
            suppliers_by_code[s["code"]]["by_hospital"].append({
                "hospital_id": h.id, "hospital": h.name, "orders": m["decided"] if m else 0,
                "otif_rate": m["otif_rate"] if m else None, "reliability_score": m["reliability_score"] if m else None,
                "grade": m["grade"] if m else None})
        r = risk[h.id]
        rows.append({
            "hospital_id": h.id, "hospital": h.name, "code": h.code, "status": h.status, "city": h.city,
            "active_members": members[h.id], "items": items[h.id],
            "risk_high": r["HIGH"], "risk_medium": r["MEDIUM"], "risk_low": r["LOW"], "risk_as_of": r["as_of"],
            "active_alerts": alerts[h.id], "critical_alerts": critical[h.id],
            "pending_recommendations": pend[h.id]["count"], "pending_with_order": pend[h.id]["with_order"],
            "pending_purchase_value": round(pend[h.id]["value"], 2),
            "supplier_otif_rate": hosp_otif, "supplier_orders_decided": sc["hospital"]["decided"],
        })
    totals = {k: sum(r[k] for r in rows) for k in ("active_members", "items", "risk_high", "risk_medium", "risk_low",
                                                    "active_alerts", "critical_alerts", "pending_recommendations",
                                                    "pending_with_order")}
    totals["pending_purchase_value"] = round(sum(r["pending_purchase_value"] for r in rows), 2)
    totals["hospitals"] = len(rows)
    totals["hospitals_with_high_risk"] = sum(1 for r in rows if r["risk_high"] > 0)
    shared = [{"code": v["code"], "names": sorted(v["names"]), "by_hospital": v["by_hospital"]}
              for v in suppliers_by_code.values() if len(v["by_hospital"]) > 1]
    shared.sort(key=lambda x: x["code"])
    as_of = [r["risk_as_of"] for r in rows if r["risk_as_of"]]
    return {
        "organization": org,
        "hospitals": rows,
        "totals": totals,
        "contributing_hospitals": [r["hospital"] for r in rows],
        "supplier_comparison": shared,
        "risk_as_of_range": [min(as_of), max(as_of)] if as_of else None,
        "notes": [
            "Each row is computed from that hospital's own data only; totals are sums over the hospitals listed.",
            "Risk counts are each hospital's latest stored V3 snapshot (reporting does not refresh risk).",
            "Supplier performance is each hospital's own V4 OTIF on its own orders — shown side by side, never pooled. "
            "Suppliers are matched across hospitals by supplier code (each hospital keeps its own supplier records).",
        ],
    }
