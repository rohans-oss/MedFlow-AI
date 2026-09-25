"""V5 — procurement engine: load V1–V4 inputs, build scenarios, evaluate them, pick with OR-Tools, explain.

Reads only; the API layer stores recommendations and records approved orders. Nothing here orders anything.
"""

import hashlib
import math
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import business_today
from app.ml.data import _local_date
from app.models import (
    ACTIVE_ORDER_STATUSES,
    Consumable,
    ModelItemMetric,
    ProcurementSettings,
    RiskLevel,
    StockBatch,
    StockoutPrediction,
    Supplier,
    SupplierOrder,
    SupplierProduct,
)
from app.procurement import arrival as arr
from app.procurement.costs import CostModel
from app.procurement.optimize import Choice, choose
from app.procurement.replenish import Replenishment, TransitLine, calculate, offset_date
from app.procurement.simulate import NEVER, Supply, make_paths, simulate
from app.risk.projection import Batch
from app.supplier_intel.metrics import OrderRec, summarize

WINDOW_DAYS = 365  # order history used for arrival distributions (same window as V4)
ATTENTION_SIMS = 200


# ---------------------------------------------------------------- inputs


@dataclass
class Offer:
    supplier_id: int
    supplier: str
    code: str
    is_active: bool
    is_preferred: bool
    unit_price: float
    moq: int
    quoted: int
    arrival: arr.Arrival
    reliability_score: float | None
    grade: str | None


@dataclass
class ItemContext:
    item: Consumable
    today: date
    batches: list[Batch]
    mu: np.ndarray
    sigma: float
    sigma_basis: str
    shelf_life: float | None
    ref_price: float
    risk: StockoutPrediction | None
    offers: list[Offer]
    transit: list[TransitLine]
    recs: list[OrderRec] = field(default_factory=list)

    @property
    def active_offers(self) -> list[Offer]:
        return [o for o in self.offers if o.is_active]


def load_settings(db: Session, hospital_id: int) -> ProcurementSettings | None:
    return db.scalar(select(ProcurementSettings).where(ProcurementSettings.hospital_id == hospital_id))


def cost_model(db: Session, hospital_id: int) -> CostModel:
    return CostModel.from_settings(load_settings(db, hospital_id))


def _rec(o: SupplierOrder) -> OrderRec:
    return OrderRec(o.id, o.supplier_id, o.consumable_id, o.ordered_date, o.expected_date, o.quoted_lead_time_days,
                    o.quantity_ordered, o.quantity_received, o.status, o.first_delivery_date, o.completed_date,
                    float(o.unit_price))


def load_contexts(db: Session, hospital_id: int, cm: CostModel, item_ids: list[int] | None = None
                  ) -> tuple[dict[int, ItemContext], dict[int, str]]:
    """Everything V5 needs per item. Returns (contexts, skipped item → reason)."""
    from app.risk import engine as risk_engine  # V3, read-only

    today = business_today()
    q = select(Consumable).where(Consumable.hospital_id == hospital_id, Consumable.is_active.is_(True))
    if item_ids is not None:
        q = q.where(Consumable.id.in_(item_ids))
    items = {c.id: c for c in db.scalars(q.order_by(Consumable.name))}
    ids = list(items)
    if not ids:
        return {}, {}
    H = int(min(max(cm.horizon_days, 14), 30))
    risks = risk_engine.latest(db, hospital_id, ids)
    _, fc = risk_engine.active_models(db, hospital_id)
    served = risk_engine._served_daily(db, fc, ids, today) if fc is not None else {}
    sigmas = {}
    if fc is not None:
        sigmas = {m.consumable_id: float(m.residual_std) for m in db.scalars(
            select(ModelItemMetric).where(ModelItemMetric.model_version_id == fc.id, ModelItemMetric.consumable_id.in_(ids)))}

    batches: dict[int, list[Batch]] = defaultdict(list)
    shelf: dict[int, list[int]] = defaultdict(list)
    for b in db.scalars(select(StockBatch).where(StockBatch.consumable_id.in_(ids))):
        if b.expiry_date is not None and b.received_at is not None:
            shelf[b.consumable_id].append((b.expiry_date - _local_date(b.received_at)).days)
        if b.quantity > 0 and (b.expiry_date is None or b.expiry_date >= today):
            batches[b.consumable_id].append(Batch(float(b.quantity), b.expiry_date))

    orders = list(db.scalars(select(SupplierOrder).where(SupplierOrder.hospital_id == hospital_id,
                                                         SupplierOrder.ordered_date >= today - timedelta(days=WINDOW_DAYS))))
    recs = [_rec(o) for o in orders]
    open_orders = list(db.scalars(select(SupplierOrder).where(SupplierOrder.hospital_id == hospital_id,
                                                              SupplierOrder.consumable_id.in_(ids),
                                                              SupplierOrder.status.in_(ACTIVE_ORDER_STATUSES))
                                  .order_by(SupplierOrder.ordered_date, SupplierOrder.id)))
    hosp = summarize(recs, today)
    prior = hosp["otif_rate"] if hosp["otif_rate"] is not None else 0.8
    sup_score: dict[int, tuple[float | None, str | None]] = {}
    for sid in {r.supplier_id for r in recs}:
        s = summarize([r for r in recs if r.supplier_id == sid], today, prior)
        sup_score[sid] = (s["reliability_score"], s["grade"])

    offers: dict[int, list[Offer]] = defaultdict(list)
    rows = db.execute(select(SupplierProduct, Supplier).join(Supplier, Supplier.id == SupplierProduct.supplier_id)
                      .where(SupplierProduct.consumable_id.in_(ids), Supplier.hospital_id == hospital_id)
                      .order_by(Supplier.name)).all()
    for sp, s in rows:
        score, grade = sup_score.get(s.id, (None, None))
        offers[sp.consumable_id].append(Offer(
            s.id, s.name, s.code, bool(s.is_active), bool(sp.is_preferred), float(sp.unit_price), int(sp.moq or 1),
            int(sp.lead_time_days), arr.new_order(recs, s.id, sp.consumable_id, int(sp.lead_time_days), H), score, grade))

    out: dict[int, ItemContext] = {}
    skipped: dict[int, str] = {}
    for cid, c in items.items():
        risk = risks.get(cid)
        if risk is not None and risk.as_of == today and risk.projection:
            mu = np.array([float(d["demand"]) for d in risk.projection][:H])
        elif cid in served:
            mu = np.array(served[cid][:H], dtype=float)
        else:
            skipped[cid] = "no forecast (train the forecasting models first)"
            continue
        if len(mu) < H:
            mu = np.concatenate([mu, np.full(H - len(mu), float(np.mean(mu[-7:])) if len(mu) else 0.0)])
        if not offers.get(cid):
            skipped[cid] = "no supplier lists this item"
            continue
        if cid in sigmas:
            sigma, basis = sigmas[cid], "forecast holdout error"
        else:
            sigma, basis = math.sqrt(max(float(np.mean(mu)), 0.0)), "Poisson (no holdout error stored)"
        prices = [o.unit_price for o in offers[cid] if o.is_active] or [o.unit_price for o in offers[cid]]
        ref = float(np.median(prices)) if prices else float(c.unit_cost or 0)
        transit = []
        for o in open_orders:
            if o.consumable_id != cid:
                continue
            transit.append(TransitLine(o.id, o.reference, o.supplier_id, o.supplier.name,
                                       o.quantity_ordered - o.quantity_received, o.expected_date,
                                       arr.in_transit(recs, o.supplier_id, cid, o.ordered_date, today,
                                                      o.quoted_lead_time_days, H)))
        out[cid] = ItemContext(c, today, batches.get(cid, []), mu, sigma, basis,
                               float(np.median(shelf[cid])) if shelf.get(cid) else None, ref, risk,
                               offers[cid], transit, recs)
    return out, skipped


def with_whatif(ctx: ItemContext, delays: dict[int, int] | None = None, demand_factor: float = 1.0) -> ItemContext:
    """What-if copy: supplier deliveries `delays[supplier_id]` days later (new and in-transit), demand × factor."""
    delays = delays or {}
    offers = [replace(o, arrival=o.arrival.shifted(delays.get(o.supplier_id, 0))) for o in ctx.offers]
    transit = [replace(t, arrival=t.arrival.shifted(delays.get(t.supplier_id, 0))) for t in ctx.transit]
    return replace(ctx, offers=offers, transit=transit, mu=ctx.mu * demand_factor)


# ---------------------------------------------------------------- V5.1 per offer


def _lead(o: Offer) -> tuple[float, float]:
    m, sd = arr.lead_time_stats(o.arrival)
    return (float(o.quoted) if m is None else m), (sd or 0.0)


def replenishment_for(ctx: ItemContext, o: Offer, cm: CostModel) -> Replenishment:
    lt, sd = _lead(o)
    return calculate(ctx.batches, ctx.mu, ctx.sigma, ctx.today, ctx.transit, lt, sd, cm.review_period_days, cm.z,
                     o.moq, ctx.shelf_life, ctx.item.max_level)


def reference_offer(ctx: ItemContext) -> Offer:
    pool = ctx.active_offers or ctx.offers
    pref = [o for o in pool if o.is_preferred]
    return pref[0] if pref else min(pool, key=lambda o: (_lead(o)[0], o.unit_price))


def rep_dict(r: Replenishment, today: date) -> dict:
    return {
        "lead_time_days": r.lead_time, "lead_time_sd": r.lead_time_sd, "review_period_days": r.cover_days - math.ceil(r.lead_time)
        if r.cover_days >= math.ceil(r.lead_time) else 0, "cover_days": r.cover_days,
        "avg_daily_demand": r.avg_daily, "demand_over_cover": r.demand_cover, "sigma_daily": r.sigma_daily,
        "safety_stock": r.safety_stock, "usable_stock": r.usable_stock, "expiring_before_use": r.expiring_before_use,
        "expected_incoming": r.expected_incoming, "reorder_point": r.reorder_point,
        "order_by_date": offset_date(today, r.order_by_offset), "need_by_date": offset_date(today, r.need_by_offset),
        "required_quantity": r.required, "moq": r.moq, "moq_adjusted_quantity": r.moq_adjusted,
        "expiry_cap": r.expiry_cap, "storage_cap": r.storage_cap, "warnings": r.warnings,
        "projection": [{"date": (today + timedelta(days=k)).isoformat(), "without_deliveries": p, "with_in_transit": w}
                       for k, (p, w) in enumerate(zip(r.physical, r.with_transit, strict=True))],
    }


# ---------------------------------------------------------------- scenarios


@dataclass
class Line:
    offer: Offer
    quantity: int


@dataclass
class Scenario:
    key: str
    kind: str  # none | single | split | custom
    lines: list[Line]
    note: str  # how the quantity was derived


def _need_day(ctx: ItemContext, ref: Replenishment) -> int:
    """Delivery window used for 'arrived within the required window': the projected stockout counting in-transit
    orders at their expected (probability-weighted) quantity; else the end of the cover period."""
    if ref.need_by_offset is None:
        return ref.cover_days
    if ref.need_by_offset == 0:  # already out of stock: judge against the reference lead time
        return max(1, math.ceil(ref.lead_time))
    return ref.need_by_offset


def scenarios(ctx: ItemContext, cm: CostModel) -> tuple[list[Scenario], dict[int, Replenishment]]:
    reps = {o.supplier_id: replenishment_for(ctx, o, cm) for o in ctx.offers}
    out = [Scenario("none", "none", [], "No new order: current stock plus orders already in transit.")]
    seen = set()

    def add(o: Offer, q: int, note: str):
        q = int(q)
        if q <= 0 or (o.supplier_id, q) in seen:
            return
        seen.add((o.supplier_id, q))
        out.append(Scenario(f"s{o.supplier_id}-q{q}", "single", [Line(o, q)], note))

    for o in ctx.active_offers:
        r = reps[o.supplier_id]
        base = r.moq_adjusted if r.moq_adjusted > 0 else o.moq
        add(o, base, "V5.1 quantity (cover period + safety stock, MOQ-adjusted)" if r.moq_adjusted > 0
            else "Supplier minimum order (no quantity strictly required yet)")
        lean = max(o.moq, math.ceil(r.demand_cover - (r.usable_stock - r.expiring_before_use) - r.expected_incoming))
        add(o, lean, "Without safety stock (cover period only, MOQ-adjusted)")
        more = max(base, r.required + math.ceil(r.avg_daily * cm.review_period_days))
        caps = [c for c in (r.expiry_cap, r.storage_cap) if c is not None]
        if caps:
            more = max(base, min(more, min(caps)))
        add(o, more, "One extra review period of cover (capped by expiry / storage)")

    active = ctx.active_offers
    if cm.allow_split and len(active) >= 2:
        ref = replenishment_for(ctx, reference_offer(ctx), cm)
        need = _need_day(ctx, ref)
        fast = max(active, key=lambda o: (o.arrival.cdf(need), -_lead(o)[0], -o.unit_price))
        cheap = min(active, key=lambda o: (o.unit_price, _lead(o)[0]))
        if fast.supplier_id != cheap.supplier_id:
            rc = reps[cheap.supplier_id]
            slow = cheap.arrival.quantile(0.9)
            bridge_days = min((slow if slow is not None else len(ctx.mu) - 1) + 1, len(ctx.mu))
            rf = reps[fast.supplier_id]
            bridge = math.ceil(float(np.sum(ctx.mu[:bridge_days])) + rf.safety_stock
                               - (rf.usable_stock - rf.expiring_before_use)
                               - sum(t.outstanding * t.arrival.cdf(bridge_days - 1) for t in ctx.transit))
            if bridge > 0:
                qf = max(fast.moq, bridge)
                total = max(rc.moq_adjusted, rc.required)
                qc = max(cheap.moq, total - qf)
                out.append(Scenario(f"split-{fast.supplier_id}-{qf}-{cheap.supplier_id}-{qc}", "split",
                                    [Line(fast, qf), Line(cheap, qc)],
                                    f"Split: {fast.code} bridges the {bridge_days} days until {cheap.code} delivers "
                                    f"in 90% of past orders; {cheap.code} supplies the rest at the lower price."))
    return out, reps


# ---------------------------------------------------------------- evaluation


def eval_horizon(ctx: ItemContext, cm: CostModel) -> int:
    """Days a scenario is judged over: the reference cover period (expected lead time + review period) — until an
    order placed at the NEXT review could arrive. Shortages after that belong to the next ordering decision.
    Capped by the configured horizon (≤ 30-day stored forecast)."""
    ref = replenishment_for(ctx, reference_offer(ctx), cm)
    return int(min(len(ctx.mu), max(ref.cover_days, 7)))


def _seed(ctx: ItemContext) -> int:
    return int(hashlib.sha256(f"{ctx.item.id}:{ctx.today.isoformat()}".encode()).hexdigest()[:8], 16)


def _supplies(ctx: ItemContext) -> tuple[list[Supply], list[Supply]]:
    on_hand = [Supply(b.quantity, None, None if b.expiry_date is None else float((b.expiry_date - ctx.today).days),
                      f"batch:{k}") for k, b in enumerate(ctx.batches)]
    transit = [Supply(float(t.outstanding), t.arrival, ctx.shelf_life, f"order:{t.order_id}") for t in ctx.transit]
    return on_hand, transit


def _arrival(today: date, days: np.ndarray) -> date | None:
    """Median simulated arrival day (among paths where it arrives within the horizon)."""
    v = days[days < NEVER]
    return None if len(v) == 0 else offset_date(today, int(np.median(v)))


def evaluate(ctx: ItemContext, cm: CostModel, scs: list[Scenario], need_day: int, paths=None,
             horizon: int | None = None) -> list[dict]:
    """Monte Carlo + cost model for every scenario (common random numbers)."""
    he = horizon or eval_horizon(ctx, cm)
    if paths is None:
        paths = make_paths(ctx.mu[:he], ctx.sigma, int(cm.simulations), _seed(ctx))
    on_hand, transit = _supplies(ctx)
    rate_after = float(np.mean(ctx.mu[max(he - 7, 0):he])) if he else 0.0
    per_day_hold = ctx.ref_price * cm.holding_cost_rate / 365.0
    short_cost = ctx.ref_price * cm.stockout_cost_multiplier
    base = simulate(on_hand, transit, [], paths, rate_after)  # "no order" on the same paths
    out = []
    for sc in scs:
        new = [Supply(float(ln.quantity), ln.offer.arrival, ctx.shelf_life, f"sup:{ln.offer.supplier_id}", True)
               for ln in sc.lines]
        r = simulate(on_hand, transit, new, paths, rate_after) if sc.lines else base
        qty = sum(ln.quantity for ln in sc.lines)
        purchase = sum(ln.quantity * ln.offer.unit_price for ln in sc.lines)
        p_avg = purchase / qty if qty else 0.0
        fixed = cm.order_cost * len(sc.lines)
        e_short = float(np.mean(r.shortage))
        # effect of the order = difference to "no order" on identical demand/arrival paths
        used = float(np.mean(r.total_consumed - base.total_consumed))  # extra demand served within the horizon
        waste = max(float(np.mean(r.total_waste - base.total_waste)), 0.0)  # extra units expiring before use
        hold = max(float(np.mean(r.total_hold_days - base.total_hold_days)), 0.0)  # extra unit-days in stock
        costs = {"purchase": round(purchase + fixed, 2), "purchase_goods": round(purchase, 2), "order_fixed": round(fixed, 2),
                 "stockout": round(e_short * short_cost, 2), "holding": round(hold * per_day_hold, 2),
                 "expiry": round(waste * p_avg * (1 + cm.disposal_cost_pct), 2),
                 "carried_forward": round(max(qty - used, 0.0) * p_avg, 2) + 0.0}
        costs["total"] = round(costs["purchase"] + costs["stockout"] + costs["holding"] + costs["expiry"]
                               - costs["carried_forward"], 2)
        lines = []
        for ln in sc.lines:
            a = ln.offer.arrival
            k_ev, n_ev = a.evidence_within(need_day)
            med, p90 = a.quantile(0.5), a.quantile(0.9)
            lines.append({
                "supplier_id": ln.offer.supplier_id, "supplier": ln.offer.supplier, "code": ln.offer.code,
                "quantity": ln.quantity, "unit_price": ln.offer.unit_price, "moq": ln.offer.moq,
                "line_value": round(ln.quantity * ln.offer.unit_price, 2),
                "quoted_lead_time_days": ln.offer.quoted, "arrival_basis": a.basis, "arrival_evidence": a.n,
                "typical_arrival_date": offset_date(ctx.today, med), "p90_arrival_date": offset_date(ctx.today, p90),
                "p_arrive_by_need": round(a.cdf(need_day), 3), "p_never_in_horizon": round(a.p_beyond, 3),
                "window_k": k_ev, "window_n": n_ev,
                "reliability_score": ln.offer.reliability_score, "grade": ln.offer.grade,
            })
        stock_paths = r.first_stockout[r.first_stockout < NEVER]
        out.append({
            "key": sc.key, "kind": sc.kind, "note": sc.note, "lines": lines,
            "quantity": sum(ln.quantity for ln in sc.lines), "purchase_value": round(purchase, 2),
            "costs": costs,
            "metrics": {
                "p_stockout": round(r.p_stockout, 3), "p_stockout_14": round(r.p_stockout_14, 3),
                "expected_shortage": round(e_short, 1),
                "shortage_p90": round(float(np.quantile(r.shortage, 0.9)), 1),
                "median_stockout_date": offset_date(ctx.today, int(np.median(stock_paths))) if len(stock_paths) else None,
                "expected_expired_units": round(waste, 1), "expected_used_in_horizon": round(used, 1),
                "expected_carried_units": round(max(qty - used - waste, 0.0), 1),
                # first delivery of the scenario (for a split: whichever line arrives first)
                "arrival_date": _arrival(ctx.today, r.new_arrival.min(axis=1)) if sc.lines else None,
                "last_arrival_date": _arrival(ctx.today, r.new_arrival.max(axis=1)) if len(sc.lines) > 1 else None,
                "p_arrive_by_need": round(float(np.mean(r.new_arrival.min(axis=1) <= need_day)), 3) if sc.lines else None,
            },
        })
    return out


def _label(s: dict, unit: str) -> str:
    if not s["lines"]:
        return "No new order"
    return " + ".join(f"{ln['quantity']:,} {unit} from {ln['code']}" for ln in s["lines"])


def _tag(scored: list[dict], rec_idx: int) -> None:
    for s in scored:
        s["tags"] = []
    scored[rec_idx]["tags"].append("recommended")
    singles = [s for s in scored if s["kind"] == "single"]
    if not singles:
        return
    # compare like with like: each supplier's V5.1 quantity (first single scenario per supplier)
    firsts: dict[int, dict] = {}
    for s in singles:
        firsts.setdefault(s["lines"][0]["supplier_id"], s)
    base = list(firsts.values())
    min(base, key=lambda s: (s["lines"][0]["unit_price"], s["purchase_value"]))["tags"].append("cheapest")
    max(base, key=lambda s: (s["lines"][0]["p_arrive_by_need"], s["lines"][0]["window_k"]))["tags"].append("most_reliable")
    min(base, key=lambda s: (s["lines"][0]["typical_arrival_date"] or date.max, -s["lines"][0]["p_arrive_by_need"])
        )["tags"].append("fastest")
    for s in scored:
        if s["kind"] == "none":
            s["tags"].append("no_order")
        if s["kind"] == "split":
            s["tags"].append("split")


def _money(v: float) -> str:
    return f"₹{v:,.0f}" if abs(v) >= 100 else f"₹{v:,.2f}"


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v:.0%}"


def explain(ctx: ItemContext, plan: dict) -> list[str]:
    """Plain-English reasons from the numbers (no generated text)."""
    unit = ctx.item.unit
    scs = plan["scenarios"]
    rec = next(s for s in scs if "recommended" in s["tags"])
    none = next(s for s in scs if s["kind"] == "none")
    cheap = next((s for s in scs if "cheapest" in s["tags"]), None)
    rep = plan["replenishment"]
    need = plan["need_by_days"]
    out: list[str] = []
    if not rec["lines"]:
        out.append(f"No new order recommended: with current stock and orders in transit the expected total cost is lowest "
                   f"without ordering ({_money(rec['costs']['total'])}; stockout probability "
                   f"{_pct(rec['metrics']['p_stockout'])} over {plan['horizon_days']} days).")
    else:
        out.append(f"Recommended: {_label(rec, unit)} — lowest expected total cost ({_money(rec['costs']['total'])}) of "
                   f"{len(scs)} scenarios evaluated.")
        if rep["required_quantity"] > 0:
            exp_txt = f" less {rep['expiring_before_use']:,.0f} expiring" if rep["expiring_before_use"] else ""
            moq_txt = (f", rounded up to the minimum order of {rep['moq']:,}"
                       if rep["moq_adjusted_quantity"] > rep["required_quantity"] else "")
            out.append(f"Quantity: demand over lead time + review period ({rep['cover_days']} days: {rep['demand_over_cover']:,.0f}) "
                       f"+ safety stock ({rep['safety_stock']:,.0f}) − usable stock ({rep['usable_stock']:,.0f}{exp_txt}) − "
                       f"expected in-transit ({rep['expected_incoming']:,.0f}) = {rep['required_quantity']:,} {unit}{moq_txt}"
                       f" (reference supplier {plan['reference_supplier']}).")
        ln = rec["lines"][0]
        window = (f"the required {need}-day window" if plan["need_by_date"] and plan["need_by_date"] > ctx.today
                  else f"{need} days")
        if cheap is not None and cheap["key"] != rec["key"]:
            cl = cheap["lines"][0]
            if rec["kind"] == "single" and ln["supplier_id"] != cl["supplier_id"]:
                ev = (f"has historically arrived within {window} in {ln['window_k']} of {ln['window_n']} orders"
                      if ln["window_n"] else "has no comparable order history")
                cev = (f"{cl['window_k']} of {cl['window_n']}" if cl["window_n"] else "no history")
                d = rec["quantity"] * (ln["unit_price"] - cl["unit_price"])
                verb = f"costs {_money(d)} more" if d > 0 else f"costs {_money(-d)} less"
                out.append(f"Buying {rec['quantity']:,} {unit} from {ln['code']} {verb} than the same quantity from the "
                           f"cheapest supplier ({cl['code']} at {_money(cl['unit_price'])}/{unit}), but {ln['code']} {ev} "
                           f"({cl['code']}: {cev}).")
            elif rec["kind"] == "single":
                out.append(f"Same supplier as the cheapest option; quantity {rec['quantity']:,} instead of "
                           f"{cheap['quantity']:,} lowers the expected total cost by "
                           f"{_money(cheap['costs']['total'] - rec['costs']['total'])}.")
            out.append(f"Expected shortage {rec['metrics']['expected_shortage']:,.1f} {unit} vs "
                       f"{cheap['metrics']['expected_shortage']:,.1f} for the cheapest option (stockout probability "
                       f"{_pct(rec['metrics']['p_stockout'])} vs {_pct(cheap['metrics']['p_stockout'])}); expected total "
                       f"cost {_money(rec['costs']['total'])} vs {_money(cheap['costs']['total'])}.")
        elif cheap is not None:
            out.append(f"It is also the lowest-price option; it has arrived within {window} in {ln['window_k']} of "
                       f"{ln['window_n']} past orders." if ln["window_n"] else
                       "It is also the lowest-price option (no comparable order history for this window).")
        out.append(f"Without a new order: stockout probability {_pct(none['metrics']['p_stockout'])}, expected shortage "
                   f"{none['metrics']['expected_shortage']:,.1f} {unit} (expected cost {_money(none['costs']['total'])}).")
        if rec["kind"] == "split":
            out.append(rec["note"])
            if cheap is not None:
                d = rec["purchase_value"] - cheap["purchase_value"]
                ev = "; ".join(f"{x['code']} arrived within {need} days in {x['window_k']} of {x['window_n']} past orders"
                               if x["window_n"] else f"{x['code']} has no comparable history" for x in rec["lines"])
                out.append(f"The split costs {_money(abs(d))} {'more' if d > 0 else 'less'} to purchase than the cheapest "
                           f"single option ({cheap['label']}); {ev}.")
    orders = [s for s in scs if s["lines"]]
    floor = min((s["metrics"]["expected_shortage"] for s in orders), default=None)
    if floor is not None and floor >= 0.5 and none["metrics"]["expected_shortage"] - floor < 0.5:
        out.append(f"No scenario reduces the expected shortage of {floor:,.1f} {unit}: it happens before any new delivery "
                   f"could arrive. Consider expediting an order in transit or borrowing stock.")
    elif floor is not None and floor >= 0.5:
        out.append(f"Even the best scenario leaves an expected shortage of {floor:,.1f} {unit} before a new delivery can "
                   f"arrive.")
    for t in plan["in_transit"]:
        if t["arrival_basis"] == "no_evidence":
            out.append(f"Order {t['reference']} ({t['outstanding']:,} {unit} from {t['supplier']}) is not counted: no past order "
                       f"from this supplier took this long, so its arrival can't be estimated from history.")
        else:
            out.append(f"Order {t['reference']} ({t['outstanding']:,} {unit} from {t['supplier']}) is counted as "
                       f"{_pct(t['p_arrive_by_need'])} likely to arrive in time ({t['window_k']} of {t['window_n']} comparable "
                       f"past orders) — {t['expected_quantity']:,.0f} expected units, not the full {t['outstanding']:,}.")
    for w in rep["warnings"]:
        out.append(w)
    if plan.get("budget_note"):
        out.append(plan["budget_note"])
    out.append("Recommendation only — nothing is ordered until a person approves it.")
    return out


def transit_rows(ctx: ItemContext, need_day: int) -> list[dict]:
    rows = []
    for t in ctx.transit:
        k, n = t.arrival.evidence_within(need_day)
        p = t.arrival.cdf(need_day)
        med = t.arrival.quantile(0.5)
        rows.append({"order_id": t.order_id, "reference": t.reference, "supplier_id": t.supplier_id, "supplier": t.supplier,
                     "outstanding": t.outstanding, "expected_date": t.expected_date,
                     "overdue_days": max((ctx.today - t.expected_date).days, 0),
                     "arrival_basis": t.arrival.basis, "p_arrive_by_need": round(p, 3), "window_k": k, "window_n": n,
                     "expected_quantity": round(t.outstanding * p, 1),
                     "typical_arrival_date": offset_date(ctx.today, med)})
    return rows


def plan_item(ctx: ItemContext, cm: CostModel) -> dict:
    """Full V5 plan for one item: V5.1 replenishment, V5.2 feasibility, scenarios evaluated, best by CP-SAT."""
    scs, reps = scenarios(ctx, cm)
    ref_offer = reference_offer(ctx)
    ref = reps[ref_offer.supplier_id]
    need = _need_day(ctx, ref)
    scored = evaluate(ctx, cm, scs, need)
    sol = choose([[Choice(s["costs"]["total"], s["purchase_value"]) for s in scored]])
    for s in scored:
        s["label"] = _label(s, ctx.item.unit)
    _tag(scored, sol.picks[0])
    scored.sort(key=lambda s: s["costs"]["total"])
    for i, s in enumerate(scored):
        s["rank"] = i + 1
    risk = ctx.risk
    plan = {
        "item": {"id": ctx.item.id, "name": ctx.item.name, "sku": ctx.item.sku, "unit": ctx.item.unit},
        "as_of": ctx.today, "horizon_days": eval_horizon(ctx, cm),
        "risk": None if risk is None else {"risk_level": risk.risk_level, "probability": risk.probability,
                                           "expected_stockout_date": risk.expected_stockout_date,
                                           "usable_stock": risk.usable_stock, "as_of": risk.as_of, "id": risk.id},
        "demand": {"forecast_30": round(float(np.sum(ctx.mu)), 1), "sigma_daily": round(ctx.sigma, 2),
                   "sigma_basis": ctx.sigma_basis, "shelf_life_days": ctx.shelf_life, "reference_price": round(ctx.ref_price, 2)},
        "reference_supplier": ref_offer.code,
        "replenishment": rep_dict(ref, ctx.today),
        "replenishment_by_supplier": {o.code: {"required_quantity": reps[o.supplier_id].required,
                                               "moq_adjusted_quantity": reps[o.supplier_id].moq_adjusted,
                                               "safety_stock": reps[o.supplier_id].safety_stock,
                                               "lead_time_days": reps[o.supplier_id].lead_time,
                                               "order_by_date": offset_date(ctx.today, reps[o.supplier_id].order_by_offset)}
                                      for o in ctx.offers},
        "need_by_days": need, "need_by_date": offset_date(ctx.today, ref.need_by_offset),
        "in_transit": transit_rows(ctx, need),
        "scenarios": scored,
        "solver": {"engine": "OR-Tools CP-SAT", "status": sol.status, "objective": round(sol.objective, 2)},
    }
    plan["recommended_key"] = next(s["key"] for s in scored if "recommended" in s["tags"])
    plan["explanation"] = explain(ctx, plan)
    return plan


def evaluate_lines(ctx: ItemContext, cm: CostModel, lines: list[tuple[int, int, float | None]]) -> dict:
    """Evaluate a human-modified plan [(supplier_id, quantity, unit_price)] with the same paths and cost model."""
    offers = {o.supplier_id: o for o in ctx.offers}
    sc_lines = []
    for sid, q, price in lines:
        o = offers[sid]
        sc_lines.append(Line(replace(o, unit_price=float(price)) if price is not None else o, int(q)))
    ref = replenishment_for(ctx, reference_offer(ctx), cm)
    need = _need_day(ctx, ref)
    scored = evaluate(ctx, cm, [Scenario("custom", "custom", sc_lines, "Modified by reviewer"),
                                Scenario("none", "none", [], "")], need)
    s = scored[0]
    s["label"] = _label(s, ctx.item.unit)
    return s


# ---------------------------------------------------------------- needs attention


def needs_attention(db: Session, hospital_id: int) -> dict:
    """Items that need a procurement decision in this review cycle, with the V5.2 in-transit-aware view."""
    cm = cost_model(db, hospital_id)
    ctxs, skipped = load_contexts(db, hospital_id, cm)
    rows = []
    for cid, ctx in ctxs.items():
        ref_offer = reference_offer(ctx)
        rep = replenishment_for(ctx, ref_offer, cm)
        risk = ctx.risk
        level = risk.risk_level if risk is not None else None
        usable = sum(b.quantity for b in ctx.batches)
        reasons = []
        if usable <= 0:
            reasons.append("Out of stock now.")
        if level in (RiskLevel.HIGH, RiskLevel.MEDIUM):
            reasons.append(f"V3 stockout risk {level} ({risk.probability:.0%} within 14 days, ignoring deliveries).")
        if rep.order_by_offset is not None and rep.order_by_offset < cm.review_period_days:
            when = ("overdue by " + f"{-rep.order_by_offset} day{'s' if rep.order_by_offset != -1 else ''}"
                    if rep.order_by_offset < 0 else "today" if rep.order_by_offset == 0
                    else f"by {offset_date(ctx.today, rep.order_by_offset).isoformat()}")
            reasons.append(f"Order {when}: projected stock (with in-transit weighted by arrival probability) falls "
                           f"below safety stock ({rep.safety_stock:,.0f}) before a {ref_offer.code} delivery "
                           f"(~{rep.lead_time:.0f} days) could arrive.")
        overdue = [t for t in ctx.transit if t.expected_date < ctx.today]
        for t in overdue:
            reasons.append(f"Order {t.reference} from {t.supplier} is {(ctx.today - t.expected_date).days} days overdue.")
        if not reasons:
            continue
        on_hand, transit = _supplies(ctx)
        he = eval_horizon(ctx, cm)
        paths = make_paths(ctx.mu[:he], ctx.sigma, ATTENTION_SIMS, _seed(ctx))
        sim = simulate(on_hand, transit, [], paths, float(np.mean(ctx.mu[max(he - 7, 0):he])))
        need = _need_day(ctx, rep)
        rows.append({
            "item": {"id": cid, "name": ctx.item.name, "sku": ctx.item.sku, "unit": ctx.item.unit},
            "risk_level": level, "risk_probability": risk.probability if risk else None,
            "v3_stockout_date": risk.expected_stockout_date if risk else None,
            "usable_stock": int(usable), "avg_daily_demand": rep.avg_daily,
            "in_transit_orders": len(ctx.transit), "in_transit_quantity": sum(t.outstanding for t in ctx.transit),
            "expected_in_transit": round(sum(t.outstanding * t.arrival.cdf(need) for t in ctx.transit), 1),
            "overdue_orders": len(overdue),
            "need_by_date": offset_date(ctx.today, rep.need_by_offset),
            "order_by_date": offset_date(ctx.today, rep.order_by_offset),
            "horizon_days": he, "p_stockout_no_order": round(sim.p_stockout, 3), "p_stockout_14_no_order": round(sim.p_stockout_14, 3),
            "expected_shortage_no_order": round(float(np.mean(sim.shortage)), 1),
            "required_quantity": rep.required, "moq_adjusted_quantity": rep.moq_adjusted,
            "reference_supplier": ref_offer.code, "safety_stock": rep.safety_stock, "reasons": reasons,
        })
    rows.sort(key=lambda r: (r["order_by_date"] or date.max, -r["p_stockout_no_order"]))
    return {"as_of": business_today(), "max_horizon_days": int(min(max(cm.horizon_days, 14), 30)), "items": rows,
            "skipped": [{"item_id": k, "reason": v} for k, v in skipped.items()], "settings": cm.as_dict()}


def plan_items(db: Session, hospital_id: int, item_ids: list[int], cm: CostModel | None = None) -> tuple[list[dict], dict]:
    """Plans for several items, then one CP-SAT run across them (respects the optional budget)."""
    cm = cm or cost_model(db, hospital_id)
    ctxs, _ = load_contexts(db, hospital_id, cm, item_ids)
    plans = [plan_item(ctxs[i], cm) for i in item_ids if i in ctxs]
    budget = float(cm.budget_limit) if cm.budget_limit else None
    sol = choose([[Choice(s["costs"]["total"], s["purchase_value"]) for s in p["scenarios"]] for p in plans], budget)
    for p, j in zip(plans, sol.picks, strict=True):
        chosen = p["scenarios"][j]
        if chosen["key"] != p["recommended_key"]:
            best = next(s for s in p["scenarios"] if s["key"] == p["recommended_key"])
            best["tags"].remove("recommended")
            chosen["tags"].insert(0, "recommended")
            p["budget_note"] = (f"Budget {_money(budget)} for this run: the optimiser chose {chosen['label']} instead of "
                                f"{best['label']} (expected cost {_money(chosen['costs']['total'] - best['costs']['total'])} "
                                f"higher, purchase {_money(best['purchase_value'] - chosen['purchase_value'])} lower).")
            p["recommended_key"] = chosen["key"]
            p["explanation"] = explain(ctxs[p["item"]["id"]], p)
        p["solver"]["run"] = {"status": sol.status, "items": len(plans), "budget": budget,
                              "budget_binding": sol.budget_binding, "run_purchase": round(sol.cash, 2)}
    return plans, {"status": sol.status, "objective": round(sol.objective, 2), "purchase": round(sol.cash, 2),
                   "budget": budget, "budget_binding": sol.budget_binding}


def whatif(db: Session, hospital_id: int, item: Consumable, delays: dict[int, int], demand_change_pct: float) -> dict:
    """Re-evaluate the SAME scenarios (same suppliers, quantities, horizon and delivery window) with deliveries from
    the given suppliers `days` later (new and in-transit orders) and/or demand changed. Same random paths, so every
    difference comes from the change. Also re-plans from scratch to show whether the best option would change."""
    cm = cost_model(db, hospital_id)
    ctxs, skipped = load_contexts(db, hospital_id, cm, [item.id])
    if item.id not in ctxs:
        return {"error": skipped.get(item.id, "item unavailable")}
    base_ctx = ctxs[item.id]
    base = plan_item(base_ctx, cm)
    alt_ctx = with_whatif(base_ctx, delays, 1 + demand_change_pct / 100.0)
    scs, _ = scenarios(base_ctx, cm)
    alt_offers = {o.supplier_id: o for o in alt_ctx.offers}
    alt_scs = [Scenario(sc.key, sc.kind, [Line(alt_offers[ln.offer.supplier_id], ln.quantity) for ln in sc.lines], sc.note)
               for sc in scs]
    he, need = base["horizon_days"], base["need_by_days"]
    before = {s["key"]: s for s in evaluate(base_ctx, cm, scs, need, horizon=he)}
    after = {s["key"]: s for s in evaluate(alt_ctx, cm, alt_scs, need, horizon=he)}
    replanned = plan_item(alt_ctx, cm)

    def m(s: dict) -> dict:
        return {"p_stockout": s["metrics"]["p_stockout"], "expected_shortage": s["metrics"]["expected_shortage"],
                "total_cost": s["costs"]["total"], "arrival_date": s["metrics"]["arrival_date"],
                "p_arrive_by_need": s["metrics"]["p_arrive_by_need"]}

    rows = []
    for s in base["scenarios"]:
        rows.append({"key": s["key"], "label": s["label"], "tags": s["tags"], "before": m(before[s["key"]]),
                     "after": m(after[s["key"]])})
    best_after = min(rows, key=lambda r: r["after"]["total_cost"])
    rec = next(r for r in rows if r["key"] == base["recommended_key"])
    names = {o.supplier_id: o.code for o in base_ctx.offers}
    change = ", ".join(f"{names.get(k, k)} +{v} day{'s' if v != 1 else ''}" for k, v in delays.items() if v) or "no supplier delay"
    if demand_change_pct:
        change += f", demand {demand_change_pct:+.0f}%"
    unit = item.unit
    b, a = rec["before"], rec["after"]
    arr_txt = f", typical first delivery {b['arrival_date']} → {a['arrival_date']}" if b["arrival_date"] else ""
    summary = [f"What-if: {change} (same {he}-day horizon and delivery window, same simulated demand).",
               f"Recommended plan ({rec['label']}): stockout probability {_pct(b['p_stockout'])} → {_pct(a['p_stockout'])}, "
               f"expected shortage {b['expected_shortage']:,.1f} → {a['expected_shortage']:,.1f} {unit}, expected cost "
               f"{_money(b['total_cost'])} → {_money(a['total_cost'])}{arr_txt}."]
    if best_after["key"] != rec["key"]:
        summary.append(f"Among the same scenarios, {best_after['label']} would now have the lowest expected cost "
                       f"({_money(best_after['after']['total_cost'])} vs {_money(a['total_cost'])}).")
    else:
        summary.append("Among the same scenarios, the recommended plan still has the lowest expected cost.")
    rp = next(s for s in replanned["scenarios"] if s["key"] == replanned["recommended_key"])
    if rp["label"] != rec["label"]:
        summary.append(f"Re-planned from scratch under the what-if (quantities recalculated): {rp['label']} "
                       f"(expected cost {_money(rp['costs']['total'])}).")
    tb, ta = transit_rows(base_ctx, need), transit_rows(alt_ctx, need)
    for x, y in zip(tb, ta, strict=True):
        if x["p_arrive_by_need"] != y["p_arrive_by_need"]:
            summary.append(f"In-transit order {x['reference']}: P(arrives in time) {_pct(x['p_arrive_by_need'])} → "
                           f"{_pct(y['p_arrive_by_need'])}.")
    return {"item": base["item"], "delays": {str(k): v for k, v in delays.items()}, "demand_change_pct": demand_change_pct,
            "horizon_days": he, "need_by_date": base["need_by_date"],
            "baseline_recommended": base["recommended_key"], "whatif_best": best_after["key"],
            "replanned_recommended": rp["label"], "scenarios": rows, "summary": summary}
