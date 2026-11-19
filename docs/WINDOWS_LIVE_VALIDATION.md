# Windows LIVE validation

## Install and verify a Windows source RC

Use Windows 10/11 with PowerShell 7 (or an existing Windows PowerShell console for the launch scripts), [uv](https://docs.astral.sh/uv/getting-started/installation/) on PATH, and Python 3.12. The installer can acquire Python 3.12 and the third-party dependencies from the locked configuration. The ZIP contains source and launchers. The first installation needs dependency downloads.

Unzip into a new user-writable directory such as `%LOCALAPPDATA%\GCLiveObservatoryReleases\<SHA>`. Check the ZIP with `Get-FileHash -Algorithm SHA256 <zip>` against the checksum published alongside it. Open PowerShell at the extracted root and run:

```powershell
.\scripts\windows\install.ps1
.\scripts\windows\preflight.ps1 -Synthetic
.\scripts\windows\run-mock.ps1 -Mode C
```

Open [the dashboard](http://127.0.0.1:8000/) and check the **MOCK / SYNTHETIC** label and M1/M5/M15/H1 charts. In a second terminal from the same release directory:

```powershell
.\scripts\windows\smoke.ps1 -Synthetic -Mode C
.\scripts\windows\stop.ps1 -Synthetic
```

`uv run --locked python -m scripts.windows_acceptance` runs the bounded subprocess acceptance against fake data, including all modes, recovery, capture, evidence and restart. It uses a temporary data directory. Manual capture writes only when `-Capture` is passed, under `%LOCALAPPDATA%\GCLiveObservatory`. Synthetic capture uses a separate database from LIVE.

To upgrade, stop the foreground app gracefully, verify and extract the new ZIP to a separate directory, install, and run synthetic smoke first. Keep the previous directory for rollback. Record the source SHA and session in each evidence packet. Back up a stopped licensed capture only if permitted and keep that backup local. The launch scripts reopen a fresh session with a restart gap. Retention prunes older rows according to the configured days, and strict verification rejects pruned prefixes. To roll back, stop the new process and start the previously verified directory with the same explicit contract and ports.

## Authorized LIVE commissioning

Use PowerShell from the repo root or extracted release with uv available. Open Fxaurum locally and confirm market-data entitlement, the exact COMEX GC symbol and exchange expiry against your terminal or exchange contract specification. Use an active trading interval. Keep gateway and browser service loopback-only. Keep capture disabled unless retention and usage rights have been confirmed.

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

The run script stays in the foreground so Ctrl+C is also available. Open the dashboard on port 8000 on IPv4 loopback. If either port differs, supply the same `-GatewayPort` and `-Port` values throughout. `-Capture -RetentionAttested -RetentionDays 7` opts into SQLite under `%LOCALAPPDATA%\GCLiveObservatory`.

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

To test local capture after confirming retention rights, use `-Capture -RetentionAttested -BatchSize 32`. For a one-record comparison use `-Capture -RetentionAttested -BatchSize 1`. Both use FULL durability. Inspect throughput, producer backlog and commit p95/p99 during the session. A nonzero backlog is pressure evidence, and a failed recorder invalidates publication. The session end writes a local `capture_summary` record on graceful shutdown. It contains local coverage diagnostics rather than a claim of exchange completeness.

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

Add `-Capture -RetentionAttested -BatchSize 32` only after confirming rights. Review queue pressure, uncertain receipt counts, p95/p99 commit and publication latency, and all three depthSnap timing intervals. `--output` can write the sanitized diagnostic to a new private path outside Git. Never include raw event payloads in pilot evidence. Verify loopback stop behavior and retain the report and exact SHA separately from licensed data.

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


### Save a single diagnostic and private checkpoint

For a permitted recording, save both files from one API observation. Use new local paths outside Git. The checkpoint contains market data. Use the session ID and durable position in the diagnostic, then stop the app and verify the retained prefix.

```powershell
uv run --locked python -m observatory.diagnose --port 8000 --require-mode B --output $Diagnostic --checkpoint-output $Checkpoint
.\scripts\windows\stop.ps1
uv run --locked python -m observatory.evidence build $Session $Database --diagnostic $Diagnostic --checkpoint $Checkpoint --output $Packet
uv run --locked python -m observatory.evidence verify $Packet $Database --diagnostic $Diagnostic --checkpoint $Checkpoint
```

The sanitized packet includes observed receipt/admission/settlement and publication counters, queue and latency summaries, gap counts, and the operator's recorded retention and expiry basis when present. An assertion of retention or expiry attestation records an operator declaration. Use `-ExpiryAttested` at launch only after independently verifying expiry, and `-RetentionAttested` with `-Capture` only after confirming rights.

| Symptom | Check |
| --- | --- |
| Gateway absent, wrong port or non-loopback bind | Run preflight against the actual port and check for a 127.0.0.1 listener. |
| Wrong expiry or mismatched contract | Recheck the exchange reference and the configured symbol/expiry. Wire Trade/BBO cannot attest expiry. |
| Wrong ACK or rejected subscription | Review the diagnostic's ACK reason and collector issue before trying the next mode. |
| CLOSED, UNKNOWN, stale or absent trades/BBO | Check each freshness state and calendar coverage. Observe during a verified open interval. |
| Invalid MBP / RPC recovery failure | Review book readiness, permanent gap evidence and the receipt, dispatch and publication stages. |
| Unavailable DBO | Check probe and observed updates independently. Keep B if available. |
| Capture pressure or recorder failure | Review queue occupancy, uncertain receipts, storage status and durable position. Check free space and write permissions. |
| Missing capture authorization | Leave capture off until data-use and retention rights are confirmed. |

## Release closeout and local-data boundary

Use the `windows-py312-source-rc-<full SHA>` Actions artifact for the approved
commit. Extract the Actions wrapper, then verify the **inner** source ZIP
against its sibling `.zip.sha256` file. The build report identifies the SHA,
build OS, Python and Git versions. The ZIP contains stored, unmodified Git
blobs in sorted order with fixed metadata. CI compares two Windows builds of
the same SHA. Compare a specific Linux build with the Windows inner ZIP
before citing cross-platform byte equality.

After installing from the verified extraction, the full synthetic acceptance
checks the A/B/C and fallback paths. The optional repeat command tests four
independent Mode C capture/reopen pairs:

```powershell
uv run --locked python -m scripts.windows_acceptance --expected-sha $Sha
uv run --locked python -m scripts.windows_acceptance --restart-only --restart-repeats 4 --expected-sha $Sha
```

Each pair shares one synthetic SQLite file across two processes on distinct
loopback ports. Each process must finish the finite durable tape, stop with
exit code zero, remove its stop marker and close its listener. The second
session must identify the first as its clean predecessor. Replay and the
sanitized evidence builder verify both prefixes. The synthetic gateway waits
for the requested repair response before intentionally closing the first
connection, including when the collector requests that response after the
market-data tail.

LIVE HTTP history serves a projected gap timeline. Use the local replay CLI
for retained event records. Keep databases, replay output, full checkpoints,
credentials and broker diagnostics outside Git and release artifacts. The
dashboard displays entitled observations on IPv4 loopback. Confirm the
terms of the applicable Fxaurum, Rithmic and exchange market-data agreements
before retaining data or sharing any data or derived material. API
documentation and synthetic entitlement are engineering references. Rights,
LIVE A/B/C, real capture capacity and authorized real out-of-sample validation
require independent operator evidence.
Public visibility does not provide a source-code redistribution license. The
repository currently has no standalone LICENSE file. Confirm redistribution
permissions with the repository owner and applicable data agreements.

CI scans credential signatures and private artifact paths in reachable Git
history and checks an exact-SHA allowlist when building and verifying the
inner ZIP. This scan cannot cover unreachable history, external copies or
arbitrary disguised data. The upload step enumerates the source ZIP,
checksum, build metadata and synthetic operational summaries explicitly.

## Biên bản nghiệm thu từng pilot

Dùng đúng bản ZIP nguồn bên trong Actions wrapper và hai SHA256 do GitHub
công bố cho đúng commit. Kiểm tra wrapper trước khi giải nén, sau đó kiểm tra
ZIP nguồn với sidecar. Có thể so sánh bằng
`(Get-FileHash -Algorithm SHA256 -LiteralPath $Zip).Hash.ToLowerInvariant()`.
Giải nén ZIP nguồn vào thư mục có quyền ghi, đọc `source_sha` trong
`RELEASE.json`, cài uv và chạy `install.ps1` để cài Python 3.12 cùng
dependencies theo lockfile. Chạy synthetic acceptance với
`--expected-sha $Sha` trước khi bật LIVE.

Tạo mã riêng cho mỗi lượt A, B hoặc C. Với recorder tắt, chạy smoke để lưu
diagnostic rồi dừng. Với recorder bật, hoàn tất lấy mẫu tài nguyên trong khi
đang ghi, sau đó lưu diagnostic và checkpoint từ cùng một snapshot trước
khi dừng. Giữ các đường dẫn dưới thư mục cục bộ ngoài Git và sử dụng tệp
mới cho mỗi lượt.

```powershell
$PilotId = [Guid]::NewGuid().ToString('N')
# Chỉ cho lượt LIVE đang capture và đã xác nhận quyền.
.\scripts\windows\measure-pilot.ps1 -Seconds 30 | Set-Content -Encoding utf8 $Resources
uv run --locked python -m observatory.diagnose --port 8000 --gateway-port 9001 --require-mode $Mode --output $Diagnostic --checkpoint-output $Checkpoint
.\scripts\windows\stop.ps1
# $Session và durable_position lấy từ diagnostic. Đọc prefix cục bộ.
uv run --locked python -m observatory.evidence build $Session $Database --diagnostic $Diagnostic --checkpoint $Checkpoint --output $Packet
uv run --locked python -m observatory.evidence verify $Packet $Database --diagnostic $Diagnostic --checkpoint $Checkpoint
```

Dựng biên bản từ diagnostic và checksum ZIP nguồn đã đối chiếu với bản
phát hành. Chỉ thêm `--operator-authorized` khi người vận hành đã xác nhận
quyền chạy pilot trên workstation. Thêm bộ tham số capture bên dưới chỉ khi
đã thu được bằng chứng tương ứng. Với lượt không capture, dùng nguyên mảng
`$PilotArgs` đầu tiên. Nếu phải kiểm tra lại biên bản, dùng cùng đầu vào với
`--verify`.

```powershell
$PilotArgs = @('--pilot-id', $PilotId, '--artifact', $Zip, '--artifact-sha256', $SourceZipSha256, '--diagnostic', $Diagnostic, '--operator-authorized')
# Chỉ thêm khi có capture được cấp quyền.
$PilotArgs += @('--capture-packet', $Packet, '--database', $Database, '--checkpoint', $Checkpoint, '--resources', $Resources)
uv run --locked python -m observatory.pilot @PilotArgs --output $PilotReport
uv run --locked python -m observatory.pilot @PilotArgs --verify $PilotReport
```

Biên bản `gc-observatory-field-pilot` v1 chỉ xuất trường allowlist. Diagnostic
và capture packet giữ nguyên schema. `PASS` trong biên bản có phạm vi
`SYSTEM_OBSERVED` hoặc `SYNTHETIC_TESTED`. Mốc UTC của phiên và các độ trễ
đo bằng monotonic clock được giữ theo nguồn đo. Báo cáo sampler là một cửa
sổ trong phiên, gồm CPU-seconds của process, working set cao nhất trong các
mẫu và kích thước tệp trước/sau. Các số này không đại diện cho disk IOPS
hay throughput toàn sàn. Khi B qua nhưng C không khả dụng, giữ riêng biên
bản B và kết quả C.

Xác minh độc lập cần người được cấp quyền đối chiếu package, workstation,
entitlement và bằng chứng cục bộ. Không gửi DB, private checkpoint, raw
payload hoặc giá. Tách riêng quyền phân phối source, quyền truy cập API,
và quyền giữ/nghiên cứu/chia sẻ dữ liệu CME/COMEX cùng dữ liệu dẫn xuất.
Mỗi quyền chưa có nguồn xác nhận giữ `UNKNOWN`. Phiên bản dependency được
ghim trong `uv.lock`; điều khoản của từng dependency theo metadata của gói
cài đặt. Chủ sở hữu cần quyết định giấy phép source của repository.
