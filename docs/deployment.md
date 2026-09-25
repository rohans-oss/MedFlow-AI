# Deployment

## Local

`docker compose up --build` — see README.

## Suggested hosted setup (when needed)

| Component | Option | Notes |
|---|---|---|
| Frontend | Vercel, or the `frontend/Dockerfile` image anywhere | Set `BACKEND_URL` **at build time** (Next.js bakes rewrites into the standalone build). |
| Backend | Render / Railway / Fly / Cloud Run using `backend/Dockerfile` | The entrypoint runs `alembic upgrade head`; set `SEED_DEMO=false` in production. |
| Database | Managed PostgreSQL 16 | Enable automated backups. |

Required backend environment:

```text
DATABASE_URL=postgresql+psycopg://USER:PASS@HOST:5432/DB
JWT_SECRET=<48+ random bytes>
COOKIE_SECURE=true
CORS_ORIGINS=["https://your-frontend.example"]   # only if calling the API cross-origin
SEED_DEMO=false
```

Because the frontend proxies `/api/*`, the browser never calls the backend directly; the backend can stay on a
private network reachable only from the frontend.

## Docker build status (V5, honest note)

The images were **not built or run in the development sandbox** for V5: `docker compose build backend` fails with
`failed to resolve source metadata for docker.io/library/python:3.11-slim … Head "https://registry-1.docker.io/v2/library/python/manifests/3.11-slim": Forbidden`
(the sandbox cannot reach Docker Hub). The same happened for V2–V4. V5 adds one Python dependency (`ortools==9.15.6755`,
a manylinux wheel, installed by the existing `pip install -r requirements.txt` step) and a migration applied by the
existing entrypoint; no Dockerfile change. Everything was verified outside Docker instead: backend tests on SQLite and
PostgreSQL 16, Alembic upgrade → check → downgrade → upgrade, production Next.js build, and the Playwright suite against
the running stack. Run `docker compose up --build` on your machine to confirm.

## V6 knowledge graph (Neo4j)

`docker compose up` now also starts `neo4j:5-community` (password `NEO4J_PASSWORD` from `.env`; the backend waits for it
to be healthy). The graph is derived data: to reset it, `docker compose down` and remove the `neo4jdata` volume, or just
run `docker compose exec backend python -m app.graph.cli sync`. To check every MedFlow graph query against your Neo4j:
`docker compose exec backend python -m app.graph.cli check`. To run MedFlow without a graph set `GRAPH_BACKEND=disabled`
(only the Knowledge graph page is then unavailable). Neo4j Browser: uncomment the `ports` line of the `neo4j` service and
open http://localhost:7474.

**Build status (V6, honest note):** as for V2–V5, the images could not be built or run in the development sandbox
(Docker Hub returns 403). The graph implementation was tested using FalkorDB, an openCypher-compatible graph engine. Neo4j is configured as the production graph store and is covered by CI configuration, but Neo4j execution could not be locally verified because the required Neo4j image/download endpoints returned HTTP 403 in the development environment.

## V7 AI assistant

Nothing to deploy by default: the assistant runs inside the API with the deterministic planner (`ASSISTANT_PROVIDER=none`)
and needs no model and no key. To try an LLM, set in `.env` (never commit it):

```bash
# local model, data stays on the machine, no key (e.g. Ollama: `ollama pull <model>` then `ollama serve`)
ASSISTANT_PROVIDER=openai_compatible
ASSISTANT_BASE_URL=http://host.docker.internal:11434/v1
ASSISTANT_MODEL=<a tool-calling model>
# or a hosted provider (tool results — item, supplier and stock data, no patient data — are sent to it)
# ASSISTANT_PROVIDER=anthropic
# ASSISTANT_MODEL=<model id>
# ASSISTANT_API_KEY=<from your secret store>
```

`GET /api/assistant/status` shows which mode is active. If the model is unreachable or its answer fails the checks, the
deterministic answer is returned with the reason. **Build status (V7):** the images still could not be built here (Docker Hub
403), and no LLM could be downloaded or reached in the development environment, so LLM mode was tested only with a scripted
provider and mocked HTTP (see `docs/assistant.md`).

## V8 multi-hospital

`alembic upgrade head` applies the V8 migration: existing hospitals move into a "Default organization", every existing
user gets a membership with their current role, and PostgreSQL tenant-consistency triggers are installed. No new
service, port or environment variable. `SEED_DEMO=true` seeds three synthetic hospitals in two organizations plus
`admin@carenet.demo` (organization admin of CareNet, admin of Sunrise and Lakeview) and `platform@medflow.demo`
(platform admin). To rename the "Default organization" after an upgrade, a platform admin uses `PATCH /api/organizations/{id}`.
**Build status (V8):** Docker images still could not be built here (Docker Hub returned 403 / "Forbidden"); the stack was
run directly (uvicorn + Next standalone + PostgreSQL 16 + FalkorDB). See `docs/multi-hospital.md`.

## V9 integrations

`alembic upgrade head` adds the 8 integration tables (and their tenant triggers). New settings (all optional):
`INTEGRATION_KEY_PEPPER` (set it in production; default derived from `JWT_SECRET`), `INTEGRATION_ALLOWED_HOSTS` (JSON list
of hosts `rest_pull` sources may call — empty = none except the simulator), `INTEGRATION_RATE_LIMIT_PER_MINUTE`,
`INTEGRATION_MAX_RECORDS_PER_REQUEST`, `INTEGRATION_MAX_UPLOAD_MB`, `INTEGRATION_HTTP_TIMEOUT_SECONDS`,
`INTEGRATION_HTTP_RETRIES`, outbound tokens as `MEDFLOW_INTEGRATION_<NAME>`, and the simulator's
`REFERENCE_ERP_ENABLED` / `REFERENCE_ERP_TOKEN` / `REFERENCE_ERP_BASE_URL` (server default off; the demo `.env.example`
turns it on with a placeholder token — change it). Docker Compose adds an `integrations-scheduler` service that runs
`python -m app.integrations.cli run-due` every 5 minutes (outside Docker: cron or any scheduler). Rate limiting and the
simulator's daily feed are in-process — run one API worker, or move them to a shared store before scaling out.
`openpyxl` was added for `.xlsx` uploads.
**Build status (V9):** Docker images still could not be built here (Docker Hub returned 403 / "Forbidden"); the stack was
run directly. See `docs/v9-integrations.md`.

## V10 pilots

`alembic upgrade head` adds the 8 pilot tables. No new service, port or environment variable. Metric computation runs
in the API process (a few seconds for a full report on the demo hospital — see `docs/v10-pilot.md`).
