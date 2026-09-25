import time
import uuid
from collections import defaultdict, deque
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import bcrypt
import jwt

from app.core.config import settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def _encode(payload: dict, expires: timedelta) -> str:
    now = datetime.now(UTC)
    to_encode = {**payload, "iat": now, "exp": now + expires, "jti": uuid.uuid4().hex}
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_access_token(user_id: int, hospital_id: int, role: str) -> str:
    return _encode(
        {"sub": str(user_id), "hid": hospital_id, "role": role, "type": "access"},
        timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )


def create_refresh_token(user_id: int, token_version: int) -> str:
    return _encode(
        {"sub": str(user_id), "ver": token_version, "type": "refresh"},
        timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )


def decode_token(token: str, expected_type: str) -> dict | None:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
    if payload.get("type") != expected_type:
        return None
    return payload


def business_today() -> date:
    return datetime.now(ZoneInfo(settings.TIMEZONE)).date()


class LoginRateLimiter:
    """Simple in-process sliding window limiter. Replace with Redis when running >1 worker."""

    def __init__(self, max_attempts: int, window_seconds: int):
        self.max_attempts = max_attempts
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> bool:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        return len(hits) < self.max_attempts

    def hit(self, key: str) -> None:
        self._hits[key].append(time.monotonic())

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)


login_limiter = LoginRateLimiter(settings.LOGIN_MAX_ATTEMPTS, settings.LOGIN_WINDOW_SECONDS)
