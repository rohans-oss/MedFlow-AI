"""V3.4 — classification metrics for stockout warnings (no scikit-learn dependency).

Row level: one row = (item, origin day); positive = the item stocked out within the next H days.
Event level: one event = the first day of a stockout episode; "caught" = warned on some day in [e−H, e−1];
"caught in time" (lead-time-aware) = warned at least `lead_time` days before e, early enough to reorder.
"""

from dataclasses import dataclass

import numpy as np


def confusion(y: np.ndarray, flag: np.ndarray) -> dict[str, int]:
    y, flag = y.astype(bool), flag.astype(bool)
    return {"tp": int((y & flag).sum()), "fp": int((~y & flag).sum()),
            "fn": int((y & ~flag).sum()), "tn": int((~y & ~flag).sum())}


def prf(c: dict[str, int], beta: float = 1.0) -> dict[str, float | None]:
    tp, fp, fn = c["tp"], c["fp"], c["fn"]
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f = (1 + beta ** 2) * p * r / (beta ** 2 * p + r) if p and r else (0.0 if p is not None and r is not None else None)
    return {"precision": p, "recall": r, "f1" if beta == 1 else f"f{beta:g}": f}


def average_precision(y: np.ndarray, score: np.ndarray) -> float | None:
    """PR-AUC as average precision (step-wise, ties broken conservatively by grouping equal scores)."""
    y = y.astype(bool)
    npos = int(y.sum())
    if npos == 0:
        return None
    order = np.argsort(-score, kind="mergesort")
    s, t = score[order], y[order]
    ap, tp, fp, prev_r = 0.0, 0, 0, 0.0
    i = 0
    while i < len(s):
        j = i
        while j < len(s) and s[j] == s[i]:
            j += 1
        tp += int(t[i:j].sum())
        fp += int((~t[i:j]).sum())
        r = tp / npos
        ap += (r - prev_r) * (tp / (tp + fp))
        prev_r = r
        i = j
    return ap


def roc_auc(y: np.ndarray, score: np.ndarray) -> float | None:
    y = y.astype(bool)
    npos, nneg = int(y.sum()), int((~y).sum())
    if npos == 0 or nneg == 0:
        return None
    ranks = _rankdata(score)
    return float((ranks[y].sum() - npos * (npos + 1) / 2) / (npos * nneg))


def _rankdata(a: np.ndarray) -> np.ndarray:
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(len(a))
    sa = a[order]
    i = 0
    while i < len(a):
        j = i
        while j < len(a) and sa[j] == sa[i]:
            j += 1
        ranks[order[i:j]] = (i + j + 1) / 2  # average rank, 1-based
        i = j
    return ranks


def pr_curve(y: np.ndarray, score: np.ndarray, points: int = 25) -> list[dict]:
    out = []
    for t in np.unique(np.quantile(score, np.linspace(0, 1, points))):
        c = confusion(y, score >= t)
        m = prf(c)
        out.append({"threshold": float(t), "precision": m["precision"], "recall": m["recall"]})
    return out


def calibration(y: np.ndarray, prob: np.ndarray, bins: int = 5) -> list[dict]:
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        m = (prob >= lo) & ((prob < hi) if hi < 1 else (prob <= hi))
        if m.any():
            out.append({"bin": f"{lo:.1f}–{hi:.1f}", "n": int(m.sum()), "mean_predicted": float(prob[m].mean()),
                        "observed_rate": float(y[m].mean())})
    return out


def best_threshold(y: np.ndarray, prob: np.ndarray, beta: float = 2.0) -> float:
    """Threshold maximising F-beta (beta = 2 weighs recall twice as much as precision: missed stockouts cost more)."""
    best_t, best_f = 0.5, -1.0
    for t in np.unique(np.round(prob, 3)):
        m = prf(confusion(y, prob >= t), beta=beta)
        f = m[f"f{beta:g}"] or 0.0
        if f > best_f:
            best_t, best_f = float(t), f
    return best_t


def threshold_for_recall(y: np.ndarray, prob: np.ndarray, target: float) -> float:
    """Highest threshold that still catches `target` of the positives (a service-level choice, independent of base rate)."""
    y = y.astype(bool)
    pos = np.sort(prob[y])[::-1]
    if len(pos) == 0:
        return 0.5
    k = max(int(np.ceil(target * len(pos))) - 1, 0)
    return float(pos[k])


def threshold_for_precision(y: np.ndarray, prob: np.ndarray, target: float, floor: float) -> float:
    """Smallest threshold ≥ floor whose precision reaches `target` (else the floor)."""
    for t in np.unique(np.round(prob, 3)):
        if t < floor:
            continue
        p = prf(confusion(y, prob >= t))["precision"]
        if p is not None and p >= target:
            return float(t)
    return floor


@dataclass
class Event:
    consumable_id: int
    date: object  # first stockout day of the episode
    lead_time: int | None
    first_warning: object | None  # earliest warning day in [e−H, e−1]
    warning_days: int | None  # e − first_warning
    caught: bool
    caught_in_time: bool


def summarize(y: np.ndarray, score: np.ndarray, flag: np.ndarray, events: list[Event], prob: np.ndarray | None = None) -> dict:
    c = confusion(y, flag)
    out: dict = {**c, **prf(c), "f2": prf(c, beta=2)["f2"], "pr_auc": average_precision(y, score), "roc_auc": roc_auc(y, score),
                 "n_rows": int(len(y)), "n_positive": int(y.sum()), "positive_rate": float(y.mean()) if len(y) else None}
    if prob is not None and len(y):
        out["brier"] = float(np.mean((prob - y) ** 2))
    out["n_events"] = len(events)
    out["event_recall"] = (sum(e.caught for e in events) / len(events)) if events else None
    out["lead_time_recall"] = (sum(e.caught_in_time for e in events) / len(events)) if events else None
    warned = [e.warning_days for e in events if e.caught and e.warning_days is not None]
    out["median_warning_days"] = float(np.median(warned)) if warned else None
    return out
