"""V10 — descriptive baseline vs pilot comparison.

Rules: a row compares only when BOTH periods have sufficient data (otherwise "Insufficient data"); percentages are
compared in percentage points (absolute) and relative change; counts are normalised to 30 days when the two periods
have different lengths (and say so). No row is labelled "better" or "worse", and nothing here claims causality.
"""

from datetime import timedelta

from app.core.security import business_today
from app.pilots.metrics import Period

LABEL = "Descriptive baseline vs pilot comparison"
CAUSALITY = ("A before/after comparison describes what changed between two periods; it does not show that MedFlow "
             "caused the change. Seasonal demand, supplier, department, policy, staffing and data-source changes can "
             "all move these numbers.")

# count metrics that accumulate over time → normalised per 30 days when period lengths differ
LEDGER_RATES = {"stockout_days", "stockout_events", "affected_items", "shortage_estimate", "emergency_stockouts",
                "emergency_purchases"}
ACTIVITY_RATES = {"recs_generated", "recs_viewed", "recs_approved", "recs_modified", "recs_rejected", "recs_expired",
                  "recommendation_views", "decisions_made", "assistant_questions", "feedback_submitted", "records_received",
                  "records_accepted", "records_rejected", "supplier_orders"}
NOT_COMPARED = {"records_recovered", "forecast_coverage"}


def _activity_days(p: Period) -> int:
    return max(0, (min(p.end, business_today()) - p.start).days + 1)


def compare(base: dict, pilot: dict, base_p: Period, pilot_p: Period) -> list[dict]:
    b_by = {m["key"]: m for m in base["metrics"]}
    rows = []
    for pm in pilot["metrics"]:
        key = pm["key"]
        bm = b_by.get(key)
        if bm is None or key in NOT_COMPARED:
            continue
        row = {"key": key, "label": pm["label"], "section": pm["section"], "unit": pm["unit"],
               "baseline": bm["value"] if bm["sufficient"] else None, "pilot": pm["value"] if pm["sufficient"] else None,
               "baseline_n": bm["n"], "pilot_n": pm["n"], "min_n": pm["min_n"], "normalized": None,
               "difference": None, "difference_pp": None, "relative_change": None, "status": "ok"}
        if not (bm["sufficient"] and pm["sufficient"]):
            row["status"] = "insufficient_data"
            rows.append(row)
            continue
        b, p = float(bm["value"]), float(pm["value"])
        if key in LEDGER_RATES | ACTIVITY_RATES:
            db_, dp = ((base_p.ledger_days, pilot_p.ledger_days) if key in LEDGER_RATES
                       else (_activity_days(base_p), _activity_days(pilot_p)))
            if db_ != dp:
                if not db_ or not dp:
                    row["status"] = "insufficient_data"
                    rows.append(row)
                    continue
                b, p = b / db_ * 30, p / dp * 30
                row["normalized"] = "per 30 days"
                row["baseline"], row["pilot"] = round(b, 2), round(p, 2)
        if pm["unit"] == "pct":
            row["difference_pp"] = round((p - b) * 100, 2)
        else:
            row["difference"] = round(p - b, 4)
        row["relative_change"] = round((p - b) / abs(b), 4) if b else None
        rows.append(row)
    return rows


def period_note(p: Period) -> str | None:
    if p.end >= business_today():
        return f"{p.kind.title()} period is still running; ledger metrics use complete days up to {business_today() - timedelta(days=1)}"
    return None
