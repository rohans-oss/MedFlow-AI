import os
from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password, login_limiter
from app.db.session import get_db
from app.db.tenant_guard import install_pg_triggers
from app.main import app
from app.models import Base, Consumable, ConsumableCategory, Department, Hospital, Organization, Role, Supplier, User

TEST_DB_URL = os.getenv("TEST_DATABASE_URL", "sqlite://")
PASSWORD = "Passw0rd!"
_HASH = hash_password(PASSWORD)  # hash once; bcrypt is deliberately slow

if TEST_DB_URL.startswith("sqlite"):
    engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")
else:
    engine = create_engine(TEST_DB_URL)

TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def db() -> Iterator[Session]:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:  # V8: the PostgreSQL tenant-consistency triggers (the migration installs the same)
        install_pg_triggers(conn)
    login_limiter._hits.clear()
    with TestSession() as s:
        yield s


@pytest.fixture()
def world(db: Session) -> dict:
    """A small hospital with one user per role, plus a second hospital for isolation tests."""
    org = Organization(name="Test Organization", code="TESTORG")  # V8: every hospital belongs to an organization
    other_org = Organization(name="Other Organization", code="OTHERORG")
    db.add_all([org, other_org])
    db.flush()
    h = Hospital(name="Test Hospital", code="TEST", expiry_warning_days=60, organization_id=org.id)
    other = Hospital(name="Other Hospital", code="OTHER", organization_id=other_org.id)
    db.add_all([h, other])
    db.flush()
    ortho = Department(hospital_id=h.id, code="ORTHO", name="Orthopaedics")
    icu = Department(hospital_id=h.id, code="ICU", name="ICU")
    other_dept = Department(hospital_id=other.id, code="ICU", name="Other ICU")
    cat = ConsumableCategory(hospital_id=h.id, name="Gloves")
    db.add_all([ortho, icu, other_dept, cat])
    db.flush()
    users = {}
    for role in Role:
        u = User(hospital_id=h.id, email=f"{role.value}@test.demo", full_name=role.value.title(), role=role,
                 hashed_password=_HASH, department_id=ortho.id if role == Role.DEPARTMENT_MANAGER else None)
        users[role.value] = u
    users["other_admin"] = User(hospital_id=other.id, email="admin@other.demo", full_name="Other", role=Role.ADMIN,
                                hashed_password=_HASH)
    db.add_all(users.values())
    gloves = Consumable(hospital_id=h.id, sku="GLV-7", name="Surgical gloves 7", category_id=cat.id, unit="pair",
                        unit_cost=40, reorder_level=100, max_level=1000)
    other_item = Consumable(hospital_id=other.id, sku="X-1", name="Other item", unit="piece", reorder_level=1)
    supplier = Supplier(hospital_id=h.id, code="SUP", name="Test Supplier", default_lead_time_days=3)
    db.add_all([gloves, other_item, supplier])
    db.commit()
    return {"org": org, "other_org": other_org,
            "hospital": h, "other": other, "ortho": ortho, "icu": icu, "other_dept": other_dept, "cat": cat,
            "gloves": gloves, "other_item": other_item, "supplier": supplier, "users": users}


@pytest.fixture()
def client(db: Session) -> Iterator[TestClient]:
    def _get_db():
        with TestSession() as s:
            yield s

    app.dependency_overrides[get_db] = _get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def login(client: TestClient):
    """Return a function that logs in as a role and returns an authenticated client (cookie-based)."""

    def _login(email: str) -> TestClient:
        client.cookies.clear()
        r = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
        assert r.status_code == 200, r.text
        return client

    return _login


def as_role(login, role: str) -> TestClient:
    return login(f"{role}@test.demo")


# The calendar day on which the calendar-sensitive fixtures below were calibrated (a Wednesday).
CALIBRATED_DAY = date(2026, 9, 23)


@pytest.fixture()
def frozen_day(monkeypatch):
    """Pin MedFlow's business "today" for fixtures whose synthetic data follow calendar weekdays (their fixed random
    seeds were chosen so that a specific outcome occurs — e.g. "V2B wins the holdout by chance"; on another weekday the
    same seed can produce a different outcome). Replaces `business_today` wherever it was imported by name.

    Audit fix (V0–V10 audit, T-1): `utcnow` is shifted by the same number of days, so timestamps the services stamp
    "now" (e.g. a batch's `received_at`) fall on the pinned day too. Before, only the business date was pinned: on a real
    date far from CALIBRATED_DAY a batch was "received" long after "today", which made its shelf life negative."""
    import sys

    from app.core import security
    from app.db import base

    real, real_now = security.business_today, base.utcnow
    shift = CALIBRATED_DAY - real()
    for mod in list(sys.modules.values()):
        if mod is None:
            continue
        if getattr(mod, "business_today", None) is real:
            monkeypatch.setattr(mod, "business_today", lambda: CALIBRATED_DAY)
        if getattr(mod, "utcnow", None) is real_now:
            monkeypatch.setattr(mod, "utcnow", lambda: real_now() + shift)
    return CALIBRATED_DAY
