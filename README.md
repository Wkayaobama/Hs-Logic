# Hubspot-Logic-Server

## What it is

Portal explorer and CRM health analytics for HubSpot portal 9201667. Standalone microservice (FastAPI backend + React/Vite frontend, docker-compose) that centralizes the scattered HubSpot - Worfklow tooling. Private-app token stays server-side; frontend queries the API and receives the portal id from it.

## API Routes

| Endpoint | Method | Purpose | Parameters |
|----------|--------|---------|------------|
| `/api/hubspot/portal` | GET | Account info and object counts | — |
| `/api/hubspot/contacts` | GET | List contacts (paginated) | `limit`, `after` |
| `/api/hubspot/companies` | GET | List companies (paginated) | `limit`, `after` |
| `/api/hubspot/deals` | GET | List deals (paginated) | `limit`, `after` |
| `/api/hubspot/tickets` | GET | List tickets (paginated) | `limit`, `after` |
| `/api/hubspot/pipelines` | GET | List pipelines | — |
| `/api/hubspot/crm-health` | GET | Orphan companies, duplicate clusters; cached 15 min | `refresh=true` bypasses cache |
| `/api/hubspot/contact-health` | GET | NQL/MQL classification, missing fields, duplicates, multi-company; cached 15 min | `refresh=true` bypasses cache |
| `/api/hubspot/suppression-list` | POST | Create static HubSpot list from contact ids | `contact_ids` (JSON array) |
| `/api/health` | GET | Service health check | — |
| `/api/docs` | GET | Interactive API documentation (OpenAPI/Swagger) | — |
| `/api/export/{object}` | GET | One page of records for `contacts`, `companies`, `deals`, `tickets`, `notes`, `calls`, `meetings`, `tasks`, optionally scoped to a business entity, with association type ids and labels | `entity`, `after`, `limit`, `properties`, `associations`, `sample`, `with_labels` |
| `/api/export/{object}/count` | GET | Record count, optionally scoped, with AND-ed filters `prop:OP:value` (repeatable) | `entity`, `filter`, `max_pages` |
| `/api/export/associations` | GET | v4 batch association read with labels | `from`, `to`, `ids` |
| `/api/meta/entities` | GET | The business-entity seed (`backend/app/data/entities.yaml`) and export defaults | — |
| `/api/meta/properties/{object}` | GET | Property definitions (type, fieldType, groupName, hubspotDefined, options) | — |

Every response of the export/meta routes carries `X-Logic-Request-Id`, `X-Logic-Duration-Ms`, `X-Logic-HS-Requests`, `X-Logic-HS-429` and `X-Logic-HS-Retries` headers so the sampling probe can measure the execution layer.

## Business entities

`backend/app/data/entities.yaml` states once how each business entity sharing the portal (WISEKEY, WISESAT, SEALSQ, SEALCOIN, QUANTUM_AI, ICALPS, MIRAEX, WECAN) is recognised on every object: deal pipelines, ticket/contact/company marker properties, or the complement (WISEKEY). `app/entities.py` turns it into HubSpot search `filterGroups` (server-side) or a client-side predicate; objects without a marker are exported as an empty page flagged `no_marker`.

## Sampling probe

`sampling/` is the PowerShell 7 pipeline (stages `00_preflight` .. `08_review_package`, `run.ps1`, gate report) that proves the backend is executable, accurate and efficient before any rule is built on it. See `sampling/README.md`.

## Tests and the fake HubSpot

```bash
cd backend && python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest                                   # backend tests against the in-process fake HubSpot
python -m tools.fake_hubspot --port 8100 # standalone fake (seeded with the 2026-09-14 reference numbers)
HUBSPOT_BASE_URL=http://127.0.0.1:8100 HUBSPOT_TOKEN=fake uvicorn app.main:app --port 8000
```

## Required Private-App Scopes

- `crm.objects.contacts.read`
- `crm.objects.companies.read`
- `crm.objects.deals.read`
- `tickets` (HubSpot's ticket scope has no `crm.objects.` prefix)
- `account-info.security.read`
- `crm.lists.read` (optional, for suppression-list read)
- `crm.lists.write` (optional, for suppression-list create; frontend falls back to CSV on 403)
- `crm.objects.*.read` also cover the export routes; engagement exports need `crm.objects.notes.read` / `calls` / `meetings` / `tasks` scopes as applicable

## Configuration

Copy `.env.example` to `.env` at repo root and fill in `HUBSPOT_TOKEN`.
Optional: `HUBSPOT_BASE_URL` (point at the fake HubSpot), `HUBSPOT_MAX_RETRIES`, `SEARCH_RPS`, `EXPORT_PAGE_LIMIT`, `ENTITIES_PATH` (see `.env.example`).

**IMPORTANT Windows note**: .env must be UTF-8 WITHOUT BOM. PowerShell `>` redirection writes UTF-16 and breaks pydantic-settings. Use `Set-Content -Encoding utf8NoBOM` or a text editor (not PowerShell redirection).

## Run with Docker

```bash
docker compose up --build
```

- App frontend: http://localhost:8080
- API docs: http://127.0.0.1:8000/api/docs

The backend environment block in docker-compose.yml is an explicit allowlist — new env vars must be added there or the container never sees them.

## Local Dev (no Docker)

**Terminal 1 (Backend)**:
```bash
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1  # Windows PowerShell
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

**Terminal 2 (Frontend)**:
```bash
cd frontend
npm install
npm run dev
# Visit http://localhost:5173 (Vite proxies /api to 127.0.0.1:8000)
```

## Behavior Notes

- Health scans page through up to 10,000 records; `capped` flag in response signals partial results.
- First scan takes ~30–90s (100+ sequential HubSpot API calls, deliberately not parallelized to respect rate limits).
- Results cached in-process for 15 min per endpoint (single uvicorn worker only — scaling workers multiplies scans).
- Refresh button in UI forces rescan, bypassing cache.
- **Known limitation**: `duplicate_ids` in both health payloads derives from cluster members truncated to 10 per cluster, so it under-counts for larger clusters.
