from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    PROCUREMENT_MANAGER = "procurement_manager"
    INVENTORY_MANAGER = "inventory_manager"
    DEPARTMENT_MANAGER = "department_manager"
    VIEWER = "viewer"


class MovementType(StrEnum):
    RECEIPT = "RECEIPT"  # stock in from supplier (creates a batch)
    ISSUE = "ISSUE"  # stock out to a department (FEFO)
    RETURN = "RETURN"  # department returns unused stock to a batch
    WASTAGE = "WASTAGE"  # expired / damaged / contaminated disposal
    ADJUSTMENT = "ADJUSTMENT"  # physical count correction


class StockStatus(StrEnum):
    OUT_OF_STOCK = "OUT_OF_STOCK"
    LOW = "LOW"
    OK = "OK"
    OVERSTOCK = "OVERSTOCK"


class AlertType(StrEnum):
    OUT_OF_STOCK = "OUT_OF_STOCK"
    LOW_STOCK = "LOW_STOCK"
    EXPIRED = "EXPIRED"
    EXPIRING_SOON = "EXPIRING_SOON"
    STOCKOUT_RISK = "STOCKOUT_RISK"  # V3: forecast-driven stockout risk
    SUPPLIER_DELAY = "SUPPLIER_DELAY"  # V4: a supplier order is past its expected delivery date


class AlertSeverity(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class AlertStatus(StrEnum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


ACTIVE_ALERT_STATUSES = (AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED)


class ProcedureStatus(StrEnum):
    """V2B — lifecycle of a procedure schedule row (counts only; no patient data)."""

    SCHEDULED = "SCHEDULED"  # planned; used for future demand
    COMPLETED = "COMPLETED"  # performed; used as history for training
    CANCELLED = "CANCELLED"  # never contributes to demand


class RiskLevel(StrEnum):
    """V3 stockout risk level."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class SupplierOrderStatus(StrEnum):
    """V4 — lifecycle of an order placed with a supplier (tracking only; no approval workflow)."""

    OPEN = "OPEN"  # placed, nothing received yet
    PARTIAL = "PARTIAL"  # some quantity received
    RECEIVED = "RECEIVED"  # closed: fully received, or closed short (quantity_received < quantity_ordered)
    CANCELLED = "CANCELLED"  # closed without (further) delivery


ACTIVE_ORDER_STATUSES = (SupplierOrderStatus.OPEN, SupplierOrderStatus.PARTIAL)


class RecommendationStatus(StrEnum):
    """V5 — lifecycle of a procurement recommendation (human approval before any order is recorded)."""

    PENDING = "PENDING"  # waiting for review
    APPROVED = "APPROVED"  # approved as recommended or modified → supplier order(s) recorded
    REJECTED = "REJECTED"  # rejected with a reason
    SUPERSEDED = "SUPERSEDED"  # replaced by a newer recommendation for the same item before review


class TenantStatus(StrEnum):
    """V8: status of an organization, hospital or membership. Only ACTIVE grants access."""

    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class OrgRole(StrEnum):
    """V8: role inside an organization (hospital group). Hospital roles stay in `Role`."""

    ORG_ADMIN = "organization_admin"
