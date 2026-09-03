# GTM / RevOps cloud platform — design spec

Date: 2026-09-03 · Repo: `hs-standalone` (remote `Wkayaobama/Hs-Logic`) · Branch: `feat/cloud-run-deploy`
Checkpoint before any external mutation: `485d232` (on top of `da17cbb`, the docs commit that
`origin/main` does not yet contain).

Evidence convention (self-adversarial-reasoning §6): every load-bearing sentence carries its source
inline — **[probe]** = command run this session, **[read]** = file read this session,
**[doc]** = vendor documentation fetched this session, **[ruling]** = operator answer this session,
**[recall]** = claude-mem / memory note not re-verified.

---

## 1. Purpose and decomposition

Turn the local docker-compose HubSpot explorer into the first brick of a cloud GTM/RevOps
platform, in three sub-projects that each ship working software on their own:

| Phase | Deliverable | Status of this spec |
|---|---|---|
| 1 | `hs-logic` on Cloud Run, deployed by Ansible playbooks, public for staging | fully specified (§3–§7), plan `docs/superpowers/plans/2026-09-03-phase1-cloud-run-deploy.md` |
| 2 | Ad-hoc enrichment of **new** contacts into a dedicated BigQuery table, pushed back to HubSpot custom properties through the Stacksync two-way sync; CRM card afterwards | design level (§8); own spec + plan when Phase 1 is live |
| 3 | `gtm-control`: Streamlit control layer over BigQuery views for operator **and** stakeholders | design level (§9); own spec + plan after Phase 2 schema exists |

Operator rulings 2026-09-03 **[ruling]**: public exposure accepted "for now, eases staging";
Ansible playbooks approved as the scaffolding executor; operator resumes the paused Stacksync sync
themselves; the deployment pipeline must not depend on sync state; a dedicated enrichment table is
required so enrichment can be discarded when irrelevant; Streamlit audience is operator and
stakeholders. GCP project was not answered → **`wisekeybq`**, kept as one variable
(`gcp_project` in `deploy/ansible/group_vars/all.yml`) because `wisekeyclourrun` has billing
disabled **[probe: `gcloud billing projects describe wisekeyclourrun` → `billingEnabled: false`]**
and only the operator can change billing.

## 2. Verified starting state

| Fact | Evidence |
|---|---|
| App = FastAPI backend (`backend/app`, single uvicorn worker, in-process single-flight TTL cache) + React/Vite SPA served by nginx which proxies `/api/` to `backend:8000` | [read] `docker-compose.yml`, `backend/app/main.py`, `backend/app/cache.py`, `frontend/nginx.conf` |
| Frontend calls the API with the relative base `/api/hubspot` | [read] `frontend/src/api/client.ts:42` |
| Route logic in `backend/app/routes/hubspot.py` is byte-for-byte from the deployed portal explorer; change policy forbids casual edits | [read] `docs/PATTERNS.md` preamble, `docs/RUNBOOK.md` §8 |
| `wisekeybq`: billing on, operator is `roles/owner`; enabled: run, cloudbuild, artifactregistry, eventarc, compute; **not** enabled: secretmanager; no Cloud Run services; no Artifact Registry repos; bucket `wisekeybq_cloudbuild` (US); project number `948062441615`; Cloud Build SA holds `roles/cloudbuild.builds.builder`; compute SA holds `roles/editor` | [probe] `gcloud billing/services/run/artifacts/projects/storage` this session |
| BigQuery data location `europe-west1` (`HubspotSync.test_anthony` location) | [probe] `bq show` |
| Stacksync HubSpot→`HubspotSync` sync last wrote 2026-08-13 08:35 UTC; `hubspot_sync.arr_snapshots` (dlt transport) = 348 rows incl. 23 `budget` written 2026-08-27 | [probe] `bq show` / `bq query` |
| Local machine today: Docker daemon down, WSL Ubuntu 24.04 available with python 3.12 and the Windows gcloud reachable through interop at `/mnt/c/Users/ayaobama/AppData/Local/Google/Cloud SDK/google-cloud-sdk/bin/gcloud` and authenticated when `CLOUDSDK_CONFIG=/mnt/c/Users/ayaobama/AppData/Roaming/gcloud`; Node 22 + npm 10 on Windows; no Ansible anywhere before this session | [probe] `docker version`, `wsl -d Ubuntu -- …`, `node --version` |
| Proven Cloud Run recipe in the codebase: `crypto - Copy/certificate_tool/cloudbuild.yaml` (Cloud Build → gcr.io → `gcloud run deploy --allow-unauthenticated --timeout=300`, us-central1) | [read] that file |

## 3. Phase 1 architecture — service `hs-logic`

```
browser ──HTTPS──▶ Cloud Run service hs-logic (europe-west1, public, max 1 instance)
                    └─ one container: uvicorn app.main:app  (binds $PORT=8080)
                         ├─ /api/*            existing FastAPI routers, unchanged
                         ├─ /assets/*         StaticFiles from /app/static/assets (Vite build)
                         └─ /{anything else}  SPA fallback → /app/static/index.html
                    env:  HUBSPOT_PORTAL_ID, HEALTH_CACHE_TTL_SECONDS
                    secret: HUBSPOT_TOKEN ← Secret Manager hs-logic-hubspot-token:latest
                    identity: hs-logic-run@wisekeybq (runtime SA, keyless)
                                 └─ outbound: api.hubapi.com only (Phase 1)
```

Design choices and why:

- **Single container, FastAPI serves the SPA.** Cloud Run runs one container per service; the
  compose shape (nginx + backend) would need two services and cross-service auth. Serving the built
  bundle from FastAPI removes nginx and keeps the frontend exactly as it is (ruling: "keep the
  front end"). The relative `/api/hubspot` base **[read]** means zero frontend code changes.
- **`max-instances=1`.** The cache is per-process by design **[read `cache.py` docstring]**; every
  extra instance would pay its own 30–90 s cold scan and multiply HubSpot calls. One instance also
  keeps the scans sequential, which the rate-limit doctrine requires **[read RUNBOOK §8]**.
  `min-instances=0` keeps idle cost at zero; the first request after idle pays a cold start plus
  the scan, which the UI already shows as "scanning…".
- **Cloud Build builds the image** (`gcloud builds submit --config deploy/cloudbuild.yaml`).
  No local Docker is needed **[probe: daemon down today]** and the operator's Cloud Shell has no
  Docker daemon either. Image lands in Artifact Registry
  `europe-west1-docker.pkg.dev/wisekeybq/hs-logic/hs-logic:<git-sha>`; `gcr.io` from the old
  recipe is deprecated and is not used.
- **Token in Secret Manager, never in env files or playbook output.** The foundation playbook reads
  `HUBSPOT_TOKEN` from the repo-root `.env` with `no_log: true` and adds it as a secret version
  through stdin; the value never appears in logs, git, or this conversation.
- **Sync independence (ruling).** No playbook task and no Phase 1 code path touches Stacksync, the
  `HubspotSync` dataset, or any BigQuery table. The only external dependency at runtime is
  `api.hubapi.com`. Pausing or resuming the sync cannot change any deploy outcome.
- **Ansible is the executor of record**, shelling out to `gcloud`/`bq`, because the official
  `google.cloud` collection has no Cloud Run, Secret Manager or Artifact Registry modules that
  match this flow. Every mutating task is bracketed by a probe (`changed_when` decided by the
  probe) and a read-back (`failed_when` on the described state), per the house doctrine
  "verify by artifacts, not exit codes".

### 3.1 Runtime configuration (Cloud Run)

| Flag | Value | Reason |
|---|---|---|
| `--region` | `europe-west1` | same region as the BigQuery data Phase 2 will read |
| `--platform` | `managed` | |
| `--port` | `8080` | Cloud Run default; uvicorn binds `${PORT:-8080}` |
| `--memory` / `--cpu` | `512Mi` / `1` | scans are I/O-bound, payloads ≤ a few MB |
| `--max-instances` / `--min-instances` | `1` / `0` | cache coherence / zero idle cost |
| `--timeout` | `300` | cold scan 30–90 s, headroom for large portals |
| `--concurrency` | `80` | single-flight cache collapses concurrent scans |
| `--service-account` | `hs-logic-run@wisekeybq.iam.gserviceaccount.com` | least privilege, keyless |
| `--set-secrets` | `HUBSPOT_TOKEN=hs-logic-hubspot-token:latest` | |
| `--set-env-vars` | `HUBSPOT_PORTAL_ID=9201667,HEALTH_CACHE_TTL_SECONDS=900` | mirrors the compose allowlist |
| `--allow-unauthenticated` | yes | ruling R2 (staging) |

### 3.2 IAM (created by `foundation.yml`)

| Principal | Role | Scope |
|---|---|---|
| `hs-logic-run@wisekeybq` | `roles/secretmanager.secretAccessor` | secret `hs-logic-hubspot-token` only |
| `hs-logic-run@wisekeybq` | `roles/bigquery.jobUser` | project (Phase 2 read path; harmless now) |
| `hs-logic-run@wisekeybq` | `roles/bigquery.dataViewer` | dataset `HubspotSync` via SQL `GRANT … ON SCHEMA` (dataset IAM CLI is allowlist-gated on this account **[recall gcp-auth-invariants #3]**) |
| `948062441615@cloudbuild.gserviceaccount.com` | `roles/artifactregistry.writer` | repo `hs-logic` (explicit, in case `builds.builder` lacks upload on this repo) |
| `allUsers` | `roles/run.invoker` | service `hs-logic` (set by `--allow-unauthenticated`) |

## 4. Components (Phase 1 files)

| Path | Responsibility |
|---|---|
| `backend/app/main.py` | adds `mount_spa(app, static_dir)`: mounts `/assets`, registers the SPA fallback **after** all API routes; no-op when the static dir is absent (dev via Vite proxy, compose via nginx keep working) |
| `backend/app/config.py` | new setting `static_dir` (default `static`, resolved relative to `backend/`) |
| `backend/tests/test_spa.py` | TestClient tests for fallback, asset serving, `/api` precedence, traversal safety, no-static no-op |
| `Dockerfile` (repo root) | multi-stage: `node:20-alpine` builds `frontend/` → `python:3.12-slim` with `backend/app` + `static/`; `CMD` binds `$PORT` |
| `.dockerignore`, `.gcloudignore` | keep venvs, node_modules, docs, screenshots, `.env` out of the build context and the Cloud Build upload |
| `deploy/cloudbuild.yaml` | build + push only (deploy flags live in one place: Ansible vars) |
| `deploy/ansible/ansible.cfg`, `inventory.yml`, `group_vars/all.yml`, `requirements.txt` | localhost inventory, all tunables (`gcp_project`, `gcloud_bin`, `bq_bin`, sizes) |
| `deploy/ansible/foundation.yml` + `roles/gcp_foundation/tasks/main.yml` | one-time: preflight (auth, billing), enable APIs, Artifact Registry repo, runtime SA, secret + version, IAM, BigQuery GRANT |
| `deploy/ansible/deploy.yml` + `roles/app_deploy/tasks/main.yml` | per release: git sha tag, Cloud Build, `gcloud run deploy`, read-backs, HTTP smoke, manifest |
| `deploy/ansible/verify.yml` | Gate 2 read-backs only (no mutation), usable any time |
| `deploy/README.md` | runbook: Cloud Shell path, WSL path, rollback, variables |
| `docs/RUNBOOK.md` §2/§7, `README.md` | Cloud Run start/stop and post-deploy checklist |
| `Codebase/_MAP.md` | register `hs-standalone` in the HubSpot platform table (rule #3) |

Unchanged on purpose: `backend/app/routes/hubspot.py`, `backend/Dockerfile`, `frontend/Dockerfile`,
`frontend/nginx.conf`, `docker-compose.yml` (local compose stays the dev path).

## 5. Error handling and rollback

- Playbooks stop at the first failed read-back; nothing is retried blindly. Preflight fails fast on
  the two traps found today: no active gcloud account, billing disabled on the target project.
- `gcloud run deploy` failures leave the previous revision serving (Cloud Run only shifts traffic
  after the new revision is ready). Rollback = `gcloud run services update-traffic hs-logic
  --to-revisions <previous>=100` (documented in `deploy/README.md`, previous revision recorded in
  the manifest).
- Secret rotation = RUNBOOK §3 plus `ansible-playbook foundation.yml -e secret_rotate=true`
  (adds a new version; `:latest` picks it up on the next revision).
- Total teardown (contained blast radius, "delete by key list"): service `hs-logic`, SA
  `hs-logic-run`, secret `hs-logic-hubspot-token`, repo `hs-logic`, the two IAM bindings, the
  dataset GRANT. All five names are variables and are printed by `verify.yml`.

## 6. Verification

Local (before any GCP mutation):

1. `pytest backend/tests` green (SPA behaviour).
2. `npm ci && npm run build` in `frontend/` produces `dist/index.html` and `dist/assets/*`.
3. `ansible-playbook --syntax-check` on all three playbooks (WSL venv `~/.venvs/ansible`).
4. `ansible-playbook foundation.yml --tags preflight` (read-only tasks) succeeds from WSL with the
   interop gcloud.

Gate 2 read-backs after the first deploy (`verify.yml`, all must hold):

| Read-back | Expected |
|---|---|
| `gcloud run services describe hs-logic` | image = `…/hs-logic:<sha>`, `autoscaling.knative.dev/maxScale: '1'`, SA = runtime SA, URL present |
| `GET {url}/api/health` | 200, `{"status":"ok"}` |
| `GET {url}/api/hubspot/portal` | 200, `portal_id == "9201667"` (proves the secret is wired and the token valid) |
| `GET {url}/` | 200, body contains `<div id="root">` |
| `GET {url}/api/does-not-exist` | 404 JSON (`/api` precedence over the SPA fallback) |
| `gcloud secrets versions list` | exactly one ENABLED version (unless rotated) |
| `bq show HubspotSync` access list | contains `hs-logic-run@…` as READER |
| Second `GET /api/hubspot/contact-health` within TTL | `cache.cached: true` (single instance confirmed) |

## 7. Gate 1 — refutation table (adversarial-git-flow, self-adversarial-reasoning)

Confidence is per atomic claim, never averaged. Load-bearing claims below 75 % carry the attack an
adversary would mount and the mitigation that raises them before or during execution.

| # | Atomic claim | Conf. | Source | Attack if wrong | Mitigation |
|---|---|---|---|---|---|
| C1 | `hs-standalone` @ `da17cbb` is the current app; `origin/main` lacks the docs commit | 95 % | [probe] `git log d777b29..da17cbb` | building from `main` would ship without RUNBOOK/PATTERNS | feature branch re-based on `rebuild-standalone-app`; checkpoint `485d232` |
| C2 | `wisekeybq` billing on, operator owner, run/cloudbuild/artifactregistry enabled | 95 % | [probe] | deploy fails at first API call | preflight task re-checks billing + auth every run |
| C3 | Secret Manager API is **not** enabled on `wisekeybq` | 95 % | [probe] `gcloud services list` | secret creation fails | foundation enables it explicitly |
| C4 | Cloud Build SA can push to the new Artifact Registry repo | 70 % | [recall] role contents of `cloudbuild.builds.builder` | build succeeds, push 403 | foundation grants `artifactregistry.writer` on the repo explicitly; read-back `gcloud artifacts docker images describe` |
| C5 | `npm ci` on node 20 builds the frontend from the committed lockfile | 75 % | [read] lockfile present; 09-02 build used `npm install` on node 20 | Cloud Build fails in stage 1 | local `npm ci && npm run build` on node 22 in Task 2 raises to ≥ 90 %; Cloud Build log is the final proof |
| C6 | Vite output uses absolute `/assets/…` URLs and the SPA works behind FastAPI with base `/api/hubspot` | 85 % | [read] `client.ts`, `index.html`, `vite.config.ts` (no `base` override) | blank page, 404 on assets | Task 1 tests + Gate 2 `GET /` + browser check of the live URL |
| C7 | With `max-instances=1` the process-local cache behaves as in compose | 90 % | [doc] Cloud Run scaling; [read] `cache.py` | duplicated scans / 429 | Gate 2 cached-second-call read-back |
| C8 | Ansible runs from WSL and drives the Windows gcloud through interop with `CLOUDSDK_CONFIG` | 95 % (was 85 %) | [probe] `deploy/wsl/check.sh` 2026-09-03: `foundation.yml --tags preflight` → `account=anthony.yaobama@gmail.com project=wisekeybq billing=True`, `failed=0` | playbook cannot authenticate | raised; wrappers in `deploy/wsl/`; fallback = Cloud Shell (documented) |
| C9 | `bq` also works through interop for the dataset GRANT | 95 % (was 60 %) | [probe] `bq version` → "BigQuery CLI 2.1.24" through the `deploy/wsl/bq` wrapper, 2026-09-03 | GRANT task fails | raised; Windows fallback one-liner kept in `deploy/README.md` |
| C10 | Ansible `ini` lookup (`type=properties`) parses the repo `.env` (comments + `KEY=value`) | 95 % (was 70 %) | [probe] `check.sh` 2026-09-03: lookup on `.env.example` returned `HUBSPOT_PORTAL_ID=9201667` | empty token → broken secret version | raised; the playbook additionally refuses an empty token before creating a version |
| C11 | The token in `.env` is valid for portal 9201667 | 60 % | [read] plan 2026-09-02: backend healthy, portal route not exercised | Gate 2 `/api/hubspot/portal` → 401 | not a deploy failure: RUNBOOK §3 rotation + `secret_rotate=true` |
| C12 | Nothing in the pipeline depends on Stacksync state | 95 % | by construction (no task references sync/BigQuery tables) | — | ruling R4 satisfied; `verify.yml` prints the dependency list |
| C13 | Public exposure is acceptable | 100 % | [ruling] R2 | CRM data reachable by URL holders | blast radius documented; IAP is the Phase 3 hardening path; URL is not published anywhere |
| C14 | `GRANT … ON SCHEMA` is idempotent | 90 % | [recall] gcp-auth-invariants (used 2026-08-13) | re-run errors | `failed_when` ignores "already exists"; read-back on `bq show` access list |

| C15 | The WSL interop runner is fast enough for full playbooks | **refuted** | [probe] 2026-09-03: one gcloud call ≈ 44 s through the wrapper; `foundation.yml` still completed (all resources read back), `deploy.yml` then failed on the dirty-tree guard (CRLF false positives: 48 → 0 with `-c core.autocrlf=true`) | operator abandons the executor | guard made host-independent; runner of record = Cloud Shell (or a WSL-native clone); follow-up: deploy step inside Cloud Build (§5.1) |

No refutation survives that blocks coding; C4/C11 are raised at Gate 2. C15 changed the runner, not the design.

### 7.1 Follow-up decision (proposed, not yet approved): deploy step inside Cloud Build

Move `gcloud run deploy` into `deploy/cloudbuild.yaml` as a second step, with every Cloud Run flag
passed as a substitution rendered from `group_vars/all.yml` (source of truth unchanged). The client
then makes three calls per release (sha, `builds submit`, describe) instead of nine, and a later
GitHub push trigger makes releases zero-touch. Cost: the Cloud Build SA gains `roles/run.admin` on
the project and `roles/iam.serviceAccountUser` on the runtime SA. A GCE VM runner was evaluated and
rejected: `ic-load-host` exists but is TERMINATED; an always-on e2-micro buys nothing that Cloud
Shell or Cloud Build does not already provide, and Phase 2/3 scheduling maps to Cloud Scheduler +
Cloud Run jobs.

## 8. Phase 2 design — enrichment layer (to be specified in its own doc)

Contract inherited from `steps/buyer-intent.md` **[read]**: normalisation lives **below** HubSpot;
HubSpot stores controlled-vocabulary properties; enrichment is gated (fit → signal freshness →
score) before any paid provider; one owner per field.

- **Storage:** dataset `wisekeybq.enrichment` (europe-west1). Table `contact_enrichment`
  (partition `DATE(computed_at)`, cluster `hubspot_contact_id`): `hubspot_contact_id INT64`,
  `email`, `domain`, `computed_at TIMESTAMP`, `engine_version`, `run_id`, `primary_pain_point`,
  `secondary_pain_points ARRAY<STRING>`, `pain_point_confidence`, `persona`, `persona_fit`,
  `relevance_themes ARRAY<STRING>`, `intent_signal_type`, `intent_signal_created_at`,
  `intent_signal_active BOOL`, `enrichment_eligible BOOL`, `enrichment_stage`,
  `composite_mql_score INT64`, `qualification_reason`, `evidence JSON`, `is_active BOOL`.
  Table `enrichment_runs` (run ledger: `run_id`, window, counts, engine_version). A **table**, not a
  view, because Stacksync syncs tables only **[doc connectors/bigquery]**.
- **Scope rule "new contacts only":** watermark on `HubspotSync.Contacts.createdate` stored in
  `enrichment_runs`; first run starts at the go-live date, never backfills legacy contacts unless
  the operator passes an explicit `--since`.
- **Compute:** endpoints on `hs-logic`: `POST /api/enrich/contacts/run` (reads BigQuery through the
  runtime SA, writes rows with `engine_version`), `GET /api/enrich/contacts/{id}`,
  `POST /api/enrich/certificates/run` (folds the pasted Stacksync certificate-qualification
  workflow: TLS probe per domain from Cloud Run, issuer allow-list, writes `Certificate_Results`-shaped
  rows), plus a UI tab "Enrichment" reading the table.
- **Write-back (ruling R5):** Stacksync sync mapping `enrichment.contact_enrichment` →
  HubSpot contact custom properties (`primary_pain_point`, `persona`, `composite_mql_score`,
  `enrichment_stage`, `qualification_reason`). Enabling the mapping and the reverse-sync field
  scope is an **operator step in the Stacksync UI** (never assumed; A3 gate precedent). Discard =
  `is_active=false` on the row, which the mapping propagates as cleared properties; hard delete of
  rows is the second lever.
- **Card:** a HubSpot UI-extension card displaying the same properties, built with the proven
  pattern in `HubSpot - Worfklow/ui-extension` (MCP `HubSpotDev` tools available), after the
  properties exist.
- **Open decisions for the Phase 2 spec:** property naming prefix (`enr_` proposed); whether
  `evidence` JSON is synced (proposed: no, table-only); provider waterfall vendors and credit caps.

## 9. Phase 3 design — control layer `gtm-control` (to be specified in its own doc)

- Second Cloud Run service on `wisekeybq`, Streamlit (`--port 8501 --session-affinity
  --max-instances 2 --min-instances 0`), runtime SA `gtm-control-run@wisekeybq` with
  `bigquery.jobUser` + `dataViewer` on `arrdash`, `HubspotSync`, `enrichment`.
- Public for staging (ruling R6 audience = operator + stakeholders; ruling R2); hardening path =
  IAP or Google-identity check when the URL leaves the team.
- Pages: **Sync freshness** (last-modified per synced table — the signal that would have surfaced
  the 08-13 halt), **ARR gates** (`arrdash.chk_*` + `rpt_measures`), **Enrichment funnel**
  (`enrichment_runs` + stage counts), **Cost** (`budget_monitoring` once the billing plan lands).
- Reuses the `deploy/ansible` roles with a second `group_vars` file; the certificate tool's
  Streamlit container is the packaging precedent **[read `crypto - Copy/certificate_tool/cloudbuild.yaml`]**.

## 10. Execution split

| Concern | Owner | Why |
|---|---|---|
| Code, Dockerfile, playbooks, docs, tests, syntax checks | machinery (this session) | approved scaffolding |
| Running `foundation.yml` and `deploy.yml` against GCP | **operator go required**, then either operator from Cloud Shell or this session from WSL | first outward mutation; public URL |
| Resume Stacksync sync | operator | ruling R4 |
| Token rotation, billing changes | operator | credentials and finance stay with the operator |
| Stacksync field mapping / reverse-sync scope (Phase 2) | operator | A3 gate |
| Streamlit sharing to stakeholders (Phase 3) | operator | audience decision |
