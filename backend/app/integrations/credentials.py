"""V9 — inbound API keys for the `api_push` connector.

Format `mfk_<prefix>_<secret>`. Only HMAC-SHA256(pepper, key) is stored; the key is shown once at creation and cannot
be recovered (revoke and create a new one). The pepper comes from the environment (INTEGRATION_KEY_PEPPER), so a
database dump alone cannot be used to test guessed keys. Keys are per source (and therefore per hospital).
"""

import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import utcnow
from app.models import IntegrationCredential, IntegrationSource, User

PREFIX = "mfk"


def _pepper() -> bytes:
    p = settings.INTEGRATION_KEY_PEPPER or hashlib.sha256(("integration-key:" + settings.JWT_SECRET).encode()).hexdigest()
    return p.encode()


def hash_key(key: str) -> str:
    return hmac.new(_pepper(), key.encode(), hashlib.sha256).hexdigest()


def create(db: Session, source: IntegrationSource, label: str, user: User | None) -> tuple[IntegrationCredential, str]:
    prefix = secrets.token_hex(6)
    key = f"{PREFIX}_{prefix}_{secrets.token_urlsafe(32)}"
    cred = IntegrationCredential(hospital_id=source.hospital_id, source_id=source.id, label=label.strip()[:120] or "API key",
                                 key_prefix=prefix, key_hash=hash_key(key), created_by_id=user.id if user else None)
    db.add(cred)
    db.flush()
    return cred, key


def authenticate(db: Session, key: str | None) -> tuple[IntegrationCredential, IntegrationSource]:
    """Resolve an API key → (credential, source). 401 for anything invalid; never says which part was wrong."""
    bad = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or revoked API key",
                        headers={"WWW-Authenticate": "Bearer"})
    if not key:
        raise bad
    parts = key.strip().split("_", 2)
    if len(parts) != 3 or parts[0] != PREFIX:
        raise bad
    cred = db.scalar(select(IntegrationCredential).where(IntegrationCredential.key_prefix == parts[1]))
    if cred is None or cred.revoked_at is not None or not hmac.compare_digest(cred.key_hash, hash_key(key.strip())):
        raise bad
    source = db.get(IntegrationSource, cred.source_id)
    if source is None or source.hospital_id != cred.hospital_id:
        raise bad
    if not source.enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This integration source is disabled in MedFlow")
    if source.connector != "api_push":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This integration source does not accept pushed data")
    from app.models import Hospital, TenantStatus

    h = db.get(Hospital, source.hospital_id)
    if h is None or h.status != TenantStatus.ACTIVE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "The hospital of this integration source is not active")
    cred.last_used_at = utcnow()
    return cred, source


_calls: dict[int, deque] = defaultdict(deque)


def rate_limit(cred: IntegrationCredential) -> None:
    """Sliding one-minute window per API key (in-process, like the login limiter)."""
    now = time.monotonic()
    q = _calls[cred.id]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= settings.INTEGRATION_RATE_LIMIT_PER_MINUTE:
        retry = max(1, int(60 - (now - q[0])) + 1)
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            f"Rate limit: at most {settings.INTEGRATION_RATE_LIMIT_PER_MINUTE} requests per minute per key",
                            headers={"Retry-After": str(retry)})
    q.append(now)


def reset_rate_limit() -> None:
    _calls.clear()
