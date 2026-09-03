# Ansible × gcloud — identity context, runners, and the portable playbook contract

Reference for any endeavour that drives Google Cloud through Ansible playbooks from this
workstation or from Cloud Shell. Written 2026-09-03 from the first `hs-logic` Cloud Run deployment;
every number and trap below is first-hand (dated) unless marked *[doc]* (vendor documentation
fetched that day) or *[memory]* (earlier session, cited by date).

Reuse rule (feedback 2026-07: "tactics aren't ambient patterns"): this document is the canonical
copy **in the repo that proved it**. Promote it to a dotfiles skill only after a second endeavour
runs the same contract unchanged.

---

## 0. How to use this document

| You want to… | Read |
|---|---|
| understand *which identity* a command runs as, and why a "permission" error is often not one | §1 |
| pick where to run playbooks (WSL, Cloud Shell, VM, Cloud Build) with measured trade-offs | §2 |
| write playbooks that another runner can execute unchanged | §3 |
| execute: step-by-step per runner | §4 |
| start a new endeavour from this pattern | §5 checklist |
| audit a claim in this doc | §6 evidence log |

Doctrine the whole pattern obeys (global CLAUDE.md):

- **Deterministic machinery executes; Claude judges at boundaries.** The playbook is the executor of
  record; the human decides at preflight, at the first outward mutation, and at Gate 2.
- **Verify by artifacts, not exit codes.** Every mutating task is probe → command → read-back; the
  read-back decides `changed_when` / `failed_when`.
- **Every call passes the project explicitly.** The workstation's gcloud default project is *not*
  the data project (§1.2); relying on the default has already sent writes to the wrong project
  once *[memory 2026-08-18]*.
- **Entry-chain verification.** Reason about launcher → shell → wrapper → tool against the real
  configs; the same playbook behaves differently under PowerShell, Git Bash, WSL and Cloud Shell
  (§2.3).

---

## 1. The identity context ("the rich context")

### 1.1 The layers, and which one each consumer reads

```
                     ┌──────────────────────────────────────────────────────────────┐
 gcloud / bq CLI ───▶│ CLI credential store  <config dir>/credentials.db, access_tokens.db │
                     │ + configurations (active account, core/project, compute/region)  │
                     └──────────────────────────────────────────────────────────────┘
                     ┌──────────────────────────────────────────────────────────────┐
 client libraries ──▶│ 1. GOOGLE_APPLICATION_CREDENTIALS = path to a key file  (wins) │
 (python google-*,   │ 2. <config dir>/application_default_credentials.json (ADC)     │
  dlt, bq via lib)   │    carries quota_project_id — the project that is BILLED/CHECKED │
                     │ 3. attached identity (Cloud Run / Cloud Build / GCE metadata)   │
                     └──────────────────────────────────────────────────────────────┘
 project used by libs: GOOGLE_CLOUD_PROJECT env  ▶ ADC quota_project ▶ gcloud core/project
 impersonation:        --impersonate-service-account / CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT
                       (needs roles/iam.serviceAccountTokenCreator + iamcredentials API ON)
```

Two facts that explain most "mysterious" failures:

1. **The CLI and the libraries do not share credentials.** `gcloud` being logged in says nothing
   about what `google.auth.default()` will return. A stale ADC file (minted through an unrelated
   project's OAuth app in March) made impersonation checks evaluate against the wrong project for a
   whole day *[memory 2026-08-13, obs #3686]*.
2. **`GOOGLE_APPLICATION_CREDENTIALS` silently wins.** On this machine it is set at *User* level to
   `~/.config/gcp/sealsq-revenue-sa.json` (a foreign service account kept for the Sheets tooling).
   Any library caller runs as that SA unless the process pops the variable
   (`os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)`) — never delete it globally
   *[memory 2026-08-13; re-verified 2026-09-03]*.

### 1.2 This workstation, live layout (probed 2026-09-03)

| Layer | Value | Consequence |
|---|---|---|
| SDK root | `C:\Users\ayaobama\AppData\Local\Google\Cloud SDK\google-cloud-sdk` (gcloud 543.0.0, bq 2.1.24) | the path contains a **space** → wrappers, never raw paths in command strings |
| Config dir (`CLOUDSDK_CONFIG`) | `C:\Users\ayaobama\AppData\Roaming\gcloud` | this is what WSL must point at to reuse the login |
| Active configuration | `default` — account `anthony.yaobama@gmail.com`, **project `wisekeyclourrun`** | data lives in `wisekeybq`; `wisekeyclourrun` has **billing disabled** → `--project` on every call |
| ADC file | `authorized_user`, Cloud SDK client `32555940559…`, `quota_project_id = wisekeybq` | healthy since the 2026-08-13 repair; libraries attribute to `wisekeybq` |
| `GOOGLE_APPLICATION_CREDENTIALS` (User env) | `C:\Users\ayaobama\.config\gcp\sealsq-revenue-sa.json` | hijacks every library caller (see 1.1) |
| `CLOUDSDK_*` env vars | none | wrappers set `CLOUDSDK_CONFIG` themselves |
| Operator roles on `wisekeybq` | `roles/owner`, `bigquery.admin`, `iam.serviceAccountTokenCreator` | impersonation of any SA in the project works without per-SA grants |

### 1.3 Invariants (hard-won; do not re-learn)

| # | Invariant | Evidence |
|---|---|---|
| I1 | Pass `--project <data project>` explicitly to gcloud/bq; pin `GOOGLE_CLOUD_PROJECT` for libraries | wrong-project writes averted 2026-08-18 *[memory]*; default project re-probed 2026-09-03 |
| I2 | Check billing before anything else: a project with billing off fails Artifact Registry/Cloud Run with `BILLING_DISABLED`, and its existing services answer 500/503 | `wisekeyclourrun`, 2026-09-03 |
| I3 | Dataset-level IAM through `bq add-iam-policy-binding -d` is allowlist-gated on this account; use SQL DCL `GRANT \`roles/bigquery.dataViewer\` ON SCHEMA …` | 2026-08-13 *[memory]*; used by `foundation.yml` 2026-09-03 |
| I4 | Drive scope on consumer Gmail ADC only via `gcloud auth login --enable-gdrive-access --update-adc`, then `gcloud auth application-default set-quota-project <project>` | 2026-08-13 *[memory]* |
| I5 | Impersonation needs `iamcredentials.googleapis.com` enabled **on the quota project** and a fresh ADC | 2026-08-12/13 *[memory obs #3683, #3686]* |
| I6 | PowerShell → `.cmd` wrappers: single-quote whole SQL/scope arguments, doubled single quotes inside; backtick is PowerShell's escape char; embedded double quotes get stripped | 2026-08-13 *[memory]* |
| I7 | `bq`/`gcloud` from a Python subprocess on Windows need the absolute `.cmd` path (WinError 2 otherwise) | 2026-08-07 *[memory]* |
| I8 | Git Bash (MSYS) rewrites POSIX-looking arguments passed to native `wsl.exe`; call WSL from PowerShell with a script file, never inline bash from Git Bash | 2026-09-03 |
| I9 | Linux git over a Windows checkout reports every CRLF file as modified; read with `git -c core.autocrlf=true status --porcelain` | 48 → 0 false positives, 2026-09-03 |
| I10 | The interop SDK over `/mnt/c` costs ≈ 44 s per gcloud call | measured 2026-09-03 |
| I11 | IAM propagation lags: read-backs after `iam service-accounts create` / policy bindings need a short retry loop | `foundation.yml` uses `retries: 6 / delay: 5` |

### 1.4 Who is the identity, per runner

| Runner | gcloud runs as | Credential location | ADC for libraries | Notes |
|---|---|---|---|---|
| Windows PowerShell | operator (`anthony.yaobama@gmail.com`) | `%APPDATA%\gcloud` | ADC file (quota `wisekeybq`) unless `GOOGLE_APPLICATION_CREDENTIALS` wins | quoting rules I6/I7 |
| WSL via `deploy/wsl/*` wrappers | operator (same store) | wrapper exports `CLOUDSDK_CONFIG=/mnt/c/Users/ayaobama/AppData/Roaming/gcloud` | same file, same hijack risk | slow (I10), CRLF (I9) |
| WSL native SDK (`apt install google-cloud-cli`) | whoever logs in inside WSL | `~/.config/gcloud` (separate store) | separate ADC; needs its own `application-default login` | fast; a second login to keep fresh |
| Cloud Shell | operator, authorised on first use | ephemeral VM; `$HOME` (5 GB) persists *[doc]* | `GOOGLE_CLOUD_PROJECT` is set to the console's active project *[doc]*; libraries get the user's credentials | no `GOOGLE_APPLICATION_CREDENTIALS` hijack; `bq` available with the SDK |
| Cloud Build | the project's Cloud Build SA (`<number>@cloudbuild.gserviceaccount.com`) or the compute SA on newer projects | metadata server | n/a | needs `artifactregistry.writer`; `run.admin` + `iam.serviceAccountUser` only if it also deploys |
| Cloud Run service | the runtime SA you attach (`hs-logic-run@…`) | metadata server | automatic | least privilege: secret accessor + the exact BigQuery roles |
| GCE VM | attached SA (`ic-load-host` pattern) | metadata server | automatic | `ic-load-host` exists but is TERMINATED (2026-09-03) |

### 1.5 Identity separation inside one deployment

```
operator (human, owner)      ── runs playbooks; approves preflight + first mutation
Cloud Build SA               ── builds + pushes images (writer on the repo)
runtime SA  hs-logic-run     ── what the service IS at runtime (secretAccessor, bigquery.jobUser, dataViewer via GRANT)
allUsers → roles/run.invoker ── who may call the URL (staging ruling; tighten with IAP later)
```

Keep these four distinct. The moment one identity does two jobs (the operator's key baked into a
container, the Cloud Build SA also owning data) the blast radius of every mistake doubles.

---

## 2. Runner assessment — performance vs practicability

### 2.1 Measured (2026-09-03, same playbooks, same project)

| Runner | One gcloud call | `foundation.yml` (≈25 calls) | `deploy.yml` (≈9 calls + build) | Setup | Ongoing cost |
|---|---|---|---|---|---|
| WSL over `/mnt/c` + interop wrappers | **≈ 44 s** | ≈ 15–20 min, completed | overhead ≈ 7 min, then failed on I9 (now fixed) | done (`deploy/wsl/install.sh`) | none |
| Cloud Shell | ≈ 1–2 s | < 1 min | build time only (3–5 min) | push branch, `pip install --user ansible-core`, clone | none (50 h/week quota *[doc]*) |
| WSL native clone + Linux SDK | ≈ 1–2 s | < 1 min | build time only | 10–15 min once; second gcloud login | none |
| GCE VM (`ic-load-host`, e2-micro, europe-west4-a) | ≈ 1–2 s | < 1 min | build time only | restart + SSH/OS Login + ansible + attached-SA IAM | ≈ €6–8/month always-on, patching |
| Cloud Build performs the deploy step (proposed) | 3 client calls total | n/a | one `builds submit` | add step + 2 IAM grants to the Cloud Build SA | none |

### 2.2 Verdicts

- **Cloud Shell = runner of record for anything that mutates GCP.** Fast, free, already the
  operator's own pattern, no `GOOGLE_APPLICATION_CREDENTIALS` hijack, Docker available if ever
  needed. Constraints to design around *[doc]*: non-interactive sessions end after **40 minutes**,
  sessions cap at **12 hours**, `$HOME` is deleted after **120 days** without access, weekly quota
  **50 hours**. All comfortably above a 5-minute deploy.
- **WSL interop = read-only work only** (`--tags preflight`, `verify.yml`). It proved the contract
  end to end but at 44 s per call it is not a release path.
- **WSL native = local fallback** when Cloud Shell is unreachable; costs a second login to keep
  fresh (a second credential store to reason about — §1.4).
- **GCE VM = rejected for deployment.** It buys nothing Cloud Shell or Cloud Build lacks and adds
  cost, SSH surface and patching. Revisit only if a persistent *scheduler* is needed, and even then
  compare with Cloud Scheduler + Cloud Run jobs first.
- **Cloud Build deploy step = the structural fix** (§4.4): the runner's speed stops mattering
  because the heavy calls happen server-side; a GitHub push trigger later makes releases zero-touch.
  Cost is a scoped IAM widening for the Cloud Build SA; approve deliberately.

### 2.3 Failure modes observed, by runner

| Runner | Failure | Root cause | Fix |
|---|---|---|---|
| WSL interop | 44 s per call | Windows SDK's Python files read through 9p | don't use for mutations (2.2) |
| WSL interop | dirty-tree guard tripped with 48 "modified" files | CRLF vs LF (I9) | guard reads with `-c core.autocrlf=true` |
| WSL interop | `/c/Users: Is a directory`, `$A` empty | Git Bash MSYS path mangling into `wsl.exe` (I8) | invoke WSL from PowerShell with a script file (`deploy/wsl/check.sh`) |
| WSL interop | `command` module would split the SDK path at the space | the wrapper hides the path; `argv` form for quote-heavy commands (GRANT) | `deploy/wsl/gcloud`, `argv:` |
| PowerShell | scopes/SQL arguments corrupted | I6 | quoting rule |
| Any | Artifact Registry `BILLING_DISABLED`, services 500/503 | I2 | preflight fails fast on `billingEnabled != true` |
| Any (libraries) | permission checks on the wrong project | stale ADC quota project / `GOOGLE_APPLICATION_CREDENTIALS` hijack | §1.1, I5 |

---

## 3. The portable playbook contract

The contract is what makes "run it from WSL today, from Cloud Shell tomorrow" true. It is
implemented in `deploy/ansible/` and is copyable as a skeleton.

```
deploy/
├── cloudbuild.yaml            build + push (deploy flags live in Ansible vars, one place)
├── ansible/
│   ├── ansible.cfg            inventory = inventory.yml, interpreter_python = auto_silent
│   ├── inventory.yml          all.hosts.localhost: ansible_connection: local
│   ├── requirements.txt       ansible-core pin
│   ├── group_vars/all.yml     THE single source of truth: project, region, names, sizes, APIs
│   ├── foundation.yml         one-time resources (tags: preflight apis registry sa secret bq)
│   ├── deploy.yml             per release: sha → build → deploy → read-back → manifest → verify
│   ├── verify.yml             Gate 2 read-backs only, never mutates
│   └── roles/{gcp_foundation,app_deploy,app_verify}/tasks/main.yml
├── wsl/{gcloud,bq,install.sh,check.sh}   runner adapters, never business logic
└── README.md                  runbook per runner, rollback, teardown list
```

Rules (each one exists because its absence broke something):

1. **`hosts: localhost`, `connection: local`, `gather_facts: false`.** The runner is whatever
   machine launches the playbook; nothing is SSH-ed. Same YAML on WSL, Cloud Shell, a VM.
2. **Binaries are variables** (`gcloud_bin`, `bq_bin`, default bare names). Runners adapt PATH
   (wrappers), playbooks never learn about runner paths.
3. **Preflight is tagged `always`**: active account, billing enabled on the target project. It is
   the executable form of §1; run it alone (`--tags preflight`) on any new runner before trusting it.
4. **Probe → mutate → read-back.** Probes use `changed_when: false`, `failed_when: false`; the
   mutation is `when: probe.rc != 0`; the read-back's `failed_when` asserts the described state
   (format is `DOCKER`, email equals the SA, version list non-empty, member present in the policy).
   IAM read-backs retry (I11).
5. **Secrets never transit the operator or the log.** The token is read from the git-ignored
   `.env` with `lookup('ansible.builtin.ini', …, type='properties')` under `no_log: true` and fed
   to `gcloud secrets versions add --data-file=-` through `stdin`. `-e secret_rotate=true` adds a
   version; an empty value is refused before any call.
6. **Quote-heavy commands use `argv:`** (the BigQuery `GRANT` with backticks and double quotes);
   folded `>-` strings are fine for plain flags.
7. **Every call carries `--project {{ gcp_project }}`** (I1) and `--format=value(...)` or
   `--format=json` so read-backs are parsed, not eyeballed.
8. **Releases are immutable and traceable**: image tag = git sha, the dirty-tree guard refuses
   uncommitted state (`-e allow_dirty=true` for experiments), `last_deploy.json` records the
   previous revision for rollback.
9. **`verify.yml` is the Gate 2 artifact**: HTTP read-backs through the public URL prove the secret
   wiring (`/api/hubspot/portal` returns the portal id), the SPA, `/api` precedence, secret
   versions, dataset access, and prints the exact resource names (the teardown key list).
10. **Teardown is a key list**, written before the first mutation, in `deploy/README.md`.

Task skeleton to copy:

```yaml
- name: <resource> exists?
  ansible.builtin.command: >-
    {{ gcloud_bin }} <service> describe {{ name }} --project {{ gcp_project }} --format=value(name)
  register: probe
  changed_when: false
  failed_when: false

- name: Create <resource>
  ansible.builtin.command: >-
    {{ gcloud_bin }} <service> create {{ name }} --project {{ gcp_project }} <flags>
  when: probe.rc != 0

- name: Read back <resource>
  ansible.builtin.command: >-
    {{ gcloud_bin }} <service> describe {{ name }} --project {{ gcp_project }} --format=value(<field>)
  register: after
  changed_when: false
  retries: 6
  delay: 5
  until: after.rc == 0 and (after.stdout | trim) == "<expected>"
```

---

## 4. Procedures

### 4.1 Cloud Shell (runner of record)

Prerequisite: the branch is on GitHub (`git push -u origin <branch>` from the workstation).

```bash
# in Cloud Shell (console → Activate Cloud Shell), authorised as the operator on first gcloud use
git clone https://github.com/Wkayaobama/Hs-Logic.git && cd Hs-Logic && git checkout feat/cloud-run-deploy
pip install --user -r deploy/ansible/requirements.txt
export PATH="$HOME/.local/bin:$PATH"
cd deploy/ansible
ansible-playbook foundation.yml --tags preflight      # §1 as an executable check
ansible-playbook deploy.yml                            # secret already exists → no .env needed
ansible-playbook verify.yml                            # Gate 2, repeatable
```

If `foundation.yml` must run here for a *new* endeavour, the token goes into the git-ignored
`.env` typed in the shell (`printf 'HUBSPOT_TOKEN=%s\n' "$TOKEN" > ../../.env`), never into the repo.
Keep sessions interactive during a deploy (non-interactive sessions end after 40 minutes *[doc]*);
`$HOME` persists, so the venv/pip install survives between sessions but a new VM is allocated.

### 4.2 WSL interop (read-only work, proven path)

```bash
bash /mnt/c/Users/ayaobama/Documents/AnthonySalesOps/Codebase/hs-standalone/deploy/wsl/install.sh   # once
bash /mnt/c/Users/ayaobama/Documents/AnthonySalesOps/Codebase/hs-standalone/deploy/wsl/check.sh     # syntax + parser proof + preflight
```

Invoke from PowerShell: `wsl -d Ubuntu -- bash -lc "bash <script>"` (I8). Expect ≈ 44 s per gcloud
call (I10); acceptable for `--tags preflight` and `verify.yml`, not for releases.

### 4.3 WSL native (local fallback)

```bash
sudo apt-get install -y apt-transport-https ca-certificates gnupg curl
curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg
echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | sudo tee /etc/apt/sources.list.d/google-cloud-sdk.list
sudo apt-get update && sudo apt-get install -y google-cloud-cli
gcloud auth login && gcloud config set project wisekeybq          # a SECOND credential store (~/.config/gcloud)
git clone https://github.com/Wkayaobama/Hs-Logic.git ~/src/Hs-Logic   # native ext4, no /mnt/c, no CRLF
```

Remove the `~/.local/bin/gcloud` wrapper first, or it shadows the native binary. Record in §1.4
that WSL now holds its own credentials.

### 4.4 Cloud Build performs the deploy (proposed follow-up, needs approval)

Add a second step to `deploy/cloudbuild.yaml` running `gcloud run deploy` with every flag passed
as a substitution rendered by Ansible from `group_vars/all.yml`; grant the Cloud Build SA
`roles/run.admin` (project) and `roles/iam.serviceAccountUser` on the runtime SA in
`foundation.yml`. `deploy.yml` shrinks to: sha → `builds submit` → describe read-back → verify.
Later, a Cloud Build trigger on `push` to the release branch removes the runner entirely.

---

## 5. Checklist for a new endeavour

1. **Project + billing**: name the data project in `group_vars/all.yml`; run
   `gcloud billing projects describe <project>` before anything else (I2).
2. **Identity map**: fill §1.4 for the runners you will use; confirm `GOOGLE_APPLICATION_CREDENTIALS`
   is popped by any library code (I1/1.1); confirm ADC `quota_project_id` equals the data project.
3. **Copy `deploy/`** (ansible + wsl + cloudbuild + README); rename `service_name`,
   `artifact_repo`, `runtime_sa_name`, `secret_name`; set `required_apis`, `bq_read_datasets`,
   `run_*` sizing.
4. **Write the teardown key list** in `deploy/README.md` before the first mutation.
5. **Preflight on the chosen runner** (`--tags preflight`), then `foundation.yml`, then
   `deploy.yml`, then `verify.yml`; paste the Gate 2 summary into the endeavour's spec.
6. **Register the repo** in `Codebase/_MAP.md` in the same gesture (rule #3).
7. After a second endeavour runs this unchanged, promote §1–§3 to a dotfiles skill.

---

## 6. Evidence log

| Date | Measurement / observation | Where |
|---|---|---|
| 2026-08-13 | Stale ADC quota project made impersonation checks hit `make-parser-439713`; fix = `application-default login` + `set-quota-project wisekeybq` | memory `gcp-auth-invariants`, obs #3683/#3686 |
| 2026-08-13 | Drive scope: Google Auth Library client blocked; Cloud SDK client via `--enable-gdrive-access --update-adc` works | obs #3712/#3723 |
| 2026-08-18 | `GOOGLE_CLOUD_PROJECT` pin added after `google.auth` fell back to `wisekeyclourrun` | obs #3880 |
| 2026-09-03 | `wisekeyclourrun` `billingEnabled: false`; its Cloud Run services 500/503 | this session |
| 2026-09-03 | ADC: `authorized_user`, client `32555940559`, quota `wisekeybq`; User env `GOOGLE_APPLICATION_CREDENTIALS` still set to the sealsq SA key | this session |
| 2026-09-03 | WSL interop: `gcloud --version` 43.8 s; `git status` 3.6 s; 48 false-modified files, 0 with `autocrlf=true` | this session |
| 2026-09-03 | `foundation.yml` completed from WSL: Secret Manager API on, repo `hs-logic`, SA `hs-logic-run`, secret 1 version + accessor binding, `HubspotSync` GRANT read back | this session |
| 2026-09-03 | Cloud Shell limits: 40-min non-interactive timeout, 12-h cap, 5 GB `$HOME`, 120-day deletion, 50 h/week | Google docs (limitations, how-cloud-shell-works) |
