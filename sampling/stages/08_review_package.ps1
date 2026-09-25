#Requires -Version 7.0
<#
.SYNOPSIS  Stage 08 — Assemble per-entity review package Excel workbooks
.NOTES     Mode: parallel
           Idempotent: yes  (sentinel file guards re-execution)
           On failure: throws
           Sheets: <object>_Delta, <object>_Structural, <object>_Format, <object>_Fuzzy, Cardinality, Profile, Accuracy, Efficiency, Summary
           Without the ImportExcel module the same sheets are written as CSV files under sheets/ (with a warning)
#>
param(
    [string]   $RunId    = $env:PIPELINE_RUN_ID,
    [string[]] $BusinessEntities = @(),
    [string[]] $Entities = @('contacts', 'companies', 'deals', 'tickets', 'notes'),
    [switch]   $Force,
    [switch]   $WhatIf
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot '..' 'config.ps1')
. (Join-Path $PSScriptRoot '..' 'queries.ps1')
Get-ChildItem (Join-Path $PSScriptRoot '..' 'functions' '*.ps1') |
    ForEach-Object { . $_.FullName }

$Entities = @(Expand-List $Entities)
$BusinessEntities = @(Expand-List $BusinessEntities)
$stageTag     = ($MyInvocation.MyCommand.Name -replace '\.ps1$','')
Assert-RunId $RunId
$sentinelPath = Get-SentinelPath -StageTag $stageTag -RunId $RunId

if (-not $Force -and (Test-Path $sentinelPath)) {
    Write-Host "[$stageTag] Already complete for run $RunId — skipping"
    exit 0
}

if ($WhatIf) {
    Write-Host "[DRYRUN] Would execute: $stageTag -RunId $RunId -BusinessEntities $($BusinessEntities -join ',')"
    exit 0
}

# ── stage work ────────────────────────────────────────────────────────────────

if (-not $BusinessEntities.Count) {
    $BusinessEntities = @(Get-ChildItem -Path (Get-StagePath -Kind 'flatten') -Directory | Where-Object { Test-Path (Join-Path $_.FullName $RunId 'manifest.json') } | ForEach-Object { $_.Name })
}
if (-not $BusinessEntities.Count) { throw "[$stageTag] nothing flattened for run $RunId" }
$sw = [System.Diagnostics.Stopwatch]::StartNew()

$BusinessEntities | ForEach-Object -Parallel {
    Set-StrictMode -Version Latest
    $ErrorActionPreference = "Stop"
    $entity = $_.ToUpperInvariant()
    $rid    = $using:RunId
    $root   = $using:PSScriptRoot
    $objects = $using:Entities

    . (Join-Path $root '..' 'config.ps1')
    . (Join-Path $root '..' 'queries.ps1')
    Get-ChildItem (Join-Path $root '..' 'functions' '*.ps1') |
        ForEach-Object { . $_.FullName }

    $outDir = Get-StagePath -Kind 'review' -Entity $entity -RunId $rid -Create
    $out = Join-Path $outDir 'review_package.xlsx'
    if (-not $using:Force -and (Test-Path $out)) {
        Write-Host "[$entity] Review package already exists for run $rid — skipping"
        return
    }
    Remove-Item $out -ErrorAction SilentlyContinue

    $excel = $false
    try { Import-Module ImportExcel -ErrorAction Stop; $excel = $true } catch { $excel = $false }

    # Input paths
    $flattenDir    = Get-StagePath -Kind 'flatten' -Entity $entity -RunId $rid
    $validationDir = Get-StagePath -Kind 'validation' -Entity $entity -RunId $rid
    $runDir        = Get-StagePath -Kind 'validation' -Entity '_run' -RunId $rid
    $deltaPath      = Join-Path (Get-StagePath -Kind 'delta' -Entity $entity -RunId $rid) 'delta_records.csv'
    $structuralPath = Join-Path $validationDir 'structural.json'
    $formatPath     = Join-Path $validationDir 'format.json'
    $fuzzyPath      = Join-Path $validationDir 'fuzzy.json'

    $sheets = [ordered]@{}
    $deltaData  = if (Test-Path $deltaPath)      { @(Import-Csv -Path $deltaPath -Encoding UTF8) } else { @() }
    $structData = if (Test-Path $structuralPath) { @(Read-JsonFile -Path $structuralPath) } else { @() }
    $formatData = if (Test-Path $formatPath)     { @(Read-JsonFile -Path $formatPath) } else { @() }
    $fuzzyData  = if (Test-Path $fuzzyPath)      { @(Read-JsonFile -Path $fuzzyPath) } else { @() }
    $summaryRows = @()
    foreach ($object in $objects) {
        if (-not (Test-Path (Join-Path $flattenDir "$object.csv"))) { continue }
        $rows = @(Import-Csv -Path (Join-Path $flattenDir "$object.csv") -Encoding UTF8).Count
        $d = @($deltaData  | Where-Object { $_.object -eq $object })
        $s = @($structData | Where-Object { $_.object -eq $object })
        $f = @($formatData | Where-Object { $_.object -eq $object })
        $z = @($fuzzyData  | Where-Object { $_.object -eq $object })
        if ($d.Count) { $sheets["${object}_Delta"] = $d }
        if ($s.Count) { $sheets["${object}_Structural"] = $s }
        if ($f.Count) { $sheets["${object}_Format"] = $f }
        if ($z.Count) { $sheets["${object}_Fuzzy"] = $z }
        $summaryRows += [pscustomobject]@{
            entity = $entity; object = $object; rows = $rows; delta = $d.Count; structural = $s.Count
            structural_errors = @($s | Where-Object { $_.severity -eq 'error' }).Count; format_issues = $f.Count; fuzzy_candidates = $z.Count
        }
    }
    foreach ($pair in @(@('Cardinality', (Join-Path $validationDir 'cardinality_summary.csv')), @('Profile', (Join-Path $validationDir 'profile.csv')), @('Accuracy', (Join-Path $runDir 'accuracy.csv')), @('Efficiency', (Join-Path $runDir 'efficiency.csv')))) {
        if (Test-Path $pair[1]) { $data = @(Import-Csv -Path $pair[1] -Encoding UTF8); if ($data.Count) { $sheets[$pair[0]] = $data } }
    }
    $sheets['Summary'] = @($summaryRows)

    if ($excel) {
        $autoSize = [bool]$IsWindows   # column auto-fit needs System.Drawing, which ImportExcel only has on Windows
        foreach ($name in $sheets.Keys) {
            @($sheets[$name]) | Export-Excel -Path $out -WorksheetName $name -AutoSize:$autoSize -FreezeTopRow -Append
        }
        Write-Host "[$entity] Review package written -> $out ($($sheets.Count) sheets)"
    } else {
        $csvDir = Join-Path $outDir 'sheets'
        New-Item -ItemType Directory -Force -Path $csvDir | Out-Null
        foreach ($name in $sheets.Keys) {
            @($sheets[$name]) | Export-Csv -Path (Join-Path $csvDir "$name.csv") -NoTypeInformation -Encoding UTF8
        }
        $index = "# Review package $entity / $rid`n`nImportExcel is not installed on this machine, so every sheet was written as a CSV under sheets/:`n`n" + (($sheets.Keys | ForEach-Object { "- $_ ($(@($sheets[$_]).Count) rows)" }) -join "`n") + "`n"
        $index | Set-Content -Path (Join-Path $outDir 'review_package.md') -Encoding UTF8
        Write-Warning "[$entity] ImportExcel not available: sheets written as CSV under $csvDir (Install-Module ImportExcel to get review_package.xlsx)"
    }
} -ThrottleLimit 4

$sw.Stop()
Write-StageLog -Stage $stageTag -RunId $RunId -Data @{ entities = @($BusinessEntities); duration_ms = $sw.ElapsedMilliseconds } | Out-Null

# ── end stage work ────────────────────────────────────────────────────────────

"done" | Set-Content $sentinelPath -Encoding UTF8
Write-Host "[$stageTag] Complete — run $RunId ($($BusinessEntities -join ', ')), $($sw.ElapsedMilliseconds) ms"
