function Test-Cardinality {
    <#
    .SYNOPSIS  Apply model/cardinality.csv to the flattened extract of one business entity.
    .PARAMETER Rules    rows of cardinality.csv
    .PARAMETER Edges    rows of associations.csv (from_object, from_id, to_object, to_id, type_id, label)
    .PARAMETER IdSets   hashtable object -> HashSet[string] of ids extracted in this scope
    .OUTPUTS   @{ Violations = [list]; Summary = [list] }
    #>
    param(
        [Parameter(Mandatory)][object[]]$Rules,
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Edges,
        [Parameter(Mandatory)][hashtable]$IdSets,
        [Parameter(Mandatory)][string]$Entity,
        [int]$MaxViolations = 20000
    )
    $violations = [System.Collections.Generic.List[object]]::new()
    $summary    = [System.Collections.Generic.List[object]]::new()
    $byFrom = @{}
    foreach ($e in $Edges) {
        $key = "$($e.from_object)|$($e.from_id)"
        if (-not $byFrom.ContainsKey($key)) { $byFrom[$key] = [System.Collections.Generic.List[object]]::new() }
        $byFrom[$key].Add($e)
    }
    $bad = [System.Collections.Generic.HashSet[string]]::new()

    function Add-Violation {
        param($Kind, $Severity, $Rule, $Object, $Id, $Expected, $Actual, $Detail)
        if ($violations.Count -ge $MaxViolations) { return }
        $violations.Add([pscustomobject]@{
            entity = $Entity; rule = $Rule.edge_name; kind = $Kind; severity = $Severity; object = $Object; id = $Id
            expected = [string]$Expected; actual = [string]$Actual; detail = [string]$Detail
        })
    }

    foreach ($rule in $Rules) {
        $src = [string]$rule.source; $tgt = [string]$rule.target
        if (-not $IdSets.ContainsKey($src)) { continue }
        $typeIds = @(([string]$rule.source_type_ids) -split '\|' | Where-Object { $_ } | ForEach-Object { [int]$_ })
        $primary = if ([string]$rule.primary_type_id) { [int]$rule.primary_type_id } else { $null }
        $min = [int]$rule.min_per_source
        $maxPrimary = if ([string]$rule.max_primary_per_source) { [int]$rule.max_primary_per_source } else { $null }
        $maxPer = if ([string]$rule.max_per_source) { [int]$rule.max_per_source } else { $null }
        $targetExtracted = $IdSets.ContainsKey($tgt)
        $total = 0; $withLink = 0; $missing = 0; $multiPrimary = 0; $tooMany = 0; $crossScope = 0
        foreach ($id in $IdSets[$src]) {
            $total++
            $links = @()
            $key = "$src|$id"
            if ($byFrom.ContainsKey($key)) {
                $links = @($byFrom[$key] | Where-Object { $_.to_object -eq $tgt -and $_.type_id -and ([int]$_.type_id) -in $typeIds })
            }
            $targets = @($links | ForEach-Object { [string]$_.to_id } | Select-Object -Unique)
            if ($targets.Count -gt 0) { $withLink++ }
            if ($targets.Count -lt $min) {
                $missing++
                Add-Violation 'missing_link' 'error' $rule $src $id ">= $min $tgt" $targets.Count "no $tgt association of type $($typeIds -join '/')"
                [void]$bad.Add("$src|$id")
            }
            if ($null -ne $primary -and $null -ne $maxPrimary) {
                $prim = @($links | Where-Object { [int]$_.type_id -eq $primary } | ForEach-Object { [string]$_.to_id } | Select-Object -Unique)
                if ($prim.Count -gt $maxPrimary) {
                    $multiPrimary++
                    Add-Violation 'multiple_primary' 'error' $rule $src $id "<= $maxPrimary primary" $prim.Count "primary $tgt ids: $($prim -join ';')"
                    [void]$bad.Add("$src|$id")
                }
            }
            if ($null -ne $maxPer -and $targets.Count -gt $maxPer) {
                $tooMany++
                Add-Violation 'too_many' 'error' $rule $src $id "<= $maxPer $tgt" $targets.Count "$tgt ids: $($targets -join ';')"
                [void]$bad.Add("$src|$id")
            }
            if ($targetExtracted) {
                foreach ($t in $targets) {
                    if (-not $IdSets[$tgt].Contains($t)) {
                        $crossScope++
                        Add-Violation 'cross_scope' 'info' $rule $src $id "$tgt in $Entity scope" $t "associated $tgt $t is outside the $Entity extract (cross-sell or other entity)"
                    }
                }
            }
        }
        $observed = if ($total) { [Math]::Round($withLink / $total, 4) } else { 0 }
        $violationRate = if ($total) { [Math]::Round(($missing + $multiPrimary + $tooMany) / $total, 4) } else { 0 }
        $summary.Add([pscustomobject]@{
            entity = $Entity; edge = $rule.edge_name; source = $src; target = $tgt; cardinality = $rule.cardinality
            sources = $total; with_link = $withLink; observed_rate = $observed; expected_rate = $rule.expected_rate
            missing = $missing; multiple_primary = $multiPrimary; too_many = $tooMany; cross_scope = $crossScope
            violation_rate = $violationRate; target_extracted = $targetExtracted
        })
    }
    # cascade: engagements attached to a record with a structural error inherit a warning
    foreach ($e in $Edges) {
        if ($e.from_object -in $Config.Engagements -and $bad.Contains("$($e.to_object)|$($e.to_id)")) {
            if ($violations.Count -ge $MaxViolations) { break }
            $violations.Add([pscustomobject]@{
                entity = $Entity; rule = 'inherited'; kind = 'inherited'; severity = 'warning'; object = $e.from_object; id = $e.from_id
                expected = 'parent without structural error'; actual = "$($e.to_object) $($e.to_id)"; detail = "linked to a $($e.to_object) that failed a cardinality rule"
            })
        }
    }
    return @{ Violations = $violations; Summary = $summary }
}
