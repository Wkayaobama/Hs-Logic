#Requires -Version 7.0
<#
.SYNOPSIS  Stage 05 — Fuzzy duplicate candidates inside one business entity (companies by domain/name, contacts by email/name)
.NOTES     Mode: sequential
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
$candidates = [System.Collections.Generic.List[object]]::new()

foreach ($object in @('companies', 'contacts')) {
    if ($object -notin $Entities) { continue }
    $csv = Join-Path $inDir "$object.csv"
    if (-not (Test-Path $csv)) { continue }
    $rows = @(Import-Csv -Path $csv -Encoding UTF8)
    $found = @(Measure-Fuzzy -Rows $rows -Object $object -Threshold $Config.FuzzyThreshold)
    foreach ($c in $found) { $candidates.Add($c) }
    $exact = @($found | Where-Object { $_.kind -like 'exact_*' }).Count
    Write-Host "[$stageTag] [$BusinessEntity/$object] $($rows.Count) rows -> $($found.Count) duplicate candidates ($exact exact, $($found.Count - $exact) fuzzy)"
}

$sw.Stop()
Write-JsonFile -Object @($candidates) -Path (Join-Path $outDir 'fuzzy.json')
Write-StageLog -Stage $stageTag -RunId $RunId -Entity $BusinessEntity -Data @{ candidates = $candidates.Count; duration_ms = $sw.ElapsedMilliseconds } | Out-Null

# ── end stage work ────────────────────────────────────────────────────────────

"done" | Set-Content $sentinelPath -Encoding UTF8
Write-Host "[$stageTag] Complete — run $RunId ($BusinessEntity): $($candidates.Count) candidates, $($sw.ElapsedMilliseconds) ms"
