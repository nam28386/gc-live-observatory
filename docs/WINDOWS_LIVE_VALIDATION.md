# Windows LIVE validation

Use Windows PowerShell from the repo root with uv available. Open Fxaurum locally and confirm market-data entitlement, the exact COMEX GC symbol and exchange expiry against your terminal or exchange contract specification. Use an active trading interval. Keep gateway and browser service loopback-only. Keep capture disabled unless retention and usage rights have been confirmed.

```powershell
# Replace both strings with the values independently confirmed for your contract.
$Symbol = '<COMEX GC symbol>'
$Expiry = '<YYYY-MM-DD>'
.\scripts\windows\install.ps1
.\scripts\windows\preflight.ps1
.\scripts\windows\run-live.ps1 -Symbol $Symbol -Expiry $Expiry
```

In a second console, use the same variables and run:

```powershell
.\scripts\windows\smoke.ps1 -Symbol $Symbol -Expiry $Expiry
.\scripts\windows\stop.ps1
```

The run script stays in the foreground so Ctrl+C is also available. Open the dashboard on port 8000 on IPv4 loopback. If either port differs, supply the same `-GatewayPort` and `-Port` values throughout. `-Capture -RetentionDays 7` opts into SQLite under `%LOCALAPPDATA%\GCLiveObservatory`.

Smoke checks gateway and dashboard IPv4 reachability and loopback-only listeners, correlated ACK, configured expiry and matched COMEX symbol, fresh eligible Trade and BBO observations, non-crossed spread and timestamp bounds. Verify the configured expiry against the exchange independently because the Trade/BBO frames do not carry expiry. Review the dashboard for timestamps, coverage and stale or recorder diagnostics. The read-only allowlist is covered by the automated suite. Record only non-sensitive pass/fail summaries, with no captured events, prices, credentials or account material.

**Windows LIVE status remains UNVERIFIED** until this procedure succeeds with an actual local gateway.
