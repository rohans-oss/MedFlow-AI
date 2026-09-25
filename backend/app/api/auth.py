from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select

from app.api.deps import DB, CurrentUser, client_ip
from app.core.config import settings
from app.core.permissions import permissions_for
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    login_limiter,
    verify_password,
)
from app.db.base import utcnow
from app.models import Hospital, Organization, User
from app.schemas.common import Message
from app.schemas.org import ChangePasswordRequest, LoginRequest, LoginResponse, MeOut
from app.services import audit, tenancy

router = APIRouter(prefix="/auth", tags=["auth"])

# Dummy hash so failed lookups take the same time as a real password check.
_DUMMY_HASH = hash_password("timing-equaliser-1")

SESSION_HINT_COOKIE = "mf_session"  # non-sensitive flag the frontend middleware can read


def _set_auth_cookies(response: Response, user: User) -> str:
    access = create_access_token(user.id, user.hospital_id, user.role)
    refresh = create_refresh_token(user.id, user.token_version)
    common = {"secure": settings.COOKIE_SECURE, "samesite": "lax", "domain": settings.COOKIE_DOMAIN}
    response.set_cookie(
        "access_token", access, httponly=True, path="/",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60, **common,
    )
    refresh_age = settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400
    response.set_cookie("refresh_token", refresh, httponly=True, path="/api/auth", max_age=refresh_age, **common)
    response.set_cookie(SESSION_HINT_COOKIE, "1", httponly=False, path="/", max_age=refresh_age, **common)
    return access


def _clear_auth_cookies(response: Response) -> None:
    for name, path in (("access_token", "/"), ("refresh_token", "/api/auth"), (SESSION_HINT_COOKIE, "/")):
        response.delete_cookie(name, path=path, domain=settings.COOKIE_DOMAIN)


def build_me(db: DB, user: User) -> MeOut:
    hospital = db.get(Hospital, user.hospital_id) if user.hospital_id is not None else None
    ms = tenancy.memberships(db, user)
    orgs = db.scalars(select(Organization).where(Organization.id.in_(tenancy.admin_org_ids(db, user)))
                      .order_by(Organization.name)).all()
    base = MeOut.model_validate(
        {
            **{k: getattr(user, k) for k in ("id", "email", "full_name", "role", "is_active", "department_id",
                                             "department", "last_login_at", "created_at", "is_platform_admin")},
            "hospital": hospital,
            "permissions": sorted(permissions_for(user.role)) if hospital is not None else [],
            "organization": hospital.organization if hospital is not None else None,
            "memberships": [{
                "membership_id": m.id, "hospital_id": m.hospital_id, "hospital_name": m.hospital.name,
                "hospital_code": m.hospital.code, "organization_id": m.hospital.organization_id,
                "organization_name": m.hospital.organization.name, "role": m.role, "department_id": m.department_id,
                "status": m.status, "available": tenancy._usable(m)} for m in ms],
            "admin_organizations": orgs,
        }
    )
    return base


def _authenticate(db: DB, request: Request, email: str, password: str) -> User:
    key = f"{client_ip(request)}|{email.lower()}"
    if not login_limiter.check(key):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many login attempts. Try again in a few minutes.")
    user = db.scalar(select(User).where(func.lower(User.email) == email.lower()))
    ok = verify_password(password, user.hashed_password if user else _DUMMY_HASH)
    if not user or not ok or not user.is_active:
        login_limiter.hit(key)
        if user:
            audit.record(db, user, "auth.login_failed", "user", user.id, ip=client_ip(request))
            db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    login_limiter.reset(key)
    tenancy.ensure_context(db, user)  # V8: keep a usable active hospital (or fall back to the first membership)
    user.last_login_at = utcnow()
    audit.record(db, user, "auth.login", "user", user.id, ip=client_ip(request))
    db.commit()
    return user


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, request: Request, response: Response, db: DB):
    user = _authenticate(db, request, body.email, body.password)
    access = _set_auth_cookies(response, user)
    return LoginResponse(user=build_me(db, user), access_token=access)


@router.post("/token", include_in_schema=True, summary="OAuth2 password flow (for Swagger 'Authorize')")
def token(form: Annotated[OAuth2PasswordRequestForm, Depends()], request: Request, response: Response, db: DB):
    user = _authenticate(db, request, form.username, form.password)
    access = _set_auth_cookies(response, user)
    return {"access_token": access, "token_type": "bearer"}


@router.post("/refresh", response_model=LoginResponse)
def refresh(request: Request, response: Response, db: DB):
    raw = request.cookies.get("refresh_token")
    payload = decode_token(raw, "refresh") if raw else None
    user = db.get(User, int(payload["sub"])) if payload else None
    if not user or not user.is_active or payload.get("ver") != user.token_version:
        _clear_auth_cookies(response)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    tenancy.ensure_context(db, user)  # V8: the new access token carries the current, verified hospital
    db.commit()
    access = _set_auth_cookies(response, user)  # rotates refresh token too
    return LoginResponse(user=build_me(db, user), access_token=access)


@router.post("/logout", response_model=Message)
def logout(request: Request, response: Response, db: DB):
    raw = request.cookies.get("refresh_token")
    payload = decode_token(raw, "refresh") if raw else None
    if payload:
        user = db.get(User, int(payload["sub"]))
        if user and payload.get("ver") == user.token_version:
            user.token_version += 1  # revokes every outstanding refresh token for this user
            audit.record(db, user, "auth.logout", "user", user.id, ip=client_ip(request))
            db.commit()
    _clear_auth_cookies(response)
    return Message(detail="Logged out")


@router.get("/me", response_model=MeOut)
def me(user: CurrentUser, db: DB):
    return build_me(db, user)


@router.post("/change-password", response_model=Message)
def change_password(body: ChangePasswordRequest, user: CurrentUser, request: Request, response: Response, db: DB):
    if not verify_password(body.current_password, user.hashed_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    from app.schemas.org import _check_password

    try:
        _check_password(body.new_password)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    user.hashed_password = hash_password(body.new_password)
    user.token_version += 1
    audit.record(db, user, "auth.password_changed", "user", user.id, ip=client_ip(request))
    db.commit()
    _set_auth_cookies(response, user)
    return Message(detail="Password changed")
