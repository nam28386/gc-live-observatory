param(
    [int]$Port = 8000,
    [ValidateSet('A','B','C')][string]$Mode = 'A',
    [switch]$Capture,
    [string]$CapturePath,
    [double]$Interval = 0.16,
    [double]$StaleAfter = 3,
    [int]$DisconnectAfter = 0,
    [int]$TapeCycles = 0,
    [switch]$NoSyntheticDbo
)
$ErrorActionPreference = 'Stop'
if ($CapturePath -and !$Capture) { throw 'Use -Capture with -CapturePath.' }
if ($CapturePath) { $CapturePath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($CapturePath) }
& (Join-Path $PSScriptRoot 'preflight.ps1') -Port $Port -Synthetic
Set-Location (Join-Path $PSScriptRoot '../..')
$runtime = Join-Path $env:LOCALAPPDATA 'GCLiveObservatory'
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
$stop = Join-Path $runtime "stop-$Port"
if (Test-Path -LiteralPath $stop) { Remove-Item -LiteralPath $stop }
$cli = @('-m', 'observatory', 'mock', '--port', $Port, '--mode', $Mode,
    '--interval', $Interval.ToString([cultureinfo]::InvariantCulture),
    '--stale-after', $StaleAfter.ToString([cultureinfo]::InvariantCulture), '--stop-file', $stop)
if ($Capture) {
    if (!$CapturePath) { $CapturePath = Join-Path $runtime "mock-$Port.sqlite" }
    $cli += @('--capture', $CapturePath)
}
if ($DisconnectAfter) { $cli += @('--disconnect-after', $DisconnectAfter) }
if ($TapeCycles) { $cli += @('--tape-cycles', $TapeCycles) }
if ($NoSyntheticDbo) { $cli += '--no-synthetic-dbo' }
uv run --locked @cli
if ($LASTEXITCODE -ne 0) { throw "Synthetic observatory exited with code $LASTEXITCODE." }
