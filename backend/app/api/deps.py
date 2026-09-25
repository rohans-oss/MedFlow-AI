from typing import Annotated, TypeVar

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import permissions_for
from app.core.security import decode_token
from app.db.session import get_db
from app.models import User
from app.services import tenancy

bearer = HTTPBearer(auto_error=False)

HOSPITAL_HEADER = "X-MedFlow-Hospital"
HOSPITAL_CHANGED = "hospital_changed: the active hospital was changed in another window — reload the page"

DB = Annotated[Session, Depends(get_db)]


def get_current_user(
    request: Request,
    db: DB,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> User:
    token = creds.credentials if creds else request.cookies.get("access_token")
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise unauthorized
    payload = decode_token(token, "access")
    if not payload:
        raise unauthorized
    user = db.scalar(select(User).where(User.id == int(payload["sub"])))
    if not user or not user.is_active:
        raise unauthorized
    # V8 tenant context. The access token is bound to the hospital that was active when it was issued: after a switch
    # (or a revoked membership) older tokens stop working and the client must refresh.
    if payload.get("hid") != user.hospital_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Hospital context changed — refresh the session",
                            headers={"WWW-Authenticate": "Bearer"})
    tenancy.verify_request_context(db, user)
    # A browser tab states which hospital it is showing; if another tab switched hospitals meanwhile, refuse rather
    # than act on the other hospital. (Consistency check only — access is decided by the membership above.)
    shown = request.headers.get(HOSPITAL_HEADER)
    if shown is not None and shown != str(user.hospital_id or ""):
        raise HTTPException(status.HTTP_409_CONFLICT, HOSPITAL_CHANGED)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require(*perms: str):
    """Dependency: user must hold at least one of the given permissions."""

    def checker(user: CurrentUser) -> User:
        if user.hospital_id is None:  # V8: hospital data needs an active hospital (a usable membership)
            raise HTTPException(status.HTTP_403_FORBIDDEN, "No hospital selected — choose a hospital you are a member of")
        granted = permissions_for(user.role)
        if not any(p in granted for p in perms):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have permission to do this")
        return user

    return checker


M = TypeVar("M")


def get_owned(db: Session, model: type[M], obj_id: int, hospital_id: int, label: str | None = None) -> M:
    obj = db.get(model, obj_id)
    if obj is None or getattr(obj, "hospital_id", None) != hospital_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{label or model.__name__} not found")
    return obj


def client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None
