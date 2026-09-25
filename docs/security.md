# Security (Version 1)

| Control | Implementation |
|---|---|
| Password storage | bcrypt (cost 12). Policy: 8–72 bytes, letters + digits. Never logged. |
| Sessions | JWT access token (15 min) + refresh token (7 days, rotated on use). HttpOnly, `SameSite=Lax`, `Secure` when `COOKIE_SECURE=true`. Refresh cookie scoped to `/api/auth`. |
| Revocation | `users.token_version` bumped on logout, password change, role change, deactivation. |
| Login abuse | Per IP+email sliding window (10 attempts / 5 min) → 429. Unknown users are timing-equalised with a dummy hash; same error message for unknown user and wrong password. *In-process only — move to Redis before running >1 worker.* |
| Authorisation | Role → permission matrix (`app/core/permissions.py`), enforced on every endpoint. Department managers are restricted to their own department server-side. |
| Tenant isolation | Every query filtered by the caller's `hospital_id`; foreign IDs return 404. Tested. |
| CSRF | Cookies are `SameSite=Lax`, state-changing endpoints accept JSON only, and the browser calls the API same-origin via the Next.js proxy. |
| Input validation | Pydantic on every request; SQLAlchemy parameterised queries only. |
| Audit | Logins (incl. failures), every create/update, every stock movement, alert actions — with actor, IP and field-level diffs. |
| Headers | `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Cache-Control: no-store` on API. |
| Secrets | Environment variables only; `.env` is git-ignored; `.env.example` has placeholders. |
| Privacy | No patient data is stored. The domain model is items, departments, suppliers and counts. |

## Before any real deployment

- Set a strong `JWT_SECRET`, `COOKIE_SECURE=true`, HTTPS only.
- Replace the in-process rate limiter with a shared store.
- Disable `SEED_DEMO`.
- Add backups, encryption at rest (managed Postgres), and log shipping without request bodies.
- Get legal/compliance review before processing any real hospital data.

## V7 AI assistant

- Read-only by construction: the tool registry contains no write operation; write requests are refused before any tool
  or model runs; tools call the existing endpoint functions with the caller's user, so hospital scoping and 404s apply.
- Default mode uses no LLM and no key. `ASSISTANT_API_KEY` (if any) comes from the environment / a secret store and is
  never committed (`.env.example` has only a commented placeholder).
- With a hosted LLM, tool results (item / supplier names, stock, prices, risks — no patient data) leave the server; use a
  local model (`openai_compatible`) if that is not acceptable.
- Prompt-injection surface: item and supplier names flow into tool results. The system prompt treats tool results as data,
  the model can only call read-only tools with validated arguments, and numbers are checked against tool results.
- Conversations are private to their user; every question is audited (`assistant.ask`). Per-user rate limit (in-process).

## V8 multi-hospital tenant isolation

- Hospital access only through an ACTIVE `hospital_memberships` row in an ACTIVE hospital of an ACTIVE organization,
  re-verified on every request; access tokens are bound to the hospital they were issued for; a stale browser tab gets
  409 instead of acting on a different hospital. Client-supplied hospital ids are never trusted as proof of access.
- Four layers: request context → router ownership checks (404 for other hospitals' ids) → ORM tenant guard →
  PostgreSQL triggers (`medflow_tenant_guard`, active-hospital pointer constraint trigger).
- Organization and platform administrators get administration rights, not operational data.
- The assistant refuses questions about other hospitals before any tool or model runs (identical reply whether or not
  the hospital exists); its tools have no hospital parameter.
- Audit: hospital, organization and platform scopes, each visible only to its administrators.
- Compliance: these are technical controls. MedFlow has **not** been assessed or certified for HIPAA, GDPR, ISO 27001 or
  any other standard. No patient data is stored.

## V9 integrations

- Inbound API keys (`mfk_<prefix>_<secret>`): per source (so per hospital), shown once, stored only as HMAC-SHA256 with a
  server-side pepper (`INTEGRATION_KEY_PEPPER`), compared in constant time, revocable, last use recorded; per-key rate
  limit (429 + `Retry-After`) and request-size limit (413). A key can only write into its own source's hospital; there is
  no hospital parameter.
- Outbound credentials are never stored in the database: a source names an environment variable (`MEDFLOW_INTEGRATION_*`)
  that holds the token; configuration that looks like a secret is refused. Nothing secret is committed to Git.
- SSRF: `rest_pull` sources may only call hosts on the operator's `INTEGRATION_ALLOWED_HOSTS`.
- The reference ERP simulator is off by default, requires its own token and serves demo hospitals only.
- Uploaded files are parsed (CSV / `.xlsx` via openpyxl, read-only, values only — formulas are not evaluated), size-limited,
  and only their name + SHA-256 are kept with the run; rejected rows keep their raw values for retry.
- Every exchange and every configuration change is audited; integration service accounts are inactive and have no memberships.
- MedFlow does not scrape or automate other systems' screens; it only uses files people upload and APIs a hospital exposes
  or calls with permission. Integrations carry operational supply data only — no patient data.

## V10 pilots

- Pilots and everything attached to them (participants, sources, issues, feedback, views, readiness, reports) are
  hospital-owned; routes use `require(...)` + `get_owned` (foreign ids → 404) and the V8 sweep covers the 22 new
  (method, path) pairs. Request bodies cannot name a hospital (`extra="forbid"`); participants must be active members and
  departments / sources / recommendations must belong to the hospital. Organization admins see pilot status only.
- Issues never change operational data; resolving needs a note. Every pilot action is audited.
- No patient data: pilots use item codes, stock, consumption, suppliers, orders, deliveries, departments and procedure
  counts. The readiness checklist asks a person to confirm "No patient data required".
- No compliance certification (HIPAA, GDPR, ISO 27001 …) is claimed; a real deployment needs legal / security / privacy review.
