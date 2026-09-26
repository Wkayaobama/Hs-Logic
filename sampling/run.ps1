#Requires -Version 7.0
<#
.SYNOPSIS  Run the sampling probe end to end: 00 preflight, 01-06 per business entity, 07 accuracy/efficiency, 08 review packages, then the gate.
.EXAMPLE   pwsh sampling/run.ps1 -WhatIf
.EXAMPLE   pwsh sampling/run.ps1 -RunId probe1 -Server http://127.0.0.1:8000
.EXAMPLE   pwsh sampling/run.ps1 -RunId probe2 -BusinessEntities SEALSQ,ICALPS -Entities deals,companies -SampleSize 200 -Target Both
.NOTES     -Entities are HubSpot object types; -BusinessEntities default to every entity the backend has loaded (/api/meta/entities).
           Stages are idempotent per RunId (sentinels under state/); -Force re-runs them.
           Exit code 1 when the gate fails (unless -SkipGate).
#>
param(
    [string]   $RunId,
    [string]   $Server = $env:LOGIC_API_BASE,
    [ValidateSet('Backend', 'HubSpot', 'Both')]
    [string]   $Target = 'Backend',
    [string[]] $BusinessEntities = @(),
    [string[]] $Entities = @('contacts', 'companies', 'deals', 'tickets', 'notes'),
    [string[]] $FileEntities = @(),
    [switch]   $IngestFake,
    [int]      $SampleSize = 0,
    [switch]   $Force,
    [switch]   $WhatIf,
    [switch]   $SkipGate
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot 'config.ps1')
. (Join-Path $PSScriptRoot 'queries.ps1')
Get-ChildItem (Join-Path $PSScriptRoot 'functions' '*.ps1') |
    ForEach-Object { . $_.FullName }

$Entities = @(Expand-List $Entities)
$BusinessEntities = @(Expand-List $BusinessEntities)
$FileEntities = @((Expand-List $FileEntities) | ForEach-Object { $_.ToUpperInvariant() })

if ([string]::IsNullOrWhiteSpace($RunId)) {
    $RunId = if ($env:PIPELINE_RUN_ID) { $env:PIPELINE_RUN_ID } else { Get-Date -Format 'yyyyMMdd-HHmmss' }
}
$env:PIPELINE_RUN_ID = $RunId
$Server = Resolve-Server $Server
$stagesDir = Join-Path $PSScriptRoot 'stages'
$timeline = [System.Collections.Generic.List[object]]::new()
$total = [System.Diagnostics.Stopwatch]::StartNew()

function Invoke-Stage {
    param([Parameter(Mandatory)][string]$Name, [hashtable]$Params = @{})
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    Write-Host ""
    Write-Host "== $Name $(if ($Params.ContainsKey('BusinessEntity')) { '(' + $Params.BusinessEntity + ')' }) ==" -ForegroundColor Cyan
    try {
        & (Join-Path $stagesDir "$Name.ps1") @Params
        $sw.Stop()
        $timeline.Add([pscustomobject]@{ stage = $Name; entity = $(if ($Params.ContainsKey('BusinessEntity')) { $Params.BusinessEntity } else { '' }); ok = $true; duration_ms = $sw.ElapsedMilliseconds })
    } catch {
        $sw.Stop()
        $timeline.Add([pscustomobject]@{ stage = $Name; entity = $(if ($Params.ContainsKey('BusinessEntity')) { $Params.BusinessEntity } else { '' }); ok = $false; duration_ms = $sw.ElapsedMilliseconds })
        throw
    }
}

Write-Host "Sampling probe run $RunId -> $Server (target $Target)$(if ($WhatIf) { ' [DRYRUN]' })" -ForegroundColor Green

try {
    Invoke-Stage '00_preflight' @{ RunId = $RunId; Entities = $Entities; Server = $Server; Force = $Force; WhatIf = $WhatIf }

    if (-not $BusinessEntities.Count) {
        $BusinessEntities = if ($WhatIf) { @('SEALSQ', 'ICALPS', 'MIRAEX', 'WECAN', 'WISEKEY') } else { Get-BusinessEntityIds -Server $Server }
    }
    $BusinessEntities = @($BusinessEntities | ForEach-Object { $_.ToUpperInvariant() })
    foreach ($fe in $FileEntities) { if ($fe -notin $BusinessEntities) { $BusinessEntities += $fe } }
    Write-Host "Business entities: $($BusinessEntities -join ', ')"

    foreach ($be in $BusinessEntities) {
        if ($be -in $FileEntities) {
            Invoke-Stage '01_import'         @{ RunId = $RunId; Entities = $Entities; Server = $Server; BusinessEntity = $be; SampleSize = $SampleSize; IngestFake = $IngestFake; Force = $Force; WhatIf = $WhatIf }
        } else {
            Invoke-Stage '01_extract'        @{ RunId = $RunId; Entities = $Entities; Server = $Server; BusinessEntity = $be; SampleSize = $SampleSize; Force = $Force; WhatIf = $WhatIf }
        }
        Invoke-Stage '02_flatten'            @{ RunId = $RunId; Entities = $Entities; BusinessEntity = $be; Force = $Force; WhatIf = $WhatIf }
        Invoke-Stage '03_profile'            @{ RunId = $RunId; Entities = $Entities; BusinessEntity = $be; Force = $Force; WhatIf = $WhatIf }
        Invoke-Stage '04_cascade_validation' @{ RunId = $RunId; Entities = $Entities; BusinessEntity = $be; Force = $Force; WhatIf = $WhatIf }
        Invoke-Stage '05_fuzzy'              @{ RunId = $RunId; Entities = $Entities; BusinessEntity = $be; Force = $Force; WhatIf = $WhatIf }
        Invoke-Stage '06_delta'              @{ RunId = $RunId; Entities = $Entities; BusinessEntity = $be; Force = $Force; WhatIf = $WhatIf }
    }

    Invoke-Stage '07_accuracy_efficiency' @{ RunId = $RunId; Server = $Server; Target = $Target; Force = $Force; WhatIf = $WhatIf }
    Invoke-Stage '08_review_package'      @{ RunId = $RunId; BusinessEntities = $BusinessEntities; Entities = $Entities; Force = $Force; WhatIf = $WhatIf }
} catch {
    Write-Host ""
    Write-Host "Run $RunId stopped: $($_.Exception.Message)" -ForegroundColor Red
    foreach ($t in $timeline) { Write-Host ("  {0,-24} {1,-10} {2,-5} {3,8} ms" -f $t.stage, $t.entity, $(if ($t.ok) { 'ok' } else { 'FAIL' }), $t.duration_ms) }
    exit 1
}

$total.Stop()
Write-Host ""
Write-Host "Timeline (run $RunId, $([Math]::Round($total.Elapsed.TotalSeconds, 1)) s):" -ForegroundColor Cyan
foreach ($t in $timeline) { Write-Host ("  {0,-24} {1,-10} {2,-5} {3,8} ms" -f $t.stage, $t.entity, $(if ($t.ok) { 'ok' } else { 'FAIL' }), $t.duration_ms) }
Write-JsonFile -Object ([ordered]@{ run_id = $RunId; server = $Server; target = $Target; entities = $BusinessEntities; objects = $Entities; sample_size = $SampleSize; duration_ms = $total.ElapsedMilliseconds; timeline = @($timeline); at = (Get-UtcNow) }) -Path (Join-Path (Get-StagePath -Kind 'logs' -RunId $RunId -Create) 'run.json')

if ($WhatIf) { exit 0 }

$gate = Test-Gate -RunId $RunId -BusinessEntities $BusinessEntities -Server $Server
Write-Host ""
Write-Host "Gate: $(if ($gate.pass) { 'PASS' } else { 'FAIL' }) -> $($gate.path)" -ForegroundColor $(if ($gate.pass) { 'Green' } else { 'Red' })
foreach ($c in $gate.criteria) { Write-Host ("  {0,-4} [{1}] {2} — {3}" -f $(if ($c.pass) { 'PASS' } else { 'FAIL' }), $c.area, $c.criterion, $c.detail) }
if (-not $gate.pass -and -not $SkipGate) { exit 1 }
exit 0
