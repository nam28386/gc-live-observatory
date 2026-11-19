param([int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$status = Invoke-RestMethod "http://127.0.0.1:$Port/api/status" -TimeoutSec 3
if ($status.mode -ne 'LIVE') { throw 'Expected a running LIVE observatory' }
$stop = Join-Path $env:LOCALAPPDATA "GCLiveObservatory/stop-$Port"
New-Item -ItemType File $stop -Force | Out-Null
$deadline = (Get-Date).AddSeconds(15)
do {
    Start-Sleep -Milliseconds 250
    if (!(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) -and
        !(Test-Path -LiteralPath $stop)) { Write-Output 'Stopped'; return }
} while ((Get-Date) -lt $deadline)
throw 'Graceful stop timed out. Check the run-live console.'
