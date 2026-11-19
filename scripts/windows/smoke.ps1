param(
    [Parameter(Mandatory=$true)][string]$Symbol,
    [Parameter(Mandatory=$true)][string]$Expiry,
    [int]$GatewayPort = 9001,
    [int]$Port = 8000,
    [int]$TimeoutSeconds = 45
)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'preflight.ps1') -GatewayPort $GatewayPort -Port $Port -Running
$url = "http://127.0.0.1:$Port/api/snapshot"
$initial = Invoke-RestMethod $url -TimeoutSec 3
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
do {
    $data = Invoke-RestMethod $url -TimeoutSec 3
    $s = $data.status
    if ($s.mode -ne 'LIVE' -or $s.exchange -ne 'COMEX' -or
        $s.symbol -ne $Symbol -or $s.expiry -ne $Expiry) { throw 'Unexpected mode or contract' }
    if ($s.recorder_error) { throw 'Recorder unavailable' }
    if ($s.transport_connected -and $s.subscribe_ack -and $null -ne $s.request_id -and
        $s.request_id -eq $s.last_ack_request_id -and
        $s.trade_freshness -eq 'OBSERVED' -and $s.bbo_freshness -eq 'OBSERVED' -and
        $data.trade -and !$data.trade.conditional -and $data.bbo -and $data.bbo.spread_ticks -ge 0 -and
        $s.quote_timestamp_basis -eq 'collector_observation' -and
        [long]$s.last_eligible_trade.utc_ns -gt [long]$initial.status.last_eligible_trade.utc_ns -and
        [long]$s.last_bbo_message.utc_ns -gt [long]$initial.status.last_bbo_message.utc_ns) {
        $age = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() - [long]$data.trade.exchange_time_ms
        if ($age -lt -5000 -or $age -gt 120000 -or
            ($null -ne $data.trade.match_time_us -and [long]$data.trade.match_time_us -lt 1000000000000000) -or
            ($null -ne $data.trade.gateway_receive_ms -and [long]$data.trade.gateway_receive_ms -lt 1000000000000)) {
            throw 'Trade source clock is outside expected bounds. Verify clock synchronization and feed latency.'
        }
        Write-Output "PASS COMEX $Symbol expiry $Expiry, ACK #$($s.request_id), fresh eligible Trade and BBO"
        return
    }
    Start-Sleep -Milliseconds 300
} while ((Get-Date) -lt $deadline)
throw 'Timed out waiting for live Trade and BBO in an active session'
