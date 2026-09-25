#Requires -Version 7.0
<#
.SYNOPSIS  Sampling probe configuration. Dot-sourced by every stage and by run.ps1.
.NOTES     Values can be overridden through environment variables:
           LOGIC_API_BASE (backend URL), SAMPLING_TARGET (Backend|HubSpot|Both),
           HUBSPOT_TOKEN (only for the direct REST cross-check), PIPELINE_RUN_ID.
#>

$Config = [ordered]@{
    Root            = $PSScriptRoot
    ApiBase         = if ($env:LOGIC_API_BASE) { $env:LOGIC_API_BASE } else { 'http://127.0.0.1:8000' }
    HubSpotBase     = if ($env:HUBSPOT_BASE_URL) { $env:HUBSPOT_BASE_URL } else { 'https://api.hubapi.com' }
    Target          = if ($env:SAMPLING_TARGET) { $env:SAMPLING_TARGET } else { 'Backend' }
    Objects         = @('contacts', 'companies', 'deals', 'tickets', 'notes')
    Engagements     = @('notes', 'calls', 'meetings', 'tasks')
    PageLimit       = 100
    ThrottleLimit   = 3
    FuzzyThreshold  = 0.85
    MaxIssuesPerObject = 5000
    # Gate thresholds (see README "Gate criteria")
    Gate = [ordered]@{
        FixedTolerancePct       = 0.0     # the six fixed reference numbers must match exactly
        DefaultTolerancePct     = 1.0     # product distribution and utilisation counts
        MaxUnrecovered429       = 0
        MaxExtractMinutes       = 15
        MaxViolationRate = [ordered]@{    # per cardinality edge; others default to 0.05
            deal_primary_company           = 0.01
            deal_primary_contact           = 0.20
            contact_primary_company        = 0.07
            company_icalps_primary_contact = 0.16
        }
        DefaultMaxViolationRate = 0.05
        MinSourcesForRate       = 100     # smaller scopes are reported, not gated (rates are noisy)
    }
}

function Resolve-Server {
    param([string]$Server)
    if ([string]::IsNullOrWhiteSpace($Server)) { return $Config.ApiBase }
    return $Server.TrimEnd('/')
}

function Assert-RunId {
    param([string]$RunId)
    if ([string]::IsNullOrWhiteSpace($RunId)) {
        throw "RunId is required: pass -RunId or set PIPELINE_RUN_ID (run.ps1 generates one)."
    }
}

function Get-StagePath {
    <# Path of a per-run output folder: state, preflight, extract, flatten, validation, delta, logs, review #>
    param(
        [Parameter(Mandatory)][string]$Kind,
        [string]$Entity,
        [string]$RunId,
        [switch]$Create
    )
    $parts = @($Config.Root, $Kind)
    if ($Entity) { $parts += $Entity }
    if ($RunId)  { $parts += $RunId }
    $path = [System.IO.Path]::Combine([string[]]$parts)
    if ($Create) { New-Item -ItemType Directory -Force -Path $path | Out-Null }
    return $path
}

function Get-SentinelPath {
    param([Parameter(Mandatory)][string]$StageTag, [Parameter(Mandatory)][string]$RunId, [string]$Entity)
    $dir = Get-StagePath -Kind 'state' -Create
    $name = if ($Entity) { "${StageTag}_${Entity}_${RunId}.done" } else { "${StageTag}_${RunId}.done" }
    return (Join-Path $dir $name)
}

function Get-ModelPath {
    param([Parameter(Mandatory)][string]$Name)
    return (Join-Path $Config.Root 'model' $Name)
}

function Import-CardinalityRules { Import-Csv -Path (Get-ModelPath 'cardinality.csv') -Encoding UTF8 }
function Import-FormatRules      { Import-Csv -Path (Get-ModelPath 'format_rules.csv') -Encoding UTF8 }
function Import-References       { (Get-Content -Raw -Path (Get-ModelPath 'reference.json') -Encoding UTF8 | ConvertFrom-Json) }

function Write-JsonFile {
    param([Parameter(Mandatory)]$Object, [Parameter(Mandatory)][string]$Path, [int]$Depth = 20)
    $Object | ConvertTo-Json -Depth $Depth | Set-Content -Path $Path -Encoding UTF8
}

function Read-JsonFile {
    param([Parameter(Mandatory)][string]$Path, [switch]$AsHashtable)
    $raw = Get-Content -Raw -Path $Path -Encoding UTF8
    if ($AsHashtable) { return ($raw | ConvertFrom-Json -AsHashtable -Depth 64) }
    return ($raw | ConvertFrom-Json -Depth 64)
}

function Get-RowValue {
    <# Strict-mode safe property access on Import-Csv / ConvertFrom-Json objects #>
    param($Row, [Parameter(Mandatory)][string]$Name)
    if ($null -eq $Row) { return $null }
    if ($Row -is [System.Collections.IDictionary]) { return $Row[$Name] }
    $p = $Row.PSObject.Properties[$Name]
    if ($p) { return $p.Value }
    return $null
}

function Get-UtcNow { (Get-Date).ToUniversalTime().ToString('o') }

function Expand-List {
    <# `-Entities deals,companies` arrives as ONE string when a script is started with `pwsh -File`; split on commas/semicolons/spaces #>
    param([AllowEmptyCollection()][AllowNull()][string[]]$Items)
    $out = [System.Collections.Generic.List[string]]::new()
    foreach ($item in @($Items)) {
        if ($null -eq $item) { continue }
        foreach ($part in (([string]$item) -split '[,;\s]+')) {
            $p = $part.Trim()
            if ($p -and ($p -notin $out)) { $out.Add($p) }
        }
    }
    return $out.ToArray()
}

function Get-Sum {
    <# Sum of a property over a possibly empty collection (Measure-Object returns nothing for empty input) #>
    param($Items, [Parameter(Mandatory)][string]$Property)
    $list = @($Items)
    if ($list.Count -eq 0) { return 0 }
    $m = $list | Measure-Object -Property $Property -Sum
    if ($null -eq $m) { return 0 }
    return [double]$m.Sum
}

function Get-Max {
    param($Items, [Parameter(Mandatory)][string]$Property)
    $list = @($Items)
    if ($list.Count -eq 0) { return 0 }
    $m = $list | Measure-Object -Property $Property -Maximum
    if ($null -eq $m) { return 0 }
    return [double]$m.Maximum
}
