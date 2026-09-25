#Requires -Version 7.0
<#
.SYNOPSIS  Stage 01 — Extract every object of one business entity through the backend export endpoint (paged, with associations)
.NOTES     Mode: parallel per object (ThrottleLimit from config)
           Idempotent: yes  (sentinel file guards re-execution)
           On failure: throws
           -Entities are HubSpot object types; -BusinessEntity (alias -Database) is the business scope
#>
param(
    [string]   $RunId    = $env:PIPELINE_RUN_ID,
    [string[]] $Entities = @('contacts', 'companies', 'deals', 'tickets', 'notes'),
    [switch]   $Force,
    [switch]   $WhatIf,
    [string]   $Server   = $env:LOGIC_API_BASE,
    [Alias('Database')]
    [string]   $BusinessEntity = 'SEALSQ',
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
    Write-Host "[DRYRUN] Would execute: $stageTag -RunId $RunId -BusinessEntity $BusinessEntity -Entities $($Entities -join ',') -Server $Server"
    exit 0
}

# ── stage work ────────────────────────────────────────────────────────────────

$BusinessEntity = Assert-BusinessEntity -Server $Server -BusinessEntity $BusinessEntity
$outDir = Get-StagePath -Kind 'extract' -Entity $BusinessEntity -RunId $RunId -Create
$sw = [System.Diagnostics.Stopwatch]::StartNew()

# every property a reference question (model/reference.json) filters on is part of the extract,
# so stage 07 can answer the same questions from the flattened CSVs
$referenceProperties = @{}
foreach ($ref in @((Import-References).references)) {
    $props = @(@($ref.filters) | ForEach-Object { [string](Get-RowValue $_ 'prop') } | Where-Object { $_ })
    if (-not $referenceProperties.ContainsKey([string]$ref.object)) { $referenceProperties[[string]$ref.object] = [System.Collections.Generic.HashSet[string]]::new() }
    foreach ($p in $props) { [void]$referenceProperties[[string]$ref.object].Add($p) }
}

$Entities | ForEach-Object -Parallel {
    Set-StrictMode -Version Latest
    $ErrorActionPreference = "Stop"
    $object = $_
    $rid    = $using:RunId
    $be     = $using:BusinessEntity
    $server = $using:Server
    $sample = $using:SampleSize
    $outDir = $using:outDir
    $root   = $using:PSScriptRoot
    $tag    = $using:stageTag
    $refProps = $using:referenceProperties

    . (Join-Path $root '..' 'config.ps1')
    . (Join-Path $root '..' 'queries.ps1')
    Get-ChildItem (Join-Path $root '..' 'functions' '*.ps1') |
        ForEach-Object { . $_.FullName }

    $outFile = Join-Path $outDir "$object.jsonl"
    $after = $null; $pages = 0; $records = 0; $capped = $false; $noMarker = $false; $mode = ''
    $hsRequests = 0; $hs429 = 0; $hsRetries = 0; $backendMs = 0
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    Remove-Item $outFile -ErrorAction SilentlyContinue
    $writer = [System.IO.StreamWriter]::new($outFile, $false, [System.Text.UTF8Encoding]::new($false))
    try {
        do {
            $limit = $Config.PageLimit
            if ($sample -gt 0) { $limit = [Math]::Max(1, [Math]::Min($limit, $sample - $records)) }
            $query = @{ entity = $be; limit = $limit }
            if ($refProps.ContainsKey($object) -and $refProps[$object].Count) { $query.properties = (@($refProps[$object]) -join ',') }
            if ($after) { $query.after = $after }
            $r = Invoke-LogicApi -Server $server -Path "/api/export/$object" -Query $query
            $body = $r.Body
            $mode = [string](Get-RowValue $body 'scope_mode')
            $noMarker = [bool](Get-RowValue $body 'no_marker')
            if ([bool](Get-RowValue $body 'capped')) { $capped = $true }
            foreach ($rec in @(Get-RowValue $body 'results')) {
                $writer.WriteLine(($rec | ConvertTo-Json -Compress -Depth 20))
                $records++
            }
            $pages++
            $hsRequests += $r.HsRequests; $hs429 += $r.Hs429; $hsRetries += $r.HsRetries; $backendMs += $r.BackendMs
            $after = [string](Get-RowValue $body 'next_after')
        } while ($after -and ($sample -le 0 -or $records -lt $sample))
    } finally { $writer.Dispose() }
    $timer.Stop()
    $seconds = [Math]::Max($timer.Elapsed.TotalSeconds, 0.001)
    $manifest = [ordered]@{
        object = $object; business_entity = $be; run_id = $rid; scope_mode = $mode; no_marker = $noMarker
        records = $records; pages = $pages; capped = $capped; sample_size = $sample
        hs_requests = $hsRequests; hs_429 = $hs429; hs_retries = $hsRetries; backend_ms = $backendMs
        duration_ms = $timer.ElapsedMilliseconds; records_per_s = [Math]::Round($records / $seconds, 2)
        file = $outFile; extracted_at = (Get-UtcNow)
    }
    Write-JsonFile -Object $manifest -Path (Join-Path $outDir "$object.manifest.json")
    Write-Host "[$tag] [$be/$object] $records records in $pages pages, mode=$mode, capped=$capped, hs_requests=$hsRequests, 429=$hs429 ($($timer.ElapsedMilliseconds) ms)"
} -ThrottleLimit $Config.ThrottleLimit

$sw.Stop()
$objects = @()
foreach ($object in $Entities) {
    $objects += (Read-JsonFile -Path (Join-Path $outDir "$object.manifest.json"))
}
$manifest = [ordered]@{
    run_id = $RunId; business_entity = $BusinessEntity; server = $Server; sample_size = $SampleSize
    objects = $objects
    records = [int](Get-Sum $objects 'records')
    hs_requests = [int](Get-Sum $objects 'hs_requests')
    hs_429 = [int](Get-Sum $objects 'hs_429')
    hs_retries = [int](Get-Sum $objects 'hs_retries')
    capped = [bool](@($objects | Where-Object { $_.capped }).Count -gt 0)
    duration_ms = $sw.ElapsedMilliseconds; extracted_at = (Get-UtcNow)
}
Write-JsonFile -Object $manifest -Path (Join-Path $outDir 'manifest.json')
Write-StageLog -Stage $stageTag -RunId $RunId -Entity $BusinessEntity -Data @{ records = $manifest.records; hs_requests = $manifest.hs_requests; hs_429 = $manifest.hs_429; capped = $manifest.capped; duration_ms = $sw.ElapsedMilliseconds } | Out-Null

# ── end stage work ────────────────────────────────────────────────────────────

"done" | Set-Content $sentinelPath -Encoding UTF8
Write-Host "[$stageTag] Complete — run $RunId ($BusinessEntity): $($manifest.records) records, $($manifest.hs_requests) HubSpot calls, $($sw.ElapsedMilliseconds) ms"
