# Phase 05 Windows commissioning and reliability evidence

**PHASE05_PARTIAL**. The Phase 05 branch descends from `625db1943499f333599127826298346d839f2316` and the earlier Phase 03 commit `177ee756169144793d389a71c3f421464f7559bf`. The receipt and recovery correction is in `ad1fa47`, and the main accounting and local evidence implementation is in `3b11f2974575b07e3d735603711fc0c8fd4f870a`.

## Historical CI failure and reproduced mechanism

The supplied [Phase 04 Actions run](https://github.com/nam28386/gc-live-observatory/actions/runs/44522616191) is **FAILURE**. Ubuntu succeeded. Windows reported 81 passed, four failed and two deselected. The supplied diagnostics showed a 64-frame saturated receive queue, three RPC timeouts and retry exhaustion with no matched receipt timing. Direct log access was forbidden. GitHub's web route returned 404 in this environment, and a separate authenticated check-runs query was forbidden. Those access results do not change the historical failure.

Phase 04 disabled batching whenever a snapshot deadline was active. Earlier data therefore incurred individual FULL transactions precisely when the FIFO had to admit the reply. The regression fixture creates a bounded four-frame queue, sends a real loopback depth gap, then emits 32 finite BBO frames before the matching Image, and adds a fixed 34 ms cost to each BBO storage transaction. The archive of the exact Phase 04 SHA failed that test on Linux with `RECOVERY_FAILED`. Phase 05 kept the same FULL durability and passed with one request, 32 BBO records before the snapshot and one durable recovery. The controlled writer-block test separately distinguishes a reply received before the deadline from delayed application. This establishes the mechanism across the chosen synthetic conditions. The original Windows filesystem timings and gateway internals remain unobserved here.

### RPC contract

One bounded request is active per connection. It carries request ID, connection epoch, contract, MBP generation and recovery epoch. Lifecycle events record creation, send, receipt, queue admission, ordered dispatch, validation, durable commit and application. Time values are offsets in milliseconds within one process's monotonic domain. Receipt expiry starts at send. The dispatch budget starts at socket receipt, and the durable publication budget starts at dispatch. Exceeding the latter two produces delay diagnostics without treating a timely reply as a receipt timeout or applying it ahead of earlier frames.

A generation-mismatched, malformed or insufficient snapshot cannot validate the book. A timeout while locally pressured is classified separately from an unpressured missing receipt. A missing receipt alone cannot tell whether the gateway received or replied to a request. Failed commit does not mark a snapshot committed or applied. Retry exhaustion leaves MBP invalid. The recorded historical gap remains after successful recovery.

## Capture accounting and evidence schema

`socket_received_frames` includes the subscribe exchange and subsequent wire receipts. `admitted_queue_frames` covers the bounded post-ACK queue. `settled_queue_frames` counts frames accounted for after ordered processing, including control and rejection outcomes. `normalized_market_frames` counts normalized selected market records, `durable_market_records` counts committed Trade/BBO/MBP/MBO rows, and `visible_market_frames` counts market records behind the publication boundary. Control, gap, coverage and evidence rows add to total recorder records. These counters have different denominators. Receipt uncertainty is a lower bound for already observed frames that cannot be reconciled at disconnect, including a pending lookahead. The system cannot count all upstream data that never reached the application. Known-gap history remains independent of that count.

The versioned `gc-observatory-pilot-evidence` schema 1 includes source and contract identity, exact recorded SHA, session and durable record range, pinned vendor and measurement versions, calendar digest if present, strict prefix digest, record and gap totals, lifecycle counts, sanitized diagnostic gates and replay-state digest. Verification reopens the selected committed prefix, checks coherence and reconstructs its state. Supplying the same-position private checkpoint additionally checks live/replay parity. Verifying the packet proves coherence of retained local evidence. Real gateway, entitlement and production capture gates stay unverified. [Operator commands](WINDOWS_LIVE_VALIDATION.md#phase-05-field-commissioning-and-evidence)

## Comparable Linux benchmark

All workloads used actual loopback socket, collector, API and SQLite FULL, batch 32. Re-execute via `uv run --locked python -m scripts.benchmark_phase03`.

| Workload flags | Market sent / durable | Phase 04 → Phase 05 market/s | Phase 05 received/s | Phase 05 replay rows/s |
| --- | ---: | ---: | ---: | ---: |
| `--mode B --cycles 2000 --batch-size 32` | 2,128 / 2,128 | 4,861 → 4,449 | 4,451 | 10,648 |
| `--mode C --cycles 2000 --batch-size 32` | 4,129 / 4,129 | 5,394 → 4,784 | 4,785 | 12,315 |
| `--mode B --cycles 240 --gap-every 31 --reconnect-every 80` | 262 / 262 | 385 → 394 | 409 | 7,026 |
| `--mode B --cycles 30000 --levels 750 --pace .025 --burst 100` | 31,878 / 31,878 | 2,647 → 2,593 | 2,593 | 5,937 |

Burst B measured commit **13.55/34.87/48.25 ms**, visibility **13.59/35.16/48.62 ms** and queue residence **8.44/23.89/33.58 ms** at p50/p95/p99. The queue reached 64, with 0 uncertain receipts in the reconciled workload. Total measured process CPU was 0.719 s, peak RSS 51,456 KiB and logical DB file growth about 2.69 MB/s. There were 4,476 all-kind durable records/s, including non-market rows. The reconnect workload had seven applied RPC snapshots, ten transport/MBP gap records and zero uncertain receipts after fixing batch-close accounting. Its last response took 0.95 ms send-to-receipt, 0.36 ms receipt-to-dispatch and 3.00 ms receipt-to-publication.

Short burst rates were about 8–11 percent lower than Phase 04, while the paced workload was about two percent lower. Phase 05 adds per-frame accounting and latency measurement and preserves durable lifecycle data. The benchmark does not isolate each cost from run-to-run disk variance. These measurements certify neither an uninterrupted real exchange session nor production capacity. **TESTED_SYNTHETIC_CAPACITY** observed on Linux. **TESTED_LOCAL_WINDOWS_CAPACITY** and **OBSERVED_REAL_CAPTURE_RATE** remain unverified. **UNVERIFIED_PRODUCTION_CAPACITY** remains true.

## Commands and gates

| Command | Exit | Evidence |
| --- | ---: | --- |
| `uv lock --check` with writable uv cache | 0 | Exact dependency lock checked |
| `PR00_REQUIRE_BROWSER=1 uv run --locked python -m pytest -q --tb=short` before Phase 05 | 0 | 87 passed |
| Same command on Phase 05 | 0 | 99 passed, zero failures, zero skips |
| `uv run --locked python -m pytest -q tests/test_phase05_rpc.py --tb=short` | 0 | Repeated pressure, classification, reconnect and commit-failure cases |
| Archived Phase 04, selected finite-burst regression | 1 | One failure, recovery exhausted |
| `node --check observatory/static/dashboard.js` and `git diff --check` | 0 each | Syntax and whitespace |
| `gh pr create --draft …` | 1 | Forbidden |
| GitHub Actions API and alternate check-run lookup | 1 | Forbidden |

| Gate | Result |
| --- | --- |
| G0 Source | PASS, exact base and verified ancestry |
| G1 Windows CI | BLOCKING, final-SHA Windows job not observable |
| G2 Ubuntu CI | BLOCKING, final-SHA Ubuntu/Chromium jobs not observable through GitHub |
| G3 RPC · G4 MBP | PASS in finite loopback tapes, including before/after pressure reproduction |
| G5 Capture · G6 Durability | PASS on tested synthetic workloads and rollback/crash scenarios |
| G7 Replay | PASS for tested fixed-prefix parity and evidence verifier |
| G8 Operator | PARTIAL, loopback CLI and packet checked on Linux; actual Windows runner unverified |
| G9 Research integrity | PASS technical, H-GC-001 remains UNRESOLVED |
| G10 Dashboard | PASS on local Chromium |
| G11 Security | PASS within tested scope, loopback and read-only RPC, no tracked capture or credential signatures |
| G12 Real gateway · G13 Real depth · G14 Real capture | UNVERIFIED |

**Major remaining work** is the authorized Windows A/B/C pilot and a local Windows capacity benchmark. **Minor limitation** is that legacy captures lack the attestation required for strict packets. No scientific evaluation was run on real market data. Draft PR creation was denied. Use the compare view against Phase 04 for review.
