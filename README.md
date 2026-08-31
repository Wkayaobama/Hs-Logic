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
| `/api/scoring/criteria` | GET | Echo the static scoring registry (rules, bands, max score) | — |
| `/api/scoring/contacts` | GET | Score every contact against the static criteria; cached 15 min | `refresh=true` bypasses cache |
| `/api/scoring/contacts/{id}` | GET | Score one contact (always fresh, single API call) | — |
| `/api/health` | GET | Service health check | — |
| `/api/docs` | GET | Interactive API documentation (OpenAPI/Swagger) | — |

## Required Private-App Scopes

- `crm.objects.contacts.read`
- `crm.objects.companies.read`
- `crm.objects.deals.read`
- `tickets` (HubSpot's ticket scope has no `crm.objects.` prefix)
- `account-info.security.read`
- `crm.lists.read` (optional, for suppression-list read)
- `crm.lists.write` (optional, for suppression-list create; frontend falls back to CSV on 403)

The lead-scoring endpoints need **no additional scopes** — they fetch the same
contact shape as the contact-health scan (`crm.objects.contacts.read`).

## Configuration

Copy `.env.example` to `.env` at repo root and fill in `HUBSPOT_TOKEN`.

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

## Tests

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest
```

The scoring engine (`app/scoring/engine.py`) is pure and sync, so its tests run
with zero I/O; the API tests fake the HubSpot calls (no token, no network).

## Lead Scoring (static criteria)

`backend/app/scoring/criteria.py` is the declarative registry: editing the
scoring model = editing that one file (rules, points, bands). Rules score
extracted features, never raw provenance markers — the synthetic IcAlps ids
(`icalps_contact_id`, `icalps_company_id`) are hard-excluded and pinned by
test. Read-only for now: scores are computed and displayed (dashboard "Lead
Scoring" tab + API), never written back to HubSpot — write-back is a later,
gated phase.

## Behavior Notes

- Health scans page through up to 10,000 records; `capped` flag in response signals partial results.
- First scan takes ~30–90s (100+ sequential HubSpot API calls, deliberately not parallelized to respect rate limits).
- Results cached in-process for 15 min per endpoint (single uvicorn worker only — scaling workers multiplies scans).
- Refresh button in UI forces rescan, bypassing cache.
- Cache keys are independent: refreshing the Lead Scoring tab never re-runs the health scans, and vice versa (`SCORING_CACHE_TTL_SECONDS` tunes the scoring TTL separately).
- **Known limitation**: `duplicate_ids` in both health payloads derives from cluster members truncated to 10 per cluster, so it under-counts for larger clusters.
