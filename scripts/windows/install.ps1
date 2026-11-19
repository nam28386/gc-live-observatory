$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '../..')
Get-Command uv -ErrorAction Stop | Out-Null
uv sync --locked --python 3.12
if ($LASTEXITCODE -ne 0) { throw 'uv sync failed' }
