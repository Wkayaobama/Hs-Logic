# Deploying hs-logic to Cloud Run

Executor of record: the Ansible playbooks in `deploy/ansible/`. They shell out to `gcloud`/`bq`
and read back every resource they touch. Spec:
`docs/superpowers/specs/2026-09-03-gtm-cloud-platform-design.md`.

Target project, region and every name live in **one** file: `deploy/ansible/group_vars/all.yml`
(`gcp_project: wisekeybq`, `gcp_region: europe-west1`). Nothing here depends on the Stacksync sync
state; the service only talks to `api.hubapi.com` at runtime.

## Where to run them

**WSL Ubuntu on the operator machine (Windows gcloud through interop):**

```bash
# one-time: wrappers gcloud/bq → ~/.local/bin (they set CLOUDSDK_CONFIG to the Windows config) + Ansible venv
bash /mnt/c/Users/ayaobama/Documents/AnthonySalesOps/Codebase/hs-standalone/deploy/wsl/install.sh
```

```bash
cd /mnt/c/Users/ayaobama/Documents/AnthonySalesOps/Codebase/hs-standalone/deploy/ansible
~/.venvs/ansible/bin/ansible-playbook foundation.yml --tags preflight   # read-only checks
~/.venvs/ansible/bin/ansible-playbook foundation.yml                    # one-time resources
~/.venvs/ansible/bin/ansible-playbook deploy.yml                        # build + deploy + verify
~/.venvs/ansible/bin/ansible-playbook verify.yml                        # read-backs only
```

Open a **new** login shell after `install.sh` so `~/.local/bin` is on PATH.

**Cloud Shell (no local tooling):**

```bash
git clone https://github.com/Wkayaobama/Hs-Logic.git && cd Hs-Logic && git checkout feat/cloud-run-deploy
pip install --user -r deploy/ansible/requirements.txt
# put the private-app token in the git-ignored .env (type it in the shell, never in the repo)
printf 'HUBSPOT_TOKEN=%s\n' "$HUBSPOT_TOKEN" > .env
cd deploy/ansible && ansible-playbook foundation.yml && ansible-playbook deploy.yml
```

Windows fallback for the BigQuery GRANT if `bq` does not run through interop (PowerShell quoting
rule: single-quote the whole SQL, doubled single quotes inside):

```powershell
bq query --project_id=wisekeybq --nouse_legacy_sql 'GRANT `roles/bigquery.dataViewer` ON SCHEMA `wisekeybq.HubspotSync` TO "serviceAccount:hs-logic-run@wisekeybq.iam.gserviceaccount.com"'
```

## Playbooks

| Playbook | When | Mutates |
|---|---|---|
| `foundation.yml` | once per project, and after `-e secret_rotate=true` | APIs, Artifact Registry repo, runtime SA, secret, IAM, BQ GRANT |
| `deploy.yml` | every release (tag = git sha; refuses a dirty tree, `-e allow_dirty=true` for experiments) | Cloud Build image, Cloud Run revision |
| `verify.yml` | any time | nothing (Gate 2 read-backs) |

Tags on `foundation.yml`: `preflight`, `apis`, `registry`, `sa`, `secret`, `bq`.

The token is read from the repo-root `.env` with `no_log: true` and pushed to Secret Manager
through stdin; it never appears in playbook output.

## Rollback

`last_deploy.json` (git-ignored) records `previous_revision`:

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
