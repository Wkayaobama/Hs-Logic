function Get-EntityScope {
    <# The entity seed as loaded by the backend (/api/meta/entities) #>
    param([Parameter(Mandatory)][string]$Server)
    return (Invoke-LogicApi -Server $Server -Path '/api/meta/entities').Body
}

function Get-BusinessEntityIds {
    param([Parameter(Mandatory)][string]$Server)
    $meta = Get-EntityScope -Server $Server
    return @($meta.entities | ForEach-Object { [string]$_.id })
}

function Assert-BusinessEntity {
    param([Parameter(Mandatory)][string]$Server, [Parameter(Mandatory)][string]$BusinessEntity)
    $ids = Get-BusinessEntityIds -Server $Server
    if ($BusinessEntity.ToUpperInvariant() -notin $ids) {
        throw "Unknown business entity '$BusinessEntity'. Known: $($ids -join ', ')"
    }
    return $BusinessEntity.ToUpperInvariant()
}
