#Requires -Version 7.0
<#
.SYNOPSIS  Stage 02 — Flatten JSONL extracts into CSV (deterministic columns) plus the association edge list
.NOTES     Mode: sequential per object
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

$inDir  = Get-StagePath -Kind 'extract' -Entity $BusinessEntity -RunId $RunId
$outDir = Get-StagePath -Kind 'flatten' -Entity $BusinessEntity -RunId $RunId -Create
if (-not (Test-Path $inDir)) { throw "[$stageTag] no extract for $BusinessEntity run $RunId — run 01_extract first" }
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$edges = [System.Collections.Generic.List[object]]::new()
$summary = @()

foreach ($object in $Entities) {
    $src = Join-Path $inDir "$object.jsonl"
    if (-not (Test-Path $src)) { Write-Host "[$stageTag] [$BusinessEntity/$object] no extract file — skipping"; continue }
    $records = Read-JsonLines -Path $src
    $columns = Get-FlatColumns @($records)
    $rows = [System.Collections.Generic.List[object]]::new()
    foreach ($rec in $records) {
        $rows.Add((ConvertTo-FlatRow -Record $rec -Columns $columns))
        foreach ($e in (Get-AssociationEdges -Record $rec -Object $object)) { $edges.Add($e) }
    }
    $csv = Join-Path $outDir "$object.csv"
    if ($rows.Count -gt 0) {
        $rows | Export-Csv -Path $csv -NoTypeInformation -Encoding UTF8
    } else {
        (($columns | ForEach-Object { '"' + $_ + '"' }) -join ',') | Set-Content -Path $csv -Encoding UTF8
    }
    $summary += [pscustomobject]@{ object = $object; rows = $rows.Count; columns = $columns.Count; file = $csv }
    Write-Host "[$stageTag] [$BusinessEntity/$object] $($rows.Count) rows x $($columns.Count) columns -> $csv"
}

$edgeFile = Join-Path $outDir 'associations.csv'
if ($edges.Count -gt 0) {
    $edges | Export-Csv -Path $edgeFile -NoTypeInformation -Encoding UTF8
} else {
    '"from_object","from_id","to_object","to_id","type_id","label","category"' | Set-Content -Path $edgeFile -Encoding UTF8
}
$sw.Stop()
$manifest = [ordered]@{ run_id = $RunId; business_entity = $BusinessEntity; objects = $summary; edges = $edges.Count; duration_ms = $sw.ElapsedMilliseconds; flattened_at = (Get-UtcNow) }
Write-JsonFile -Object $manifest -Path (Join-Path $outDir 'manifest.json')
Write-StageLog -Stage $stageTag -RunId $RunId -Entity $BusinessEntity -Data @{ rows = [int](Get-Sum $summary 'rows'); edges = $edges.Count; duration_ms = $sw.ElapsedMilliseconds } | Out-Null

# ── end stage work ────────────────────────────────────────────────────────────

"done" | Set-Content $sentinelPath -Encoding UTF8
Write-Host "[$stageTag] Complete — run $RunId ($BusinessEntity): $($edges.Count) edges, $($sw.ElapsedMilliseconds) ms"
