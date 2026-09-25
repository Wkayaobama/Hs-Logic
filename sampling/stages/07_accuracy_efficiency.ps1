#Requires -Version 7.0
<#
.SYNOPSIS  Stage 07 — Accuracy (reference questions answered through the backend, the extract and optionally HubSpot directly) and efficiency (timings, HubSpot calls, 429s)
.NOTES     Mode: sequential, run-level (once per RunId, after every business entity has been extracted)
           Idempotent: yes  (sentinel file guards re-execution)
           On failure: throws
           -Target Backend | HubSpot | Both  (HubSpot needs $env:HUBSPOT_TOKEN and is the cross-check only)
#>
param(
    [string]   $RunId    = $env:PIPELINE_RUN_ID,
    [switch]   $Force,
    [switch]   $WhatIf,
    [string]   $Server   = $env:LOGIC_API_BASE,
    [string]   $Target   = $env:SAMPLING_TARGET
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot '..' 'config.ps1')
. (Join-Path $PSScriptRoot '..' 'queries.ps1')
Get-ChildItem (Join-Path $PSScriptRoot '..' 'functions' '*.ps1') |
    ForEach-Object { . $_.FullName }

$stageTag     = ($MyInvocation.MyCommand.Name -replace '\.ps1$','')
Assert-RunId $RunId
$Server       = Resolve-Server $Server
if ([string]::IsNullOrWhiteSpace($Target)) { $Target = 'Backend' }
if ($Target -notin @('Backend', 'HubSpot', 'Both')) { throw "-Target must be Backend, HubSpot or Both (got '$Target')" }
$sentinelPath = Get-SentinelPath -StageTag $stageTag -RunId $RunId

if (-not $Force -and (Test-Path $sentinelPath)) {
    Write-Host "[$stageTag] Already complete for run $RunId — skipping"
    exit 0
}

if ($WhatIf) {
    Write-Host "[DRYRUN] Would execute: $stageTag -RunId $RunId -Server $Server -Target $Target"
    exit 0
}

# ── stage work ────────────────────────────────────────────────────────────────

$outDir = Get-StagePath -Kind 'validation' -Entity '_run' -RunId $RunId -Create
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$apiLog = New-ApiCallLog
$references = @((Import-References).references)
$flattenRoot = Get-StagePath -Kind 'flatten'
$extractCache = @{}

function Get-ExtractRows {
    <# union of every business entity's flattened rows for one object (deduplicated by id) #>
    param([string]$Object)
    if ($extractCache.ContainsKey($Object)) { return ,$extractCache[$Object] }
    $seen = [System.Collections.Generic.HashSet[string]]::new()
    $rows = [System.Collections.Generic.List[object]]::new()
    foreach ($dir in @(Get-ChildItem -Path $flattenRoot -Directory -ErrorAction SilentlyContinue)) {
        $csv = Join-Path $dir.FullName $RunId "$Object.csv"
        if (-not (Test-Path $csv)) { continue }
        foreach ($row in @(Import-Csv -Path $csv -Encoding UTF8)) {
            if ($seen.Add([string](Get-RowValue $row 'id'))) { $rows.Add($row) }
        }
    }
    $extractCache[$Object] = $rows
    return ,$rows
}

$results = [System.Collections.Generic.List[object]]::new()
foreach ($ref in $references) {
    $object = [string]$ref.object
    $filters = @($ref.filters)
    $backend = $null; $extract = $null; $direct = $null; $errors = @()
    if ($Target -in @('Backend', 'Both')) {
        try {
            $r = Invoke-LogicApi -Server $Server -Path "/api/export/$object/count" -Query @{ filter = @(ConvertTo-CountFilterParams $filters) }
            Add-ApiCall $apiLog $r $ref.id
            $backend = [int](Get-RowValue $r.Body 'total')
        } catch { $errors += "backend: $($_.Exception.Message)" }
    }
    $rows = Get-ExtractRows -Object $object
    if ($rows.Count -gt 0) {
        $columns = @($rows[0].PSObject.Properties | ForEach-Object { $_.Name })
        $missingProps = @($filters | ForEach-Object { [string](Get-RowValue $_ 'prop') } | Where-Object { $_ -and $_ -notin $columns })
        if ($missingProps.Count) { $errors += "extract: property not extracted: $($missingProps -join ', ')" }
        else { $extract = Measure-ExtractCount -Rows $rows -Filters $filters }
    }
    if ($Target -in @('HubSpot', 'Both')) {
        try { $direct = Invoke-HubSpotCount -Object $object -Filters $filters } catch { $errors += "direct: $($_.Exception.Message)" }
    }
    $primary = if ($null -ne $backend) { $backend } elseif ($null -ne $direct) { $direct } else { $extract }
    $agree = $true
    foreach ($v in @($backend, $extract, $direct)) { if ($null -ne $v -and $null -ne $primary -and $v -ne $primary) { $agree = $false } }
    $cmp = if ($null -ne $primary) { Compare-Reference -Reference $ref -Actual $primary } else { $null }
    $results.Add([pscustomobject]@{
        id = $ref.id; group = $ref.group; label = $ref.label; object = $object; expected = [int]$ref.expected
        backend = $backend; extract = $extract; direct = $direct
        abs_diff = $(if ($cmp) { $cmp.abs_diff } else { $null }); drift_pct = $(if ($cmp) { $cmp.drift_pct } else { $null })
        tolerance_abs = [int](0 + (Get-RowValue $ref 'tolerance_abs')); tolerance_pct = [double](0 + (Get-RowValue $ref 'tolerance_pct'))
        within_tolerance = $(if ($cmp) { [bool]$cmp.pass } else { $false })
        pass = $(if ($cmp) { [bool]($cmp.pass -and $agree) } else { $false }); paths_agree = $agree
        errors = ($errors -join ' | '); source = $ref.source; captured = $ref.captured
    })
}
$failed = @($results | Where-Object { -not $_.pass })
foreach ($x in $results) {
    $mark = if ($x.pass) { 'OK  ' } else { 'FAIL' }
    Write-Host ("[$stageTag] {0} {1,-44} expected {2,6}  backend {3,6}  extract {4,6}  direct {5,6}  drift {6}%" -f $mark, $x.label.Substring(0, [Math]::Min(44, $x.label.Length)), $x.expected, $(if ($null -ne $x.backend) { $x.backend } else { '-' }), $(if ($null -ne $x.extract) { $x.extract } else { '-' }), $(if ($null -ne $x.direct) { $x.direct } else { '-' }), $x.drift_pct)
}

# efficiency: extract manifests + stage logs
$manifests = @()
foreach ($dir in @(Get-ChildItem -Path (Get-StagePath -Kind 'extract') -Directory -ErrorAction SilentlyContinue)) {
    $m = Join-Path $dir.FullName $RunId 'manifest.json'
    if (Test-Path $m) { $manifests += (Read-JsonFile -Path $m) }
}
$perEntity = @($manifests | ForEach-Object {
    $seconds = [Math]::Max([double]$_.duration_ms / 1000.0, 0.001)
    [pscustomobject]@{ entity = $_.business_entity; records = $_.records; hs_requests = $_.hs_requests; hs_429 = $_.hs_429; hs_retries = $_.hs_retries; capped = $_.capped; duration_ms = $_.duration_ms; records_per_s = [Math]::Round($_.records / $seconds, 2) }
})
$perObject = @{}
foreach ($m in $manifests) {
    foreach ($o in @($m.objects)) {
        $name = [string]$o.object
        if (-not $perObject.ContainsKey($name)) { $perObject[$name] = [ordered]@{ object = $name; records = 0; pages = 0; hs_requests = 0; hs_429 = 0; duration_ms = 0; capped = $false } }
        $perObject[$name].records += [int]$o.records; $perObject[$name].pages += [int]$o.pages; $perObject[$name].hs_requests += [int]$o.hs_requests
        $perObject[$name].hs_429 += [int]$o.hs_429; $perObject[$name].duration_ms += [int]$o.duration_ms
        if ([bool]$o.capped) { $perObject[$name].capped = $true }
    }
}
$perObjectRows = @($perObject.Keys | Sort-Object | ForEach-Object { $x = $perObject[$_]; $x.records_per_s = [Math]::Round($x.records / [Math]::Max($x.duration_ms / 1000.0, 0.001), 2); [pscustomobject]$x })
$stageLogs = @()
foreach ($f in @(Get-ChildItem -Path (Get-StagePath -Kind 'logs' -RunId $RunId) -Filter '*.json' -ErrorAction SilentlyContinue)) {
    $l = Read-JsonFile -Path $f.FullName
    $stageName = [string](Get-RowValue $l 'stage')
    if (-not $stageName) { continue }   # run.json and other non-stage files
    $stageLogs += [pscustomobject]@{ stage = $stageName; entity = [string](Get-RowValue $l 'business_entity'); duration_ms = (Get-RowValue $l 'duration_ms') }
}
$sw.Stop()
$totals = [ordered]@{
    records = [int](Get-Sum $perEntity 'records')
    hs_requests = [int](Get-Sum $perEntity 'hs_requests')
    hs_429 = [int](Get-Sum $perEntity 'hs_429')
    hs_retries = [int](Get-Sum $perEntity 'hs_retries')
    unrecovered_429 = 0
    extract_duration_ms = [int](Get-Sum $perEntity 'duration_ms')
    max_entity_extract_ms = [int](Get-Max $perEntity 'duration_ms')
    capped_any = [bool](@($perEntity | Where-Object { $_.capped }).Count -gt 0)
    accuracy_duration_ms = $sw.ElapsedMilliseconds
}
$accuracy = [ordered]@{
    run_id = $RunId; server = $Server; target = $Target; at = (Get-UtcNow)
    summary = [ordered]@{ references = $results.Count; passed = ($results.Count - $failed.Count); failed = $failed.Count; drifted = @($results | Where-Object { -not $_.within_tolerance }).Count; paths_disagree = @($results | Where-Object { -not $_.paths_agree }).Count }
    results = @($results)
}
$efficiency = [ordered]@{
    run_id = $RunId; server = $Server; target = $Target; at = (Get-UtcNow)
    entities = @($perEntity | ForEach-Object { $_.entity })
    totals = $totals; per_entity = $perEntity; per_object = $perObjectRows; stages = $stageLogs
    accuracy_api = (Measure-ApiCalls $apiLog)
}
Write-JsonFile -Object $accuracy -Path (Join-Path $outDir 'accuracy.json')
$results | Export-Csv -Path (Join-Path $outDir 'accuracy.csv') -NoTypeInformation -Encoding UTF8
Write-JsonFile -Object $efficiency -Path (Join-Path $outDir 'efficiency.json')
if ($perEntity.Count) { $perEntity | Export-Csv -Path (Join-Path $outDir 'efficiency.csv') -NoTypeInformation -Encoding UTF8 }
Write-StageLog -Stage $stageTag -RunId $RunId -Data @{ references = $results.Count; failed = $failed.Count; duration_ms = $sw.ElapsedMilliseconds } -ApiLog $apiLog | Out-Null

# ── end stage work ────────────────────────────────────────────────────────────

"done" | Set-Content $sentinelPath -Encoding UTF8
Write-Host "[$stageTag] Complete — run $RunId : $($results.Count - $failed.Count)/$($results.Count) references pass, $($totals.hs_requests) HubSpot calls during extraction, $($totals.hs_429) x 429"
