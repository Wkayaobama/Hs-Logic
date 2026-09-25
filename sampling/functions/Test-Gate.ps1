function Test-Gate {
    <#
    .SYNOPSIS  Evaluate the gate criteria of a completed run and write review/<RunId>/GATE.md
    .OUTPUTS   [pscustomobject] pass, criteria, path
    #>
    param(
        [Parameter(Mandatory)][string]$RunId,
        [Parameter(Mandatory)][string[]]$BusinessEntities,
        [string]$Server = ''
    )
    $criteria = [System.Collections.Generic.List[object]]::new()
    function Add-Criterion {
        param([string]$Area, [string]$Name, [bool]$Pass, [string]$Detail)
        $criteria.Add([pscustomobject]@{ area = $Area; criterion = $Name; pass = $Pass; detail = $Detail })
    }

    # executability
    $perEntityStages = @('01_extract', '02_flatten', '03_profile', '04_cascade_validation', '05_fuzzy', '06_delta')
    $missing = @()
    foreach ($be in $BusinessEntities) {
        foreach ($s in $perEntityStages) { if (-not (Test-Path (Get-SentinelPath -StageTag $s -RunId $RunId -Entity $be))) { $missing += "$s/$be" } }
    }
    foreach ($s in @('00_preflight', '07_accuracy_efficiency', '08_review_package')) {
        if (-not (Test-Path (Get-SentinelPath -StageTag $s -RunId $RunId))) { $missing += $s }
    }
    Add-Criterion 'executability' 'every stage completed and left a sentinel' ($missing.Count -eq 0) $(if ($missing.Count) { "missing: $($missing -join ', ')" } else { "$($BusinessEntities.Count) entities x $($perEntityStages.Count) stages + 3 run-level stages" })

    $cappedEntities = @()
    $noMarker = @()
    foreach ($be in $BusinessEntities) {
        $m = Join-Path (Get-StagePath -Kind 'extract' -Entity $be -RunId $RunId) 'manifest.json'
        if (-not (Test-Path $m)) { continue }
        $manifest = Read-JsonFile -Path $m
        if ([bool]$manifest.capped) { $cappedEntities += $be }
        foreach ($o in @($manifest.objects)) { if ([bool]$o.no_marker) { $noMarker += "$be/$($o.object)" } }
    }
    Add-Criterion 'executability' 'no extract hit the 10,000-record search cap' ($cappedEntities.Count -eq 0) $(if ($cappedEntities.Count) { "capped: $($cappedEntities -join ', ')" } else { 'all scoped extracts completed below the cap' })
    Add-Criterion 'executability' 'entities without markers are reported, not hidden' $true $(if ($noMarker.Count) { "no_marker: $($noMarker -join ', ')" } else { 'every entity/object pair has a marker' })

    # accuracy
    $accPath = Join-Path (Get-StagePath -Kind 'validation' -Entity '_run' -RunId $RunId) 'accuracy.json'
    if (Test-Path $accPath) {
        $acc = Read-JsonFile -Path $accPath
        $rows = @($acc.results)
        $disagree  = @($rows | Where-Object { -not $_.paths_agree })
        $fixedFail = @($rows | Where-Object { $_.group -eq 'fixed' -and -not $_.within_tolerance })
        $otherFail = @($rows | Where-Object { $_.group -ne 'fixed' -and -not $_.within_tolerance })
        $fixedRows = @($rows | Where-Object { $_.group -eq 'fixed' })
        $maxFixedDrift = if ($fixedRows.Count) { [double](Get-Max $fixedRows 'drift_pct') } else { 0 }
        Add-Criterion 'accuracy' 'computation paths agree exactly (backend = extract = direct)' ($disagree.Count -eq 0) $(if ($disagree.Count) { "$($disagree.Count) disagree: " + (($disagree | Select-Object -First 8 | ForEach-Object { "$($_.id) backend=$($_.backend) extract=$($_.extract) direct=$($_.direct)" }) -join '; ') } else { "target $($acc.target), $($rows.Count) references, every path returned the same count" })
        Add-Criterion 'accuracy' 'fixed reference counts within tolerance of the dated capture' ($fixedFail.Count -eq 0) $(if ($fixedFail.Count) { ($fixedFail | ForEach-Object { "$($_.id): captured $($_.expected), now $($_.backend) ($($_.drift_pct)%)" }) -join '; ' } else { "$($fixedRows.Count) fixed references, max drift $maxFixedDrift% (captured $(($fixedRows | Select-Object -First 1).captured))" })
        Add-Criterion 'accuracy' 'product distribution and utilisation counts within tolerance' ($otherFail.Count -eq 0) $(if ($otherFail.Count) { "$($otherFail.Count) drifted beyond tolerance: " + (($otherFail | Select-Object -First 8 | ForEach-Object { "$($_.id) $($_.expected)->$($_.backend) ($($_.drift_pct)%)" }) -join '; ') } else { "$(@($rows | Where-Object { $_.group -ne 'fixed' }).Count) references within tolerance; drift is listed per reference in accuracy.csv" })
    } else {
        Add-Criterion 'accuracy' 'accuracy stage produced accuracy.json' $false "missing $accPath"
    }

    # efficiency
    $effPath = Join-Path (Get-StagePath -Kind 'validation' -Entity '_run' -RunId $RunId) 'efficiency.json'
    if (Test-Path $effPath) {
        $eff = Read-JsonFile -Path $effPath
        $t = $eff.totals
        Add-Criterion 'efficiency' 'zero unrecovered 429s' ([int]$t.unrecovered_429 -le $Config.Gate.MaxUnrecovered429) "429 seen $($t.hs_429), retries $($t.hs_retries), HubSpot calls $($t.hs_requests)"
        $maxMs = [int]$t.max_entity_extract_ms
        Add-Criterion 'efficiency' "largest entity extract under $($Config.Gate.MaxExtractMinutes) minutes" ($maxMs -le $Config.Gate.MaxExtractMinutes * 60000) "max entity extract $([Math]::Round($maxMs / 1000.0, 1)) s, total $([Math]::Round([int]$t.extract_duration_ms / 1000.0, 1)) s for $($t.records) records"
    } else {
        Add-Criterion 'efficiency' 'efficiency stage produced efficiency.json' $false "missing $effPath"
    }

    # structural feasibility
    $failing = @()
    $edges = 0
    foreach ($be in $BusinessEntities) {
        $csv = Join-Path (Get-StagePath -Kind 'validation' -Entity $be -RunId $RunId) 'cardinality_summary.csv'
        if (-not (Test-Path $csv)) { continue }
        foreach ($row in @(Import-Csv -Path $csv -Encoding UTF8)) {
            $edges++
            if ([int]$row.sources -lt $Config.Gate.MinSourcesForRate) { continue }
            $threshold = if ($Config.Gate.MaxViolationRate.Contains($row.edge)) { [double]$Config.Gate.MaxViolationRate[$row.edge] } else { [double]$Config.Gate.DefaultMaxViolationRate }
            if ([double]$row.violation_rate -gt $threshold) { $failing += "$be/$($row.edge) $([Math]::Round([double]$row.violation_rate * 100, 1))% > $([Math]::Round($threshold * 100, 1))%" }
        }
    }
    Add-Criterion 'structural' 'cardinality violation rates within the workbook-derived thresholds' ($failing.Count -eq 0) $(if ($failing.Count) { "needs remediation before it can be a rule: $($failing -join '; ')" } else { "$edges edge evaluations, gated where sources >= $($Config.Gate.MinSourcesForRate), all within thresholds" })

    $pass = (@($criteria | Where-Object { -not $_.pass }).Count -eq 0)
    $dir = Get-StagePath -Kind 'review' -RunId $RunId -Create
    $path = Join-Path $dir 'GATE.md'
    $lines = @("# Sampling probe gate — run $RunId", '', "Result: **$(if ($pass) { 'PASS' } else { 'FAIL' })**  (server: $Server, entities: $($BusinessEntities -join ', '), evaluated $(Get-UtcNow))", '', '| Area | Criterion | Result | Detail |', '|---|---|---|---|')
    foreach ($c in $criteria) { $lines += "| $($c.area) | $($c.criterion) | $(if ($c.pass) { 'PASS' } else { 'FAIL' }) | $($c.detail -replace '\|', '/') |" }
    $lines += ''
    $lines += 'Phases B-E (blueprint, rules, operations, ruler agents) start only on PASS; on FAIL the fix goes into the backend and the probe is re-run.'
    ($lines -join "`n") + "`n" | Set-Content -Path $path -Encoding UTF8
    return [pscustomobject]@{ pass = $pass; criteria = @($criteria); path = $path }
}
