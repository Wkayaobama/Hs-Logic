# Sampling probe (`sampling/`)

The first and gating deliverable of the multi-entity plan: prove that the Hs-Logic FastAPI backend
(the execution layer, local today, Cloud Run later) is **executable**, **accurate** and **efficient**
against portal 9201667 before any rule or constraint is built on top of it. Modelled on the
IC'Alps-Dashboard sampling pipeline: staged PowerShell 7 scripts, one `RunId` per run, sentinel
files that make every stage idempotent, `-Force` / `-WhatIf`, per-entity review packages in Excel.

## Requirements

- PowerShell 7 (`pwsh`). Optional: `Install-Module ImportExcel -Scope CurrentUser` for `.xlsx`
  review packages (without it stage 08 writes the same sheets as CSV files and warns).
- A running backend (`backend/`): `uvicorn app.main:app --port 8000` with a **read-only**
  `HUBSPOT_TOKEN`, or, without a token, the fake HubSpot:
  ```bash
  cd backend && . .venv/bin/activate
  python -m tools.fake_hubspot --port 8100 &
  HUBSPOT_BASE_URL=http://127.0.0.1:8100 HUBSPOT_TOKEN=fake uvicorn app.main:app --port 8000
  ```
- `model/` holds the committed, machine-readable derivations of the HubSpot-Ruler assets
  (`tools/derive_*.py` regenerate them; stdlib Python only):
  - `cardinality.csv` from `ontology_workbook_full.xlsx` (13 edges, association type ids, expected rates)
  - `reference.json` known answers captured 2026-09-14 (75 counts: 6 fixed, 7 pipelines, 36 products, 26 utilisation)
  - `format_rules.csv` enum sets from `hubspot_crm_schema.json` plus generic email/domain/number/date rules

## Run

```powershell
pwsh sampling/run.ps1 -WhatIf                                   # dry run: every stage prints what it would do
pwsh sampling/run.ps1 -RunId probe1 -Server http://127.0.0.1:8000
pwsh sampling/run.ps1 -RunId probe1                              # second run: every stage skips (sentinels)
pwsh sampling/run.ps1 -RunId probe1 -Force                       # re-run everything
pwsh sampling/run.ps1 -RunId quick -BusinessEntities SEALSQ,ICALPS -Entities deals,companies -SampleSize 200
pwsh sampling/run.ps1 -RunId live1 -Target Both                  # adds the direct HubSpot REST cross-check ($env:HUBSPOT_TOKEN)
```

Stages can also run one at a time, e.g. `pwsh sampling/stages/01_extract.ps1 -RunId probe1 -BusinessEntity MIRAEX -Entities contacts`.

Parameter mapping from the original pipeline: `-Entities` = HubSpot object types
(`contacts, companies, deals, tickets, notes`); `-BusinessEntity` (alias `-Database`) =
`WISEKEY | WISESAT | SEALSQ | SEALCOIN | QUANTUM_AI | ICALPS | MIRAEX | WECAN` (the seed in
`backend/app/data/entities.yaml`); `-Server` = backend base URL; `-Target Backend | HubSpot | Both`.

## Stages and outputs (all per `RunId`, gitignored)

| Stage | Does | Output |
|---|---|---|
| `00_preflight` | health, portal counts, entities loaded, one sample row per object flattened | `preflight/<RunId>/sample_<object>.csv`, `preflight.json` |
| `01_extract` | pages `GET /api/export/{object}?entity=` with association labels, parallel per object | `extract/<entity>/<RunId>/<object>.jsonl`, `manifest.json` |
| `02_flatten` | JSONL to CSV with deterministic columns + edge list | `flatten/<entity>/<RunId>/<object>.csv`, `associations.csv` |
| `03_profile` | fill rate, distinct, enum/format conformity | `validation/<entity>/<RunId>/profile.csv`, `format.json` |
| `04_cascade_validation` | cardinality rules and referential checks in dependency order, cascading to engagements | `structural.json`, `cardinality_summary.csv` |
| `05_fuzzy` | duplicate candidates (exact normalised keys, token Jaccard on names) | `fuzzy.json` |
| `06_delta` | new / changed / removed vs the previous run | `delta/<entity>/<RunId>/delta_records.csv` |
| `07_accuracy_efficiency` | 75 reference questions through backend, extract and (optionally) HubSpot; timings and HubSpot call counts | `validation/_run/<RunId>/accuracy.json|csv`, `efficiency.json|csv` |
| `08_review_package` | one workbook per business entity | `review/<entity>/<RunId>/review_package.xlsx` (or `sheets/*.csv`) |
| gate | criteria below | `review/<RunId>/GATE.md` |

Every backend response carries `X-Logic-Request-Id`, `X-Logic-Duration-Ms`, `X-Logic-HS-Requests`,
`X-Logic-HS-429`, `X-Logic-HS-Retries`; the stages record them in `logs/<RunId>/*.json`.

## Gate criteria

- **Executability**: all stages complete for every business entity and leave a sentinel; a re-run
  without `-Force` skips every stage; no extract hit the 10,000-record search cap; entities without
  markers (today WECAN everywhere, MIRAEX outside contacts) are reported as `no_marker`, not hidden.
- **Accuracy**: backend, flattened extract and (with `-Target Both`) direct HubSpot REST return the
  *same* count for every one of the 75 reference questions (exact agreement is the accuracy proof);
  the counts stay within tolerance of the 2026-09-14 capture (fixed group: all deals 1,960; SEALSQ
  scope 1,820; `QVault TPM IOT` 32; `QVault TPM` 82; `wisekey___seal` Seal 1,798 / Wisekey 22, with
  10 records or 1 % of slack; products 5 records or 2 %; utilisation 10 records or 2 %), and the
  drift per reference is written to `accuracy.csv`: the portal keeps growing, so drift against a
  dated capture is documented, never hidden. On 2026-09-25 the live portal already had 1,964 deals
  and 1,824 in scope while both QVault counts were unchanged.
- **Efficiency**: zero unrecovered 429s (backoff honours `Retry-After`); the largest entity extract
  finishes under 15 minutes locally; records/s per object recorded as the Cloud Run baseline.
- **Structural feasibility**: cardinality violation rates per edge stay under the workbook-derived
  thresholds (deal-company 1 %, deal-contact 20 %, contact-company 7 %, company IcAlps primary
  contact 16 %, others 5 %) on every edge with at least 100 source records (smaller scopes are
  reported, not gated); anything above is listed as "needs remediation before it can be a rule".

Phases B-E of the plan (blueprint, rules engine, operations, ruler agents) start only on PASS.
