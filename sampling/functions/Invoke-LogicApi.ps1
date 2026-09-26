function Get-HeaderValue {
    param($Headers, [Parameter(Mandatory)][string]$Name)
    if ($null -eq $Headers) { return $null }
    foreach ($key in $Headers.Keys) {
        if ($key -ieq $Name) { return (@($Headers[$key]) | Select-Object -First 1) }
    }
    return $null
}

function Invoke-LogicApi {
    <#
    .SYNOPSIS  Call the Hs-Logic backend (the execution layer under test) and capture its X-Logic-* headers.
    .OUTPUTS   [pscustomobject] Body, Headers, Status, DurationMs, BackendMs, HsRequests, Hs429, HsRetries, RequestId, Uri
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Server,
        [Parameter(Mandatory)][string]$Path,
        [hashtable]$Query = @{},
        [ValidateSet('GET', 'POST')][string]$Method = 'GET',
        [object]$Body = $null,
        [int]$Retries = 3,
        [int]$TimeoutSec = 600
    )
    $pairs = [System.Collections.Generic.List[string]]::new()
    foreach ($key in ($Query.Keys | Sort-Object)) {
        $value = $Query[$key]
        if ($null -eq $value) { continue }
        foreach ($v in @($value)) {
            if ($null -eq $v) { continue }
            $pairs.Add(('{0}={1}' -f [Uri]::EscapeDataString([string]$key), [Uri]::EscapeDataString([string]$v)))
        }
    }
    $uri = $Server.TrimEnd('/') + $Path
    if ($pairs.Count) { $uri += '?' + ($pairs -join '&') }

    $attempt = 0
    while ($true) {
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $hdr = $null; $code = 0; $resp = $null; $failure = $null
        try {
            $args = @{ Method = $Method; Uri = $uri; TimeoutSec = $TimeoutSec; SkipHttpErrorCheck = $true }
            if ($null -ne $Body) { $args.Body = ($Body | ConvertTo-Json -Depth 20); $args.ContentType = 'application/json' }
            $raw = Invoke-WebRequest @args
            $hdr = $raw.Headers
            $code = [int]$raw.StatusCode
            $text = [string]$raw.Content
            # keep ISO timestamps as strings (PowerShell would otherwise turn them into culture-formatted DateTime)
            $resp = if ($text) { ConvertFrom-JsonText $text } else { $null }
        } catch {
            $failure = $_.Exception.Message
        }
        $sw.Stop()
        $retryable = ($null -ne $failure) -or ($code -in 429, 502, 503, 504)
        if ($retryable -and $attempt -lt $Retries) {
            $attempt++
            Start-Sleep -Seconds ([Math]::Min([Math]::Pow(2, $attempt), 10))
            continue
        }
        if ($null -ne $failure) { throw "Logic API $Method $uri failed: $failure" }
        if ($code -ge 400) {
            $detail = if ($null -ne $resp -and (Get-RowValue $resp 'detail')) { Get-RowValue $resp 'detail' } else { "$resp" }
            throw "Logic API $Method $uri returned HTTP $code : $detail"
        }
        return [pscustomobject]@{
            Body       = $resp
            Headers    = $hdr
            Status     = $code
            Uri        = $uri
            DurationMs = $sw.ElapsedMilliseconds
            BackendMs  = [int](0 + (Get-HeaderValue $hdr 'X-Logic-Duration-Ms'))
            HsRequests = [int](0 + (Get-HeaderValue $hdr 'X-Logic-HS-Requests'))
            Hs429      = [int](0 + (Get-HeaderValue $hdr 'X-Logic-HS-429'))
            HsRetries  = [int](0 + (Get-HeaderValue $hdr 'X-Logic-HS-Retries'))
            RequestId  = [string](Get-HeaderValue $hdr 'X-Logic-Request-Id')
            Attempts   = $attempt + 1
        }
    }
}

function New-ApiCallLog { return ,([System.Collections.Generic.List[object]]::new()) }

function Add-ApiCall {
    param([Parameter(Mandatory)]$Log, [Parameter(Mandatory)]$Result, [string]$Label = '')
    $Log.Add([pscustomobject]@{
        label = $Label; uri = $Result.Uri; status = $Result.Status; duration_ms = $Result.DurationMs
        backend_ms = $Result.BackendMs; hs_requests = $Result.HsRequests; hs_429 = $Result.Hs429
        hs_retries = $Result.HsRetries; request_id = $Result.RequestId; attempts = $Result.Attempts
    })
}

function Measure-ApiCalls {
    param($Log)
    $calls = @($Log)
    return [ordered]@{
        calls        = $calls.Count
        duration_ms  = [int](Get-Sum $calls 'duration_ms')
        hs_requests  = [int](Get-Sum $calls 'hs_requests')
        hs_429       = [int](Get-Sum $calls 'hs_429')
        hs_retries   = [int](Get-Sum $calls 'hs_retries')
        max_attempts = [int](Get-Max $calls 'attempts')
    }
}
