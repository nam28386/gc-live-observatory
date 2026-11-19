param([int]$Port = 8000, [switch]$Synthetic)
$ErrorActionPreference = 'Stop'
$status = Invoke-RestMethod "http://127.0.0.1:$Port/api/status" -TimeoutSec 3
$expected = if ($Synthetic) { 'MOCK' } else { 'LIVE' }
if ($status.mode -ne $expected) { throw "Expected a running $expected observatory." }
$stop = Join-Path $env:LOCALAPPDATA "GCLiveObservatory/stop-$Port"
New-Item -ItemType File $stop -Force | Out-Null
$deadline = (Get-Date).AddSeconds(20)
do {
    Start-Sleep -Milliseconds 250
    if (!(Test-Path -LiteralPath $stop)) { Write-Output 'Stopped'; return }
} while ((Get-Date) -lt $deadline)
throw 'Graceful stop could not be confirmed. Check the foreground launcher and recorder status.'
