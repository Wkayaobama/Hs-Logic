#Requires -Version 7.0
<#
.SYNOPSIS  Stage 03 — Profile each flattened object: fill rates, distinct counts, enum/format conformity (model/format_rules.csv)
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

$inDir  = Get-StagePath -Kind 'flatten' -Entity $BusinessEntity -RunId $RunId
$outDir = Get-StagePath -Kind 'validation' -Entity $BusinessEntity -RunId $RunId -Create
if (-not (Test-Path $inDir)) { throw "[$stageTag] no flattened extract for $BusinessEntity run $RunId — run 02_flatten first" }
$rules = @(Import-FormatRules)
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$profile = [System.Collections.Generic.List[object]]::new()
$issues  = [System.Collections.Generic.List[object]]::new()
$issueCounts = [ordered]@{}
$invariant = [System.Globalization.CultureInfo]::InvariantCulture

function Test-FormatValue {
    param([string]$RuleType, [string]$Spec, [string]$Value)
    switch ($RuleType) {
        'enum'  { return (@($Spec -split '\|') -contains $Value) }
        'regex' { return ($Value -match $Spec) }
        'type'  {
            switch ($Spec) {
                'number'   { $n = 0.0; return [double]::TryParse($Value, [System.Globalization.NumberStyles]::Float, $invariant, [ref]$n) }
                'bool'     { return ($Value -in @('true', 'false')) }
                'date'     { $d = [datetime]::MinValue; return ([datetime]::TryParse($Value, $invariant, [System.Globalization.DateTimeStyles]::AssumeUniversal, [ref]$d) -or ($Value -match '^\d{10,13}$')) }
                'datetime' { $d = [datetime]::MinValue; return ([datetime]::TryParse($Value, $invariant, [System.Globalization.DateTimeStyles]::AssumeUniversal, [ref]$d) -or ($Value -match '^\d{10,13}$')) }
                default    { return $true }
            }
        }
        default { return $true }
    }
}

foreach ($object in $Entities) {
    $csv = Join-Path $inDir "$object.csv"
    if (-not (Test-Path $csv)) { continue }
    $rows = @(Import-Csv -Path $csv -Encoding UTF8)
    $header = @((Get-Content -Path $csv -TotalCount 1) -replace '"', '' -split ',')
    $objectRules = @($rules | Where-Object { $_.object -eq $object })
    $objectIssues = 0
    foreach ($column in $header) {
        if ($column -in @('id', 'created_at', 'updated_at', 'archived') -or $column -like 'assoc_*') { continue }
        $filled = 0
        $distinct = [System.Collections.Generic.HashSet[string]]::new()
        $columnRules = @($objectRules | Where-Object { $_.property -eq $column })
        foreach ($row in $rows) {
            $v = [string](Get-RowValue $row $column)
            if ([string]::IsNullOrEmpty($v)) { continue }
            $filled++
            [void]$distinct.Add($v)
            foreach ($rule in $columnRules) {
                if (-not (Test-FormatValue -RuleType $rule.rule_type -Spec $rule.spec -Value $v)) {
                    $key = "$object/$column/$($rule.rule_type)"
                    $issueCounts[$key] = 1 + $(if ($issueCounts.Contains($key)) { $issueCounts[$key] } else { 0 })
                    $objectIssues++
                    if ($issues.Count -lt $Config.MaxIssuesPerObject * $Entities.Count) {
                        $issues.Add([pscustomobject]@{ entity = $BusinessEntity; object = $object; id = (Get-RowValue $row 'id'); property = $column; issue = $rule.rule_type; value = $v; spec = $rule.spec })
                    }
                }
            }
        }
        $profile.Add([pscustomobject]@{
            entity = $BusinessEntity; object = $object; property = $column; rows = $rows.Count; filled = $filled
            fill_rate = $(if ($rows.Count) { [Math]::Round($filled / $rows.Count, 4) } else { 0 })
            distinct = $distinct.Count; rules = $columnRules.Count
        })
    }
    Write-Host "[$stageTag] [$BusinessEntity/$object] $($rows.Count) rows, $($header.Count) columns, $objectIssues format issues"
}

$sw.Stop()
$profile | Export-Csv -Path (Join-Path $outDir 'profile.csv') -NoTypeInformation -Encoding UTF8
Write-JsonFile -Object @($issues) -Path (Join-Path $outDir 'format.json')
Write-JsonFile -Object ([ordered]@{ run_id = $RunId; business_entity = $BusinessEntity; issues = $issues.Count; by_rule = $issueCounts; duration_ms = $sw.ElapsedMilliseconds }) -Path (Join-Path $outDir 'format_summary.json')
Write-StageLog -Stage $stageTag -RunId $RunId -Entity $BusinessEntity -Data @{ properties = $profile.Count; issues = $issues.Count; duration_ms = $sw.ElapsedMilliseconds } | Out-Null

# ── end stage work ────────────────────────────────────────────────────────────

"done" | Set-Content $sentinelPath -Encoding UTF8
Write-Host "[$stageTag] Complete — run $RunId ($BusinessEntity): $($issues.Count) format issues, $($sw.ElapsedMilliseconds) ms"
