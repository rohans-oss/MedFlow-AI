"""V4 — supplier intelligence: metric definitions, score, evidence, backtest (no look-ahead), order log API,
receiving against orders, SUPPLIER_DELAY alerts, item comparison linked to V3, isolation."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.core.security import business_today
from app.models import (
    Alert,
    AlertStatus,
    AlertType,
    Consumable,
    RiskModelVersion,
    StockoutPrediction,
    Supplier,
    SupplierDelivery,
    SupplierOrder,
    SupplierProduct,
)
from app.services import supplier_orders as so
from app.supplier_intel import metrics as M
from app.supplier_intel.predict import backtest
from tests.conftest import as_role

D0 = date(2026, 1, 1)


def rec(i, sup=1, item=1, ordered=D0, quoted=3, lead=3, qty=100, got=100, status="RECEIVED", price=10.0, done=None):
    first = None if lead is None else ordered + timedelta(days=lead)
    return M.OrderRec(i, sup, item, ordered, ordered + timedelta(days=quoted), quoted, qty, got, status, first,
                      done if done is not None else first, price)


# ---------------------------------------------------------------- definitions (pure)


def test_otif_on_time_and_decided_definitions():
    today = D0 + timedelta(days=30)
    assert rec(1).otif(today) is True
    assert rec(2, lead=5).otif(today) is False and rec(2, lead=5).on_time(today) is False
    short = rec(3, got=80)  # closed short, on time
    assert short.on_time(today) is True and short.otif(today) is False
    cancelled = rec(4, lead=None, got=0, status="CANCELLED", done=D0 + timedelta(days=2))
    assert cancelled.decided(today) and cancelled.otif(today) is False and cancelled.on_time(today) is None
    overdue = rec(5, lead=None, got=0, status="OPEN")
    assert overdue.decided(today) and overdue.otif(today) is False and overdue.on_time(today) is False
    not_due = rec(6, ordered=today - timedelta(days=1), lead=None, got=0, status="OPEN")
    assert not not_due.decided(today) and not_due.otif(today) is None and not_due.on_time(today) is None
    # a partial delivery on time, balance later: on time (first delivery) but not OTIF
    late_balance = rec(7, lead=2, done=D0 + timedelta(days=9))
    assert late_balance.on_time(today) and late_balance.otif(today) is False


def test_summary_components_are_measured_rates():
    today = D0 + timedelta(days=60)
    orders = [rec(i, ordered=D0 + timedelta(days=i), lead=3) for i in range(8)]
    orders += [rec(8, lead=6), rec(9, got=50),
               rec(10, lead=None, got=0, status="CANCELLED", done=D0 + timedelta(days=1))]
    s = M.summarize(orders, today)
    assert s["decided"] == 11 and s["otif_successes"] == 8 and s["otif_rate"] == pytest.approx(8 / 11)
    assert s["cancellation_rate"] == pytest.approx(1 / 11)
    assert s["on_time_rate"] == pytest.approx(9 / 10) and s["avg_days_late"] == 3
    assert s["lead_time_median"] == 3 and s["quoted_lead_time"] == 3
    assert s["fill_rate"] == pytest.approx((9 * 100 + 50) / 1000)
    lo, hi = s["otif_ci_low"], s["otif_ci_high"]
    assert lo < 8 / 11 < hi
    # score = shrunk OTIF: with prior = its own rate the score equals the raw rate
    assert s["reliability_score"] == pytest.approx(100 * 8 / 11, abs=0.1)


def test_small_samples_are_shrunk_and_flagged():
    today = D0 + timedelta(days=60)
    perfect_two = M.summarize([rec(1), rec(2)], today, prior=0.6)
    assert perfect_two["otif_rate"] == 1.0 and perfect_two["limited_evidence"]
    assert perfect_two["reliability_score"] == pytest.approx(100 * (2 + 5 * 0.6) / 7, abs=0.1)
    many = M.summarize([rec(i) for i in range(50)], today, prior=0.6)
    assert many["reliability_score"] > perfect_two["reliability_score"] and not many["limited_evidence"]
    assert M.grade(91) == "A" and M.grade(80) == "B" and M.grade(70) == "C" and M.grade(10) == "D"


def test_price_stability_counts_moves_above_two_percent():
    today = D0 + timedelta(days=60)
    prices = [10.0, 10.1, 10.9, 10.9, 11.5]  # moves: +1 %, +7.9 %, 0 %, +5.5 %
    s = M.price_stats([rec(i, ordered=D0 + timedelta(days=i), price=p) for i, p in enumerate(prices)])
    assert s["price_pairs"] == 4 and s["price_changes"] == 2 and s["price_stability"] == 0.5
    assert s["price_change_pct"] == pytest.approx(0.15)
    assert M.summarize([rec(1)], today)["price_stability"] is None


def test_p_within_handles_cancellations_open_orders_and_transit():
    today = D0 + timedelta(days=40)
    orders = [rec(1, lead=2), rec(2, lead=4), rec(3, lead=9),
              rec(4, lead=None, got=0, status="CANCELLED"),
              rec(5, lead=None, got=0, status="OPEN", ordered=today - timedelta(days=10)),  # not in 5 days: known miss
              rec(6, lead=None, got=0, status="OPEN", ordered=today - timedelta(days=1))]  # outcome unknown
    e = M.p_within(orders, 5, today)
    assert (e["k"], e["n"]) == (2, 5) and e["p"] == pytest.approx(2.5 / 6)
    # already 3 days in transit: only orders that took > 3 days are comparable
    t = M.p_within(orders, 5, today, elapsed=3)
    assert (t["k"], t["n"]) == (1, 4)


def test_backtest_uses_only_outcomes_known_on_the_order_date():
    """No look-ahead: changing an order's outcome cannot change predictions for orders placed before it arrived."""
    orders = []
    for i in range(80):
        sup = 1 + i % 2
        lead = 3 if sup == 1 else (3 if i % 3 else 8)
        orders.append(rec(i, sup=sup, ordered=D0 + timedelta(days=2 * i), quoted=3, lead=lead))
    today = D0 + timedelta(days=200)
    before: list = []
    b = backtest(orders, today, warmup_days=30, rows_out=before)
    assert b["n_test"] > 20 and b["delay"]["supplier_rate"]["roc_auc"] > 0.5
    late = orders[-1]
    changed = orders[:-1] + [rec(late.id, sup=late.supplier_id, ordered=late.ordered, lead=20)]
    after: list = []
    backtest(changed, today, warmup_days=30, rows_out=after)
    cutoff = late.ordered + timedelta(days=late.lead_time)
    for x, y in zip(before, after, strict=True):
        o = next(r for r in orders if r.id == x[0])
        if o.ordered <= cutoff:
            assert x == y  # the changed outcome was not yet known
    # supplier 2 is late 1 in 3 times: its history predicts a longer p90 than the quoted lead time
    assert b["lead_time"]["supplier_history"]["p90_coverage"] > b["lead_time"]["quoted"]["p90_coverage"]


# ---------------------------------------------------------------- order log + receiving (DB / API)


def _setup(db, world):
    h, gloves, a = world["hospital"], world["gloves"], world["supplier"]
    b = Supplier(hospital_id=h.id, code="CHEAP", name="Cheap Supplier", default_lead_time_days=2)
    db.add(b)
    db.flush()
    db.add_all([SupplierProduct(supplier_id=a.id, consumable_id=gloves.id, unit_price=42, lead_time_days=3, moq=10,
                                is_preferred=True),
                SupplierProduct(supplier_id=b.id, consumable_id=gloves.id, unit_price=38, lead_time_days=2, moq=10)])
    db.commit()
    return a, b


def _history(db, world, a, b, n=20):
    """A: always on time (3 days). B: quotes 2 days, actually takes 2 or 9 days; one cancellation."""
    today = business_today()
    for i in range(n):
        start = today - timedelta(days=200 - 8 * i)
        for sup, lead, price in ((a, 3, 42), (b, 2 if i % 2 else 9, 38 + (i % 4 == 0) * 2)):
            o = so.create_order(db, None, world["hospital"].id, sup, world["gloves"], 100, price, start,
                                reference=f"T-{sup.code}-{i}", is_synthetic=True)
            if sup is b and i == 3:
                so.cancel_or_close(db, o, start + timedelta(days=1), "test")
                continue
            so.record_delivery(db, o, start + timedelta(days=lead), 100)
    db.commit()


@pytest.mark.parametrize("role,code", [("admin", 201), ("procurement_manager", 201), ("inventory_manager", 403),
                                       ("department_manager", 403), ("viewer", 403)])
def test_record_order_permissions(login, world, db, role, code):
    a, _ = _setup(db, world)
    c = as_role(login, role)
    r = c.post("/api/supplier-orders", json={"supplier_id": a.id, "consumable_id": world["gloves"].id, "quantity_ordered": 50})
    assert r.status_code == code, r.text
    assert c.get("/api/supplier-orders").status_code == 200


def test_order_defaults_validation_and_isolation(login, world, db):
    a, b = _setup(db, world)
    c = as_role(login, "procurement_manager")
    r = c.post("/api/supplier-orders", json={"supplier_id": a.id, "consumable_id": world["gloves"].id,
                                             "quantity_ordered": 50, "reference": "PO-1"}).json()
    today = business_today()
    assert r["expected_date"] == (today + timedelta(days=3)).isoformat() and r["unit_price"] == 42 and r["status"] == "OPEN"
    base = {"supplier_id": a.id, "consumable_id": world["gloves"].id, "quantity_ordered": 5}
    assert c.post("/api/supplier-orders", json={**base, "reference": "PO-1"}).status_code == 409
    assert c.post("/api/supplier-orders", json={**base, "quantity_ordered": 0}).status_code == 422
    assert c.post("/api/supplier-orders", json={**base, "ordered_date": (today + timedelta(days=2)).isoformat()}).status_code == 422
    assert c.post("/api/supplier-orders", json={**base, "consumable_id": world["other_item"].id}).status_code == 404
    other = login("admin@other.demo")
    assert other.get("/api/supplier-orders").json()["total"] == 0
    assert other.post(f"/api/supplier-orders/{r['id']}/cancel", json={}).status_code == 404
    assert other.get(f"/api/supplier-intelligence/suppliers/{a.id}").status_code == 404
    assert other.get(f"/api/supplier-intelligence/items/{world['gloves'].id}").status_code == 404


def test_receiving_against_an_order_records_deliveries(login, world, db):
    a, b = _setup(db, world)
    buyer = as_role(login, "procurement_manager")
    o = buyer.post("/api/supplier-orders", json={"supplier_id": a.id, "consumable_id": world["gloves"].id,
                                                 "quantity_ordered": 100}).json()
    store = as_role(login, "inventory_manager")
    exp = (business_today() + timedelta(days=400)).isoformat()
    base = {"consumable_id": world["gloves"].id, "lot_number": "L1", "expiry_date": exp, "supplier_order_id": o["id"]}
    assert store.post("/api/inventory/receive", json={**base, "quantity": 10, "consumable_id": world["other_item"].id}).status_code == 404
    assert store.post("/api/inventory/receive", json={**base, "quantity": 10, "supplier_id": b.id}).status_code == 422
    r = store.post("/api/inventory/receive", json={**base, "quantity": 60})
    assert r.status_code == 201, r.text
    got = db.scalar(select(SupplierOrder).where(SupplierOrder.id == o["id"]))
    db.refresh(got)
    assert got.status == "PARTIAL" and got.quantity_received == 60 and got.first_delivery_date == business_today()
    d = db.scalars(select(SupplierDelivery).where(SupplierDelivery.order_id == o["id"])).all()
    assert len(d) == 1 and d[0].stock_movement_id is not None and float(d[0].unit_price) == 42
    store.post("/api/inventory/receive", json={**base, "quantity": 40, "lot_number": "L2"})
    db.refresh(got)
    assert got.status == "RECEIVED" and got.completed_date == business_today()
    assert store.post("/api/inventory/receive", json={**base, "quantity": 1, "lot_number": "L3"}).status_code == 409
    # V1 receive without an order still works exactly as before
    assert store.post("/api/inventory/receive", json={"consumable_id": world["gloves"].id, "quantity": 5,
                                                      "lot_number": "L4"}).status_code == 201


def test_cancel_open_and_close_partial_short(login, world, db):
    a, _ = _setup(db, world)
    buyer = as_role(login, "procurement_manager")
    body = {"supplier_id": a.id, "consumable_id": world["gloves"].id, "quantity_ordered": 100}
    o1 = buyer.post("/api/supplier-orders", json=body).json()
    r = buyer.post(f"/api/supplier-orders/{o1['id']}/cancel", json={"reason": "not needed"})
    assert r.json()["status"] == "CANCELLED" and r.json()["close_reason"] == "not needed"
    assert buyer.post(f"/api/supplier-orders/{o1['id']}/cancel", json={}).status_code == 409
    o2 = buyer.post("/api/supplier-orders", json=body).json()
    as_role(login, "inventory_manager").post("/api/inventory/receive", json={
        "consumable_id": world["gloves"].id, "quantity": 30, "lot_number": "P", "supplier_order_id": o2["id"]})
    r = as_role(login, "procurement_manager").post(f"/api/supplier-orders/{o2['id']}/cancel", json={"reason": "rest backordered"})
    assert r.json()["status"] == "RECEIVED" and r.json()["quantity_received"] == 30  # closed short
    actions = {x["action"] for x in as_role(login, "admin").get("/api/audit-logs").json()["items"]}
    assert {"supplier_order.create", "supplier_order.cancel", "supplier_order.close_short"} <= actions


def test_overdue_order_raises_supplier_delay_alert_until_received(login, world, db):
    a, _ = _setup(db, world)
    buyer = as_role(login, "procurement_manager")
    past = (business_today() - timedelta(days=8)).isoformat()
    o = buyer.post("/api/supplier-orders", json={"supplier_id": a.id, "consumable_id": world["gloves"].id,
                                                 "quantity_ordered": 100, "ordered_date": past}).json()
    alert = db.scalar(select(Alert).where(Alert.alert_type == AlertType.SUPPLIER_DELAY))
    assert alert is not None and alert.status == AlertStatus.OPEN and o["reference"] in alert.message
    assert "5 days ago" in alert.message
    as_role(login, "inventory_manager").post("/api/inventory/receive", json={
        "consumable_id": world["gloves"].id, "quantity": 100, "lot_number": "X", "supplier_order_id": o["id"]})
    db.refresh(alert)
    assert alert.status == AlertStatus.RESOLVED


# ---------------------------------------------------------------- intelligence views


def test_scorecards_rank_by_measured_otif_and_expose_components(login, world, db):
    a, b = _setup(db, world)
    _history(db, world, a, b)
    sc = as_role(login, "viewer").get("/api/supplier-intelligence/scorecards").json()
    rows = {r["code"]: r for r in sc["suppliers"]}
    ma, mb = rows["SUP"]["metrics"], rows["CHEAP"]["metrics"]
    assert ma["reliability_score"] > mb["reliability_score"] and ma["grade"] == "A"
    assert ma["otif_rate"] == 1.0 and ma["on_time_rate"] == 1.0 and ma["lead_time_median"] == 3
    assert mb["cancellation_rate"] == pytest.approx(1 / 20) and mb["lead_time_p90"] >= 9 and mb["quoted_lead_time"] == 2
    assert mb["price_stability"] < 1
    assert "OTIF" in sc["method"]["formula"] and "Wilson" in sc["method"]["interval"]
    d = as_role(login, "viewer").get(f"/api/supplier-intelligence/suppliers/{b.id}").json()
    assert d["metrics"]["decided"] == 20 and d["items"][0]["price_history"] and d["lead_time_histogram"]
    assert len(d["recent_orders"]) == 20 and d["monthly"]


def test_item_options_use_the_v3_deadline_and_never_place_orders(login, world, db):
    a, b = _setup(db, world)
    _history(db, world, a, b)
    today = business_today()
    mv = RiskModelVersion(hospital_id=world["hospital"].id, run_id="t", name="stockout_xgb_v1", model_type="xgboost_classifier",
                          horizon_days=14, data_start=today, data_end=today, dataset_hash="x", metrics={},
                          warn_threshold=0.2, high_threshold=0.5, is_active=True)
    db.add(mv)
    db.flush()
    db.add(StockoutPrediction(hospital_id=world["hospital"].id, consumable_id=world["gloves"].id, risk_model_version_id=mv.id,
                              as_of=today, horizon_days=14, usable_stock=40, forecast_7=70, forecast_14=140, forecast_30=300,
                              days_of_stock_remaining=4, expected_stockout_date=today + timedelta(days=4), shortage_quantity=100,
                              probability=0.8, risk_level="HIGH", probability_source="xgboost_classifier"))
    db.commit()
    n_orders = db.query(SupplierOrder).count()
    v = as_role(login, "viewer").get(f"/api/supplier-intelligence/items/{world['gloves'].id}").json()
    assert v["deadline_days"] == 4 and v["risk"]["risk_level"] == "HIGH"
    opts = {o["code"]: o for o in v["options"]}
    assert opts["SUP"]["verdict"] == "likely" and opts["SUP"]["in_time_k"] == 20
    assert opts["CHEAP"]["verdict"] == "unlikely" and opts["CHEAP"]["price_vs_cheapest"] == 0
    assert opts["SUP"]["price_vs_cheapest"] == pytest.approx(42 / 38 - 1, abs=1e-3)
    assert opts["CHEAP"]["typical_lead_time_days"] > opts["CHEAP"]["quoted_lead_time_days"]
    text = " ".join(v["summary"])
    assert "cheapest supplier (Cheap Supplier) is unlikely" in text and "no order has been placed" in text
    assert db.query(SupplierOrder).count() == n_orders  # reading intelligence never creates orders
    at = as_role(login, "viewer").get("/api/supplier-intelligence/at-risk").json()
    assert at[0]["item"]["id"] == world["gloves"].id and at[0]["n_likely"] == 1 and at[0]["best"]["code"] == "SUP"


def test_evaluation_endpoint_and_empty_hospital(login, world, db):
    a, b = _setup(db, world)
    c = as_role(login, "viewer")
    assert c.get("/api/supplier-intelligence/evaluation").json()["n_test"] == 0
    sc = c.get("/api/supplier-intelligence/scorecards").json()
    assert all(r["metrics"] is None for r in sc["suppliers"])  # no evidence yet → no invented score
    _history(db, world, a, b)
    e = c.get("/api/supplier-intelligence/evaluation").json()
    assert e["n_test"] > 0 and set(e["lead_time"]) == {"quoted", "supplier_history", "supplier_item_history"}
    assert db.scalar(select(Consumable).where(Consumable.id == world["gloves"].id)) is not None
