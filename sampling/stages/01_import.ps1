#Requires -Version 7.0
<#
.SYNOPSIS  Stage 01 (file surface) — Load an entity's ad-hoc sources (entities/<ENTITY>/sources/*.source.json in HubSpot-Ruler) into the probe surface through the backend's ad-hoc source module
.NOTES     Mode: sequential per source spec
           Idempotent: yes  (sentinel file guards re-execution)
           On failure: throws
           Writes the same extract/<entity>/<RunId>/<object>.jsonl + manifest contract as 01_extract, so stages 02-08 run unchanged.
           One spec per (entity, object) in V1: a second spec for the same object would overwrite the first extract.
#>
param(
    [string]   $RunId    = $env:PIPELINE_RUN_ID,
    [string[]] $Entities = @('contacts', 'companies', 'deals', 'tickets', 'notes'),
    [switch]   $Force,
    [switch]   $WhatIf,
    [string]   $Server   = $env:LOGIC_API_BASE,
    [Alias('Database')]
    [string]   $BusinessEntity = 'MIRAEX',
    [switch]   $IngestFake,
    [int]      $SampleSize = 0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot '..' 'config.ps1')
. (Join-Path $PSScriptRoot '..' 'queries.ps1')
Get-ChildItem (Join-Path $PSScriptRoot '..' 'functions' '*.ps1') |
    ForEach-Object { . $_.FullName }

$Entities = @(Expand-List $Entities)
$stageTag       = ($MyInvocation.MyCommand.Name -replace '\.ps1$','')
Assert-RunId $RunId
$Server         = Resolve-Server $Server
$BusinessEntity = $BusinessEntity.ToUpperInvariant()
$sentinelPath   = Get-SentinelPath -StageTag $stageTag -RunId $RunId -Entity $BusinessEntity

if (-not $Force -and (Test-Path $sentinelPath)) {
    Write-Host "[$stageTag] Already complete for run $RunId ($BusinessEntity) — skipping"
    exit 0
}

if ($WhatIf) {
    Write-Host "[DRYRUN] Would execute: $stageTag -RunId $RunId -BusinessEntity $BusinessEntity -Server $Server -IngestFake:$IngestFake"
    exit 0
}

# ── stage work ────────────────────────────────────────────────────────────────

$BusinessEntity = Assert-BusinessEntity -Server $Server -BusinessEntity $BusinessEntity
$outDir = Get-StagePath -Kind 'extract' -Entity $BusinessEntity -RunId $RunId -Create
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$apiLog = New-ApiCallLog

$specs = @((Invoke-LogicApi -Server $Server -Path '/api/sources/specs' -Query @{ entity = $BusinessEntity }).Body.specs)
if (-not $specs.Count) { throw "[$stageTag] no source spec for $BusinessEntity under entities/$BusinessEntity/sources (run: python -m app.sources.cli match $BusinessEntity <object> <file> --save)" }

$reports = @()
foreach ($spec in $specs) {
    if ((Get-RowValue $spec 'error')) { throw "[$stageTag] invalid spec $($spec.path): $($spec.error)" }
    if ($spec.object -notin $Entities) { Write-Host "[$stageTag] [$BusinessEntity] spec $($spec.name) targets $($spec.object), not requested — skipping"; continue }
    $body = [ordered]@{ spec = $spec.path; run_id = $RunId; ingest_fake = [bool]$IngestFake; match_records = $true }
    if ($SampleSize -gt 0) { $body.limit = $SampleSize }
    $r = Invoke-LogicApi -Server $Server -Path '/api/sources/load' -Method POST -Body $body -TimeoutSec 1800
    Add-ApiCall $apiLog $r "load_$($spec.name)"
    $rep = $r.Body
    $reports += $rep
    $counts = Get-RowValue $rep 'counts'
    $countText = (@($counts.PSObject.Properties | ForEach-Object { "$($_.Name)=$($_.Value)" }) -join ', ')
    $matched = Get-RowValue $rep 'matched_existing'
    $matchedText = (@($matched.PSObject.Properties | ForEach-Object { "$($_.Name)=$($_.Value)" }) -join ', ')
    Write-Host "[$stageTag] [$BusinessEntity/$($spec.name)] $($rep.rows) rows -> $countText; matched existing: $(if ($matchedText) { $matchedText } else { 'none' }); skipped(no key)=$($rep.skipped_no_key) duplicates=$($rep.duplicate_keys) ($($r.DurationMs) ms)"
    $issues = Get-RowValue $rep 'issues'
    foreach ($p in @($issues.PSObject.Properties)) { Write-Host "[$stageTag]     issue: $($p.Name) x $($p.Value)" }
}

# merge per-object manifests exactly like 01_extract, marking the surface
$objects = @()
foreach ($f in @(Get-ChildItem -Path $outDir -Filter '*.manifest.json')) { $objects += (Read-JsonFile -Path $f.FullName) }
$sw.Stop()
$manifest = [ordered]@{
    run_id = $RunId; business_entity = $BusinessEntity; server = $Server; sample_size = $SampleSize; source = 'file'
    sources = @($specs | ForEach-Object { $_.name })
    objects = $objects
    records = [int](Get-Sum $objects 'records')
    hs_requests = [int](Get-Sum $reports 'hs_requests')
    hs_429 = 0; hs_retries = 0; capped = $false
    duration_ms = $sw.ElapsedMilliseconds; extracted_at = (Get-UtcNow)
}
Write-JsonFile -Object $manifest -Path (Join-Path $outDir 'manifest.json')
Write-StageLog -Stage $stageTag -RunId $RunId -Entity $BusinessEntity -Data @{ records = $manifest.records; sources = $manifest.sources; duration_ms = $sw.ElapsedMilliseconds } -ApiLog $apiLog | Out-Null

# ── end stage work ────────────────────────────────────────────────────────────

"done" | Set-Content $sentinelPath -Encoding UTF8
Write-Host "[$stageTag] Complete — run $RunId ($BusinessEntity): $($manifest.records) records from $($specs.Count) source(s), $($sw.ElapsedMilliseconds) ms"
