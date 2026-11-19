param(
    [Parameter(Mandatory=$true)][string]$Symbol,
    [Parameter(Mandatory=$true)][string]$Expiry,
    [int]$GatewayPort = 9001,
    [int]$Port = 8000,
    [ValidateSet("A", "B", "C")][string]$Mode = "A",
    [ValidateRange(1,120)][int]$TimeoutSeconds = 45,
    [string]$DiagnosticPath,
    [switch]$ExpiryAttested
)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'preflight.ps1') -GatewayPort $GatewayPort -Port $Port -Running
$url = "http://127.0.0.1:$Port/api/snapshot"
$initial = Invoke-RestMethod $url -TimeoutSec 3
function Save-PilotDiagnostic {
    if (!$DiagnosticPath) { return }
    $target = [System.IO.Path]::GetFullPath($DiagnosticPath)
    if (Test-Path -LiteralPath $target) { throw 'Diagnostic report path already exists' }
    $diagnoseArgs = @('run', '--locked', 'python', '-m', 'observatory.diagnose',
        '--port', "$Port", '--gateway-port', "$GatewayPort", '--require-mode', $Mode, '--output', $target)
    if ($ExpiryAttested) { $diagnoseArgs += '--expiry-verified' }
    Push-Location (Join-Path $PSScriptRoot '../..')
    try {
        uv @diagnoseArgs
        if (!(Test-Path -LiteralPath $target)) {
            throw 'Diagnostic report could not be saved. Use a new local path outside Git.'
        }
    } finally {
        Pop-Location
    }
}
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
do {
    $data = Invoke-RestMethod $url -TimeoutSec 3
    $s = $data.status
    if ($s.mode -ne 'LIVE' -or $s.exchange -ne 'COMEX' -or
        $s.symbol -ne $Symbol -or $s.expiry -ne $Expiry) { throw 'Unexpected mode or contract' }
    if ($s.market_data_mode -ne $Mode) {
        throw "Requested validation mode $Mode differs from effective mode $($s.market_data_mode)"
    }
    if ($s.recorder_error) { throw 'Recorder unavailable' }
    if ($s.transport_connected -and $s.subscribe_ack -and $null -ne $s.request_id -and
        $s.request_id -eq $s.last_ack_request_id -and
        $s.trade_freshness -eq 'OBSERVED' -and $s.bbo_freshness -eq 'OBSERVED' -and
        $data.trade -and !$data.trade.conditional -and $data.bbo -and $data.bbo.spread_ticks -ge 0 -and
        $s.quote_timestamp_basis -eq 'collector_observation' -and
        [long]$s.counters.eligible_trade -gt [long]$initial.status.counters.eligible_trade -and
        [long]$s.counters.eligible_bbo -gt [long]$initial.status.counters.eligible_bbo) {
        if ($Mode -ne "A" -and ($data.microstructure.liquidity.status -ne "VALID" -or
            $data.microstructure.ofi.status -ne "VALID" -or
            ($data.microstructure.mbp.generation -eq $initial.microstructure.mbp.generation -and
             $data.microstructure.mbp.sequence -le $initial.microstructure.mbp.sequence))) {
            Start-Sleep -Milliseconds 300
            continue
        }
        if ($Mode -eq "C" -and ($s.mbo_entitlement -ne "OPERATOR_REPORTED" -or
            $data.microstructure.mbo.status -ne "OBSERVED_PARTIAL_BOOK")) {
            Start-Sleep -Milliseconds 300
            continue
        }
        $age = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() - [long]$data.trade.exchange_time_ms
        if ($age -lt -5000 -or $age -gt 120000 -or
            ($null -ne $data.trade.match_time_us -and [long]$data.trade.match_time_us -lt 1000000000000000) -or
            ($null -ne $data.trade.gateway_receive_ms -and [long]$data.trade.gateway_receive_ms -lt 1000000000000)) {
            throw 'Trade source clock is outside expected bounds. Verify clock synchronization and feed latency.'
        }
        Save-PilotDiagnostic
        Write-Output "PASS COMEX $Symbol expiry $Expiry, ACK #$($s.request_id), fresh eligible Trade and BBO"
        return
    }
    Start-Sleep -Milliseconds 300
} while ((Get-Date) -lt $deadline)
Save-PilotDiagnostic
$rpc = $s.receiver.depthsnap_rpc.recent | Select-Object -Last 1
$outcome = if ($rpc) { $rpc.outcomes -join ',' } else { 'none' }
throw ("Pilot readiness timed out: trade={0}, bbo={1}, mbp={2}, effective={3}, rpc={4}, uncertain={5}" -f
    $s.trade_freshness, $s.bbo_freshness, $s.mbp_status, $s.market_data_mode, $outcome,
    $s.receiver.uncertain_received_frames)
