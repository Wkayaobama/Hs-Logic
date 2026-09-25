#Requires -Version 7.0
<#
.SYNOPSIS  Stage 06 — Delta against the previous run of the same business entity (new / changed / removed by id + updated_at)
.NOTES     Mode: sequential
           Idempotent: yes  (sentinel file guards re-execution)
           On failure: throws
           First run: every record is 'new'
#>
param(
    [string]   $RunId    = $env:PIPELINE_RUN_ID,
    [string[]] $Entities = @('contacts', 'companies', 'deals', 'tickets', 'notes'),
    [switch]   $Force,
    [switch]   $WhatIf,
    [Alias('Database')]
    [string]   $BusinessEntity = 'SEALSQ'
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
$BusinessEntity = $BusinessEntity.ToUpperInvariant()
$sentinelPath   = Get-SentinelPath -StageTag $stageTag -RunId $RunId -Entity $BusinessEntity

if (-not $Force -and (Test-Path $sentinelPath)) {
    Write-Host "[$stageTag] Already complete for run $RunId ($BusinessEntity) — skipping"
    exit 0
}

if ($WhatIf) {
    Write-Host "[DRYRUN] Would execute: $stageTag -RunId $RunId -BusinessEntity $BusinessEntity"
    exit 0
}

# ── stage work ────────────────────────────────────────────────────────────────

$entityRoot = Get-StagePath -Kind 'flatten' -Entity $BusinessEntity
$inDir  = Join-Path $entityRoot $RunId
$outDir = Get-StagePath -Kind 'delta' -Entity $BusinessEntity -RunId $RunId -Create
if (-not (Test-Path $inDir)) { throw "[$stageTag] no flattened extract for $BusinessEntity run $RunId — run 02_flatten first" }
$sw = [System.Diagnostics.Stopwatch]::StartNew()

# previous run = the most recently flattened other RunId of this entity
$previous = $null
$candidates = Get-ChildItem -Path $entityRoot -Directory | Where-Object { $_.Name -ne $RunId -and (Test-Path (Join-Path $_.FullName 'manifest.json')) }
if ($candidates) {
    $previous = ($candidates | Sort-Object { (Read-JsonFile -Path (Join-Path $_.FullName 'manifest.json')).flattened_at } -Descending | Select-Object -First 1).Name
}
$delta = [System.Collections.Generic.List[object]]::new()
$summary = @()
foreach ($object in $Entities) {
    $csv = Join-Path $inDir "$object.csv"
    if (-not (Test-Path $csv)) { continue }
    $current = @{}
    foreach ($row in @(Import-Csv -Path $csv -Encoding UTF8)) { $current[[string](Get-RowValue $row 'id')] = [string](Get-RowValue $row 'updated_at') }
    $before = @{}
    if ($previous) {
        $prevCsv = Join-Path $entityRoot $previous "$object.csv"
        if (Test-Path $prevCsv) {
            foreach ($row in @(Import-Csv -Path $prevCsv -Encoding UTF8)) { $before[[string](Get-RowValue $row 'id')] = [string](Get-RowValue $row 'updated_at') }
        }
    }
    $new = 0; $changed = 0; $removed = 0
    foreach ($id in $current.Keys) {
        if (-not $before.ContainsKey($id)) { $new++; $delta.Add([pscustomobject]@{ entity = $BusinessEntity; object = $object; id = $id; change = 'new'; previous_updated_at = ''; current_updated_at = $current[$id] }) }
        elseif ($before[$id] -ne $current[$id]) { $changed++; $delta.Add([pscustomobject]@{ entity = $BusinessEntity; object = $object; id = $id; change = 'changed'; previous_updated_at = $before[$id]; current_updated_at = $current[$id] }) }
    }
    foreach ($id in $before.Keys) {
        if (-not $current.ContainsKey($id)) { $removed++; $delta.Add([pscustomobject]@{ entity = $BusinessEntity; object = $object; id = $id; change = 'removed'; previous_updated_at = $before[$id]; current_updated_at = '' }) }
    }
    $summary += [pscustomobject]@{ object = $object; current = $current.Count; previous = $before.Count; new = $new; changed = $changed; removed = $removed }
    Write-Host "[$stageTag] [$BusinessEntity/$object] current=$($current.Count) previous=$($before.Count) new=$new changed=$changed removed=$removed"
}

$sw.Stop()
$deltaFile = Join-Path $outDir 'delta_records.csv'
if ($delta.Count -gt 0) {
    $delta | Export-Csv -Path $deltaFile -NoTypeInformation -Encoding UTF8
} else {
    '"entity","object","id","change","previous_updated_at","current_updated_at"' | Set-Content -Path $deltaFile -Encoding UTF8
}
Write-JsonFile -Object ([ordered]@{ run_id = $RunId; business_entity = $BusinessEntity; previous_run_id = $previous; objects = $summary; records = $delta.Count; duration_ms = $sw.ElapsedMilliseconds }) -Path (Join-Path $outDir 'delta_summary.json')
Write-StageLog -Stage $stageTag -RunId $RunId -Entity $BusinessEntity -Data @{ previous_run_id = $previous; delta_records = $delta.Count; duration_ms = $sw.ElapsedMilliseconds } | Out-Null

# ── end stage work ────────────────────────────────────────────────────────────

"done" | Set-Content $sentinelPath -Encoding UTF8
Write-Host "[$stageTag] Complete — run $RunId ($BusinessEntity): $($delta.Count) delta records vs $(if ($previous) { $previous } else { 'no previous run' }), $($sw.ElapsedMilliseconds) ms"
