"""Demo data for Version 1.

Creates a FICTIONAL 180-bed hospital with 90 days of simulated stock history. Nothing here is real
hospital data.

V2B adds SYNTHETIC procedure data: procedure types, "kit" mappings (illustrative quantities — NOT clinical
standards), 90 days of completed/cancelled procedure history and 30 days of future schedules. Part of the
operating-theatre consumption is simulated as procedure-driven (with noise: true usage per procedure differs
from the kit quantity, and non-procedure demand continues), so the procedure-aware model has to *earn* its place. The V2 synthetic data generator (ml/data_generation) will replace this simulation
with a procedure-driven one.

Usage:
    python -m app.seed            # seed if the demo hospital does not exist
    python -m app.seed --reset    # delete the demo hospital and re-seed
"""

import argparse
import math
import random
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import business_today, hash_password
from app.db.session import SessionLocal
from app.models import (
    Consumable,
    ConsumableCategory,
    Department,
    Hospital,
    HospitalMembership,
    Organization,
    OrganizationMembership,
    OrgRole,
    ProcedureItemMapping,
    ProcedureSchedule,
    ProcedureStatus,
    ProcedureType,
    Role,
    StockBatch,
    Supplier,
    SupplierDelivery,
    SupplierOrder,
    SupplierOrderStatus,
    SupplierProduct,
    User,
)
from app.services import alerts, stock

DEMO_CODE = "SUNRISE-BLR"
DEMO_ORG_CODE = "CARENET-DEMO"
DEMO_ORG_NAME = "CareNet Hospitals Group (Demo)"
DEMO_PASSWORD = "Demo@1234"
DAYS = 90
TZ = ZoneInfo(settings.TIMEZONE)

DEPARTMENTS = [
    ("ORTHO", "Orthopaedics OT", "Joint replacement, trauma and arthroscopy theatre"),
    ("GSURG", "General Surgery OT", "Laparoscopic and open general surgery theatre"),
    ("ICU", "Intensive Care Unit", "12-bed mixed medical/surgical ICU"),
    ("ER", "Emergency", "24x7 emergency and trauma"),
    ("WARD", "General Wards", "Medical and surgical inpatient wards"),
    ("LAB", "Diagnostic Laboratory", "Haematology, biochemistry, microbiology"),
    ("OPD", "Outpatient Clinics", "Consultation and minor procedures"),
    ("OBG", "Obstetrics & Gynaecology OT", "Obstetric theatre (added in V2B demo data)"),
]

CATEGORIES = [
    "Gloves & PPE", "Syringes & Needles", "IV & Infusion", "Sutures", "Wound Care & Dressings",
    "Catheters & Tubes", "Drapes & Gowns", "Orthopaedic Consumables", "Lab Consumables", "Respiratory",
]

# sku, name, category, unit, unit_cost ₹, base daily use, shelf life days, {dept: weight}
ITEMS = [
    ("GLV-EXM-M", "Examination gloves, nitrile, medium", "Gloves & PPE", "pair", 6.5, 260, 1095, {"ICU": 3, "ER": 3, "WARD": 4, "LAB": 2, "OPD": 2}),
    ("GLV-SRG-7", "Surgical gloves, sterile, size 7", "Gloves & PPE", "pair", 38, 70, 1095, {"ORTHO": 4, "GSURG": 4, "ER": 1}),
    ("GLV-SRG-75", "Surgical gloves, sterile, size 7.5", "Gloves & PPE", "pair", 38, 55, 1095, {"ORTHO": 5, "GSURG": 3}),
    ("MSK-SRG-3P", "Surgical mask, 3-ply", "Gloves & PPE", "piece", 2.8, 320, 730, {"ORTHO": 1, "GSURG": 1, "ICU": 2, "ER": 2, "WARD": 3, "OPD": 3}),
    ("MSK-N95", "N95 respirator", "Gloves & PPE", "piece", 42, 22, 1095, {"ICU": 5, "ER": 3, "LAB": 2}),
    ("SYR-2ML", "Syringe 2 ml with needle", "Syringes & Needles", "piece", 3.2, 240, 1825, {"ICU": 3, "ER": 3, "WARD": 4, "OPD": 2}),
    ("SYR-5ML", "Syringe 5 ml with needle", "Syringes & Needles", "piece", 4.1, 180, 1825, {"ICU": 3, "ER": 2, "WARD": 4, "LAB": 2}),
    ("SYR-10ML", "Syringe 10 ml", "Syringes & Needles", "piece", 6.0, 90, 1825, {"ICU": 4, "ORTHO": 1, "GSURG": 1, "WARD": 3}),
    ("SYR-50ML", "Syringe 50 ml, luer lock", "Syringes & Needles", "piece", 28, 18, 1825, {"ICU": 6, "ER": 1}),
    ("NDL-IVC-20", "IV cannula 20G", "Syringes & Needles", "piece", 24, 60, 1460, {"ICU": 2, "ER": 3, "WARD": 4, "ORTHO": 1, "GSURG": 1}),
    ("NDL-IVC-22", "IV cannula 22G", "Syringes & Needles", "piece", 24, 35, 1460, {"ER": 3, "WARD": 4, "OPD": 1}),
    ("IV-SET-ADL", "IV infusion set, adult", "IV & Infusion", "piece", 18, 85, 1460, {"ICU": 3, "ER": 3, "WARD": 5}),
    ("IV-NS-500", "Normal saline 0.9%, 500 ml", "IV & Infusion", "bottle", 22, 140, 540, {"ICU": 3, "ER": 3, "WARD": 5, "ORTHO": 1, "GSURG": 1}),
    ("IV-RL-500", "Ringer lactate, 500 ml", "IV & Infusion", "bottle", 26, 70, 540, {"ICU": 2, "ER": 2, "ORTHO": 2, "GSURG": 2, "WARD": 2}),
    ("IV-EXT-3W", "3-way stopcock with extension", "IV & Infusion", "piece", 21, 40, 1460, {"ICU": 5, "ORTHO": 1, "GSURG": 1}),
    ("SUT-VIC-1", "Absorbable suture, polyglactin 1-0", "Sutures", "foil", 310, 9, 1825, {"ORTHO": 3, "GSURG": 5}),
    ("SUT-VIC-20", "Absorbable suture, polyglactin 2-0", "Sutures", "foil", 290, 11, 1825, {"ORTHO": 2, "GSURG": 5, "ER": 1}),
    ("SUT-NYL-30", "Nylon suture 3-0", "Sutures", "foil", 120, 14, 1825, {"ER": 4, "OPD": 3, "GSURG": 2}),
    ("SUT-STP-35", "Skin stapler, 35W", "Sutures", "piece", 950, 3, 1825, {"ORTHO": 5, "GSURG": 3}),
    ("DRS-GAU-10", "Gauze swab 10x10 cm, sterile (pack of 10)", "Wound Care & Dressings", "pack", 14, 150, 1095, {"ORTHO": 2, "GSURG": 2, "ER": 3, "WARD": 4, "OPD": 2}),
    ("DRS-ABD-PAD", "Abdominal pad, sterile", "Wound Care & Dressings", "piece", 19, 35, 1095, {"GSURG": 6, "WARD": 2}),
    ("DRS-FILM-10", "Transparent film dressing 10x12 cm", "Wound Care & Dressings", "piece", 48, 30, 1095, {"ICU": 4, "WARD": 3, "ORTHO": 2}),
    ("DRS-CRP-10", "Crepe bandage 10 cm", "Wound Care & Dressings", "roll", 35, 28, 1460, {"ORTHO": 5, "ER": 3}),
    ("DRS-POP-15", "Plaster of Paris bandage 15 cm", "Orthopaedic Consumables", "roll", 55, 16, 730, {"ORTHO": 6, "ER": 3}),
    ("ORT-CST-PAD", "Orthopaedic cast padding 10 cm", "Orthopaedic Consumables", "roll", 42, 15, 1460, {"ORTHO": 6, "ER": 2}),
    ("ORT-BNWAX", "Bone wax 2.5 g", "Orthopaedic Consumables", "piece", 160, 3, 1825, {"ORTHO": 8}),
    ("ORT-SAWBL", "Oscillating saw blade", "Orthopaedic Consumables", "piece", 1450, 1.2, 1825, {"ORTHO": 10}),
    ("ORT-LAVAG", "Pulsed lavage kit", "Orthopaedic Consumables", "kit", 2400, 0.9, 1095, {"ORTHO": 10}),
    ("CTH-FOL-16", "Foley catheter 16Fr, 2-way", "Catheters & Tubes", "piece", 38, 18, 1460, {"ICU": 3, "WARD": 3, "ORTHO": 2, "GSURG": 2}),
    ("CTH-URB-2L", "Urine collection bag 2 L", "Catheters & Tubes", "piece", 17, 22, 1460, {"ICU": 3, "WARD": 4, "ORTHO": 1, "GSURG": 1}),
    ("CTH-RT-16", "Ryle's tube 16Fr", "Catheters & Tubes", "piece", 14, 6, 1460, {"ICU": 5, "WARD": 2, "GSURG": 2}),
    ("CTH-SUC-12", "Suction catheter 12Fr", "Catheters & Tubes", "piece", 9, 75, 1460, {"ICU": 8, "ER": 2}),
    ("DRP-SRG-UNI", "Universal surgical drape pack", "Drapes & Gowns", "pack", 420, 8, 1095, {"ORTHO": 4, "GSURG": 5}),
    ("GWN-SRG-L", "Surgical gown, sterile, large", "Drapes & Gowns", "piece", 140, 26, 1095, {"ORTHO": 5, "GSURG": 5}),
    ("CAP-BOUF", "Bouffant cap", "Drapes & Gowns", "piece", 1.5, 120, 1825, {"ORTHO": 3, "GSURG": 3, "ICU": 2, "LAB": 1}),
    ("LAB-VAC-EDTA", "Vacutainer EDTA 3 ml", "Lab Consumables", "tube", 7.5, 190, 365, {"LAB": 10}),
    ("LAB-VAC-SST", "Vacutainer SST 5 ml", "Lab Consumables", "tube", 9.5, 150, 365, {"LAB": 10}),
    ("LAB-URC", "Urine container, sterile 30 ml", "Lab Consumables", "piece", 4.0, 80, 1095, {"LAB": 7, "OPD": 3}),
    ("LAB-GLU-STR", "Glucometer test strip", "Lab Consumables", "strip", 11, 110, 300, {"ICU": 4, "WARD": 5, "ER": 1}),
    ("RSP-NEB-MSK", "Nebuliser mask kit, adult", "Respiratory", "kit", 65, 12, 1460, {"ER": 4, "WARD": 3, "ICU": 2}),
    ("RSP-O2-MSK", "Oxygen mask, adult", "Respiratory", "piece", 32, 14, 1460, {"ER": 3, "WARD": 3, "ICU": 4}),
    ("RSP-HMEF", "HME filter, adult", "Respiratory", "piece", 115, 10, 1460, {"ICU": 9, "ORTHO": 1}),
]

SUPPLIERS = [
    # code, name, city, lead days, reliability (prob on time), price factor, categories supplied
    ("KSD", "Karnataka Surgical Distributors", "Bengaluru", 3, 0.93, 1.00,
     {"Gloves & PPE", "Syringes & Needles", "Wound Care & Dressings", "Drapes & Gowns"}),
    ("NMS", "Nandi Medisupplies", "Bengaluru", 2, 0.80, 0.96,
     {"Gloves & PPE", "Syringes & Needles", "IV & Infusion", "Catheters & Tubes"}),
    ("DHT", "Deccan Healthcare Traders", "Hyderabad", 5, 0.90, 0.92,
     {"IV & Infusion", "Respiratory", "Catheters & Tubes", "Wound Care & Dressings"}),
    ("CPS", "Cauvery Pharma & Surgicals", "Mysuru", 4, 0.88, 1.03, {"Sutures", "Drapes & Gowns", "Wound Care & Dressings"}),
    ("OPI", "OrthoPrime Consumables", "Chennai", 7, 0.85, 1.00, {"Orthopaedic Consumables", "Sutures"}),
    ("SCL", "Silicon City Lab Supplies", "Bengaluru", 3, 0.95, 1.01, {"Lab Consumables", "Gloves & PPE"}),
]

# Items that end the simulation in trouble, to make the demo realistic.
STARVED = {"SUT-STP-35", "GLV-SRG-75", "RSP-HMEF", "DRS-POP-15"}
# Over-bought lots with short expiry: sku -> (days of demand bought, expiry offset from today).
# FEFO consumes them first, but demand is not enough to use them up -> expired / expiring stock remains.
EXPIRY_SCENARIOS = {"RSP-NEB-MSK": (100, -5), "ORT-BNWAX": (130, 25), "LAB-GLU-STR": (95, 10), "IV-NS-500": (93, 40)}

# ---------------- V2B: SYNTHETIC procedures ----------------
# code, name, department, avg minutes, mean count per weekday (Mon..Sun), synthetic kit {sku: qty per procedure}
PROCEDURES = [
    ("TKR", "Total knee replacement", "ORTHO", 120, [2, 2, 2, 2, 2, 1, 0],
     {"GLV-SRG-75": 6, "GLV-SRG-7": 4, "GWN-SRG-L": 4, "DRP-SRG-UNI": 1, "SUT-VIC-1": 3, "SUT-STP-35": 1,
      "ORT-SAWBL": 1, "ORT-LAVAG": 1, "ORT-BNWAX": 1, "DRS-GAU-10": 6, "IV-RL-500": 3, "CTH-FOL-16": 1, "DRS-CRP-10": 2}),
    ("THR", "Total hip replacement", "ORTHO", 110, [1, 1, 1, 1, 1, 0.5, 0],
     {"GLV-SRG-75": 6, "GLV-SRG-7": 4, "GWN-SRG-L": 4, "DRP-SRG-UNI": 1, "SUT-VIC-1": 3, "SUT-STP-35": 1,
      "ORT-SAWBL": 1, "ORT-LAVAG": 1, "DRS-GAU-10": 6, "IV-RL-500": 3, "CTH-FOL-16": 1}),
    ("ORIF", "Fracture fixation (ORIF)", "ORTHO", 90, [2, 2, 2, 2, 2, 1.5, 1],
     {"GLV-SRG-75": 4, "GLV-SRG-7": 2, "GWN-SRG-L": 3, "DRP-SRG-UNI": 1, "SUT-VIC-1": 2, "DRS-POP-15": 3,
      "ORT-CST-PAD": 2, "DRS-GAU-10": 4}),
    ("ARTH", "Knee arthroscopy", "ORTHO", 60, [1.5, 1.5, 1.5, 1.5, 1.5, 1, 0],
     {"GLV-SRG-75": 3, "GWN-SRG-L": 2, "DRP-SRG-UNI": 1, "IV-RL-500": 6, "DRS-CRP-10": 1}),
    ("HERN", "Hernia repair", "GSURG", 60, [2, 2, 2, 2, 2, 1, 0],
     {"GLV-SRG-7": 4, "GWN-SRG-L": 3, "DRP-SRG-UNI": 1, "SUT-VIC-20": 3, "SUT-VIC-1": 1, "DRS-ABD-PAD": 2, "DRS-GAU-10": 4}),
    ("LCHOL", "Laparoscopic cholecystectomy", "GSURG", 75, [3, 3, 3, 3, 3, 1.5, 0],
     {"GLV-SRG-7": 4, "GWN-SRG-L": 3, "DRP-SRG-UNI": 1, "SUT-VIC-20": 2, "IV-NS-500": 3, "DRS-GAU-10": 3}),
    ("APPX", "Appendectomy", "GSURG", 60, [1, 1, 1, 1, 1, 1, 1],
     {"GLV-SRG-7": 4, "GWN-SRG-L": 3, "DRP-SRG-UNI": 1, "SUT-VIC-20": 2, "DRS-ABD-PAD": 2, "DRS-GAU-10": 4}),
    ("CSEC", "Caesarean section", "OBG", 50, [2.5, 2.5, 2.5, 2.5, 2.5, 2, 1.5],
     {"GLV-SRG-7": 6, "GWN-SRG-L": 4, "DRP-SRG-UNI": 1, "SUT-VIC-1": 3, "SUT-VIC-20": 2, "DRS-ABD-PAD": 3,
      "DRS-GAU-10": 10, "IV-NS-500": 2, "CTH-FOL-16": 1, "CTH-URB-2L": 1, "SYR-5ML": 3}),
    ("DIAG", "General diagnostic procedure", "OPD", 20, [12, 12, 12, 12, 12, 6, 0],
     {"GLV-EXM-M": 2, "SYR-5ML": 1, "LAB-VAC-EDTA": 2, "LAB-VAC-SST": 1, "DRS-GAU-10": 1}),
]
CANCEL_RATE = 0.06
# Future "joint replacement camp": knee/hip volumes ×2.5 on these future day offsets (demo scenario, synthetic)
CAMP = {"codes": {"TKR", "THR"}, "days": range(3, 10), "factor": 2.5}
FUTURE_DAYS = 30


@dataclass
class Profile:
    """V8: one demo hospital. SUNRISE uses exactly the V1–V7 constants and random streams, so its data (and every
    V2–V7 demo result) is unchanged; the other profiles are deliberately different hospitals."""

    code: str
    name: str
    city: str
    state: str
    bed_count: int
    org_code: str
    org_name: str
    users: list  # (email, full name, role, department code | None)
    departments: list
    categories: list
    items: list
    suppliers: list
    starved: set
    expiry_scenarios: dict
    procedures: list
    camp: dict
    seeds: tuple[int, int, int] = (42, 7, 11)  # daily simulation, procedures, V4 order history
    days: int = DAYS


SUNRISE = Profile(
    code=DEMO_CODE, name="Sunrise Multispecialty Hospital (Demo)", city="Bengaluru", state="Karnataka", bed_count=180,
    org_code=DEMO_ORG_CODE, org_name=DEMO_ORG_NAME,
    users=[("admin@sunrise.demo", "Suresh Menon", Role.ADMIN, None),
           ("procurement@sunrise.demo", "Priya Raghavan", Role.PROCUREMENT_MANAGER, None),
           ("inventory@sunrise.demo", "Ramesh Gowda", Role.INVENTORY_MANAGER, None),
           ("ortho@sunrise.demo", "Dr. Kavitha Rao", Role.DEPARTMENT_MANAGER, "ORTHO"),
           ("viewer@sunrise.demo", "Anil Kumar", Role.VIEWER, None)],
    departments=DEPARTMENTS, categories=CATEGORIES, items=ITEMS, suppliers=SUPPLIERS, starved=STARVED,
    expiry_scenarios=EXPIRY_SCENARIOS, procedures=PROCEDURES, camp=CAMP,
)


def _derive_items(keep_depts: set[str], extra_weights: dict[str, dict], scale: dict[str, float], default_scale: float,
                  drop: set[str] = frozenset()) -> list:
    """Items of another demo hospital: weights restricted to its departments, demand rescaled per category."""
    out = []
    for sku, name, cat, unit, cost, base, shelf, weights in ITEMS:
        w = {d: v for d, v in weights.items() if d in keep_depts} | extra_weights.get(sku, {})
        if not w or sku in drop:
            continue
        out.append((sku, name, cat, unit, cost, round(base * scale.get(cat, default_scale), 2), shelf, w))
    return out


_B_DEPTS = [d for d in DEPARTMENTS if d[0] != "ORTHO"] + [("PAED", "Paediatrics", "Paediatric ward and clinic")]
LAKEVIEW = Profile(
    code="LAKEVIEW-MYS", name="Lakeview Community Hospital (Demo)", city="Mysuru", state="Karnataka", bed_count=120,
    org_code=DEMO_ORG_CODE, org_name=DEMO_ORG_NAME,
    users=[("admin@lakeview.demo", "Meera Iyer", Role.ADMIN, None),
           ("procurement@lakeview.demo", "Farhan Sheikh", Role.PROCUREMENT_MANAGER, None),
           ("inventory@lakeview.demo", "Lakshmi Devi", Role.INVENTORY_MANAGER, None),
           ("obg@lakeview.demo", "Dr. Asha Kulkarni", Role.DEPARTMENT_MANAGER, "OBG"),
           ("viewer@lakeview.demo", "Rahul Bhat", Role.VIEWER, None)],
    departments=_B_DEPTS,
    categories=CATEGORIES,
    items=_derive_items({d[0] for d in _B_DEPTS},
                        {"SYR-2ML": {"PAED": 3}, "NDL-IVC-22": {"PAED": 3}, "RSP-NEB-MSK": {"PAED": 3},
                         "RSP-O2-MSK": {"PAED": 2}, "IV-SET-ADL": {"PAED": 1}},
                        {"Gloves & PPE": 0.45, "Sutures": 0.8, "Lab Consumables": 1.3, "Respiratory": 1.2}, 0.7,
                        drop={"ORT-BNWAX", "ORT-SAWBL", "ORT-LAVAG"}),
    suppliers=[
        ("KSD", "Karnataka Surgical Distributors", "Bengaluru", 4, 0.72, 1.02,
         {"Gloves & PPE", "Syringes & Needles", "Wound Care & Dressings", "Drapes & Gowns"}),
        ("MSV", "Mysuru Surgical Ventures", "Mysuru", 2, 0.95, 1.04,
         {"Syringes & Needles", "IV & Infusion", "Sutures", "Catheters & Tubes", "Orthopaedic Consumables"}),
        ("CPS", "Cauvery Pharma & Surgicals", "Mysuru", 3, 0.62, 0.97, {"Sutures", "Drapes & Gowns", "Wound Care & Dressings"}),
        ("DHT", "Deccan Healthcare Traders", "Hyderabad", 6, 0.86, 0.92,
         {"IV & Infusion", "Respiratory", "Catheters & Tubes", "Wound Care & Dressings"}),
        ("SCL", "Silicon City Lab Supplies", "Bengaluru", 4, 0.93, 1.01, {"Lab Consumables", "Gloves & PPE"}),
    ],
    starved={"SUT-VIC-20", "IV-RL-500", "CTH-FOL-16"},
    expiry_scenarios={"LAB-VAC-SST": (90, 12), "DRS-FILM-10": (120, -3)},
    procedures=[p for p in PROCEDURES if p[0] in {"HERN", "LCHOL", "APPX"}] + [
        ("CSEC", "Caesarean section", "OBG", 50, [4, 4, 4, 4, 4, 3, 2.5], dict(PROCEDURES[7][5])),
        ("DIAG", "General diagnostic procedure", "OPD", 20, [8, 8, 8, 8, 8, 4, 0], dict(PROCEDURES[8][5])),
    ],
    camp={"codes": set(), "days": range(0), "factor": 1.0},
    seeds=(142, 107, 111),
)

_C_DEPTS = [d for d in DEPARTMENTS if d[0] in {"ER", "WARD", "LAB", "OPD"}]
HARBOR = Profile(
    code="HARBOR-KOCHI", name="Harbor Clinic & Nursing Home (Demo)", city="Kochi", state="Kerala", bed_count=60,
    org_code="HARBOR-DEMO", org_name="Harbor Health Trust (Demo)",
    users=[("admin@harbor.demo", "Thomas Varghese", Role.ADMIN, None),
           ("viewer@harbor.demo", "Anjali Nair", Role.VIEWER, None)],
    departments=_C_DEPTS, categories=CATEGORIES,
    items=_derive_items({d[0] for d in _C_DEPTS}, {}, {"Lab Consumables": 0.5}, 0.35),
    suppliers=[
        ("CKM", "Cochin Medical Mart", "Kochi", 2, 0.90, 1.00, set(CATEGORIES)),
        ("DHT", "Deccan Healthcare Traders", "Hyderabad", 6, 0.78, 0.93,
         {"IV & Infusion", "Respiratory", "Catheters & Tubes", "Wound Care & Dressings"}),
    ],
    starved={"GLV-EXM-M"},
    expiry_scenarios={},
    procedures=[("DIAG", "General diagnostic procedure", "OPD", 20, [6, 6, 6, 6, 6, 3, 0], dict(PROCEDURES[8][5]))],
    camp={"codes": set(), "days": range(0), "factor": 1.0},
    seeds=(242, 207, 211),
    days=60,
)

PROFILES = [SUNRISE, LAKEVIEW, HARBOR]
# Cross-hospital demo accounts (V8): an organization admin of the CareNet group who is also hospital admin of both
# of its hospitals, and a platform admin with no hospital membership.
GROUP_ADMIN = ("admin@carenet.demo", "Nandini Rao")
PLATFORM_ADMIN = ("platform@medflow.demo", "Platform Operator")
DEMO_EMAIL_DOMAINS = ("@sunrise.demo", "@lakeview.demo", "@harbor.demo", "@carenet.demo", "@medflow.demo")


class Clock:
    """Monotonic event clock: events on a day get increasing timestamps, and nothing lands in the future."""

    def __init__(self, rng: random.Random):
        self.rng = rng
        self.now = datetime.now(TZ)
        self.day: date | None = None
        self.t = self.now
        self.scale = 1.0

    def at(self, day: date) -> datetime:
        if day != self.day:
            self.day = day
            self.t = datetime.combine(day, time(7, 0), tzinfo=TZ)
            if day == self.now.date():
                if self.now < self.t:
                    self.t = datetime.combine(day, time(0, 0), tzinfo=TZ)
                available = max((self.now - self.t).total_seconds() / 60 - 5, 1)
                self.scale = min(1.0, available / 900)
            else:
                self.scale = 1.0
        self.t += timedelta(minutes=self.rng.uniform(0.5, 12) * self.scale)
        return self.t.astimezone(UTC)


def _poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= L:
            return k
        k += 1


def reset(db: Session) -> None:
    """Delete every demo hospital (cascades to everything it owns), the demo accounts and the demo organizations."""
    from sqlalchemy import delete, or_

    from app.models import IntegrationSource

    for p in PROFILES:
        h = db.scalar(select(Hospital).where(Hospital.code == p.code))
        if h:
            service_users = list(db.scalars(select(IntegrationSource.service_user_id)
                                            .where(IntegrationSource.hospital_id == h.id)))  # V9 integration accounts
            db.delete(h)
            db.commit()
            db.execute(delete(User).where(User.id.in_([u for u in service_users if u])))
            db.commit()
    db.execute(delete(User).where(or_(*[User.email.like(f"%{d}") for d in DEMO_EMAIL_DOMAINS])))
    for code in sorted({p.org_code for p in PROFILES}):
        org = db.scalar(select(Organization).where(Organization.code == code))
        if org is not None and not db.scalar(select(Hospital.id).where(Hospital.organization_id == org.id)):
            db.delete(org)
    db.commit()


def _organization(db: Session, p: Profile) -> Organization:
    org = db.scalar(select(Organization).where(Organization.code == p.org_code))
    if org is None:
        org = Organization(name=p.org_name, code=p.org_code, is_demo=True)
        db.add(org)
        db.flush()
    return org


def seed(db: Session, days: int | None = None, verbose: bool = True, profile: Profile = SUNRISE) -> Hospital:
    """Seed one demo hospital (default: Sunrise, exactly as in V1–V7)."""
    p = profile
    days = p.days if days is None else days
    rng = random.Random(p.seeds[0])
    clock = Clock(rng)
    today = business_today()
    start = today - timedelta(days=days)

    org = _organization(db, p)
    hospital = Hospital(name=p.name, code=p.code, city=p.city, state=p.state, bed_count=p.bed_count,
                        expiry_warning_days=60, is_demo=True, organization_id=org.id)
    db.add(hospital)
    db.flush()

    depts = {code: Department(hospital_id=hospital.id, code=code, name=name, description=desc)
             for code, name, desc in p.departments}
    db.add_all(depts.values())
    cats = {n: ConsumableCategory(hospital_id=hospital.id, name=n) for n in p.categories}
    db.add_all(cats.values())
    db.flush()

    pw = hash_password(DEMO_PASSWORD)
    users = [User(hospital_id=hospital.id, email=email, full_name=name, role=role, hashed_password=pw,
                  department_id=depts[dept].id if dept else None) for email, name, role, dept in p.users]
    db.add_all(users)
    db.flush()
    store = next((u for u in users if u.role == Role.INVENTORY_MANAGER), users[0])

    suppliers = {}
    for code, name, city, lead, _rel, _pf, _cats in p.suppliers:
        suppliers[code] = Supplier(
            hospital_id=hospital.id, code=code, name=name, city=city, default_lead_time_days=lead,
            contact_person="Sales desk", phone="+91 80 0000 0000", email=f"orders@{code.lower()}.demo",
            notes="Fictional supplier (demo data).",
        )
    db.add_all(suppliers.values())
    db.flush()

    # V2B: expected procedure-driven use per item → non-procedure demand is scaled down so volumes stay plausible
    proc_expected = _expected_procedure_use(p)
    items: dict[str, dict] = {}
    for sku, name, cat, unit, cost, raw_base, shelf, weights in p.items:
        e_proc = proc_expected.get(sku, 0.0)
        nonproc_factor = max(0.35, 1 - e_proc / raw_base) if e_proc else 1.0
        base = raw_base * nonproc_factor + e_proc  # effective expected daily demand (used for stock policy)
        lead = 5
        reorder = int(math.ceil(base * (lead + 3)))  # cover lead time + 3 days buffer
        c = Consumable(hospital_id=hospital.id, sku=sku, name=name, category_id=cats[cat].id, unit=unit,
                       unit_cost=Decimal(str(cost)), reorder_level=max(reorder, 2), max_level=max(int(base * 30), 10))
        db.add(c)
        db.flush()
        offers = []
        for code, _n, _c, lead_days, rel, pf, supplied in p.suppliers:
            if cat in supplied:
                price = round(cost * pf * rng.uniform(0.97, 1.03), 2)
                sp = SupplierProduct(supplier_id=suppliers[code].id, consumable_id=c.id, unit_price=Decimal(str(price)),
                                     lead_time_days=lead_days, moq=max(1, int(base * rng.choice([3, 5, 7]))),
                                     supplier_sku=f"{code}-{sku}")
                db.add(sp)
                offers.append((sp, rel))
        best = min(offers, key=lambda o: float(o[0].unit_price) * (2 - o[1]))
        best[0].is_preferred = True
        items[sku] = {"c": c, "base": base, "nonproc": raw_base * nonproc_factor, "shelf": shelf, "weights": weights,
                      "offer": best, "pending": None}
    db.flush()

    # ---- V2B: synthetic procedure types, kit mappings and schedule (history + future) ----
    prng = random.Random(p.seeds[1])  # separate stream for procedure data
    proc_usage = _seed_procedures(db, hospital, depts, items, prng, start, days, today, p)

    # ---- V4: 12 months of (synthetic) supplier order history before the ledger starts — records only ----
    refs = {"PO": 0, "H": 0}
    _seed_order_history(db, hospital, items, suppliers, start, random.Random(p.seeds[2]), refs, p)

    # ---- Opening stock (day 0) ----
    for sku, it in items.items():
        c, base = it["c"], it["base"]
        qty = max(int(base * rng.uniform(25, 40)), 5)
        expiry = start + timedelta(days=it["shelf"] - rng.randint(0, 120))
        if sku in p.expiry_scenarios:
            days_bought, offset = p.expiry_scenarios[sku]
            stock.receive(db, store, c, int(base * days_bought), f"BULK-{sku}", today + timedelta(days=offset),
                          it["offer"][0].supplier, float(it["offer"][0].unit_price) * 0.85, "PO-BULK-DEAL",
                          "Bulk discount purchase", at=clock.at(start))
        stock.receive(db, store, c, qty, f"OPEN-{sku}", expiry, it["offer"][0].supplier,
                      float(it["offer"][0].unit_price), "OPENING", "Opening balance", at=clock.at(start))

    grn = 1000
    # ---- Daily simulation ----
    for d in range(1, days + 1):
        day = start + timedelta(days=d)
        weekday_factor = 0.6 if day.weekday() == 6 else (0.85 if day.weekday() == 5 else 1.0)
        season = 1.0 + 0.12 * math.sin(2 * math.pi * d / 45)
        for sku, it in items.items():
            c = it["c"]
            # deliveries arriving today
            if it["pending"] and it["pending"][0] <= day:
                _, qty, sp, order = it["pending"]
                grn += 1
                moves = stock.receive(db, store, c, qty, f"L{day:%y%m%d}-{rng.randint(100, 999)}",
                                      day + timedelta(days=int(it["shelf"] * rng.uniform(0.7, 1.0))), sp.supplier,
                                      float(sp.unit_price), f"GRN-{grn}", None, at=clock.at(day))
                _deliver(db, order, day, qty, float(sp.unit_price), moves[0])  # V4: link the receipt to its order
                it["pending"] = None

            demand = _poisson(rng, it["nonproc"] * weekday_factor * season)
            if sku in p.starved and d > days - 25:
                demand = int(demand * 1.5)  # demand spike towards the end
            if demand:
                depts_w = it["weights"]
                total_w = sum(depts_w.values())
                remaining = demand
                codes = list(depts_w)
                rng.shuffle(codes)
                for i, code in enumerate(codes):
                    q = remaining if i == len(codes) - 1 else int(round(demand * depts_w[code] / total_w))
                    q = min(q, remaining)
                    if q <= 0:
                        continue
                    usable = _usable_on(db, c.id, day)
                    q = min(q, usable)
                    if q <= 0:
                        break
                    stock.issue(db, store, c, q, depts[code], f"IND-{day:%m%d}-{code}", None,
                                at=clock.at(day), today=day)
                    remaining -= q

            # V2B: procedure-driven consumption (completed procedures × true per-procedure usage, with noise)
            for dept_code, qty, ref in proc_usage.get((day, sku), []):
                usable = _usable_on(db, c.id, day)
                q = min(qty, usable)
                if q > 0:
                    stock.issue(db, store, c, q, depts[dept_code], ref, None, at=clock.at(day), today=day)

            # occasional return / wastage
            if rng.random() < 0.004:
                b = _any_batch(db, c.id)
                if b and b.quantity > 5:
                    stock.wastage(db, store, b, c, rng.randint(1, min(5, b.quantity)), "Packaging damaged",
                                  at=clock.at(day))

            # reorder policy: when usable ≤ reorder level, order up to max via preferred supplier
            usable = _usable_on(db, c.id, day)
            if usable <= c.reorder_level and it["pending"] is None:
                if sku in p.starved and d > days - 25:
                    if not it.get("backorder"):  # V4: the order was placed but the supplier backordered it
                        sp = it["offer"][0]
                        _order(db, hospital, c, sp, day, max(c.max_level - usable, sp.moq), refs,
                               notes="Supplier backorder (synthetic demo scenario) — not delivered.")
                        it["backorder"] = True
                    continue  # supplier backorder -> item ends LOW or OUT
                sp, rel = it["offer"]
                delay = 0 if rng.random() < rel else rng.randint(2, 6)
                qty = max(c.max_level - usable, sp.moq)
                order = _order(db, hospital, c, sp, day, qty, refs)  # V4: log the order (expected = quoted lead time)
                it["pending"] = (day + timedelta(days=sp.lead_time_days + delay), qty, sp, order)
        if verbose and d % 15 == 0:
            print(f"  simulated day {d}/{days}")
        db.flush()

    # one physical count correction for realism
    b = _any_batch(db, items["GLV-EXM-M"]["c"].id) if "GLV-EXM-M" in items else None
    if b and b.quantity > 20:
        stock.adjust(db, store, b, items["GLV-EXM-M"]["c"], b.quantity - 12, "Cycle count: 12 pairs short",
                     at=clock.at(today))

    alerts.evaluate(db, hospital.id)
    db.commit()
    return hospital


# ---------------------------------------------------------------- V4: supplier order evidence (synthetic)

# How each fictional supplier actually performs (synthetic, derived from SUPPLIERS' on-time probability):
# late deliveries are 1..(2 + 25 × (1 − on-time)) days late; cancellations 1 % + 20 % × (1 − on-time);
# short first deliveries 60 % × (1 − on-time); price volatility grows as reliability falls.
HISTORY_DAYS = 365


def _order(db: Session, hospital: Hospital, c: Consumable, sp: SupplierProduct, day: date, qty: float, refs: dict,
           notes: str | None = None) -> SupplierOrder:
    refs["PO"] += 1
    o = SupplierOrder(hospital_id=hospital.id, supplier_id=sp.supplier_id, consumable_id=c.id,
                      reference=f"PO-{refs['PO']:05d}", ordered_date=day, quoted_lead_time_days=sp.lead_time_days,
                      expected_date=day + timedelta(days=sp.lead_time_days), quantity_ordered=int(math.ceil(qty)),
                      unit_price=sp.unit_price, status=SupplierOrderStatus.OPEN, notes=notes, is_synthetic=True)
    db.add(o)
    return o


def _deliver(db: Session, order: SupplierOrder, day: date, qty: int, price: float, movement) -> None:
    db.flush()
    db.add(SupplierDelivery(hospital_id=order.hospital_id, order_id=order.id, received_date=day, quantity=qty,
                            unit_price=Decimal(str(price)), stock_movement_id=movement.id, batch_id=movement.batch_id,
                            is_synthetic=True))
    order.quantity_received += qty
    order.first_delivery_date = order.first_delivery_date or day
    if order.quantity_received >= order.quantity_ordered:
        order.status, order.completed_date = SupplierOrderStatus.RECEIVED, day
    else:
        order.status = SupplierOrderStatus.PARTIAL


def _seed_order_history(db: Session, hospital: Hospital, items: dict, suppliers: dict, start: date,
                        rng: random.Random, refs: dict, p: Profile = SUNRISE) -> None:
    """Imported-style order history (no stock movements): the preferred supplier gets the regular replenishment
    cycle, alternative suppliers occasional orders. Every order is complete before the ledger starts."""
    profile = {code: {"rel": rel} for code, _n, _c, _l, rel, _pf, _cats in p.suppliers}
    by_id = {s.id: code for code, s in suppliers.items()}
    first, last = start - timedelta(days=HISTORY_DAYS), start - timedelta(days=30)
    for sku in sorted(items):
        it = items[sku]
        c, base = it["c"], it["base"]
        cycle = max((c.max_level - c.reorder_level) / max(base, 0.1), 7.0)
        offers = sorted(db.scalars(select(SupplierProduct).where(SupplierProduct.consumable_id == c.id)),
                        key=lambda sp: sp.supplier_id)
        for sp in offers:
            code = by_id[sp.supplier_id]
            rel = profile[code]["rel"]
            gap = (0.8, 1.2) if sp.is_preferred else (2.0, 3.5)
            t = first + timedelta(days=rng.uniform(0, cycle * gap[1]))
            dates = []
            while t <= last:
                dates.append(t)
                t += timedelta(days=max(3, cycle * rng.uniform(*gap)))
            if not dates:
                continue
            # price path: occasional steps, scaled so the latest order is at today's catalogue price
            vol = 0.05 + (1 - rel) * 0.8
            path, p = [], 1.0
            for _ in dates:
                if rng.random() < vol:
                    p *= rng.uniform(0.97, 1.0) if rng.random() < 0.2 else rng.uniform(1.01, 1.06)
                path.append(p)
            for d, pf in zip(dates, path, strict=True):
                price = round(float(sp.unit_price) * pf / path[-1], 2)
                qty = max(int((c.max_level - c.reorder_level) * rng.uniform(0.8, 1.2)), sp.moq, 1)
                refs["H"] += 1
                o = SupplierOrder(hospital_id=hospital.id, supplier_id=sp.supplier_id, consumable_id=c.id,
                                  reference=f"H-{refs['H']:05d}", ordered_date=d, quoted_lead_time_days=sp.lead_time_days,
                                  expected_date=d + timedelta(days=sp.lead_time_days), quantity_ordered=qty,
                                  unit_price=Decimal(str(price)), status=SupplierOrderStatus.OPEN, is_synthetic=True,
                                  notes="Imported order history (synthetic demo data)")
                db.add(o)
                db.flush()
                if rng.random() < 0.01 + (1 - rel) * 0.2:
                    o.status, o.cancelled_date = SupplierOrderStatus.CANCELLED, d + timedelta(days=rng.randint(1, 5))
                    o.completed_date, o.close_reason = o.cancelled_date, "Supplier could not fulfil (synthetic)"
                    continue
                late = 0 if rng.random() < rel else rng.randint(1, 2 + round((1 - rel) * 25))
                early = 1 if late == 0 and sp.lead_time_days > 1 and rng.random() < 0.15 else 0
                first_day = d + timedelta(days=sp.lead_time_days + late - early)
                if rng.random() < (1 - rel) * 0.6:  # short first delivery
                    q1 = max(1, int(qty * rng.uniform(0.5, 0.9)))
                    parts = [(first_day, q1)]
                    if rng.random() < 0.8:
                        parts.append((first_day + timedelta(days=rng.randint(2, 8)), qty - q1))
                else:
                    parts = [(first_day, qty)]
                for day, q in parts:
                    db.add(SupplierDelivery(hospital_id=hospital.id, order_id=o.id, received_date=day, quantity=q,
                                            unit_price=Decimal(str(price)), is_synthetic=True))
                    o.quantity_received += q
                o.first_delivery_date = parts[0][0]
                o.completed_date = parts[-1][0]
                o.status = SupplierOrderStatus.RECEIVED
                if o.quantity_received < qty:
                    o.close_reason = "Balance not supplied (synthetic)"
    db.flush()


def _expected_procedure_use(p: Profile = SUNRISE) -> dict[str, float]:
    """Average daily kit usage per SKU across all synthetic procedure types (after cancellations)."""
    out: dict[str, float] = {}
    for _code, _name, _dept, _mins, weekday_means, kit in p.procedures:
        mean_per_day = sum(weekday_means) / 7 * (1 - CANCEL_RATE)
        for sku, qty in kit.items():
            out[sku] = out.get(sku, 0.0) + mean_per_day * qty
    return out


def _binomial(rng: random.Random, n: int, p: float) -> int:
    return sum(1 for _ in range(n) if rng.random() < p)


def _seed_procedures(db: Session, hospital: Hospital, depts: dict, items: dict, rng: random.Random,
                     start: date, days: int, today: date,
                     p: Profile = SUNRISE) -> dict[tuple[date, str], list[tuple[str, int, str]]]:
    """Create SYNTHETIC procedure types, kit mappings, completed/cancelled history and future schedules.

    Returns the procedure-driven consumption to simulate: {(day, sku): [(dept_code, units, reference)]}.
    True usage per procedure = kit qty × a hidden per-(procedure, item) factor (0.75–1.25) + Poisson noise,
    so the kit in the database is a good but imperfect guide — as it would be in a real hospital.
    """
    types: dict[str, ProcedureType] = {}
    for code, name, dept, mins, _means, _kit in p.procedures:
        types[code] = ProcedureType(
            hospital_id=hospital.id, department_id=depts[dept].id, code=code, name=name, avg_duration_minutes=mins,
            description="Synthetic demo procedure type", is_synthetic=True,
        )
    db.add_all(types.values())
    db.flush()
    true_factor: dict[tuple[str, str], float] = {}
    for code, _name, _dept, _mins, _means, kit in p.procedures:
        for sku, qty in kit.items():
            db.add(ProcedureItemMapping(
                hospital_id=hospital.id, procedure_type_id=types[code].id, consumable_id=items[sku]["c"].id,
                quantity_per_procedure=Decimal(str(qty)), is_synthetic=True,
                notes="Synthetic kit quantity for the demo — not a clinical standard",
            ))
            true_factor[(code, sku)] = rng.uniform(0.75, 1.25)

    # weekly list factor per department (surgeon leave / extra lists) — known in the schedule, not in consumption history
    first_week = start - timedelta(days=start.weekday())
    n_weeks = (days + FUTURE_DAYS) // 7 + 3
    week_factor = {(dept, w): rng.choice([0.55, 1.0, 1.0, 1.0, 1.35])
                   for dept in sorted({pr[2] for pr in p.procedures}) for w in range(n_weeks)}

    usage: dict[tuple[date, str], list[tuple[str, int, str]]] = {}
    rows = []
    for offset in range(1, days + FUTURE_DAYS + 1):
        day = start + timedelta(days=offset)
        future = day > today
        w = (day - first_week).days // 7
        for code, _name, dept, _mins, weekday_means, kit in p.procedures:
            lam = weekday_means[day.weekday()] * week_factor[(dept, w)]
            if future and code in p.camp["codes"] and (day - today).days in p.camp["days"]:
                lam *= p.camp["factor"]
            planned = _poisson(rng, lam)
            if planned == 0:
                continue
            cancelled = _binomial(rng, planned, CANCEL_RATE)
            done = planned - cancelled
            common = {"hospital_id": hospital.id, "procedure_type_id": types[code].id, "department_id": depts[dept].id,
                      "scheduled_date": day, "is_synthetic": True}
            if done:
                status = ProcedureStatus.SCHEDULED if future else ProcedureStatus.COMPLETED
                rows.append(ProcedureSchedule(count=done, status=status, **common))
            if cancelled:
                rows.append(ProcedureSchedule(count=cancelled, status=ProcedureStatus.CANCELLED,
                                              notes="Synthetic cancellation", **common))
            if not future and done:
                for sku, qty in kit.items():
                    units = _poisson(rng, done * qty * true_factor[(code, sku)])
                    if units:
                        usage.setdefault((day, sku), []).append((dept, units, f"PROC-{code}-{day:%m%d}"))
    db.add_all(rows)
    db.flush()
    return usage


def _usable_on(db: Session, consumable_id: int, day: date) -> int:
    from sqlalchemy import func

    return int(db.scalar(
        select(func.coalesce(func.sum(StockBatch.quantity), 0)).where(
            StockBatch.consumable_id == consumable_id, StockBatch.quantity > 0,
            (StockBatch.expiry_date.is_(None)) | (StockBatch.expiry_date >= day))
    ))


def _any_batch(db: Session, consumable_id: int) -> StockBatch | None:
    return db.scalar(select(StockBatch).where(StockBatch.consumable_id == consumable_id, StockBatch.quantity > 0)
                     .order_by(StockBatch.quantity.desc()).limit(1))


def seed_all(db: Session, verbose: bool = True) -> list[Hospital]:
    """V8: every demo hospital + the cross-hospital demo accounts (organization admin, platform admin)."""
    hospitals = []
    for p in PROFILES:
        if verbose:
            print(f"Seeding {p.name} ({p.days} days, synthetic)…")
        hospitals.append(seed(db, verbose=verbose, profile=p))
    by_code = {h.code: h for h in hospitals}
    pw = hash_password(DEMO_PASSWORD)
    sunrise, lakeview = by_code[SUNRISE.code], by_code[LAKEVIEW.code]
    group = User(email=GROUP_ADMIN[0], full_name=GROUP_ADMIN[1], hashed_password=pw, hospital_id=sunrise.id, role=Role.ADMIN)
    db.add(group)
    db.flush()
    db.add(HospitalMembership(user_id=group.id, hospital_id=lakeview.id, role=Role.ADMIN, status="ACTIVE"))
    db.add(OrganizationMembership(user_id=group.id, organization_id=sunrise.organization_id, role=OrgRole.ORG_ADMIN))
    db.add(User(email=PLATFORM_ADMIN[0], full_name=PLATFORM_ADMIN[1], hashed_password=pw, hospital_id=None, role=None,
                is_platform_admin=True))
    # V9: demo integration sources (configuration only — nothing is imported at seed time)
    from app.integrations.demo import seed_sources

    for h in (sunrise, lakeview):
        seed_sources(db, h)
    db.commit()
    return hospitals


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed MedFlow demo data")
    parser.add_argument("--reset", action="store_true", help="delete the demo hospitals first")
    args = parser.parse_args()
    with SessionLocal() as db:
        if args.reset:
            reset(db)
        if db.scalar(select(Hospital.id).where(Hospital.code == DEMO_CODE)):
            print("Demo hospitals already exist (use --reset to recreate).")
            return
        print("Seeding demo hospitals (synthetic data)…")
        seed_all(db)
        print(f"Done. Log in with admin@sunrise.demo, admin@lakeview.demo, admin@carenet.demo (both hospitals) "
              f"or platform@medflow.demo — password {DEMO_PASSWORD}")


if __name__ == "__main__":
    main()
