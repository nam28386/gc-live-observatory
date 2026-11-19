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

## Phase 03 pilot report

Run the existing setup and loopback checks. Run A first, B after A passes and C only after a positive local probe. These commands assume the repo root and operator-confirmed `$Symbol` and `$Expiry`.

```powershell
.\scripts\windows\run-live.ps1 -Symbol $Symbol -Expiry $Expiry -Mode A
.\scripts\windows\smoke.ps1 -Symbol $Symbol -Expiry $Expiry -Mode A
uv run --locked python -m observatory.diagnose --port 8000 --gateway-port 9001
.\scripts\windows\stop.ps1
```

Use separate consoles for the foreground run and the checks. To save an allowlisted diagnostic report add `--output "$env:LOCALAPPDATA\gc-pilot-report.json"` using a new file. The report omits raw events, prices, account fields and gateway logs. TCP reachability, matching ACK and observed trade/depth gates are operational evidence. Confirm the expiry and data entitlement independently using the local gateway. Record only sanitized outcomes.

To test local capture after confirming retention rights, use `-Capture -BatchSize 32`. For a one-record comparison use `-Capture -BatchSize 1`. Both use FULL durability. Inspect throughput, producer backlog and commit p95/p99 during the session. A nonzero backlog is pressure evidence, and a failed recorder invalidates publication. The session end writes a local `capture_summary` record on graceful shutdown. It contains local coverage diagnostics rather than a claim of exchange completeness.

`-Calendar <local JSON>` uses operator-sourced COMEX GC intervals. Use schema version 1, `exchange: COMEX`, `product: GC`, `exchange_timezone: America/New_York`, `version`, `provenance`, explicit UTC `coverage_start` and `coverage_end`, and an ordered `open_sessions` array of `id`, `open` and `close`. Verify coverage against the [CME product specification](https://www.cmegroup.com/markets/metals/precious/gold.contractSpecs.html) and [holiday schedule](https://www.cmegroup.com/tools-information/holiday-calendar.html). The app returns `UNKNOWN` when there is no calendar coverage or the source is Mock. Expiry and roll selection remain explicit operator inputs.

Real Windows gateway, MBP and DBO remain **UNVERIFIED** until the local pilot is run.

## Phase 04 commissioning

Use the existing run-live and smoke scripts in separate terminals. Validate A first, then B. Only try C after a positive local `mbo-probe`. Check COMEX, symbol and expiry against the authorized gateway and exchange reference before passing the optional expiry-attestation flag.

```powershell
.\scripts\windows\run-live.ps1 -Symbol $Symbol -Expiry $Expiry -Mode A
.\scripts\windows\smoke.ps1 -Symbol $Symbol -Expiry $Expiry -Mode A
uv run --locked python -m observatory.diagnose --port 8000 --gateway-port 9001 --require-mode A --wait-seconds 30 --expiry-verified
.\scripts\windows\stop.ps1
```

For B, substitute `-Mode B` and `--require-mode B`. The smoke script now requires a valid OFI window in addition to MBP progress. For C, use `-Mode C -MboEntitled` only after a positive probe, then `--require-mode C`. An absence of DBO entitlement leaves A/B usable. Compare the reported exact and inferred counts without requiring an inferred fill to occur. The probe status in a report is an operator assertion, with observed stream readiness reported separately.

Add `-Capture -BatchSize 32` only after confirming rights. Review queue pressure, uncertain receipt counts, p95/p99 commit and publication latency, and all three depthSnap timing intervals. `--output` can write the sanitized diagnostic to a new private path outside Git. Never include raw event payloads in pilot evidence. Verify loopback stop behavior and retain the report and exact SHA separately from licensed data.

## Phase 05 field commissioning and evidence

1. Verify the clean checkout uses the exact approved SHA. Run `.\scripts\windows\install.ps1` from the repository root.
2. Check contract and expiry independently. Run `.\scripts\windows\preflight.ps1`, then `.\scripts\windows\run-live.ps1 -Symbol $Symbol -Expiry $Expiry -Mode A`. Leave capture disabled until retention and usage rights are confirmed.
3. In a second terminal, run `.\scripts\windows\smoke.ps1 -Symbol $Symbol -Expiry $Expiry -Mode A`. Check the four charts update. To save the diagnostic on success or readiness timeout, add `-DiagnosticPath $Diagnostic` using a new local path outside Git. Only add `-ExpiryAttested` after independently checking the exact expiry.
4. Repeat B after A. Confirm fresh MBP updates and valid OFI. Run the existing operator-only `uv run --locked python -m observatory mbo-probe --symbol $Symbol --expiry $Expiry` before C. Only use C with `-MboEntitled` after a positive result. Preserve usable A/B if C is unavailable.
5. Review the requested/effective mode, queue age and uncertain counts, and RPC receipt/dispatch/publication timing. Use `.\scripts\windows\stop.ps1` to verify graceful shutdown.

The diagnostic is also available independently without capture:

```powershell
uv run --locked python -m observatory.diagnose --port 8000 --gateway-port 9001 --require-mode A --wait-seconds 30
```

Run the full socket-to-recorder-to-API benchmark using synthetic data before a permitted recording:

```powershell
uv run --locked python -m scripts.benchmark_phase03 --mode B --cycles 2000 --batch-size 32
uv run --locked python -m scripts.benchmark_phase03 --mode B --cycles 30000 --levels 750 --pace .025 --burst 100
```

The output labels the local Windows synthetic capacity separately from observed real capture rate and unverified production capacity. Windows RSS uses working set. Database growth is logical file bytes/s and does not measure physical disk write bandwidth.

After a **permitted** capture, the diagnostic contains a session ID and durable position. Keep the original database outside Git. The following commands pin that exact prefix. A private checkpoint is optional and must be saved from `/api/snapshot` at the **same** session and position. It can contain prices and remains local. Use UTF-8 for the JSON files.

```powershell
uv run --locked python -m observatory.evidence build $Session $Database --diagnostic $Diagnostic --output $Packet
uv run --locked python -m observatory.evidence verify $Packet $Database --diagnostic $Diagnostic
```

If using a private checkpoint, add `--checkpoint $Checkpoint` to both calls. The verifier compares the reconstructed market state at its recorded observation time. With no checkpoint it reports successful prefix reconstruction and leaves live/replay parity unverified. The packet contains allowlisted aggregate counts, digests and gate summaries. Share it only through the approved evidence channel. Keep the database, private checkpoint and source payloads local.
