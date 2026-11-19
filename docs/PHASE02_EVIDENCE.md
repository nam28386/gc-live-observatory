# Phase 02 validation

Phase 02 extends the exact Phase 01 base `51aa8a4d32b42916d7cd2cd226c31cd7b424eebf` on `feat/phase02-microstructure-engine`. The implementation commit is `99936e5c726294c4338d221625b2a9213d1667b0`. The follow-up evidence commit includes an additional Chromium assertion for the evidence inspector. The pinned vendor API/MBO revision is `581cb6b3706c93ccd1667b32234e904654a44728`, confirmed from the official repository.

## Entry points and acceptance

- `uv run --locked python -m observatory mock --mode C` starts the real synthetic gateway, shared collector and loopback API/UI. Mode A remains the default.
- `uv run --locked python -m observatory.replay <database> <session-id> --mode C` reads journal records in ID order and reconstructs the selected measurement state.
- `observatory/books.py` normalizes MBP/MBO and owns book construction. `observatory/collector.py` assembles subscriptions and bounds the recovery RPC. `observatory/microstructure.py` owns feature validity and evidence. The existing chart and journal paths remain in `observatory/static/`, `observatory/web.py` and `observatory/journal.py`.
- The [Windows operator procedure](WINDOWS_LIVE_VALIDATION.md) explains modes, seed confirmation and the explicit DBO probe. The [measurement contract](MEASUREMENT_INTEGRITY.md) defines eligibility, formulae, clocks and limitations.

| Gate | Outcome | Evidence |
| --- | --- | --- |
| G0 source | PASS | Exact base, verified ancestry and original 37-test run. |
| G1 protocol | PASS, synthetic | Genuine loopback sockets in A/B/C, correlated ACK and recovery/probe responses, fallback on rejection. |
| G2 MBP | PASS, synthetic | Absolute updates, Reset/Image, sequence continuity, snapshot deadline, bounded retry, buffered replay and retained gap evidence. |
| G3 MBO | PASS, synthetic | Patch, authoritative Fill, censoring, observed epoch, duplicate rejection, exact/inferred split and no double decrement. |
| G4 measurement | PASS, synthetic | Golden OFI/depth/rate tests, fixed anchors, gap and mixed-clock exclusion, cutoff and label timing. H-GC-001 remains UNRESOLVED. |
| G5 storage | PASS, synthetic | Existing crash/restart fixture, versioned stateless journal payloads, persistent MBP recovery and deterministic replay. |
| G6 full stack | PASS, synthetic | Fake vendor socket through collector/books/API and recorder in A/B/C. |
| G7 UI | PASS, Chromium | A and C startup, four updating canvases, recovery/gap history and analytic state. Mode C exercised the evidence inspector. |
| G8 performance | PASS, synthetic benchmark | Decoder/engine and durable write tests measured separately. Recovery buffer saturated at its bound and invalidated measurements until recovery. |
| G9 security | PASS, tested scope | Loopback guards, local operator probe, no arbitrary browser RPC, existing order/account rejection, tracked path/signature scans. |
| G10 CI | UNVERIFIED | Ubuntu/Windows Python 3.12 workflow. GitHub API refused the run query. |
| G11 real market | UNVERIFIED | Windows gateway, active GC expiry, MBP and DBO entitlements await local confirmation. |

## Executed commands

uv used a writable local cache. Chromium used the installed Linux executable.

| Command | Exit | Evidence |
| --- | ---: | --- |
| `uv lock --check` | 0 | Dependency lock verified. |
| `PR00_REQUIRE_BROWSER=1 uv run --locked python -m pytest -q` at Phase 01 | 0 | 37 passed, zero skips. |
| Same command on Phase 02 | 0 | 58 passed, zero skips. |
| `uv run --locked python -m pytest -q -m 'not browser'` | 0 | 56 passed, two deselected. |
| `PR00_REQUIRE_BROWSER=1 uv run --locked python -m pytest -q -m browser -rs` | 0 | Two passed, 56 deselected. |
| `node --check observatory/static/dashboard.js` and `git diff --check` | 0 each | JS syntax and whitespace. |
| `uv run --locked python -m scripts.benchmark_phase02 --events 10000 --writes 1000` | 0 | Measured synthetic rates below. |
| `git push -u origin feat/phase02-microstructure-engine` and `git ls-remote --exit-code origin refs/heads/feat/phase02-microstructure-engine` | 0 each | Branch published and read back. |
| `gh pr create --draft --base feat/phase01-live-ready --head feat/phase02-microstructure-engine …` | 1 | GraphQL Forbidden. No PR URL observed. |
| `gh run list --branch feat/phase02-microstructure-engine --json headSha,status,conclusion,url --limit 5` | 1 | Actions API Forbidden. No run URLs observed. |

## Synthetic performance

The 500-level engine test decoded and applied 10,000 MBP JSON updates at **18,816 events/s**, with **70.55 µs p95** application time, **0.526 CPU seconds**, and **3,242,834 bytes** Python peak during a separate instrumented pass. The aggregate window stopped retaining events at **8,192** and suppressed eligible output when saturated. The steady book run rejected zero transitions.

SQLite sustained **1,486 durable writes/s** for 1,000 inserts, at **0.673 ms mean commit path**. An earlier per-event connection run measured 450 writes/s. Keeping an idle WAL connection avoids a checkpoint whenever the writer is the last handle, while preserving one FULL-synchronous transaction per record. The receiver awaits each durable write, so SQLite remains the throughput bound in capture mode.

The recovery pressure case reached **512 buffered deltas**, recorded one overflow and discarded **513** buffered transitions. A snapshot covering the highest discarded sequence recovered the state. The local snapshot call took **24.87 µs** with **51.3 ms modeled observation time** from gap to recovery. These are separate local measurements. They exclude gateway/network latency and do not establish sustainable production capture rates on Windows.

## Publication and remaining evidence

The branch is stacked on Phase 01. Git write succeeded. GitHub refused Draft PR creation and Actions lookup. Verify real Windows Trade/BBO/MBP, run the operator-controlled DBO probe, test live recording throughput and confirm feed rights before using real data. No authorized real-data H-GC-001 evidence or out-of-sample result exists.
