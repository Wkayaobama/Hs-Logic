# Phase 1 — hs-logic on Cloud Run via Ansible: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the existing FastAPI + React HubSpot explorer as one public Cloud Run service on `wisekeybq`, built by Cloud Build and deployed by Ansible playbooks with read-back verification at every step.

**Architecture:** One container: uvicorn serves `/api/*` (unchanged routers) and the built Vite bundle (SPA fallback). Cloud Build builds the multi-stage image into Artifact Registry; `gcloud run deploy` runs under a keyless runtime SA reading the HubSpot token from Secret Manager. Ansible (localhost, shelling gcloud/bq) is the executor of record; every mutation is probe → command → read-back.

**Tech Stack:** Python 3.12, FastAPI 0.141, uvicorn, pytest 9; Node 20 (Cloud Build) / 22 (local), Vite 6; Docker multi-stage; gcloud SDK 543; ansible-core (WSL venv `~/.venvs/ansible`); BigQuery `bq` CLI.

**Spec:** `docs/superpowers/specs/2026-09-03-gtm-cloud-platform-design.md`

## Global Constraints

- `backend/app/routes/hubspot.py` is **not modified** (spec §4, RUNBOOK §8 change policy).
- `docker-compose.yml`, `backend/Dockerfile`, `frontend/Dockerfile`, `frontend/nginx.conf` stay as they are (local compose remains the dev path).
- Every gcloud/bq call passes `--project {{ gcp_project }}` explicitly (gcloud default project is `wisekeyclourrun`).
- Project: `wisekeybq` · region: `europe-west1` · service: `hs-logic` · repo: `hs-logic` · SA: `hs-logic-run` · secret: `hs-logic-hubspot-token` · Cloud Run: `512Mi`, cpu `1`, `max-instances 1`, `min-instances 0`, `timeout 300`, `concurrency 80`, `--allow-unauthenticated`.
- The HubSpot token never appears in logs, git, playbook output or the conversation (`no_log: true`).
- No task references Stacksync or BigQuery tables except the dataset GRANT (ruling: pipeline independent of sync state).
- Commit after every task on branch `feat/cloud-run-deploy`; do not push.
- Windows shell for local steps is PowerShell 7 (`pwsh`); WSL Ubuntu for Ansible; the backend venv is `backend/.venv/Scripts/python.exe`.

---

### Task 1: FastAPI serves the SPA (no-op when the bundle is absent)

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/__init__.py` (empty)
- Create: `backend/tests/test_spa.py`
- Create: `backend/pytest.ini`

**Interfaces:**
- Produces: `mount_spa(app: FastAPI, static_dir: pathlib.Path) -> bool` in `app.main`; setting `settings.static_dir: str` (default `"static"`, resolved against the `backend/` directory).

- [ ] **Step 1: Write the failing tests**

`backend/pytest.ini`:

```ini
[pytest]
testpaths = tests
pythonpath = .
```

`backend/tests/__init__.py`: empty file.

`backend/tests/test_spa.py`:

```python
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import mount_spa


def _build(tmp_path: Path, with_bundle: bool = True) -> tuple[FastAPI, bool]:
    app = FastAPI()

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    if with_bundle:
        (tmp_path / "assets").mkdir()
        (tmp_path / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
        (tmp_path / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
        (tmp_path / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    mounted = mount_spa(app, tmp_path)
    return app, mounted


def test_no_bundle_is_a_noop(tmp_path):
    app, mounted = _build(tmp_path, with_bundle=False)
    assert mounted is False
    client = TestClient(app)
    assert client.get("/api/health").status_code == 200
    assert client.get("/").status_code == 404


def test_root_serves_index(tmp_path):
    app, mounted = _build(tmp_path)
    assert mounted is True
    res = TestClient(app).get("/")
    assert res.status_code == 200
    assert '<div id="root">' in res.text


def test_client_route_falls_back_to_index(tmp_path):
    app, _ = _build(tmp_path)
    res = TestClient(app).get("/contacts/some/deep/link")
    assert res.status_code == 200
    assert '<div id="root">' in res.text


def test_static_file_at_root_is_served(tmp_path):
    app, _ = _build(tmp_path)
    res = TestClient(app).get("/favicon.svg")
    assert res.status_code == 200
    assert res.text == "<svg/>"


def test_assets_are_served(tmp_path):
    app, _ = _build(tmp_path)
    res = TestClient(app).get("/assets/app.js")
    assert res.status_code == 200
    assert res.text == "console.log(1)"


def test_api_routes_keep_precedence(tmp_path):
    app, _ = _build(tmp_path)
    client = TestClient(app)
    assert client.get("/api/health").json() == {"status": "ok"}
    missing = client.get("/api/does-not-exist")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Not Found"


def test_traversal_never_leaves_static_dir(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    app, _ = _build(tmp_path)
    res = TestClient(app).get("/../outside.txt")
    assert res.status_code == 200
    assert "secret" not in res.text


def test_real_app_health_without_bundle():
    from app.main import app

    res = TestClient(app).get("/api/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (PowerShell, from repo root):

```powershell
Set-Location backend; .\.venv\Scripts\python.exe -m pytest -q; Set-Location ..
```

Expected: `ImportError: cannot import name 'mount_spa' from 'app.main'`.

- [ ] **Step 3: Add the setting**

Replace `backend/app/config.py` with:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App settings; env vars win over the .env file.

    env_file lists both the CWD .env and the repo-root ../.env so uvicorn
    launched from backend/ still picks up the single root .env.
    """

    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    project_name: str = "hubspot-logic-server"
    hubspot_token: str = ""
    hubspot_portal_id: str = ""
    health_cache_ttl_seconds: int = 900
    # Directory holding the built React bundle (index.html + assets/). Relative
    # paths resolve against backend/. Absent in dev (Vite proxy) and in compose
    # (nginx serves the SPA); present in the single-container Cloud Run image.
    static_dir: str = "static"


settings = Settings()
```

- [ ] **Step 4: Implement `mount_spa` in `backend/app/main.py`**

Replace the file with:

```python
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.routes import hubspot

BACKEND_DIR = Path(__file__).resolve().parents[1]

app = FastAPI(
    title=settings.project_name,
    # Keep the OpenAPI spec under /api so it rides the same /api proxy
    # (Vite in dev, nginx in prod) as every other backend route.
    openapi_url="/api/openapi.json",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# No CORS middleware on purpose: the frontend reaches this API same-origin
# in every mode (Vite /api proxy in dev, nginx /api proxy in compose, this
# process serving the bundle on Cloud Run), and a wildcard policy would let
# any page in a local browser read CRM data from the published port.

app.include_router(hubspot.router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "project": settings.project_name}


def mount_spa(app: FastAPI, static_dir: Path) -> bool:
    """Serve the built React bundle from this process (single-container mode).

    Registered LAST so every /api route keeps precedence. Returns False and
    changes nothing when static_dir has no index.html — dev (Vite proxy) and
    compose (nginx) run exactly as before.
    """
    static_dir = static_dir.resolve()
    index = static_dir / "index.html"
    if not index.is_file():
        return False

    assets = static_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = (static_dir / full_path).resolve()
        if full_path and candidate.is_file() and static_dir in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index)

    return True


mount_spa(app, BACKEND_DIR / settings.static_dir)
```

- [ ] **Step 5: Run the tests to verify they pass**

```powershell
Set-Location backend; .\.venv\Scripts\python.exe -m pytest -q; Set-Location ..
```

Expected: `8 passed`.

- [ ] **Step 6: Prove the no-op locally against the real app**

```powershell
Set-Location backend; .\.venv\Scripts\python.exe -c "from app.main import app; print([r.path for r in app.routes][-3:])"; Set-Location ..
```

Expected: the last routes are API routes and `/api/health` (no `/{full_path:path}` because `backend/static/` does not exist).

- [ ] **Step 7: Commit**

```powershell
git add backend/app/config.py backend/app/main.py backend/tests backend/pytest.ini
git commit -m "backend: serve the built SPA from FastAPI when static/ exists (Cloud Run single container)"
```

---

### Task 2: Container image, build context, Cloud Build config

**Files:**
- Create: `Dockerfile` (repo root)
- Create: `.dockerignore` (repo root)
- Create: `.gcloudignore` (repo root)
- Create: `deploy/cloudbuild.yaml`

**Interfaces:**
- Produces: image tags `${_REGION}-docker.pkg.dev/${PROJECT_ID}/${_REPO}/${_SERVICE}:${_TAG}` and `:latest`; substitutions `_REGION`, `_REPO`, `_SERVICE`, `_TAG` consumed by Task 4.

- [ ] **Step 1: Prove the frontend builds from the lockfile (raises spec claim C5)**

```powershell
Set-Location frontend; npm ci; npm run build; Get-ChildItem dist, dist/assets | Select-Object Name, Length; Set-Location ..
```

Expected: `dist/index.html` plus `dist/assets/index-*.js` and `index-*.css`. `Select-String -Path frontend/dist/index.html -Pattern '/assets/'` shows absolute `/assets/...` URLs (spec claim C6).

- [ ] **Step 2: Write the root `Dockerfile`**

```dockerfile
# syntax=docker/dockerfile:1
# Single-container image for Cloud Run: FastAPI serves /api/* and the built SPA.
# Local dev keeps docker-compose.yml (backend/Dockerfile + frontend/Dockerfile).

FROM node:20-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install -r requirements.txt
COPY backend/app ./app
# settings.static_dir defaults to "static", resolved against /app.
COPY --from=web /web/dist ./static
EXPOSE 8080
# Cloud Run injects PORT; shell form so the variable expands. exec keeps
# uvicorn as PID 1 for clean SIGTERM handling.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
```

- [ ] **Step 3: Write `.dockerignore`**

```
.git
.env
.env.*
!.env.example
**/node_modules
**/.venv
**/__pycache__
**/*.pyc
frontend/dist
docs
steps
synopsis
deploy/ansible
.specstory
.claude
.cursorindexingignore
```

- [ ] **Step 4: Write `.gcloudignore`** (Cloud Build source upload; same list plus the ignore file itself)

```
.gcloudignore
.git
.env
.env.*
!.env.example
**/node_modules
**/.venv
**/__pycache__
**/*.pyc
frontend/dist
docs
steps
synopsis
deploy/ansible
.specstory
.claude
.cursorindexingignore
```

- [ ] **Step 5: Write `deploy/cloudbuild.yaml`** (build + push only; deploy flags live in Ansible vars)

```yaml
# Build + push the hs-logic image. Deployment is done by deploy/ansible/deploy.yml
# so every Cloud Run flag lives in exactly one place (group_vars/all.yml).
substitutions:
  _REGION: europe-west1
  _REPO: hs-logic
  _SERVICE: hs-logic
  _TAG: manual

steps:
  - id: build
    name: gcr.io/cloud-builders/docker
    args:
      - build
      - -f
      - Dockerfile
      - -t
      - ${_REGION}-docker.pkg.dev/${PROJECT_ID}/${_REPO}/${_SERVICE}:${_TAG}
      - -t
      - ${_REGION}-docker.pkg.dev/${PROJECT_ID}/${_REPO}/${_SERVICE}:latest
      - .

images:
  - ${_REGION}-docker.pkg.dev/${PROJECT_ID}/${_REPO}/${_SERVICE}:${_TAG}
  - ${_REGION}-docker.pkg.dev/${PROJECT_ID}/${_REPO}/${_SERVICE}:latest

timeout: 1200s
options:
  logging: CLOUD_LOGGING_ONLY
```

- [ ] **Step 6: Validate the YAML and the ignore lists locally**

```powershell
backend\.venv\Scripts\python.exe -c "import yaml,sys; d=yaml.safe_load(open('deploy/cloudbuild.yaml')); print(sorted(d['substitutions']), len(d['steps']), d['images'])"
```

If `yaml` is missing: `backend\.venv\Scripts\python.exe -m pip install -q pyyaml` then rerun. Expected: `['_REGION', '_REPO', '_SERVICE', '_TAG'] 1 [...]`.

```powershell
git status --short --ignored | Select-String "frontend/dist|node_modules|\.env$"
```

Expected: `dist` and `node_modules` listed as ignored (`!!`), `.env` ignored.

- [ ] **Step 7: Commit**

```powershell
git add Dockerfile .dockerignore .gcloudignore deploy/cloudbuild.yaml
git commit -m "deploy: single-container Dockerfile + Cloud Build config for Cloud Run"
```

---

### Task 3: Ansible foundation playbook (one-time GCP resources)

**Files:**
- Create: `deploy/ansible/ansible.cfg`
- Create: `deploy/ansible/inventory.yml`
- Create: `deploy/ansible/requirements.txt`
- Create: `deploy/ansible/group_vars/all.yml`
- Create: `deploy/ansible/foundation.yml`
- Create: `deploy/ansible/roles/gcp_foundation/tasks/main.yml`
- Create: `deploy/ansible/roles/gcp_foundation/tasks/preflight.yml`

**Interfaces:**
- Produces: variables in `group_vars/all.yml` consumed by Tasks 4–5 (`gcp_project`, `gcp_region`, `service_name`, `image_uri`, `runtime_sa`, `secret_name`, `run_*`); GCP resources: API enablement, Artifact Registry repo, runtime SA, secret + version, IAM bindings, dataset GRANT.
- Consumes: `.env` at repo root (`HUBSPOT_TOKEN`) — read with `no_log`.

- [ ] **Step 1: Write the static config files**

`deploy/ansible/ansible.cfg`:

```ini
[defaults]
inventory = inventory.yml
retry_files_enabled = False
interpreter_python = auto_silent
```

`deploy/ansible/inventory.yml`:

```yaml
all:
  hosts:
    localhost:
      ansible_connection: local
```

`deploy/ansible/requirements.txt`:

```
ansible-core>=2.17,<2.19
```

`deploy/ansible/group_vars/all.yml`:

```yaml
# ---- target (the ONLY place the project is chosen) --------------------------
gcp_project: wisekeybq
gcp_project_number: "948062441615"
gcp_region: europe-west1

# ---- binaries (override for WSL interop / Cloud Shell) ----------------------
gcloud_bin: gcloud
bq_bin: bq

# ---- names -----------------------------------------------------------------
service_name: hs-logic
artifact_repo: hs-logic
image_uri: "{{ gcp_region }}-docker.pkg.dev/{{ gcp_project }}/{{ artifact_repo }}/{{ service_name }}"
runtime_sa_name: hs-logic-run
runtime_sa: "{{ runtime_sa_name }}@{{ gcp_project }}.iam.gserviceaccount.com"
secret_name: hs-logic-hubspot-token
cloudbuild_sa: "{{ gcp_project_number }}@cloudbuild.gserviceaccount.com"

# ---- app config (mirrors the compose allowlist) ----------------------------
env_file: "{{ playbook_dir }}/../../.env"
hubspot_portal_id: "9201667"
health_cache_ttl_seconds: "900"
secret_rotate: false            # -e secret_rotate=true adds a new version

# ---- BigQuery read scope for the runtime SA (Phase 2 read path) ------------
bq_read_datasets:
  - HubspotSync

# ---- Cloud Run sizing (spec §3.1) ------------------------------------------
run_port: "8080"
run_memory: 512Mi
run_cpu: "1"
run_max_instances: "1"
run_min_instances: "0"
run_timeout: "300"
run_concurrency: "80"
run_allow_unauthenticated: true

required_apis:
  - run.googleapis.com
  - cloudbuild.googleapis.com
  - artifactregistry.googleapis.com
  - secretmanager.googleapis.com
  - iam.googleapis.com
```

- [ ] **Step 2: Write the preflight tasks** (`roles/gcp_foundation/tasks/preflight.yml`, read-only, tag `preflight`)

```yaml
---
- name: Active gcloud account
  ansible.builtin.command: >-
    {{ gcloud_bin }} auth list --filter=status:ACTIVE --format=value(account)
  register: gcloud_account
  changed_when: false

- name: Fail when no account is active
  ansible.builtin.fail:
    msg: "No active gcloud account. Run: gcloud auth login"
  when: gcloud_account.stdout | trim | length == 0

- name: Billing state of {{ gcp_project }}
  ansible.builtin.command: >-
    {{ gcloud_bin }} billing projects describe {{ gcp_project }} --format=value(billingEnabled)
  register: billing
  changed_when: false

- name: Fail when billing is disabled (the wisekeyclourrun trap, 2026-09-03)
  ansible.builtin.fail:
    msg: "Billing is disabled on {{ gcp_project }}; Cloud Run/Artifact Registry calls will fail."
  when: billing.stdout | trim | lower != "true"

- name: Preflight summary
  ansible.builtin.debug:
    msg: "account={{ gcloud_account.stdout | trim }} project={{ gcp_project }} billing={{ billing.stdout | trim }}"
```

- [ ] **Step 3: Write the foundation tasks** (`roles/gcp_foundation/tasks/main.yml`)

```yaml
---
- name: Preflight
  ansible.builtin.import_tasks: preflight.yml
  tags: [preflight, always]

# ---- APIs ------------------------------------------------------------------
- name: Enabled services
  ansible.builtin.command: >-
    {{ gcloud_bin }} services list --enabled --project {{ gcp_project }} --format=value(config.name)
  register: enabled_apis
  changed_when: false
  tags: [apis]

- name: Enable missing APIs
  ansible.builtin.command: >-
    {{ gcloud_bin }} services enable {{ item }} --project {{ gcp_project }}
  loop: "{{ required_apis | difference(enabled_apis.stdout_lines) }}"
  tags: [apis]

- name: Read back enabled services
  ansible.builtin.command: >-
    {{ gcloud_bin }} services list --enabled --project {{ gcp_project }} --format=value(config.name)
  register: enabled_apis_after
  changed_when: false
  failed_when: required_apis | difference(enabled_apis_after.stdout_lines) | length > 0
  tags: [apis]

# ---- Artifact Registry -----------------------------------------------------
- name: Artifact repo exists?
  ansible.builtin.command: >-
    {{ gcloud_bin }} artifacts repositories describe {{ artifact_repo }}
    --location {{ gcp_region }} --project {{ gcp_project }} --format=value(name)
  register: repo_probe
  changed_when: false
  failed_when: false
  tags: [registry]

- name: Create Artifact repo
  ansible.builtin.command: >-
    {{ gcloud_bin }} artifacts repositories create {{ artifact_repo }}
    --repository-format=docker --location {{ gcp_region }} --project {{ gcp_project }}
    --description "hs-logic images (Cloud Run)"
  when: repo_probe.rc != 0
  tags: [registry]

- name: Read back Artifact repo
  ansible.builtin.command: >-
    {{ gcloud_bin }} artifacts repositories describe {{ artifact_repo }}
    --location {{ gcp_region }} --project {{ gcp_project }} --format=value(format)
  register: repo_after
  changed_when: false
  failed_when: repo_after.stdout | trim != "DOCKER"
  tags: [registry]

- name: Cloud Build SA may push to the repo (spec claim C4)
  ansible.builtin.command: >-
    {{ gcloud_bin }} artifacts repositories add-iam-policy-binding {{ artifact_repo }}
    --location {{ gcp_region }} --project {{ gcp_project }}
    --member serviceAccount:{{ cloudbuild_sa }} --role roles/artifactregistry.writer
    --format=none
  register: repo_iam
  changed_when: "'Updated IAM policy' in repo_iam.stderr"
  tags: [registry]

# ---- Runtime service account ----------------------------------------------
- name: Runtime SA exists?
  ansible.builtin.command: >-
    {{ gcloud_bin }} iam service-accounts describe {{ runtime_sa }} --project {{ gcp_project }} --format=value(email)
  register: sa_probe
  changed_when: false
  failed_when: false
  tags: [sa]

- name: Create runtime SA
  ansible.builtin.command: >-
    {{ gcloud_bin }} iam service-accounts create {{ runtime_sa_name }}
    --display-name "hs-logic Cloud Run runtime" --project {{ gcp_project }}
  when: sa_probe.rc != 0
  tags: [sa]

- name: Read back runtime SA
  ansible.builtin.command: >-
    {{ gcloud_bin }} iam service-accounts describe {{ runtime_sa }} --project {{ gcp_project }} --format=value(email)
  register: sa_after
  changed_when: false
  failed_when: sa_after.stdout | trim != runtime_sa
  tags: [sa]

- name: Runtime SA project roles (bigquery.jobUser)
  ansible.builtin.command: >-
    {{ gcloud_bin }} projects add-iam-policy-binding {{ gcp_project }}
    --member serviceAccount:{{ runtime_sa }} --role roles/bigquery.jobUser
    --condition=None --format=none
  register: sa_project_iam
  changed_when: "'Updated IAM policy' in sa_project_iam.stderr"
  tags: [sa]

# ---- Secret ------------------------------------------------------------------
- name: Secret exists?
  ansible.builtin.command: >-
    {{ gcloud_bin }} secrets describe {{ secret_name }} --project {{ gcp_project }} --format=value(name)
  register: secret_probe
  changed_when: false
  failed_when: false
  tags: [secret]

- name: Create secret container
  ansible.builtin.command: >-
    {{ gcloud_bin }} secrets create {{ secret_name }} --replication-policy=automatic --project {{ gcp_project }}
  when: secret_probe.rc != 0
  tags: [secret]

- name: Enabled secret versions
  ansible.builtin.command: >-
    {{ gcloud_bin }} secrets versions list {{ secret_name }} --project {{ gcp_project }}
    --filter=state:ENABLED --format=value(name)
  register: secret_versions
  changed_when: false
  tags: [secret]

- name: Read HUBSPOT_TOKEN from the repo .env (never echoed)
  ansible.builtin.set_fact:
    hubspot_token: "{{ lookup('ansible.builtin.ini', 'HUBSPOT_TOKEN', type='properties', file=env_file) }}"
  no_log: true
  when: secret_versions.stdout_lines | length == 0 or secret_rotate | bool
  tags: [secret]

- name: Refuse an empty token
  ansible.builtin.fail:
    msg: "HUBSPOT_TOKEN is empty in {{ env_file }}"
  when: (secret_versions.stdout_lines | length == 0 or secret_rotate | bool) and (hubspot_token | default('') | length == 0)
  no_log: true
  tags: [secret]

- name: Add secret version from stdin
  ansible.builtin.command:
    cmd: "{{ gcloud_bin }} secrets versions add {{ secret_name }} --project {{ gcp_project }} --data-file=-"
    stdin: "{{ hubspot_token }}"
    stdin_add_newline: false
  when: secret_versions.stdout_lines | length == 0 or secret_rotate | bool
  no_log: true
  tags: [secret]

- name: Read back secret versions
  ansible.builtin.command: >-
    {{ gcloud_bin }} secrets versions list {{ secret_name }} --project {{ gcp_project }}
    --filter=state:ENABLED --format=value(name)
  register: secret_versions_after
  changed_when: false
  failed_when: secret_versions_after.stdout_lines | length == 0
  tags: [secret]

- name: Runtime SA may read the secret
  ansible.builtin.command: >-
    {{ gcloud_bin }} secrets add-iam-policy-binding {{ secret_name }} --project {{ gcp_project }}
    --member serviceAccount:{{ runtime_sa }} --role roles/secretmanager.secretAccessor --format=none
  register: secret_iam
  changed_when: "'Updated IAM policy' in secret_iam.stderr"
  tags: [secret]

# ---- BigQuery dataset read grant (DCL; dataset IAM CLI is allowlist-gated) --
- name: Grant dataViewer on datasets to the runtime SA
  ansible.builtin.command: >-
    {{ bq_bin }} query --project_id={{ gcp_project }} --nouse_legacy_sql --format=none
    'GRANT `roles/bigquery.dataViewer` ON SCHEMA `{{ gcp_project }}.{{ item }}` TO "serviceAccount:{{ runtime_sa }}"'
  loop: "{{ bq_read_datasets }}"
  register: bq_grant
  changed_when: false
  tags: [bq]

- name: Read back dataset access
  ansible.builtin.command: >-
    {{ bq_bin }} show --project_id={{ gcp_project }} --format=json {{ gcp_project }}:{{ item }}
  loop: "{{ bq_read_datasets }}"
  register: bq_access
  changed_when: false
  failed_when: runtime_sa not in bq_access.stdout
  tags: [bq]

- name: Foundation summary
  ansible.builtin.debug:
    msg:
      - "project={{ gcp_project }} region={{ gcp_region }}"
      - "repo={{ image_uri }}"
      - "runtime_sa={{ runtime_sa }}"
      - "secret={{ secret_name }} versions={{ secret_versions_after.stdout_lines | length }}"
      - "bq_read={{ bq_read_datasets | join(',') }}"
```

- [ ] **Step 4: Write the playbook** (`deploy/ansible/foundation.yml`)

```yaml
---
# One-time GCP foundation for hs-logic. Idempotent; safe to re-run.
#   ansible-playbook foundation.yml                 # everything
#   ansible-playbook foundation.yml --tags preflight  # read-only checks
#   ansible-playbook foundation.yml -e secret_rotate=true
- name: hs-logic — GCP foundation
  hosts: localhost
  gather_facts: false
  roles:
    - gcp_foundation
```

- [ ] **Step 5: Syntax-check from WSL and verify the `.env` parser against the example file only**

```powershell
wsl -d Ubuntu -- bash -lc "cd /mnt/c/Users/ayaobama/Documents/AnthonySalesOps/Codebase/hs-standalone/deploy/ansible && ~/.venvs/ansible/bin/ansible-playbook --syntax-check foundation.yml"
```

Expected: `playbook: foundation.yml`.

```powershell
wsl -d Ubuntu -- bash -lc "cd /mnt/c/Users/ayaobama/Documents/AnthonySalesOps/Codebase/hs-standalone/deploy/ansible && ~/.venvs/ansible/bin/ansible localhost -m debug -a \"msg={{ lookup('ansible.builtin.ini', 'HUBSPOT_PORTAL_ID', type='properties', file='../../.env.example') }}\""
```

Expected: `"msg": "9201667"` (proves the properties parser handles the comment lines; the real `.env` is never printed).

- [ ] **Step 6: Run the read-only preflight through the interop gcloud (raises spec claim C8)**

```powershell
wsl -d Ubuntu -- bash -lc "cd /mnt/c/Users/ayaobama/Documents/AnthonySalesOps/Codebase/hs-standalone/deploy/ansible && export CLOUDSDK_CONFIG=/mnt/c/Users/ayaobama/AppData/Roaming/gcloud && ~/.venvs/ansible/bin/ansible-playbook foundation.yml --tags preflight -e gcloud_bin='/mnt/c/Users/ayaobama/AppData/Local/Google/Cloud\ SDK/google-cloud-sdk/bin/gcloud'"
```

Expected: `ok=…  failed=0` and the summary line `account=anthony.yaobama@gmail.com project=wisekeybq billing=True`.

Also probe `bq` through interop (spec claim C9):

```powershell
wsl -d Ubuntu -- bash -lc "export CLOUDSDK_CONFIG=/mnt/c/Users/ayaobama/AppData/Roaming/gcloud && '/mnt/c/Users/ayaobama/AppData/Local/Google/Cloud SDK/google-cloud-sdk/bin/bq' version"
```

Expected: a version string. If it errors, record it in the spec's Gate 1 row C9 and use the Windows fallback documented in Task 5.

- [ ] **Step 7: Commit**

```powershell
git add deploy/ansible
git commit -m "deploy: Ansible foundation playbook (APIs, Artifact Registry, runtime SA, secret, IAM, BQ grant)"
```

---

### Task 4: Ansible deploy + verify playbooks

**Files:**
- Create: `deploy/ansible/deploy.yml`
- Create: `deploy/ansible/roles/app_deploy/tasks/main.yml`
- Create: `deploy/ansible/verify.yml`
- Create: `deploy/ansible/roles/app_verify/tasks/main.yml`

**Interfaces:**
- Consumes: all variables from Task 3; the image produced by `deploy/cloudbuild.yaml` (Task 2).
- Produces: Cloud Run service `{{ service_name }}`; `deploy/ansible/last_deploy.json` manifest (git-ignored) with `url`, `revision`, `previous_revision`, `image`.

- [ ] **Step 1: Write the deploy tasks** (`roles/app_deploy/tasks/main.yml`)

```yaml
---
- name: Repo root
  ansible.builtin.set_fact:
    repo_root: "{{ playbook_dir }}/../.."

- name: Git sha (image tag)
  ansible.builtin.command: git rev-parse --short=12 HEAD
  args:
    chdir: "{{ repo_root }}"
  register: git_sha
  changed_when: false

- name: Working tree must be clean (override with -e allow_dirty=true)
  ansible.builtin.command: git status --porcelain
  args:
    chdir: "{{ repo_root }}"
  register: git_dirty
  changed_when: false
  failed_when: git_dirty.stdout | length > 0 and not (allow_dirty | default(false) | bool)

- name: Image tag
  ansible.builtin.set_fact:
    image_tag: "{{ git_sha.stdout | trim }}"
    image_ref: "{{ image_uri }}:{{ git_sha.stdout | trim }}"

- name: Previous revision (for rollback)
  ansible.builtin.command: >-
    {{ gcloud_bin }} run services describe {{ service_name }} --region {{ gcp_region }}
    --project {{ gcp_project }} --format=value(status.latestReadyRevisionName)
  register: previous_revision
  changed_when: false
  failed_when: false

- name: Image already built for this sha?
  ansible.builtin.command: >-
    {{ gcloud_bin }} artifacts docker images describe {{ image_ref }} --project {{ gcp_project }} --format=value(image_summary.digest)
  register: image_probe
  changed_when: false
  failed_when: false

- name: Cloud Build (build + push)
  ansible.builtin.command: >-
    {{ gcloud_bin }} builds submit --project {{ gcp_project }} --region {{ gcp_region }}
    --config deploy/cloudbuild.yaml
    --substitutions _REGION={{ gcp_region }},_REPO={{ artifact_repo }},_SERVICE={{ service_name }},_TAG={{ image_tag }}
    .
  args:
    chdir: "{{ repo_root }}"
  when: image_probe.rc != 0 or (force_build | default(false) | bool)

- name: Read back the image
  ansible.builtin.command: >-
    {{ gcloud_bin }} artifacts docker images describe {{ image_ref }} --project {{ gcp_project }} --format=value(image_summary.digest)
  register: image_after
  changed_when: false
  failed_when: image_after.stdout | trim | length == 0

- name: Deploy to Cloud Run
  ansible.builtin.command: >-
    {{ gcloud_bin }} run deploy {{ service_name }}
    --image {{ image_ref }}
    --region {{ gcp_region }} --project {{ gcp_project }} --platform managed
    --service-account {{ runtime_sa }}
    --port {{ run_port }} --memory {{ run_memory }} --cpu {{ run_cpu }}
    --max-instances {{ run_max_instances }} --min-instances {{ run_min_instances }}
    --timeout {{ run_timeout }} --concurrency {{ run_concurrency }}
    --set-secrets HUBSPOT_TOKEN={{ secret_name }}:latest
    --set-env-vars HUBSPOT_PORTAL_ID={{ hubspot_portal_id }},HEALTH_CACHE_TTL_SECONDS={{ health_cache_ttl_seconds }}
    {{ '--allow-unauthenticated' if run_allow_unauthenticated | bool else '--no-allow-unauthenticated' }}
    --quiet

- name: Read back the service
  ansible.builtin.command: >-
    {{ gcloud_bin }} run services describe {{ service_name }} --region {{ gcp_region }}
    --project {{ gcp_project }} --format=json
  register: service_json
  changed_when: false

- name: Parse service
  ansible.builtin.set_fact:
    svc: "{{ service_json.stdout | from_json }}"

- name: Assert deployed shape
  ansible.builtin.assert:
    that:
      - svc.spec.template.spec.containers[0].image == image_ref
      - svc.spec.template.spec.serviceAccountName == runtime_sa
      - svc.spec.template.metadata.annotations['autoscaling.knative.dev/maxScale'] == run_max_instances
      - svc.status.url is defined
    fail_msg: "Service read-back does not match the requested deploy"

- name: Write manifest
  ansible.builtin.copy:
    dest: "{{ playbook_dir }}/last_deploy.json"
    content: "{{ {'service': service_name, 'url': svc.status.url, 'image': image_ref, 'revision': svc.status.latestReadyRevisionName, 'previous_revision': previous_revision.stdout | trim, 'deployed_at': lookup('pipe', 'date -u +%Y-%m-%dT%H:%M:%SZ')} | to_nice_json }}"
    mode: "0644"

- name: Deployed
  ansible.builtin.debug:
    msg: "{{ svc.status.url }}  revision={{ svc.status.latestReadyRevisionName }}  previous={{ previous_revision.stdout | trim | default('none', true) }}"
```

- [ ] **Step 2: Write the verify tasks** (`roles/app_verify/tasks/main.yml`, read-only Gate 2)

```yaml
---
- name: Service description
  ansible.builtin.command: >-
    {{ gcloud_bin }} run services describe {{ service_name }} --region {{ gcp_region }}
    --project {{ gcp_project }} --format=json
  register: service_json
  changed_when: false

- name: Parse
  ansible.builtin.set_fact:
    svc: "{{ service_json.stdout | from_json }}"
    url: "{{ (service_json.stdout | from_json).status.url }}"

- name: GET /api/health
  ansible.builtin.uri:
    url: "{{ url }}/api/health"
    return_content: true
  register: health
  failed_when: health.status != 200 or health.json.status != 'ok'

- name: GET /api/hubspot/portal (proves secret wiring + token validity)
  ansible.builtin.uri:
    url: "{{ url }}/api/hubspot/portal"
    return_content: true
    timeout: 60
  register: portal
  failed_when: portal.status != 200 or (portal.json.portal_id | string) != hubspot_portal_id

- name: GET / (SPA)
  ansible.builtin.uri:
    url: "{{ url }}/"
    return_content: true
  register: spa
  failed_when: spa.status != 200 or '<div id="root">' not in spa.content

- name: GET /api/does-not-exist keeps API precedence
  ansible.builtin.uri:
    url: "{{ url }}/api/does-not-exist"
    status_code: 404
    return_content: true
  register: notfound
  failed_when: notfound.json.detail != 'Not Found'

- name: Secret versions
  ansible.builtin.command: >-
    {{ gcloud_bin }} secrets versions list {{ secret_name }} --project {{ gcp_project }}
    --filter=state:ENABLED --format=value(name)
  register: versions
  changed_when: false
  failed_when: versions.stdout_lines | length == 0

- name: Dataset access read-back
  ansible.builtin.command: >-
    {{ bq_bin }} show --project_id={{ gcp_project }} --format=json {{ gcp_project }}:{{ item }}
  loop: "{{ bq_read_datasets }}"
  register: bq_access
  changed_when: false
  failed_when: runtime_sa not in bq_access.stdout

- name: Gate 2 summary (blast radius = these names)
  ansible.builtin.debug:
    msg:
      - "url={{ url }} revision={{ svc.status.latestReadyRevisionName }} image={{ svc.spec.template.spec.containers[0].image }}"
      - "maxScale={{ svc.spec.template.metadata.annotations['autoscaling.knative.dev/maxScale'] }} sa={{ svc.spec.template.spec.serviceAccountName }}"
      - "portal_id={{ portal.json.portal_id }} secret_versions={{ versions.stdout_lines | length }}"
      - "created resources: run/{{ service_name }}, sa/{{ runtime_sa }}, secret/{{ secret_name }}, artifacts/{{ artifact_repo }}, iam: secretAccessor+jobUser(+dataViewer via GRANT), allUsers:run.invoker"
      - "external dependencies at runtime: api.hubapi.com only — independent of Stacksync state"
```

- [ ] **Step 3: Write the playbooks**

`deploy/ansible/deploy.yml`:

```yaml
---
# Per-release: build (Cloud Build) → deploy (Cloud Run) → read-back → manifest.
#   ansible-playbook deploy.yml
#   ansible-playbook deploy.yml -e force_build=true
#   ansible-playbook deploy.yml -e allow_dirty=true   # local experiments only
- name: hs-logic — build and deploy
  hosts: localhost
  gather_facts: false
  roles:
    - app_deploy
    - app_verify
```

`deploy/ansible/verify.yml`:

```yaml
---
# Gate 2 read-backs only. No mutation. Run any time.
- name: hs-logic — verify deployment
  hosts: localhost
  gather_facts: false
  roles:
    - app_verify
```

- [ ] **Step 4: Git-ignore the manifest and syntax-check**

Append to `.gitignore`:

```
# Ansible deploy manifest (per-machine state)
deploy/ansible/last_deploy.json
```

```powershell
wsl -d Ubuntu -- bash -lc "cd /mnt/c/Users/ayaobama/Documents/AnthonySalesOps/Codebase/hs-standalone/deploy/ansible && for p in foundation.yml deploy.yml verify.yml; do ~/.venvs/ansible/bin/ansible-playbook --syntax-check \$p; done"
```

Expected: three `playbook:` lines, no errors.

- [ ] **Step 5: Commit**

```powershell
git add .gitignore deploy/ansible
git commit -m "deploy: Ansible deploy + verify playbooks (Cloud Build, Cloud Run, Gate 2 read-backs)"
```

---

### Task 5: Runbook, README, repo registration

**Files:**
- Create: `deploy/README.md`
- Modify: `docs/RUNBOOK.md` (§1 table, §2, §7)
- Modify: `README.md` ("Deploy to Cloud Run" section)
- Modify: `C:\Users\ayaobama\Documents\AnthonySalesOps\Codebase\_MAP.md` (HubSpot platform table)

- [ ] **Step 1: Write `deploy/README.md`**

````markdown
# Deploying hs-logic to Cloud Run

Executor of record: the Ansible playbooks in `deploy/ansible/`. They shell out to `gcloud`/`bq`
and read back every resource they touch. Spec: `docs/superpowers/specs/2026-09-03-gtm-cloud-platform-design.md`.

## Where to run them

**Cloud Shell (no local tooling needed):**

```bash
git clone https://github.com/Wkayaobama/Hs-Logic.git && cd Hs-Logic && git checkout feat/cloud-run-deploy
pip install --user -r deploy/ansible/requirements.txt
printf 'HUBSPOT_TOKEN=%s\n' "$HUBSPOT_TOKEN" > .env     # paste the token into the shell variable first; .env is git-ignored
cd deploy/ansible && ansible-playbook foundation.yml && ansible-playbook deploy.yml
```

**WSL Ubuntu on the operator machine (Windows gcloud through interop):**

```bash
cd /mnt/c/Users/ayaobama/Documents/AnthonySalesOps/Codebase/hs-standalone/deploy/ansible
export CLOUDSDK_CONFIG=/mnt/c/Users/ayaobama/AppData/Roaming/gcloud
G='/mnt/c/Users/ayaobama/AppData/Local/Google/Cloud SDK/google-cloud-sdk/bin'
~/.venvs/ansible/bin/ansible-playbook foundation.yml -e "gcloud_bin='$G/gcloud'" -e "bq_bin='$G/bq'"
~/.venvs/ansible/bin/ansible-playbook deploy.yml     -e "gcloud_bin='$G/gcloud'" -e "bq_bin='$G/bq'"
```

Windows fallback for the BigQuery GRANT if `bq` does not run through interop (PowerShell quoting rule:
single-quote the whole SQL, doubled single quotes inside):

```powershell
bq query --project_id=wisekeybq --nouse_legacy_sql 'GRANT `roles/bigquery.dataViewer` ON SCHEMA `wisekeybq.HubspotSync` TO "serviceAccount:hs-logic-run@wisekeybq.iam.gserviceaccount.com"'
```

## Playbooks

| Playbook | When | Mutates |
|---|---|---|
| `foundation.yml` | once per project, and after `-e secret_rotate=true` | APIs, Artifact Registry repo, runtime SA, secret, IAM, BQ GRANT |
| `deploy.yml` | every release (tag = git sha; refuses a dirty tree) | Cloud Build image, Cloud Run revision |
| `verify.yml` | any time | nothing (Gate 2 read-backs) |

Tags on `foundation.yml`: `preflight`, `apis`, `registry`, `sa`, `secret`, `bq`.

## Variables

Everything lives in `group_vars/all.yml`. The project is chosen in exactly one place (`gcp_project`).
Runtime sizing (`run_*`) mirrors spec §3.1. `bq_read_datasets` is the Phase 2 read scope.

## Rollback

`last_deploy.json` records `previous_revision`:

```bash
gcloud run services update-traffic hs-logic --region europe-west1 --project wisekeybq --to-revisions <previous_revision>=100
```

## Teardown (delete by key list)

```bash
gcloud run services delete hs-logic --region europe-west1 --project wisekeybq
gcloud secrets delete hs-logic-hubspot-token --project wisekeybq
gcloud iam service-accounts delete hs-logic-run@wisekeybq.iam.gserviceaccount.com --project wisekeybq
gcloud artifacts repositories delete hs-logic --location europe-west1 --project wisekeybq
bq query --project_id=wisekeybq --nouse_legacy_sql 'REVOKE `roles/bigquery.dataViewer` ON SCHEMA `wisekeybq.HubspotSync` FROM "serviceAccount:hs-logic-run@wisekeybq.iam.gserviceaccount.com"'
```
````

- [ ] **Step 2: Update `docs/RUNBOOK.md`**

In §1 add a row after "App (prod/compose)":

```markdown
| App (Cloud Run, staging) | URL printed by `deploy/ansible/deploy.yml` (also in `deploy/ansible/last_deploy.json`); public, single instance | 
```

In §2 add after the compose block:

```markdown
**Cloud Run (staging):** see `deploy/README.md`. `ansible-playbook deploy.yml` builds and
deploys the current commit; `verify.yml` re-runs the post-deploy checks without deploying.
```

In §7 add after item 7:

```markdown
8. Cloud Run only: `ansible-playbook verify.yml` green (health, portal id, SPA, `/api` precedence,
   secret version, dataset access).
```

- [ ] **Step 3: Update `README.md`**

Add after "## Run with Docker":

```markdown
## Deploy to Cloud Run

Single-container image (`Dockerfile` at the repo root: FastAPI serves the built SPA), built by
Cloud Build and deployed by the Ansible playbooks in `deploy/ansible/`. See `deploy/README.md`.
```

- [ ] **Step 4: Register the repo in `Codebase/_MAP.md`**

In the "HubSpot platform" table, ✔ row, add at the front: `` `hs-standalone` (Hubspot-Logic-Server: FastAPI+React CRM-health explorer, Cloud Run deploy via `deploy/ansible`; `Hubspot-Logic-Server/` = older checkout of the same repo) ``.

- [ ] **Step 5: Commit**

```powershell
git add deploy/README.md docs/RUNBOOK.md README.md
git commit -m "docs: Cloud Run deploy runbook, RUNBOOK/README pointers"
```

(`_MAP.md` lives outside this repo; it is not under git.)

---

### Task 6: Execute against GCP (operator go required) and close Gate 2

**Files:**
- Modify: `docs/superpowers/specs/2026-09-03-gtm-cloud-platform-design.md` (append "Gate 2 results")

- [ ] **Step 1: Obtain the operator's go** (the first outward mutation creates a public URL).

- [ ] **Step 2: Run `foundation.yml`** from WSL (commands in `deploy/README.md`). Expected: `failed=0`, summary shows `versions=1`.

- [ ] **Step 3: Run `deploy.yml`.** Expected: Cloud Build ≈ 3–5 min, then `Deployed` with a `run.app` URL and `app_verify` green.

- [ ] **Step 4: Browser check** of the URL: Overview tab shows portal counts; CRM Health cold scan completes; second load shows "Last scanned N min ago".

- [ ] **Step 5: Append Gate 2 results to the spec** (URL, revision, image digest, timestamp, every read-back with its observed value) and commit:

```powershell
git add docs/superpowers/specs/2026-09-03-gtm-cloud-platform-design.md
git commit -m "spec: Gate 2 results for the first Cloud Run deploy"
```

- [ ] **Step 6: Post-deploy gate** — invoke self-adversarial-reasoning against "the mutation is correct and contained" using the `verify.yml` output; any surviving refutation stops further outward action (Phase 2).
