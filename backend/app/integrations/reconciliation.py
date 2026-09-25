"""V9 — resolving reconciliation issues (hospital system count ≠ MedFlow ledger). Always a person's decision.

* adjust          — MedFlow was wrong: post stock ADJUSTMENT movements for the difference (normal V1 ledger adjustments,
                    needs the stock:receive permission, like a stock count).
* external_wrong  — the hospital system is wrong; MedFlow's ledger stays as it is.
* accept          — a known/explained difference (timing, unit of measure…); nothing changes.
Issues also close themselves ("matched_later") when a later snapshot matches the ledger again.
"""

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import business_today
from app.db.base import utcnow
from app.models import ReconciliationIssue, StockBatch, User
from app.services import stock

ACTIONS = {"adjust": "adjusted", "external_wrong": "external_wrong", "accept": "accepted"}


def resolve(db: Session, issue: ReconciliationIssue, user: User, action: str, note: str) -> list:
    if issue.status != "OPEN":
        raise HTTPException(status.HTTP_409_CONFLICT, "This reconciliation issue is already resolved")
    if action not in ACTIONS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"action must be one of {', '.join(ACTIONS)}")
    moves = []
    if action == "adjust":
        moves = _adjust(db, issue, user, note)
    now = utcnow()
    issue.status, issue.resolution, issue.note = "RESOLVED", ACTIONS[action], note
    issue.resolved_by_id, issue.resolved_at, issue.updated_at = user.id, now, now
    db.flush()
    return moves


def _adjust(db: Session, issue: ReconciliationIssue, user: User, note: str) -> list:
    """Apply the difference (external − MedFlow at the snapshot time) to today's batches."""
    item, diff = issue.consumable, issue.difference
    reason = f"Reconciliation with {issue.source.name} ({issue.as_of}): {note}"[:300]
    today = business_today()
    usable = db.scalars(select(StockBatch).where(StockBatch.consumable_id == item.id, StockBatch.quantity > 0)
                        .order_by(StockBatch.expiry_date.asc().nulls_last(), StockBatch.received_at.asc(), StockBatch.id)).all()
    moves = []
    if diff > 0:
        batches = [b for b in usable if b.expiry_date is None or b.expiry_date >= today]
        if not batches:  # add to the most recent batch even if it is empty now
            b = db.scalar(select(StockBatch).where(StockBatch.consumable_id == item.id)
                          .order_by(StockBatch.received_at.desc(), StockBatch.id.desc()).limit(1))
            if b is None or (b.expiry_date is not None and b.expiry_date < today):
                raise HTTPException(status.HTTP_409_CONFLICT, "No usable batch to adjust — receive the stock instead")
            batches = [b]
        b = batches[-1]  # latest-expiring usable batch
        moves += stock.adjust(db, user, b, item, b.quantity + diff, reason)
    else:
        need = -diff
        if sum(b.quantity for b in usable) < need:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"MedFlow now holds less than {need} {item.unit} of {item.sku}; count the stock instead")
        for b in usable:  # expired and earliest-expiring stock first
            if need == 0:
                break
            take = min(b.quantity, need)
            moves += stock.adjust(db, user, b, item, b.quantity - take, reason)
            need -= take
    return moves
