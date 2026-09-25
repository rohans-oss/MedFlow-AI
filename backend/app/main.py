import logging

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.api import (
    alerts,
    assistant,
    auth,
    catalog,
    dashboard,
    forecasts,
    graph,
    integrations,
    inventory,
    org,
    pilots,
    procedures,
    procurement,
    risk,
    supplier_intel,
    suppliers,
    tenancy,
)
from app.core.config import settings
from app.db.session import SessionLocal
from app.integrations import reference_erp

logger = logging.getLogger("medflow")

app = FastAPI(
    title=f"{settings.APP_NAME} API",
    version="10.0.0",
    description=("Hospital supply intelligence platform — Version 10: inventory, forecasting, stockout risk, "
                 "supplier intelligence, procurement optimisation (recommend-only), operational knowledge graph, "
                 "read-only AI operations assistant, multi-hospital SaaS (organizations, memberships, tenant isolation), "
                 "integrations & data exchange (CSV/Excel upload, API push with keys, REST pull, mapping, validation, "
                 "reconciliation), real hospital pilot & business validation (baseline vs pilot metrics, data quality, "
                 "decisions, feedback, issues, readiness, reports — descriptive, synthetic results labelled)."),
    docs_url=f"{settings.API_PREFIX}/docs",
    openapi_url=f"{settings.API_PREFIX}/openapi.json",
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if request.url.path.startswith(settings.API_PREFIX) and "/docs" not in request.url.path:
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.exception_handler(IntegrityError)
async def integrity_error(_: Request, exc: IntegrityError):
    logger.warning("Integrity error: %s", exc.orig)
    return JSONResponse(status_code=status.HTTP_409_CONFLICT,
                        content={"detail": "This change conflicts with existing data."})


for r in (auth.router, org.router, catalog.router, inventory.router, suppliers.router, alerts.router, dashboard.router,
          forecasts.router, procedures.router, risk.router, supplier_intel.orders_router, supplier_intel.intel_router,
          procurement.router, graph.router, assistant.router, tenancy.router, integrations.router,
          integrations.ingest_router, reference_erp.router, pilots.router):
    app.include_router(r, prefix=settings.API_PREFIX)


@app.get(f"{settings.API_PREFIX}/health", tags=["meta"])
def health():
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
    return {"status": "ok", "version": app.version}
