"""Role → permission matrix (see docs/research/mvp-spec.md)."""

from app.models.enums import Role

READ = "read"
MANAGE_USERS = "users:manage"
MANAGE_HOSPITAL = "hospital:manage"
MANAGE_DEPARTMENTS = "departments:manage"
READ_AUDIT = "audit:read"
MANAGE_CATALOG = "catalog:manage"  # categories + consumables
MANAGE_SUPPLIERS = "suppliers:manage"
STOCK_RECEIVE = "stock:receive"  # receipt, wastage, adjustment
STOCK_ISSUE = "stock:issue"  # issue + return (any department)
STOCK_ISSUE_OWN_DEPT = "stock:issue:own"  # issue + return to own department only
MANAGE_ALERTS = "alerts:manage"
TRAIN_FORECASTS = "forecasts:train"  # V2: retrain demand-forecasting models
MANAGE_PROCEDURES = "procedures:manage"  # V2B: procedure types, schedule and item mappings (any department)
MANAGE_PROCEDURES_OWN_DEPT = "procedures:manage:own"  # V2B: same, restricted to the user's own department
TRAIN_RISK = "risk:train"  # V3: retrain the stockout-risk model / refresh risk for all items
PROCUREMENT_RECOMMEND = "procurement:recommend"  # V5: generate procurement recommendations (no orders)
PROCUREMENT_APPROVE = "procurement:approve"  # V5: approve / modify / reject recommendations (records supplier orders)
PROCUREMENT_CONFIGURE = "procurement:configure"  # V5: edit the expected-cost model parameters
GRAPH_SYNC = "graph:sync"  # V6: rebuild the knowledge-graph projection from PostgreSQL on demand
INTEGRATIONS_RUN = "integrations:run"  # V9: see integration monitoring, upload files, run syncs, retry, resolve reconciliation
INTEGRATIONS_MANAGE = "integrations:manage"  # V9: configure integration sources, mappings and API keys
PILOTS_MANAGE = "pilots:manage"  # V10: create / configure pilots, lifecycle, readiness confirmations, report snapshots
PILOTS_CONTRIBUTE = "pilots:contribute"  # V10: report and work on pilot issues, give feedback

ROLE_PERMISSIONS: dict[Role, set[str]] = {
    Role.ADMIN: {
        READ, MANAGE_USERS, MANAGE_HOSPITAL, MANAGE_DEPARTMENTS, READ_AUDIT, MANAGE_CATALOG,
        MANAGE_SUPPLIERS, STOCK_RECEIVE, STOCK_ISSUE, MANAGE_ALERTS, TRAIN_FORECASTS, MANAGE_PROCEDURES,
        TRAIN_RISK, PROCUREMENT_RECOMMEND, PROCUREMENT_APPROVE, PROCUREMENT_CONFIGURE, GRAPH_SYNC,
        INTEGRATIONS_RUN, INTEGRATIONS_MANAGE, PILOTS_MANAGE, PILOTS_CONTRIBUTE,
    },
    Role.PROCUREMENT_MANAGER: {READ, MANAGE_CATALOG, MANAGE_SUPPLIERS, MANAGE_ALERTS, TRAIN_FORECASTS, TRAIN_RISK,
                               PROCUREMENT_RECOMMEND, PROCUREMENT_APPROVE, PROCUREMENT_CONFIGURE, GRAPH_SYNC,
                               INTEGRATIONS_RUN, PILOTS_CONTRIBUTE},
    Role.INVENTORY_MANAGER: {READ, MANAGE_CATALOG, STOCK_RECEIVE, STOCK_ISSUE, MANAGE_ALERTS, TRAIN_FORECASTS, TRAIN_RISK,
                             PROCUREMENT_RECOMMEND, GRAPH_SYNC, INTEGRATIONS_RUN, PILOTS_CONTRIBUTE},
    Role.DEPARTMENT_MANAGER: {READ, STOCK_ISSUE_OWN_DEPT, MANAGE_PROCEDURES_OWN_DEPT, PILOTS_CONTRIBUTE},
    Role.VIEWER: {READ},
}


def permissions_for(role: str) -> set[str]:
    try:
        return ROLE_PERMISSIONS[Role(role)]
    except ValueError:
        return set()
