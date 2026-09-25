#Requires -Version 7.0
<#
.SYNOPSIS  Stage 04 — Cascade validation: cardinality rules and referential constraints from the spreadsheet-derived model (model/cardinality.csv)
.NOTES     Mode: sequential (dependency order companies -> contacts -> deals -> tickets -> engagements)
           Idempotent: yes  (sentinel file guards re-execution)
           On failure: throws
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

$inDir  = Get-StagePath -Kind 'flatten' -Entity $BusinessEntity -RunId $RunId
$outDir = Get-StagePath -Kind 'validation' -Entity $BusinessEntity -RunId $RunId -Create
if (-not (Test-Path $inDir)) { throw "[$stageTag] no flattened extract for $BusinessEntity run $RunId — run 02_flatten first" }
$sw = [System.Diagnostics.Stopwatch]::StartNew()

$order = @('companies', 'contacts', 'deals', 'tickets') + $Config.Engagements
$idSets = @{}
foreach ($object in ($order | Where-Object { $_ -in $Entities })) {
    $csv = Join-Path $inDir "$object.csv"
    if (-not (Test-Path $csv)) { continue }
    $set = [System.Collections.Generic.HashSet[string]]::new()
    foreach ($row in @(Import-Csv -Path $csv -Encoding UTF8)) { [void]$set.Add([string](Get-RowValue $row 'id')) }
    $idSets[$object] = $set
    Write-Host "[$stageTag] [$BusinessEntity/$object] $($set.Count) ids in scope"
}
$edges = @(Import-Csv -Path (Join-Path $inDir 'associations.csv') -Encoding UTF8)
$rules = @(Import-CardinalityRules)
$result = Test-Cardinality -Rules $rules -Edges $edges -IdSets $idSets -Entity $BusinessEntity

$sw.Stop()
Write-JsonFile -Object @($result.Violations) -Path (Join-Path $outDir 'structural.json')
$summaryRows = @($result.Summary)
if ($summaryRows.Count -gt 0) {
    $summaryRows | Export-Csv -Path (Join-Path $outDir 'cardinality_summary.csv') -NoTypeInformation -Encoding UTF8
} else {
    '"entity","edge","source","target","cardinality","sources","with_link","observed_rate","expected_rate","missing","multiple_primary","too_many","cross_scope","violation_rate","target_extracted"' | Set-Content -Path (Join-Path $outDir 'cardinality_summary.csv') -Encoding UTF8
}
foreach ($s in $summaryRows) {
    Write-Host ("[$stageTag] {0,-32} {1,6} sources, linked {2,6} ({3,7:P1} vs expected {4}), missing {5}, multi-primary {6}, too-many {7}, cross-scope {8}" -f $s.edge, $s.sources, $s.with_link, $s.observed_rate, $s.expected_rate, $s.missing, $s.multiple_primary, $s.too_many, $s.cross_scope)
}
$errors = @($result.Violations | Where-Object { $_.severity -eq 'error' }).Count
Write-StageLog -Stage $stageTag -RunId $RunId -Entity $BusinessEntity -Data @{ edges = $edges.Count; rules = $rules.Count; violations = $result.Violations.Count; errors = $errors; duration_ms = $sw.ElapsedMilliseconds } | Out-Null

# ── end stage work ────────────────────────────────────────────────────────────

"done" | Set-Content $sentinelPath -Encoding UTF8
Write-Host "[$stageTag] Complete — run $RunId ($BusinessEntity): $($result.Violations.Count) findings ($errors errors), $($sw.ElapsedMilliseconds) ms"
