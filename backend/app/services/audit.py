from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog, Hospital, User


def record(
    db: Session,
    user: User | None,
    action: str,
    entity_type: str,
    entity_id: int | None = None,
    details: dict[str, Any] | None = None,
    ip: str | None = None,
    hospital_id: int | None = None,
    organization_id: int | None = None,
    scope: str = "hospital",
) -> None:
    """Add an audit row to the current transaction (committed with the business change).

    V8 scopes: "hospital" (default — the given or the user's active hospital; skipped when there is none, as before),
    "organization" (organization_id, no hospital) and "platform" (neither)."""
    if scope == "hospital":
        hid = hospital_id if hospital_id is not None else (user.hospital_id if user else None)
        if hid is None and user is None:
            return
        # an account with no hospital selected (e.g. a platform admin signing in) → platform-level event
        h = db.get(Hospital, hid) if hid is not None else None
        org = h.organization_id if h is not None else None
    elif scope == "organization":
        hid, org = hospital_id, organization_id
    else:
        hid, org = None, None
    db.add(
        AuditLog(
            hospital_id=hid,
            organization_id=org,
            user_id=user.id if user else None,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=_jsonable(details) if details else None,
            ip_address=ip,
        )
    )


def diff(obj: Any, changes: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Apply changes to obj and return {field: {from, to}} for fields that actually changed."""
    out: dict[str, dict[str, Any]] = {}
    for key, new in changes.items():
        old = getattr(obj, key)
        if _norm(old) != _norm(new):
            out[key] = {"from": _norm(old), "to": _norm(new)}
            setattr(obj, key, new)
    return out


def _norm(v: Any) -> Any:
    from decimal import Decimal

    if isinstance(v, Decimal):
        return float(v)
    return v


def _jsonable(d: dict) -> dict:
    import json

    return json.loads(json.dumps(d, default=str))
