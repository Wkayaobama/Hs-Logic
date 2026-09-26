function Get-NormalizedPropertyValue {
    param($Value)
    if ($null -eq $Value) { return '' }
    if ($Value -is [bool]) { return $Value.ToString().ToLowerInvariant() }
    if ($Value -is [datetime]) { return $Value.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffZ') }   # older PowerShell parses ISO dates
    return [string]$Value
}

function Get-FlatColumns {
    <# Deterministic column order: fixed columns, sorted property names, sorted association columns #>
    param([object[]]$Records)
    $props  = [System.Collections.Generic.HashSet[string]]::new()
    $assocs = [System.Collections.Generic.HashSet[string]]::new()
    foreach ($r in $Records) {
        $p = $r['properties']
        if ($p) { foreach ($k in $p.Keys) { [void]$props.Add([string]$k) } }
        $a = $r['associations']
        if ($a) { foreach ($k in $a.Keys) { [void]$assocs.Add([string]$k) } }
    }
    $cols = [System.Collections.Generic.List[string]]::new()
    foreach ($c in @('id', 'created_at', 'updated_at', 'archived')) { $cols.Add($c) }
    foreach ($p in ($props | Sort-Object)) { $cols.Add($p) }
    foreach ($a in ($assocs | Sort-Object)) {
        foreach ($suffix in @('ids', 'primary_ids', 'labels', 'count')) { $cols.Add("assoc_${a}_${suffix}") }
    }
    return $cols.ToArray()
}

function ConvertTo-FlatRow {
    <# One export record (hashtable from ConvertFrom-Json -AsHashtable) -> one flat row #>
    param([Parameter(Mandatory)]$Record, [Parameter(Mandatory)][string[]]$Columns)
    $row = [ordered]@{}
    foreach ($c in $Columns) { $row[$c] = '' }
    $row['id']         = [string]$Record['id']
    $row['created_at'] = Get-NormalizedPropertyValue $Record['created_at']
    $row['updated_at'] = Get-NormalizedPropertyValue $Record['updated_at']
    $row['archived']   = Get-NormalizedPropertyValue $Record['archived']
    $p = $Record['properties']
    if ($p) {
        foreach ($k in $p.Keys) {
            $key = [string]$k
            if ($row.Contains($key)) { $row[$key] = Get-NormalizedPropertyValue $p[$k] }
        }
    }
    $a = $Record['associations']
    if ($a) {
        foreach ($to in $a.Keys) {
            $links = @($a[$to])
            $ids     = @($links | ForEach-Object { [string]$_['id'] } | Select-Object -Unique)
            $primary = @($links | Where-Object { [string]$_['label'] -eq 'Primary' } | ForEach-Object { [string]$_['id'] } | Select-Object -Unique)
            $labels  = @($links | Where-Object { $_['label'] } | ForEach-Object { [string]$_['label'] } | Select-Object -Unique)
            $row["assoc_${to}_ids"]         = ($ids -join ';')
            $row["assoc_${to}_primary_ids"] = ($primary -join ';')
            $row["assoc_${to}_labels"]      = ($labels -join ';')
            $row["assoc_${to}_count"]       = $ids.Count
        }
    }
    return [pscustomobject]$row
}

function Get-AssociationEdges {
    <# One export record -> edge rows (from_object, from_id, to_object, to_id, type_id, label, category) #>
    param([Parameter(Mandatory)]$Record, [Parameter(Mandatory)][string]$Object)
    $edges = [System.Collections.Generic.List[object]]::new()
    $a = $Record['associations']
    if (-not $a) { return $edges }
    foreach ($to in $a.Keys) {
        foreach ($l in @($a[$to])) {
            $edges.Add([pscustomobject]@{
                from_object = $Object; from_id = [string]$Record['id']; to_object = [string]$to; to_id = [string]$l['id']
                type_id = [string]$l['typeId']; label = [string]$l['label']; category = [string]$l['category']
            })
        }
    }
    return $edges
}

function Read-JsonLines {
    <# JSONL -> hashtables (memory-light streaming read) #>
    param([Parameter(Mandatory)][string]$Path)
    $records = [System.Collections.Generic.List[object]]::new()
    if (-not (Test-Path $Path)) { return ,$records }
    $reader = [System.IO.StreamReader]::new($Path, [System.Text.Encoding]::UTF8)
    try {
        while ($null -ne ($line = $reader.ReadLine())) {
            if ([string]::IsNullOrWhiteSpace($line)) { continue }
            $records.Add((ConvertFrom-JsonText -Text $line -AsHashtable))
        }
    } finally { $reader.Dispose() }
    return ,$records
}
