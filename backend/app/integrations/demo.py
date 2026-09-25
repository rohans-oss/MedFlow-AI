"""V9 — demo integration sources for the seeded demo hospitals (configuration only: nothing is imported at seed time,
so the demo hospitals' V1–V8 data stay exactly as before). Everything here is labelled simulated / demo."""

from sqlalchemy.orm import Session

from app.integrations import reference_erp
from app.integrations.entities import ORDER
from app.integrations.sources import create_source
from app.models import Hospital, IntegrationMapping

SIMULATED_ERP = "Reference ERP (simulated)"


def seed_sources(db: Session, hospital: Hospital) -> None:
    erp = create_source(db, hospital.id, None, SIMULATED_ERP, "erp", "rest_pull", ORDER,
                        {"base_url": "reference-erp", "auth_env": "REFERENCE_ERP_TOKEN", "schedule_minutes": 60,
                         "page_size": 200, "reconciliation_tolerance": 0}, is_simulated=True)
    for entity, field_map in reference_erp.MAPPINGS.items():
        db.add(IntegrationMapping(hospital_id=hospital.id, source_id=erp.id, entity=entity, field_map=field_map,
                                  defaults={}))
    create_source(db, hospital.id, None, "Stores spreadsheet (CSV / Excel)", "spreadsheet", "upload",
                  ["departments", "suppliers", "items", "consumption", "inventory"], {})
    create_source(db, hospital.id, None, "Procurement system (API push)", "procurement", "api_push",
                  ["supplier_items", "purchase_orders", "deliveries"], {})
    db.flush()
