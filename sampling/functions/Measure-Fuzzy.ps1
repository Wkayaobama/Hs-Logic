function Get-NormalizedName {
    param([string]$Text)
    if ([string]::IsNullOrWhiteSpace($Text)) { return '' }
    $t = $Text.ToLowerInvariant()
    $t = $t -replace '[^\p{L}\p{N}\s]', ' '
    $t = $t -replace '\b(sa|sas|ag|gmbh|inc|ltd|llc|plc|srl|bv|co|corp|corporation|company|limited|holding|group)\b', ' '
    return (($t -replace '\s+', ' ').Trim())
}

function Get-NormalizedDomain {
    param([string]$Text)
    if ([string]::IsNullOrWhiteSpace($Text)) { return '' }
    $t = $Text.ToLowerInvariant().Trim()
    $t = $t -replace '^https?://', ''
    $t = $t -replace '^www\.', ''
    return (($t -split '/')[0])
}

function Get-TokenJaccard {
    param([string]$A, [string]$B)
    $sa = [System.Collections.Generic.HashSet[string]]::new([string[]]($A -split ' ' | Where-Object { $_ }))
    $sb = [System.Collections.Generic.HashSet[string]]::new([string[]]($B -split ' ' | Where-Object { $_ }))
    if ($sa.Count -eq 0 -or $sb.Count -eq 0) { return 0.0 }
    $inter = [System.Collections.Generic.HashSet[string]]::new($sa); $inter.IntersectWith($sb)
    $union = [System.Collections.Generic.HashSet[string]]::new($sa); $union.UnionWith($sb)
    return [Math]::Round($inter.Count / $union.Count, 3)
}

function Measure-Fuzzy {
    <#
    .SYNOPSIS  Duplicate candidates inside one entity's extract (exact normalised keys + token Jaccard on names).
    .OUTPUTS   [pscustomobject] object, kind, key, count, ids, score, samples
    #>
    param(
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Rows,
        [Parameter(Mandatory)][string]$Object,
        [double]$Threshold = 0.85,
        [int]$MaxCandidates = 5000
    )
    $results = [System.Collections.Generic.List[object]]::new()
    $groups = @{}
    $names  = @{}   # id -> normalised name (for the fuzzy pass)
    $labels = @{}   # id -> display label
    foreach ($r in $Rows) {
        $id = [string](Get-RowValue $r 'id')
        switch ($Object) {
            'companies' {
                $d = Get-NormalizedDomain ([string](Get-RowValue $r 'domain'))
                $n = Get-NormalizedName ([string](Get-RowValue $r 'name'))
                if ($d) { if (-not $groups.ContainsKey("domain:$d")) { $groups["domain:$d"] = [System.Collections.Generic.List[string]]::new() }; $groups["domain:$d"].Add($id) }
                if ($n) { if (-not $groups.ContainsKey("name:$n")) { $groups["name:$n"] = [System.Collections.Generic.List[string]]::new() }; $groups["name:$n"].Add($id); $names[$id] = $n }
                $labels[$id] = [string](Get-RowValue $r 'name')
            }
            'contacts' {
                $e = ([string](Get-RowValue $r 'email')).ToLowerInvariant().Trim()
                $n = Get-NormalizedName ("$(Get-RowValue $r 'firstname') $(Get-RowValue $r 'lastname')")
                if ($e) { if (-not $groups.ContainsKey("email:$e")) { $groups["email:$e"] = [System.Collections.Generic.List[string]]::new() }; $groups["email:$e"].Add($id) }
                if ($n) { if (-not $groups.ContainsKey("name:$n")) { $groups["name:$n"] = [System.Collections.Generic.List[string]]::new() }; $groups["name:$n"].Add($id); $names[$id] = $n }
                $labels[$id] = "$(Get-RowValue $r 'firstname') $(Get-RowValue $r 'lastname') <$e>"
            }
            default { return $results }
        }
    }
    foreach ($key in $groups.Keys) {
        $ids = @($groups[$key] | Select-Object -Unique)
        if ($ids.Count -lt 2) { continue }
        if ($results.Count -ge $MaxCandidates) { break }
        $results.Add([pscustomobject]@{
            object = $Object; kind = 'exact_' + ($key -split ':', 2)[0]; key = ($key -split ':', 2)[1]; count = $ids.Count
            ids = ($ids -join ';'); score = 1.0; samples = (($ids | Select-Object -First 3 | ForEach-Object { $labels[$_] }) -join ' | ')
        })
    }
    # fuzzy names: block on the first three characters, compare distinct normalised names
    $blocks = @{}
    foreach ($id in $names.Keys) {
        $n = $names[$id]
        $b = if ($n.Length -ge 3) { $n.Substring(0, 3) } else { $n }
        if (-not $blocks.ContainsKey($b)) { $blocks[$b] = @{} }
        if (-not $blocks[$b].ContainsKey($n)) { $blocks[$b][$n] = [System.Collections.Generic.List[string]]::new() }
        $blocks[$b][$n].Add($id)
    }
    foreach ($b in $blocks.Keys) {
        $distinct = @($blocks[$b].Keys)
        if ($distinct.Count -lt 2 -or $distinct.Count -gt 400) { continue }
        for ($i = 0; $i -lt $distinct.Count; $i++) {
            for ($j = $i + 1; $j -lt $distinct.Count; $j++) {
                if ($results.Count -ge $MaxCandidates) { return $results }
                $score = Get-TokenJaccard $distinct[$i] $distinct[$j]
                if ($score -ge $Threshold) {
                    $ids = @($blocks[$b][$distinct[$i]]) + @($blocks[$b][$distinct[$j]])
                    $results.Add([pscustomobject]@{
                        object = $Object; kind = 'fuzzy_name'; key = "$($distinct[$i]) ~ $($distinct[$j])"; count = $ids.Count
                        ids = ($ids -join ';'); score = $score; samples = (($ids | Select-Object -First 3 | ForEach-Object { $labels[$_] }) -join ' | ')
                    })
                }
            }
        }
    }
    return $results
}
