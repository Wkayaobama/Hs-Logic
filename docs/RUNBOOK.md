# Operator runbook — Hubspot-Logic-Server

Companion to [PATTERNS.md](PATTERNS.md) (why the code is shaped the way it
is). This document is the *what do I do* reference for the team running the
service against HubSpot portal **9201667**.

## 1. Service at a glance

| Thing | Value |
|---|---|
| App (prod/compose) | http://localhost:8080 — nginx serves the SPA, proxies `/api/` to the backend |
| API debug | http://127.0.0.1:8000/api/docs (host-only bind) |
| Backend | FastAPI, single uvicorn worker, port 8000 |
| Health probe | `GET /api/health` → `{"status": "ok", ...}` (no HubSpot call) |
| Expensive endpoints | `GET /api/hubspot/crm-health`, `GET /api/hubspot/contact-health` — 100+ sequential HubSpot calls, 30–90 s cold |
| Cache | in-process, per endpoint, TTL `HEALTH_CACHE_TTL_SECONDS` (default 900 s), bypass with `?refresh=true` |
| Scan cap | 10,000 records per scan; payload `capped: true` marks partial results |

## 2. Start / stop

**Docker (the normal way):**

```bash
docker compose up --build -d
```

```bash
docker compose down
```

Backend waits on its own healthcheck before nginx starts routing. First build
needs the Docker daemon running (Docker Desktop on this machine).

**Local dev (no Docker), two terminals:**

```bash
cd backend && .\.venv\Scripts\Activate.ps1 && uvicorn app.main:app --reload --port 8000
```

```bash
cd frontend && npm run dev
```

Dev app is at http://localhost:5173 (Vite proxies `/api` → 127.0.0.1:8000).
First-time setup: `python -m venv .venv` + `pip install -r requirements.txt`
in `backend/`, `npm install` in `frontend/`.

## 3. Configuration and secrets

- Single `.env` at the **repo root**, copied from `.env.example`:
  `HUBSPOT_TOKEN`, `HUBSPOT_PORTAL_ID` (=9201667), `HEALTH_CACHE_TTL_SECONDS`.
- **Two invariants that have each caused a real outage:**
  1. `.env` must be **UTF-8 without BOM**. PowerShell `>` redirection writes
     UTF-16 and pydantic-settings then sees nothing. Use an editor or
     `Set-Content -Encoding utf8NoBOM`.
  2. The backend `environment:` block in `docker-compose.yml` is an **explicit
     allowlist**. A variable not listed there never reaches the container —
     adding a new setting means adding it in *both* `.env` and the compose
     allowlist (and `backend/app/config.py`).
- The token never reaches the browser. The frontend learns the portal id from
  `GET /api/hubspot/portal`.

### Token rotation

1. In HubSpot → Settings → Integrations → Private Apps, create/rotate the
   token with scopes: `crm.objects.contacts.read`,
   `crm.objects.companies.read`, `crm.objects.deals.read`, `tickets`,
   `account-info.security.read`; optionally `crm.lists.read` +
   `crm.lists.write` (suppression-list export — without it the UI falls back
   to CSV download).
2. Update `HUBSPOT_TOKEN` in root `.env`.
3. `docker compose up -d backend` (recreates with the new env) or restart
   uvicorn in dev.
4. Verify: `GET /api/hubspot/portal` returns `portal_id: 9201667` and
   non-negative counts. Tokens live in `.env` / secret stores only — never in
   the repo.

## 4. Routine operations

### Running a health scan

Open the **CRM Health** or **Contact Health** tab. A cold scan shows a
spinner for 30–90 s — this is normal (sequential HubSpot paging, deliberately
not parallelized; see PATTERNS.md §8). Subsequent loads are instant for the
TTL window; the banner shows "Last scanned N min ago".

- **Refresh button** (or `?refresh=true` on the endpoint) forces a rescan.
  Concurrent refreshes collapse into one scan — hammering the button cannot
  stampede HubSpot.
- **"cap reached — results partial"** means the scan hit the 10,000-record
  guard. Counts are floors, not totals. Raising the cap is a code change
  (`CAP` in `backend/app/routes/hubspot.py`) with a cost: +1 API call per 100
  records, and the deals *search* API hard-stops at 10,000 results regardless.

### Reading the numbers

- **Orphan companies** — zero deal associations, not attached to any
  `final_customer` deal, name not itself a `final_customer` value. The
  analytically actionable cut.
- **NQL contacts** — unreachable (no email + no phone) or anonymous (no
  name). Candidates for the suppression list.
- **Duplicate clusters** — grouped by normalized domain/name (companies) or
  email/name (contacts); accordion shows up to 60 clusters, 10 members each,
  with truncation notes when there is more.

### Suppression-list export

The button on the NQL card posts all `nql_ids` to
`POST /api/hubspot/suppression-list`, which creates a static HubSpot list.

- Success banner links straight to the list. **Amber banner with "N failed to
  add"** = partial add (usually rate limiting) — the list exists but is
  incomplete; re-running creates a *new* list, so prefer topping up in
  HubSpot or re-exporting after a pause.
- **403 → automatic CSV fallback**: the token lacks `crm.lists.write`. The
  CSV contains every NQL id; name/email detail only for the first 150 rows
  (payload detail cap). CSV cells are formula-injection-neutralized — a
  leading `'` on cells starting with `=`, `+`, `-`, `@` is intentional.

## 5. Troubleshooting matrix

| Symptom | Likely cause | Action |
|---|---|---|
| Every route 500 "HubSpot token not configured." | Env not reaching the process | Check root `.env` exists, is UTF-8 no BOM; in Docker run `docker compose config` and confirm `HUBSPOT_TOKEN` appears under the backend service (allowlist!); restart backend |
| 401 "token is invalid or expired" | Token rotated/revoked | Rotate per §3 |
| 403 "token lacks required scopes" | Scope missing from private app | Compare against the scope list in §3; re-authorize |
| Overview counts show "—" | Search API total unavailable (scope or API drift) | Not fatal; verify `crm.objects.*.read` scopes; counts use `POST /crm/v3/objects/{obj}/search` |
| First health load times out at the browser | Scan exceeded proxy window | nginx allows 180 s; if exceeded, portal is huge — consider raising `proxy_read_timeout` in `frontend/nginx.conf`, or accept the cap |
| Red "Refresh failed — showing the previous scan" banner | Rescan errored (often expired token) mid-session | Fix the cause (usually §3), Refresh again; the stale scan stays visible on purpose |
| Numbers differ between two browser tabs / after redeploy | Multiple backend processes each own a cache | Run exactly **one** uvicorn worker (the shipped CMD does); never add `--workers N` |
| 429s in backend logs | Something added parallelism or another consumer shares the token | Scans must stay sequential (PATTERNS.md §8); check for other apps using the same private app token |
| `docker compose up` fails to connect to daemon | Docker Desktop not running | Start Docker Desktop, retry |
| Port already in use (8000/8080) | Stale dev server or another service | `Get-NetTCPConnection -LocalPort 8000` → stop the owner; dev preview servers from the IDE also bind 8000 |
| Frontend loads, all API calls 404 | nginx proxy or Vite proxy misrouted | Prod: `location /api/` must proxy to `http://backend:8000`; dev: Vite target must be the IPv4 literal `127.0.0.1:8000` (Node resolves `localhost` to `::1`) |

## 6. Known limitations (accepted, documented — do not "fix" casually)

- `duplicate_ids` in both health payloads derives from cluster members
  truncated to 10 per cluster → under-counts for larger clusters.
- Deals search API stops at 10,000 results; `capped` + the cap guard make
  partiality explicit rather than preventing it.
- Inline `?associations=` results are truncated per record: orphan logic is
  safe (any association ⇒ not orphan), but `multi_company` counts can
  under-count extreme cases.
- Per-request `httpx.AsyncClient` (a TLS handshake per call) matches the
  deployed app; a module-level client is a known, deliberate non-change.

## 7. Post-deploy verification checklist

1. `docker compose config` — `HUBSPOT_TOKEN` and `HUBSPOT_PORTAL_ID` resolved
   under the backend service.
2. `GET /api/health` → 200.
3. `GET /api/hubspot/portal` → `portal_id: 9201667`, counts present.
4. Cold `GET /api/hubspot/contact-health` completes (30–90 s), payload has
   `cache.cached: false`.
5. Immediate second call returns in <1 s with `cache.cached: true`.
6. `?refresh=true` recomputes (new `cache.computed_at`).
7. In the UI: one ↗ deep link per object type opens the right HubSpot record.

## 8. Change policy

- `backend/app/routes/hubspot.py` scan/route logic is verbatim from the
  deployed app. Changes there need a deliberate, single-purpose commit with
  the reasoning in the message (precedent: the `/portal` count fix).
- New env vars: `.env.example` + compose allowlist + `config.py`, always all
  three.
- The scans stay sequential and the worker count stays 1 unless the cache
  moves out of process first.
