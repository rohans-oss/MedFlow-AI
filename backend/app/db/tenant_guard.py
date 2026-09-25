"""V8 — tenant-consistency guard (defence in depth under the per-request hospital scoping in the routers).

Two layers enforce the same rule: **a row that belongs to hospital H may only reference hospital-owned rows of H**
(an issue of hospital A's item to hospital B's department, a supplier product joining A's supplier to B's item, a
membership pointing at another hospital's department, … are rejected).

* ORM layer (every database, incl. SQLite tests): a `before_flush` listener checks new rows and changed foreign keys.
* PostgreSQL layer: `medflow_tenant_guard()` row triggers on every hospital-owned table (installed by the V8 migration,
  and by the Postgres test fixture), plus a deferred constraint trigger that only lets `users.hospital_id` (the active
  hospital) point at a hospital where the user has an ACTIVE membership.

The ORM listener also keeps existing code working: creating `User(hospital_id=…, role=…)` adds the matching
HospitalMembership automatically (the membership, not the user row, is what grants access).
"""

from functools import cache

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session, attributes

# Tables whose hospital_id is not an ownership relation (users.hospital_id is the *active* hospital pointer).
NOT_OWNED = {"users", "hospitals", "organizations"}
# Hospital-owned tables without their own hospital_id: table → (column, parent) the hospital is derived through.
DERIVED = {"stock_batches": ("consumable_id", "consumables")}


class TenantViolation(ValueError):
    """A write tried to link rows of two different hospitals."""


def owned_tables(metadata) -> set[str]:
    return {t.name for t in metadata.tables.values() if "hospital_id" in t.c and t.name not in NOT_OWNED} | set(DERIVED)


def tenant_refs(metadata) -> dict[str, tuple[bool, list[tuple[str, str]]]]:
    """table → (has its own hospital_id, [(fk column, hospital-owned parent table)])."""
    owned = owned_tables(metadata)
    out: dict[str, tuple[bool, list[tuple[str, str]]]] = {}
    for t in metadata.tables.values():
        if t.name in NOT_OWNED:
            continue
        refs = sorted({(fk.parent.name, fk.column.table.name) for fk in t.foreign_keys
                       if fk.column.table.name in owned and fk.parent.name != "hospital_id"})
        own = "hospital_id" in t.c
        if refs and (own or len(refs) >= 2):
            out[t.name] = (own, refs)
    return out


@cache
def _class_refs(cls) -> tuple[bool, list[tuple[str, type]]]:
    from app.models import Base

    by_table = {m.class_.__tablename__: m.class_ for m in Base.registry.mappers}
    spec = tenant_refs(Base.metadata).get(cls.__tablename__)
    if not spec:
        return False, []
    own, refs = spec
    mapper = inspect(cls)
    return own, [(mapper.get_property_by_column(cls.__table__.c[col]).key, by_table[parent]) for col, parent in refs]


def _check(session: Session, obj, is_new: bool) -> None:
    own, refs = _class_refs(type(obj))
    if not refs:
        return
    if not is_new:
        keys = [k for k, _ in refs] + (["hospital_id"] if own else [])
        if not any(attributes.get_history(obj, k).has_changes() for k in keys):
            return
    hid = obj.hospital_id if own else None
    for key, parent_cls in refs:
        value = getattr(obj, key)
        if value is None:
            continue
        with session.no_autoflush:
            parent = session.get(parent_cls, value)
            parent_hid = _hid_of(session, parent)
        if parent_hid is None:
            continue
        if hid is None:
            hid = parent_hid
        elif parent_hid != hid:
            raise TenantViolation(f"{obj.__tablename__}.{key} references a {parent_cls.__tablename__} row of another hospital")


def _hid_of(session: Session, parent) -> int | None:
    if parent is None:
        return None
    if hasattr(parent, "hospital_id"):
        return parent.hospital_id
    col, _table = DERIVED[parent.__tablename__]
    grand = getattr(parent, col)
    if grand is None:
        return None
    from app.models import Consumable

    c = session.get(Consumable, grand)
    return c.hospital_id if c else None


def _check_active_pointer(session: Session, user, pending: list) -> None:
    """users.hospital_id may only point at a hospital where the user holds an ACTIVE membership."""
    from sqlalchemy import select

    from app.models import HospitalMembership, TenantStatus

    if user.hospital_id is None:
        return
    if any(m.hospital_id == user.hospital_id and (m.user is user or m.user_id == user.id)
           and m.status in (None, TenantStatus.ACTIVE) for m in pending):  # None = column default ACTIVE
        return
    if user.id is not None:
        with session.no_autoflush:
            ok = session.scalar(select(HospitalMembership.id).where(
                HospitalMembership.user_id == user.id, HospitalMembership.hospital_id == user.hospital_id,
                HospitalMembership.status == TenantStatus.ACTIVE))
        if ok:
            return
    raise TenantViolation("users.hospital_id must be a hospital where the user has an active membership")


@event.listens_for(Session, "before_flush")
def _before_flush(session: Session, _ctx, _instances) -> None:
    from app.models import HospitalMembership, Role, User

    pending = [o for o in session.new if isinstance(o, HospitalMembership)]
    for obj in list(session.new):
        if isinstance(obj, User) and obj.hospital_id is not None and obj.role and not any(
                m.user is obj for m in pending):
            m = HospitalMembership(user=obj, hospital_id=obj.hospital_id, role=Role(obj.role).value,
                                   department_id=obj.department_id, status="ACTIVE")
            session.add(m)
            pending.append(m)
    for obj in session.new:
        if isinstance(obj, User):
            _check_active_pointer(session, obj, pending)
        else:
            _check(session, obj, True)
    for obj in session.dirty:
        if isinstance(obj, User):
            if attributes.get_history(obj, "hospital_id").has_changes():
                _check_active_pointer(session, obj, pending)
        elif session.is_modified(obj, include_collections=False):
            _check(session, obj, False)


# ---------------------------------------------------------------- PostgreSQL triggers

GUARD_FUNCTION = """
CREATE OR REPLACE FUNCTION medflow_tenant_guard() RETURNS trigger AS $$
DECLARE
    row_json jsonb := to_jsonb(NEW);
    h bigint := NULL;
    ref_hid bigint;
    v bigint;
    i int := 1;
BEGIN
    IF TG_ARGV[0] = 'own' THEN
        h := (row_json->>'hospital_id')::bigint;
    END IF;
    WHILE i < TG_NARGS LOOP
        v := (row_json->>TG_ARGV[i])::bigint;
        IF v IS NOT NULL THEN
            IF TG_ARGV[i + 1] = 'stock_batches' THEN
                SELECT c.hospital_id INTO ref_hid FROM stock_batches b JOIN consumables c ON c.id = b.consumable_id
                WHERE b.id = v;
            ELSE
                EXECUTE format('SELECT hospital_id FROM %I WHERE id = $1', TG_ARGV[i + 1]) INTO ref_hid USING v;
            END IF;
            IF ref_hid IS NOT NULL THEN
                IF h IS NULL THEN
                    h := ref_hid;
                ELSIF h <> ref_hid THEN
                    RAISE EXCEPTION 'tenant violation: %.% references % of another hospital',
                        TG_TABLE_NAME, TG_ARGV[i], TG_ARGV[i + 1] USING ERRCODE = '23514';
                END IF;
            END IF;
        END IF;
        i := i + 2;
    END LOOP;
    RETURN NEW;
END $$ LANGUAGE plpgsql;
"""

ACTIVE_POINTER_FUNCTION = """
CREATE OR REPLACE FUNCTION medflow_active_hospital_guard() RETURNS trigger AS $$
BEGIN
    IF NEW.hospital_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM hospital_memberships m
        WHERE m.user_id = NEW.id AND m.hospital_id = NEW.hospital_id AND m.status = 'ACTIVE') THEN
        RAISE EXCEPTION 'tenant violation: user % has no active membership in hospital %', NEW.id, NEW.hospital_id
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END $$ LANGUAGE plpgsql;
"""


def trigger_statements(refs: dict[str, tuple[bool, list[tuple[str, str]]]]) -> list[str]:
    stmts = [GUARD_FUNCTION, ACTIVE_POINTER_FUNCTION]
    for table, (own, cols) in sorted(refs.items()):
        args = ", ".join([f"'{'own' if own else 'child'}'"] + [f"'{c}', '{p}'" for c, p in cols])
        stmts.append(f"DROP TRIGGER IF EXISTS trg_tenant_guard ON {table}")
        stmts.append(f"CREATE TRIGGER trg_tenant_guard BEFORE INSERT OR UPDATE ON {table} "
                     f"FOR EACH ROW EXECUTE FUNCTION medflow_tenant_guard({args})")
    stmts.append("DROP TRIGGER IF EXISTS trg_active_hospital_guard ON users")
    stmts.append("CREATE CONSTRAINT TRIGGER trg_active_hospital_guard AFTER INSERT OR UPDATE OF hospital_id ON users "
                 "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION medflow_active_hospital_guard()")
    return stmts


def drop_statements(refs: dict[str, tuple[bool, list[tuple[str, str]]]]) -> list[str]:
    stmts = [f"DROP TRIGGER IF EXISTS trg_tenant_guard ON {t}" for t in sorted(refs)]
    stmts += ["DROP TRIGGER IF EXISTS trg_active_hospital_guard ON users",
              "DROP FUNCTION IF EXISTS medflow_tenant_guard()", "DROP FUNCTION IF EXISTS medflow_active_hospital_guard()"]
    return stmts


def install_pg_triggers(connection) -> None:
    """Install the triggers for the current metadata (Postgres only; used by tests that build the schema with create_all)."""
    from sqlalchemy import text

    from app.models import Base

    if connection.dialect.name != "postgresql":
        return
    for s in trigger_statements(tenant_refs(Base.metadata)):
        connection.execute(text(s))
