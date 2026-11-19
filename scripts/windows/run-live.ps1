param(
    [Parameter(Mandatory=$true)][string]$Symbol,
    [Parameter(Mandatory=$true)][string]$Expiry,
    [int]$GatewayPort = 9001,
    [int]$Port = 8000,
    [ValidateSet("A", "B", "C")][string]$Mode = "A",
    [switch]$MboEntitled,
    [switch]$Capture,
    [switch]$RetentionAttested,
    [switch]$ExpiryAttested,
    [int]$BatchSize = 32,
    [string]$Calendar,
    [int]$RetentionDays = 7
)
$ErrorActionPreference = 'Stop'
if ($Capture -and !$RetentionAttested) {
    throw 'LIVE capture requires confirmed data-use and retention authorization. Supply -RetentionAttested only when confirmed.'
}
if ($Calendar) { $Calendar = (Resolve-Path -LiteralPath $Calendar).Path }
& (Join-Path $PSScriptRoot 'preflight.ps1') -GatewayPort $GatewayPort -Port $Port
Set-Location (Join-Path $PSScriptRoot '../..')
$runtime = Join-Path $env:LOCALAPPDATA 'GCLiveObservatory'
New-Item -ItemType Directory -Force $runtime | Out-Null
$stop = Join-Path $runtime "stop-$Port"
if (Test-Path -LiteralPath $stop) { Remove-Item -LiteralPath $stop }
$cli = @('-m', 'observatory', 'live', '--symbol', $Symbol, '--expiry', $Expiry,
    '--mode', $Mode, '--batch-size', $BatchSize, '--gateway-port', $GatewayPort, '--port', $Port, '--stop-file', $stop)
if ($Calendar) { $cli += @("--calendar", $Calendar) }
if ($ExpiryAttested) { $cli += "--expiry-attested" }
if ($MboEntitled) { $cli += "--mbo-entitled" }
if ($Capture) {
    $cli += @('--capture', (Join-Path $runtime 'live.sqlite'), '--retention-days', $RetentionDays, '--retention-attested')
}
uv run --locked @cli
if ($LASTEXITCODE -ne 0) { throw "Observatory exited with code $LASTEXITCODE. Check contract, storage access and the startup message." }
