"""V5 — procurement intelligence: arrival distributions, replenishment maths, simulation, cost model, OR-Tools
selection, in-transit weighting, recommendations, human approval (approve / modify / reject), what-if, settings,
permissions and hospital isolation. Recommend-only: generating and reading never creates orders."""

from datetime import date, timedelta

import numpy as np
import pytest
from sqlalchemy import func, select

from app.core.security import business_today
from app.models import (
    AuditLog,
    ProcurementRecommendation,
    RiskModelVersion,
    StockoutPrediction,
    Supplier,
    SupplierOrder,
    SupplierProduct,
)
from app.procurement import arrival as A
from app.procurement import engine
from app.procurement.costs import CostModel
from app.procurement.optimize import Choice, choose
from app.procurement.replenish import TransitLine, calculate
from app.procurement.simulate import NEVER, Supply, make_paths, simulate
from app.risk.projection import Batch
from app.services import stock
from app.services import supplier_orders as so
from app.supplier_intel.metrics import OrderRec
from tests.conftest import as_role

D0 = date(2026, 1, 1)


def rec(i, sup=1, item=1, ordered=D0, quoted=3, lead=3, status="RECEIVED"):
    first = None if lead is None else ordered + timedelta(days=lead)
    got = 0 if lead is None else 100
    return OrderRec(i, sup, item, ordered, ordered + timedelta(days=quoted), quoted, 100, got, status, first, first, 10.0)


# ---------------------------------------------------------------- V5.2 arrival distributions (pure)


def test_new_order_distribution_uses_item_then_supplier_history_and_counts_cancellations():
    recs = [rec(i, quoted=2, lead=3) for i in range(6)] + [rec(9, lead=None, status="CANCELLED")]
    a = A.new_order(recs, 1, 1, quoted=2, horizon=30)
    assert a.basis == "item" and a.n == 7
    # 6 of 7 outcomes arrived on day 3; the cancellation never arrives
    # (7 × 6/7 own + 5 × 6/7 supplier) ÷ (7 + 5 + 1 pseudo-order that never arrives)
    assert a.cdf(2) == 0 and a.cdf(3) == pytest.approx(12 * (6 / 7) / 13) and a.p_beyond == pytest.approx(1 - 12 * (6 / 7) / 13)
    assert a.evidence_within(3) == (6, 7)
    # a thin item history is shrunk towards the supplier's (5 pseudo-orders), as V4's reliability score
    other = [rec(100 + i, item=2, quoted=2, lead=8) for i in range(5)]
    thin = A.new_order(recs + other, 1, 2, quoted=2, horizon=30)
    assert thin.basis == "item" and thin.cdf(8) == pytest.approx((5 + 5 * 11 / 12) / 11) and thin.cdf(3) == pytest.approx(5 * (6 / 12) / 11)
    # another item of the same supplier with no history → the supplier's orders
    assert A.new_order(recs, 1, 2, quoted=2, horizon=30).basis == "supplier"
    # unknown supplier → hospital-wide delay vs quote applied to its quote (here: +1 day)
    h = A.new_order(recs, 5, 1, quoted=4, horizon=30)
    assert h.basis == "hospital" and h.quantile(0.5) == 5
    # no history anywhere → the quote, flagged
    q = A.new_order([], 5, 1, quoted=4, horizon=30)
    assert q.basis == "quoted" and q.cdf(4) == 1.0


def test_shift_and_in_transit_are_conditional_on_elapsed_days():
    recs = [rec(i, lead=2) for i in range(5)] + [rec(10 + i, lead=6) for i in range(5)]
    a = A.new_order(recs, 1, 1, 3, 30)
    s = a.shifted(2)
    assert s.cdf(3) == 0 and s.cdf(4) == pytest.approx(a.cdf(2)) and s.evidence_within(4) == a.evidence_within(2)
    today = D0 + timedelta(days=100)
    t = A.in_transit(recs, 1, 1, ordered=today - timedelta(days=3), today=today, quoted=3, horizon=30)
    # already 3 days out: only the 6-day orders are comparable → arrives in 3 more days
    assert t.n == 5 and t.quantile(0.5) == 3 and t.cdf(3) == pytest.approx(10 / 11)
    late = A.in_transit(recs, 1, 1, ordered=today - timedelta(days=9), today=today, quoted=3, horizon=30)
    assert late.basis == "no_evidence" and late.cdf(29) == 0  # longer than any past order: not counted


# ---------------------------------------------------------------- V5.1 replenishment (pure)


def _line(q, p_by_day2):
    pmf = np.zeros(30)
    pmf[2] = p_by_day2
    return TransitLine(1, "PO-1", 1, "S", q, D0, A.Arrival(pmf, 1 - p_by_day2, "item", 10, []))


def test_replenishment_quantities_moq_safety_stock_and_expiry_cap():
    mu = np.full(30, 10.0)
    today = D0
    r = calculate([Batch(40, None)], mu, 0.0, today, [], 3, 0, 7, 1.645, moq=1, shelf_life=None, max_level=None)
    assert r.cover_days == 10 and r.demand_cover == 100 and r.safety_stock == 0 and r.required == 60
    assert r.order_by_offset == 4 - 3  # projected below safety stock (0) from day 4, lead 3 → order within 1 day
    r = calculate([Batch(40, None)], mu, 3.0, today, [], 3, 1.0, 7, 1.645, moq=100, shelf_life=None, max_level=None)
    ss = 1.645 * np.sqrt(10 * 9 + 100 * 1)
    assert r.safety_stock == pytest.approx(ss, abs=0.1) and r.required == int(np.ceil(60 + ss))
    assert r.moq_adjusted == 100 and any("minimum order" in w for w in r.warnings)
    # in-transit counts at its arrival probability, not in full
    half = calculate([Batch(40, None)], mu, 0.0, today, [_line(50, 0.5)], 3, 0, 7, 1.645, 1, None, None)
    assert half.expected_incoming == 25 and half.required == 35
    assert half.with_transit[2] == pytest.approx(half.physical[2] + 25)
    # expiring stock is not usable: 30 of 40 expire tomorrow
    exp = calculate([Batch(30, today + timedelta(days=1)), Batch(10, None)], mu, 0.0, today, [], 3, 0, 7, 1.645, 1, None, None)
    assert exp.expiring_before_use == 10 and exp.required == 70
    # a MOQ bigger than what can be used before a new batch expires is flagged
    cap = calculate([Batch(40, None)], mu, 0.0, today, [], 3, 0, 7, 1.645, moq=500, shelf_life=20, max_level=None)
    assert cap.expiry_cap == 10 * 20 - 10 and any("expires" in w for w in cap.warnings)


# ---------------------------------------------------------------- V5.3 simulation + optimiser (pure)


def _certain(day, h=30):
    pmf = np.zeros(h)
    pmf[day] = 1.0
    return A.Arrival(pmf, 0.0, "item", 10, [day] * 10)


def test_simulation_fefo_expiry_arrivals_and_determinism():
    paths = make_paths(np.full(10, 10.0), 0.0, 50, seed=1)
    on_hand = [Supply(25, None, 1.0, "b0"), Supply(30, None, None, "b1")]  # 25 expire after day 1
    r = simulate(on_hand, [], [], paths, 10.0)
    # days 0-1 take 20 from the expiring batch, 5 expire, 30 cover days 2-4, short from day 5
    assert r.shortage[0] == pytest.approx(50) and r.first_stockout[0] == 5 and r.existing_expired[0] == 5
    r2 = simulate(on_hand, [], [Supply(60, _certain(4), None, "sup:1", True)], paths, 10.0)
    assert r2.shortage[0] == 0 and r2.new_arrival[0, 0] == 4
    assert r2.total_consumed[0] - r.total_consumed[0] == pytest.approx(50)  # extra demand served
    never = simulate(on_hand, [], [Supply(60, A.Arrival(np.zeros(10), 1.0, "x", 0, []), None, "sup:2", True)], paths, 10.0)
    assert never.new_arrival[0, 0] == NEVER and never.shortage[0] == pytest.approx(50)
    noisy = make_paths(np.full(10, 10.0), 3.0, 200, seed=7)
    a, b = simulate(on_hand, [], [], noisy, 10.0), simulate(on_hand, [], [], make_paths(np.full(10, 10.0), 3.0, 200, 7), 10.0)
    assert np.array_equal(a.shortage, b.shortage)


def test_optimizer_picks_min_cost_and_respects_the_budget():
    items = [[Choice(100, 0), Choice(40, 500)], [Choice(80, 0), Choice(30, 400)]]
    free = choose(items)
    assert free.picks == [1, 1] and free.status == "OPTIMAL" and not free.budget_binding
    tight = choose(items, budget=450)  # can afford only one order: the solver picks the best affordable combination
    assert tight.budget_binding and tight.cash <= 450 and tight.picks == [0, 1]
    assert tight.objective == pytest.approx(100 + 30)


# ---------------------------------------------------------------- DB world


def _world(db, world, usable=120, demand=40.0, transit_from=None):
    """Gloves: reliable RELY (₹42, 3 days, always on time) vs CHEAP (₹38, quotes 2 days, takes 2 or 9; one cancel).
    120 usable, forecast 40/day → runs out in 3 days. A V3 snapshot supplies the forecast (no training needed)."""
    h, gloves = world["hospital"], world["gloves"]
    rely = world["supplier"]
    rely.code, rely.name = "RELY", "Reliable Supplies"
    cheap = Supplier(hospital_id=h.id, code="CHEAP", name="Cheap Supplier", default_lead_time_days=2)
    db.add(cheap)
    db.flush()
    db.add_all([SupplierProduct(supplier_id=rely.id, consumable_id=gloves.id, unit_price=42, lead_time_days=3, moq=50,
                                is_preferred=True),
                SupplierProduct(supplier_id=cheap.id, consumable_id=gloves.id, unit_price=38, lead_time_days=2, moq=50)])
    today = business_today()
    for i in range(20):
        start = today - timedelta(days=200 - 8 * i)
        for sup, lead, price in ((rely, 3, 42), (cheap, 2 if i % 2 else 9, 38)):
            o = so.create_order(db, None, h.id, sup, gloves, 100, price, start, reference=f"H-{sup.code}-{i}",
                                is_synthetic=True)
            if sup is cheap and i == 3:
                so.cancel_or_close(db, o, start + timedelta(days=1), "test")
                continue
            so.record_delivery(db, o, start + timedelta(days=lead), 100)
    stock.receive(db, world["users"]["admin"], gloves, usable, "L1", today + timedelta(days=400), rely, 42, None, None)
    if transit_from is not None:
        so.create_order(db, None, h.id, rely, gloves, 200, 42, today - timedelta(days=1), reference="PO-TRANSIT")
    mv = RiskModelVersion(hospital_id=h.id, run_id="t", name="stockout_xgb_v1", model_type="xgboost_classifier",
                          horizon_days=14, data_start=today, data_end=today, dataset_hash="x", metrics={},
                          warn_threshold=0.2, high_threshold=0.5, is_active=True)
    db.add(mv)
    db.flush()
    proj = [{"date": (today + timedelta(days=k)).isoformat(), "demand": demand, "stock_end": 0, "unmet": 0, "expired": 0}
            for k in range(30)]
    db.add(StockoutPrediction(hospital_id=h.id, consumable_id=gloves.id, risk_model_version_id=mv.id, as_of=today,
                              horizon_days=14, usable_stock=usable, forecast_7=demand * 7, forecast_14=demand * 14,
                              forecast_30=demand * 30, days_of_stock_remaining=3, expected_stockout_date=today + timedelta(days=3),
                              shortage_quantity=400, probability=0.9, risk_level="HIGH", probability_source="xgboost_classifier",
                              projection=proj))
    db.commit()
    return rely, cheap


def _orders(db) -> int:
    return db.scalar(select(func.count(SupplierOrder.id)))


@pytest.mark.usefixtures("frozen_day")  # calendar-sensitive fixture (see conftest.frozen_day)
def test_plan_does_not_simply_pick_the_cheapest_and_explains_with_numbers(login, world, db):
    rely, cheap = _world(db, world)
    n = _orders(db)
    split = as_role(login, "viewer").get(f"/api/procurement/items/{world['gloves'].id}/plan").json()
    rec_split = next(s for s in split["scenarios"] if "recommended" in s["tags"])
    assert any(ln["code"] == "RELY" for ln in rec_split["lines"])  # never "just the cheapest"
    buyer = as_role(login, "procurement_manager")
    s = buyer.get("/api/procurement/settings").json()["values"]
    buyer.put("/api/procurement/settings", json={**s, "allow_split": False})
    p = as_role(login, "viewer").get(f"/api/procurement/items/{world['gloves'].id}/plan").json()
    assert not any(x["kind"] == "split" for x in p["scenarios"])
    rec_s = next(s for s in p["scenarios"] if "recommended" in s["tags"])
    cheapest = next(s for s in p["scenarios"] if "cheapest" in s["tags"])
    assert rec_s["lines"][0]["code"] == "RELY" and cheapest["lines"][0]["code"] == "CHEAP"
    assert rec_s["metrics"]["p_stockout"] < cheapest["metrics"]["p_stockout"]
    assert rec_s["costs"]["total"] < cheapest["costs"]["total"] and rec_s["purchase_value"] > 0
    c = rec_s["costs"]
    assert c["total"] == pytest.approx(c["purchase"] + c["stockout"] + c["holding"] + c["expiry"] - c["carried_forward"], abs=0.02)
    text = " ".join(p["explanation"])
    assert "more than the same quantity from the cheapest supplier (CHEAP at ₹38.00/pair)" in text
    assert "within the required 3-day window in 20 of 20 orders (CHEAP: 9 of 20)" in text and "Recommendation only" in text
    assert {"none", "single"} <= {s["kind"] for s in p["scenarios"]} and len(p["scenarios"]) >= 5
    assert p["solver"]["engine"] == "OR-Tools CP-SAT" and p["solver"]["status"] == "OPTIMAL"
    assert p["replenishment"]["required_quantity"] > 0 and p["replenishment"]["moq"] == 50
    assert _orders(db) == n  # reading a plan never creates orders


def test_cost_model_is_configurable_not_hidden(login, world, db):
    _world(db, world)
    buyer = as_role(login, "procurement_manager")
    s = buyer.get("/api/procurement/settings").json()
    assert s["is_default"] and s["values"]["stockout_cost_multiplier"] == 5 and "Total expected cost" in s["formula"][0]
    assert set(s["explain"]) == set(s["values"])
    assert as_role(login, "viewer").put("/api/procurement/settings", json=s["values"]).status_code == 403
    assert as_role(login, "inventory_manager").put("/api/procurement/settings", json=s["values"]).status_code == 403
    buyer = as_role(login, "procurement_manager")  # (one test client: log back in)
    bad = buyer.put("/api/procurement/settings", json={**s["values"], "service_level": 1.5})
    assert bad.status_code == 422
    # stockouts cost nothing and holding is expensive → the model no longer pays for reliability
    r = buyer.put("/api/procurement/settings", json={**s["values"], "stockout_cost_multiplier": 0, "holding_cost_rate": 2})
    assert r.status_code == 200 and not r.json()["is_default"] and r.json()["updated_by"]
    p = buyer.get(f"/api/procurement/items/{world['gloves'].id}/plan").json()
    rec_s = next(s for s in p["scenarios"] if "recommended" in s["tags"])
    assert not rec_s["lines"] or rec_s["lines"][0]["code"] == "CHEAP"
    log = db.scalar(select(AuditLog).where(AuditLog.action == "procurement.settings.update"))
    assert log is not None and log.details["changed"]["stockout_cost_multiplier"]["to"] == 0


def test_in_transit_orders_are_weighted_by_arrival_probability(login, world, db):
    _world(db, world, transit_from=True)
    att = as_role(login, "viewer").get("/api/procurement/needs-attention").json()
    row = next(r for r in att["items"] if r["item"]["id"] == world["gloves"].id)
    assert row["in_transit_orders"] == 1 and row["in_transit_quantity"] == 200
    # RELY always delivers in 3 days (20 of 20): counted strongly but never as certain
    assert 0 < row["expected_in_transit"] < 200
    p = as_role(login, "viewer").get(f"/api/procurement/items/{world['gloves'].id}/plan").json()
    t = p["in_transit"][0]
    assert t["reference"] == "PO-TRANSIT" and t["window_n"] == 20 and 0.9 < t["p_arrive_by_need"] < 1
    assert any("PO-TRANSIT" in x and "expected units, not the full 200" in x for x in p["explanation"])


def test_generate_approve_creates_order_only_after_human_approval(login, world, db):
    rely, _ = _world(db, world)
    gloves = world["gloves"]
    n = _orders(db)
    assert as_role(login, "viewer").post("/api/procurement/recommendations/generate", json={}).status_code == 403
    g = as_role(login, "inventory_manager").post("/api/procurement/recommendations/generate", json={})
    assert g.status_code == 201, g.text
    out = g.json()
    assert out["created"] >= 1 and out["solver"]["status"] == "OPTIMAL"
    assert _orders(db) == n  # generating recommends; it never orders
    rec_row = next(r for r in out["recommendations"] if r["item"]["id"] == gloves.id)
    assert rec_row["status"] == "PENDING" and rec_row["lines"][0]["code"] == "RELY" and rec_row["is_current"]
    queue = as_role(login, "viewer").get("/api/procurement/recommendations").json()
    assert [r["id"] for r in queue] == [rec_row["id"]]
    # inventory may generate but not approve
    assert as_role(login, "inventory_manager").post(f"/api/procurement/recommendations/{rec_row['id']}/approve",
                                                    json={}).status_code == 403
    r = as_role(login, "procurement_manager").post(f"/api/procurement/recommendations/{rec_row['id']}/approve", json={})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "APPROVED" and not d["modified"] and len(d["orders"]) == len(rec_row["lines"])
    made = db.scalars(select(SupplierOrder).where(SupplierOrder.recommendation_id == rec_row["id"])
                      .order_by(SupplierOrder.id)).all()
    assert [(x.supplier_id, x.quantity_ordered) for x in made] == [(ln["supplier_id"], ln["quantity"]) for ln in rec_row["lines"]]
    o = made[0]
    assert o.supplier_id == rely.id and o.status == "OPEN" and "Not sent to the supplier" in o.notes
    assert as_role(login, "procurement_manager").post(f"/api/procurement/recommendations/{rec_row['id']}/approve",
                                                      json={}).status_code == 409
    actions = [a.action for a in db.scalars(select(AuditLog))]
    assert {"procurement.generate", "procurement.approve", "supplier_order.create"} <= set(actions)
    # regenerating supersedes nothing that was decided; the new plan counts the approved order as in transit
    again = as_role(login, "procurement_manager").post("/api/procurement/recommendations/generate",
                                                       json={"item_ids": [gloves.id]}).json()
    assert again["superseded"] == 0
    new = db.get(ProcurementRecommendation, again["recommendations"][0]["id"])
    assert {t["reference"] for t in new.in_transit} == {x.reference for x in made}


def test_modify_needs_a_reason_and_valid_supplier_then_is_re_evaluated(login, world, db):
    rely, cheap = _world(db, world)
    buyer = as_role(login, "procurement_manager")
    rid = buyer.post("/api/procurement/recommendations/generate", json={}).json()["recommendations"][0]["id"]
    url = f"/api/procurement/recommendations/{rid}/approve"
    change = {"lines": [{"supplier_id": cheap.id, "quantity": 200}]}
    assert buyer.post(url, json=change).status_code == 422  # a change needs a reason
    assert buyer.post(url, json={**change, "lines": [{"supplier_id": cheap.id, "quantity": 20}],
                                 "reason": "cheaper"}).status_code == 422  # below MOQ 50
    other_sup = Supplier(hospital_id=world["other"].id, code="OX", name="Other supplier")
    db.add(other_sup)
    db.commit()
    assert buyer.post(url, json={"lines": [{"supplier_id": other_sup.id, "quantity": 100}], "reason": "x y"}).status_code == 404
    r = buyer.post(url, json={**change, "reason": "Rate contract with Cheap Supplier"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["modified"] and d["decision_reason"] == "Rate contract with Cheap Supplier"
    assert d["final_lines"][0]["supplier_id"] == cheap.id and d["final_evaluation"]["metrics"]["p_stockout"] is not None
    assert d["final_evaluation"]["costs"]["total"] >= d["expected_cost"] - 0.01  # the optimiser's choice was cheaper
    o = db.scalar(select(SupplierOrder).where(SupplierOrder.recommendation_id == rid))
    assert o.supplier_id == cheap.id and float(o.unit_price) == 38


def test_reject_requires_reason_stale_recommendations_cannot_be_approved_and_isolation(login, world, db):
    _world(db, world)
    buyer = as_role(login, "procurement_manager")
    ids = [r["id"] for r in buyer.post("/api/procurement/recommendations/generate", json={}).json()["recommendations"]]
    rid = ids[0]
    assert buyer.post(f"/api/procurement/recommendations/{rid}/reject", json={"reason": ""}).status_code == 422
    other = login("admin@other.demo")
    assert other.get(f"/api/procurement/recommendations/{rid}").status_code == 404
    assert other.post(f"/api/procurement/recommendations/{rid}/approve", json={}).status_code == 404
    assert other.get(f"/api/procurement/items/{world['gloves'].id}/plan").status_code == 404
    assert other.get("/api/procurement/recommendations?status=all").json() == []
    assert other.get("/api/procurement/needs-attention").json()["items"] == []
    rec_ = db.get(ProcurementRecommendation, rid)
    rec_.as_of = business_today() - timedelta(days=1)
    db.commit()
    buyer = as_role(login, "procurement_manager")
    r = buyer.post(f"/api/procurement/recommendations/{rid}/approve", json={})
    assert r.status_code == 409 and "Generate a new one" in r.json()["detail"]
    r = buyer.post(f"/api/procurement/recommendations/{rid}/reject", json={"reason": "Stock borrowed from ward"})
    assert r.json()["status"] == "REJECTED" and r.json()["orders"] == []
    assert buyer.get("/api/procurement/recommendations?status=decided").json()[0]["decision_reason"] == "Stock borrowed from ward"


def test_what_if_supplier_delay_recomputes_without_storing_anything(login, world, db):
    rely, _ = _world(db, world)
    n_orders, n_recs = _orders(db), db.scalar(select(func.count(ProcurementRecommendation.id)))
    c = as_role(login, "viewer")
    w = c.post(f"/api/procurement/items/{world['gloves'].id}/what-if",
               json={"delays": [{"supplier_id": rely.id, "days": 4}]}).json()
    rec_row = next(s for s in w["scenarios"] if s["key"] == w["baseline_recommended"])
    assert rec_row["after"]["p_stockout"] > rec_row["before"]["p_stockout"]
    assert rec_row["after"]["expected_shortage"] > rec_row["before"]["expected_shortage"]
    assert rec_row["after"]["arrival_date"] > rec_row["before"]["arrival_date"]
    none = next(s for s in w["scenarios"] if s["key"] == "none")
    assert none["before"] == none["after"]  # no RELY order in transit: unaffected
    assert "RELY +4 days" in w["summary"][0]
    same = c.post(f"/api/procurement/items/{world['gloves'].id}/what-if", json={}).json()
    assert all(s["before"] == s["after"] for s in same["scenarios"])  # same random paths → identical
    assert c.post(f"/api/procurement/items/{world['gloves'].id}/what-if",
                  json={"delays": [{"supplier_id": 999999, "days": 1}]}).status_code == 404
    assert _orders(db) == n_orders and db.scalar(select(func.count(ProcurementRecommendation.id))) == n_recs
    ev = c.get("/api/procurement/evaluation").json()
    assert ev["n"] > 0 and ev["brier_model"] <= ev["brier_quote_certain"]  # CHEAP is late half the time


def test_budget_limit_is_respected_across_items(login, world, db):
    _world(db, world)
    buyer = as_role(login, "procurement_manager")
    free = buyer.post("/api/procurement/recommendations/generate", json={}).json()
    assert free["purchase_value"] > 0
    s = buyer.get("/api/procurement/settings").json()["values"]
    buyer.put("/api/procurement/settings", json={**s, "budget_limit": 100})
    tight = buyer.post("/api/procurement/recommendations/generate", json={}).json()
    assert tight["superseded"] == free["created"]
    assert tight["solver"]["budget_binding"] and tight["purchase_value"] <= 100
    assert any("Budget" in x for x in buyer.get(f"/api/procurement/recommendations/{tight['recommendations'][0]['id']}")
               .json()["explanation"])


def test_cost_model_defaults_round_trip():
    cm = CostModel()
    assert cm.z == pytest.approx(1.645, abs=1e-3)
    assert CostModel.from_settings(None).as_dict() == cm.as_dict()
    assert engine.WINDOW_DAYS == 365


def test_arrival_backtest_has_no_look_ahead_and_beats_trusting_the_quote():
    # supplier 1 quotes 3 days, delivers in 3 (70 %) or 7 days (30 %): trusting the quote is over-confident
    recs = [rec(i, ordered=D0 + timedelta(days=3 * i), quoted=3, lead=7 if i % 10 in (2, 5, 8) else 3) for i in range(60)]
    b = A.backtest(recs, warmup_days=30)
    assert b["n"] > 50 and b["brier_model"] < b["brier_quote_certain"]
    assert abs(b["mean_predicted"] - b["observed_rate"]) < 0.1
    # changing the last order's outcome cannot change what earlier orders were scored with
    changed = recs[:-1] + [rec(59, ordered=recs[-1].ordered, quoted=3, lead=20)]
    b2 = A.backtest(changed, warmup_days=30)
    assert b2["n"] == b["n"] and b2["mean_predicted"] == pytest.approx(b["mean_predicted"])


def test_pending_queue_lists_a_no_order_recommendation_dated_29_february(login, world, db):
    """Audit regression (V0–V10 audit, bug B-1): the queue's sort key moved `as_of` to year 9999 with date.replace, which
    raises ValueError for 29 February (9999 is not a leap year) — GET /procurement/recommendations answered 500 on leap days."""
    for as_of, lines in ((date(2028, 2, 29), []), (date(2028, 2, 29), [{"supplier_id": world["supplier"].id, "quantity": 100}])):
        db.add(ProcurementRecommendation(
            hospital_id=world["hospital"].id, consumable_id=world["gloves"].id, as_of=as_of, run_id="leap", status="PENDING",
            scenario_key="none" if not lines else "s", lines=lines, quantity=100 if lines else 0, order_by_date=as_of if lines else None,
            purchase_value=0, expected_cost=0, cost_breakdown={}, metrics={}, scenarios=[], replenishment={}, in_transit=[],
            explanation=[], settings_snapshot={}, solver={}))
    db.commit()
    r = as_role(login, "procurement_manager").get("/api/procurement/recommendations")
    assert r.status_code == 200, r.text
    assert [bool(x["lines"]) for x in r.json()] == [True, False]  # orders first, then "no order"
