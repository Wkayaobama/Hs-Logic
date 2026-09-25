#Requires -Version 7.0
<#
.SYNOPSIS  Stage 00 — Preflight: prove the execution layer answers (health, portal, entities, one sample row per object)
.NOTES     Mode: sequential
           Idempotent: yes  (sentinel file guards re-execution)
           On failure: throws (after writing preflight/<RunId>/preflight.json)
#>
param(
    [string]   $RunId    = $env:PIPELINE_RUN_ID,
    [string[]] $Entities = @('contacts', 'companies', 'deals', 'tickets', 'notes'),
    [switch]   $Force,
    [switch]   $WhatIf,
    [string]   $Server   = $env:LOGIC_API_BASE
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot '..' 'config.ps1')
. (Join-Path $PSScriptRoot '..' 'queries.ps1')
Get-ChildItem (Join-Path $PSScriptRoot '..' 'functions' '*.ps1') |
    ForEach-Object { . $_.FullName }

$Entities = @(Expand-List $Entities)
$stageTag     = ($MyInvocation.MyCommand.Name -replace '\.ps1$','')
Assert-RunId $RunId
$Server       = Resolve-Server $Server
$sentinelPath = Get-SentinelPath -StageTag $stageTag -RunId $RunId

if (-not $Force -and (Test-Path $sentinelPath)) {
    Write-Host "[$stageTag] Already complete for run $RunId — skipping"
    exit 0
}

if ($WhatIf) {
    Write-Host "[DRYRUN] Would execute: $stageTag -RunId $RunId -Server $Server"
    exit 0
}

# ── stage work ────────────────────────────────────────────────────────────────

$outDir = Get-StagePath -Kind 'preflight' -RunId $RunId -Create
$apiLog = New-ApiCallLog
$checks = [System.Collections.Generic.List[object]]::new()
$sw     = [System.Diagnostics.Stopwatch]::StartNew()
$portal = $null

function Add-Check {
    param([string]$Name, [bool]$Ok, [string]$Detail)
    $checks.Add([pscustomobject]@{ check = $Name; ok = $Ok; detail = $Detail })
    $mark = if ($Ok) { 'OK  ' } else { 'FAIL' }
    Write-Host "[$stageTag] $mark $Name — $Detail"
}

try {
    $r = Invoke-LogicApi -Server $Server -Path '/api/health'
    Add-ApiCall $apiLog $r 'health'
    Add-Check 'backend_health' ((Get-RowValue $r.Body 'status') -eq 'ok') "$Server/api/health -> $(Get-RowValue $r.Body 'status') ($($r.DurationMs) ms)"
} catch { Add-Check 'backend_health' $false $_.Exception.Message }

try {
    $r = Invoke-LogicApi -Server $Server -Path '/api/hubspot/portal'
    Add-ApiCall $apiLog $r 'portal'
    $portal = $r.Body
    $counts = Get-RowValue $portal 'counts'
    Add-Check 'portal' $true "portal $(Get-RowValue $portal 'portal_id') counts contacts=$(Get-RowValue $counts 'contacts') companies=$(Get-RowValue $counts 'companies') deals=$(Get-RowValue $counts 'deals') tickets=$(Get-RowValue $counts 'tickets') ($($r.DurationMs) ms, $($r.HsRequests) HubSpot calls)"
} catch { Add-Check 'portal' $false $_.Exception.Message }

try {
    $r = Invoke-LogicApi -Server $Server -Path '/api/meta/entities'
    Add-ApiCall $apiLog $r 'entities'
    $ids = @((Get-RowValue $r.Body 'entities') | ForEach-Object { [string](Get-RowValue $_ 'id') })
    Add-Check 'entities_loaded' ($ids.Count -gt 0) "$($ids.Count) entities loaded: $($ids -join ', ')"
} catch { Add-Check 'entities_loaded' $false $_.Exception.Message }

foreach ($q in $Queries.Preflight) {
    if ($q.Object -notin $Entities) { continue }
    $query = @{ sample = 1; properties = $q.Properties }
    if ($q.Entity) { $query.entity = $q.Entity }
    $name = "sample_$($q.Object)"
    try {
        $r = Invoke-LogicApi -Server $Server -Path "/api/export/$($q.Object)" -Query $query
        Add-ApiCall $apiLog $r $name
        $body = $r.Body
        $mode = [string](Get-RowValue $body 'scope_mode')
        $results = @(Get-RowValue $body 'results')
        if ($results.Count -eq 0) {
            Add-Check $name ($mode -eq 'no_marker') "no rows (scope_mode=$mode)"
            continue
        }
        $rec  = $results[0] | ConvertTo-Json -Depth 20 | ConvertFrom-Json -AsHashtable -Depth 20
        $cols = Get-FlatColumns @($rec)
        $row  = ConvertTo-FlatRow -Record $rec -Columns $cols
        $row | Export-Csv -Path (Join-Path $outDir "$name.csv") -NoTypeInformation -Encoding UTF8
        $missing = @($q.ExpectProperties | Where-Object { -not $rec['properties'].Contains($_) })
        $detail = "id=$($rec['id']) scope_mode=$mode columns=$($cols.Count) hs_requests=$($r.HsRequests) backend_ms=$($r.BackendMs)"
        if ($missing.Count) { $detail += " missing properties: $($missing -join ', ')" }
        Add-Check $name ($missing.Count -eq 0) $detail
    } catch { Add-Check $name $false $_.Exception.Message }
}

$sw.Stop()
$failed = @($checks | Where-Object { -not $_.ok })
$ok = ($failed.Count -eq 0)
$summary = [ordered]@{
    run_id = $RunId; server = $Server; ok = $ok; checks = @($checks); portal = $portal
    duration_ms = $sw.ElapsedMilliseconds; api = (Measure-ApiCalls $apiLog); at = (Get-UtcNow)
}
Write-JsonFile -Object $summary -Path (Join-Path $outDir 'preflight.json')
Write-StageLog -Stage $stageTag -RunId $RunId -Data @{ ok = $ok; checks = $checks.Count; failed = $failed.Count; duration_ms = $sw.ElapsedMilliseconds } -ApiLog $apiLog | Out-Null

if (-not $ok) {
    throw "[$stageTag] preflight failed: $(($failed | ForEach-Object { $_.check }) -join ', ') — see $(Join-Path $outDir 'preflight.json')"
}

# ── end stage work ────────────────────────────────────────────────────────────

"done" | Set-Content $sentinelPath -Encoding UTF8
Write-Host "[$stageTag] Complete — run $RunId ($($sw.ElapsedMilliseconds) ms)"
