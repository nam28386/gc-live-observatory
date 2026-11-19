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

## Phase 02 depth and DBO

Validate mode A first. For MBP run and smoke with `-Mode B`. Confirm that generation and sequence appear, the book is VALID_BOOK, valid MBP updates arrive and the history retains any actual gaps after recovery. Avoid intentionally disrupting an active production gateway to induce a recovery.

For DBO, open the chosen GC contract in Fxaurum to establish BBO, run the local entitlement command, then use mode C only after a positive result.

```powershell
uv run --locked python -m observatory mbo-probe --symbol $Symbol --expiry $Expiry
.\scripts\windows\run-live.ps1 -Symbol $Symbol -Expiry $Expiry -Mode C -MboEntitled
# In a second console with the same values:
.\scripts\windows\smoke.ps1 -Symbol $Symbol -Expiry $Expiry -Mode C
```

The probe emits only the entitlement result. A negative probe or a failed probe leaves B usable. Verify an observed DBO update and look at exact and inferred Fill counts independently. LIVE on Windows and both depth entitlements remain unverified until checked on the local gateway.
