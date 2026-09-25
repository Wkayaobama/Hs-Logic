function Test-RowFilter {
    <# Evaluate one reference filter (prop, op, value) on a flattened CSV row #>
    param([Parameter(Mandatory)]$Row, [Parameter(Mandatory)]$Filter)
    $prop = [string](Get-RowValue $Filter 'prop'); $op = [string](Get-RowValue $Filter 'op'); $value = [string](Get-RowValue $Filter 'value')
    $raw = [string](Get-RowValue $Row $prop)
    $present = -not [string]::IsNullOrEmpty($raw)
    $tokens = @($raw -split ';' | Where-Object { $_ })
    switch ($op) {
        'HAS_PROPERTY'     { return $present }
        'NOT_HAS_PROPERTY' { return (-not $present) }
        'EQ'               { return ($present -and (($raw -eq $value) -or ($tokens -contains $value))) }
        'NEQ'              { return (-not ($present -and (($raw -eq $value) -or ($tokens -contains $value)))) }
        'IN'               { $set = @($value -split '\|'); return ($present -and (($set -contains $raw) -or (@($tokens | Where-Object { $set -contains $_ }).Count -gt 0))) }
        'NOT_IN'           { $set = @($value -split '\|'); return (-not ($present -and (($set -contains $raw) -or (@($tokens | Where-Object { $set -contains $_ }).Count -gt 0)))) }
        'CONTAINS_TOKEN'   { return ($present -and $raw.ToLowerInvariant().Contains($value.ToLowerInvariant())) }
        default            { throw "Unsupported filter operator '$op' for extract evaluation." }
    }
}

function Measure-ExtractCount {
    param([Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Rows, [AllowEmptyCollection()][object[]]$Filters = @())
    $n = 0
    foreach ($r in $Rows) {
        $ok = $true
        foreach ($f in @($Filters)) { if (-not (Test-RowFilter $r $f)) { $ok = $false; break } }
        if ($ok) { $n++ }
    }
    return $n
}

function Compare-Reference {
    param([Parameter(Mandatory)]$Reference, [Parameter(Mandatory)][int]$Actual)
    $expected = [int](Get-RowValue $Reference 'expected')
    $tolerancePct = [double](0 + (Get-RowValue $Reference 'tolerance_pct'))
    $toleranceAbs = [int](0 + (Get-RowValue $Reference 'tolerance_abs'))
    $absDiff = [Math]::Abs($Actual - $expected)
    $drift = if ($expected -eq 0) { if ($Actual -eq 0) { 0.0 } else { 100.0 } } else { [Math]::Round(($absDiff / $expected) * 100, 3) }
    return [pscustomobject]@{ expected = $expected; actual = $Actual; abs_diff = $absDiff; drift_pct = $drift; tolerance_pct = $tolerancePct; tolerance_abs = $toleranceAbs; pass = (($absDiff -le $toleranceAbs) -or ($drift -le $tolerancePct)) }
}
