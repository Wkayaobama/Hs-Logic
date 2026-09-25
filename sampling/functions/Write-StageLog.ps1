function Write-StageLog {
    <# logs/<RunId>/<stage>[_<entity>].json with timings and API call statistics #>
    param(
        [Parameter(Mandatory)][string]$Stage,
        [Parameter(Mandatory)][string]$RunId,
        [string]$Entity = '',
        [hashtable]$Data = @{},
        $ApiLog = $null
    )
    $dir = Get-StagePath -Kind 'logs' -RunId $RunId -Create
    $name = if ($Entity) { "${Stage}_${Entity}.json" } else { "${Stage}.json" }
    $payload = [ordered]@{ stage = $Stage; run_id = $RunId; business_entity = $Entity; logged_at = (Get-UtcNow) }
    foreach ($k in $Data.Keys) { $payload[$k] = $Data[$k] }
    if ($null -ne $ApiLog) {
        $payload['api'] = Measure-ApiCalls $ApiLog
        $payload['api_calls'] = @($ApiLog)
    }
    Write-JsonFile -Object $payload -Path (Join-Path $dir $name)
    return (Join-Path $dir $name)
}
