function Invoke-HubSpotCount {
    <#
    .SYNOPSIS  Direct HubSpot search count (accuracy cross-check only). Needs $env:HUBSPOT_TOKEN.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Object,
        [object[]]$Filters = @(),
        [string]$BaseUrl = $Config.HubSpotBase
    )
    if (-not $env:HUBSPOT_TOKEN) { throw "HUBSPOT_TOKEN is not set; the direct cross-check needs a read-only private-app token." }
    $body = [ordered]@{ limit = 1; properties = @('hs_object_id') }
    if ($Filters.Count) { $body.filterGroups = @([ordered]@{ filters = @(ConvertTo-HubSpotFilters $Filters) }) }
    $headers = @{ Authorization = "Bearer $env:HUBSPOT_TOKEN"; 'Content-Type' = 'application/json' }
    $uri = "$($BaseUrl.TrimEnd('/'))/crm/v3/objects/$Object/search"
    for ($attempt = 0; $attempt -le 2; $attempt++) {
        $hdr = $null; $code = 0
        $resp = Invoke-RestMethod -Method POST -Uri $uri -Headers $headers -Body ($body | ConvertTo-Json -Depth 10) `
            -SkipHttpErrorCheck -ResponseHeadersVariable hdr -StatusCodeVariable code -TimeoutSec 120
        if ($code -eq 429) {
            $wait = [int](1 + (Get-HeaderValue $hdr 'Retry-After'))
            Start-Sleep -Seconds ([Math]::Min($wait, 30)); continue
        }
        if ($code -ge 400) { throw "HubSpot search $Object returned HTTP $code : $($resp | ConvertTo-Json -Compress -Depth 5)" }
        return [int](Get-RowValue $resp 'total')
    }
    throw "HubSpot search $Object kept returning 429."
}
