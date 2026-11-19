# Phase 03 validation and capture contract

**Release classification: PHASE03_PARTIAL.** The code and Linux synthetic validation are available. CI at the final SHA and physical Windows checks remain separate gates.

The branch `feat/phase03-live-capture-reliability` descends from Phase 02 `546234267b20d5842c0ef99694e440e66c7b5e6e`. Recovery/test correction is in `32d2645`. The main implementation is `72b06db4ce8a111d4dc0c0145230df2a309adda7`.

## Windows CI investigation

The supplied [Phase 02 Actions run](https://github.com/nam28386/gc-live-observatory/actions/runs/44512109997) reported Ubuntu success and Windows 53 passing, three failing and two deselected. GitHub API returned Forbidden on the failed-log and job queries, so the observed Windows regression was not independently reproduced here.

Source inspection identified two concrete failure paths. The three tests limited their wait by dashboard publication count, coupling readiness to polling cadence and per-record SQLite latency. They also could leave lifespan while recovery was in flight. The mock handler did not handle an expected peer closure during a reply, which explains the possible teardown trace but does not establish what caused the recovery observation failure on Windows. The repaired tests wait on valid book, observed fills where applicable and the persisted recovery marker. Controlled fixtures hold a depthSnap reply while the peer closes, force a reply beyond its original deadline with the next request as the barrier, inject malformed or wrong-generation snapshots and test three successive disconnects. ACK and recovery deadlines now govern receive operations, leaving local commit time separate. Phase 03 triggers Ubuntu and Windows CI and repeats recovery regressions on Windows.

## Durability and replay

- A selected, parsed market frame can be staged with its normalized record and control/evidence sidecars. It becomes accepted for the published snapshot when its SQLite WAL FULL transaction commits. The browser/API snapshot takes a publication lock. Historical readers see committed rows through SQLite WAL.
- Capture defaults to 32 frames per group with a four-millisecond gather window and approximately two MiB of wire payload per group. Recovery control frames get their own publication boundary. The recorder has one persistent writer connection and one in-flight transaction. A full group backpressures the receiver. Bounded normalized staging fails closed if its limit is exceeded. A failed transaction stops collection, suppresses tentative views and leaves prior durable history intact.
- `--batch-size 1` keeps per-record FULL-synchronous transactions. Commit latency, feature availability, backlog and high-water telemetry have separate denominators. Frames observed by the receiver and records committed are separate counts.
- Shutdown waits for an admitted transaction. An unclean exit preserves the last committed prefix and records a restart gap on next open. Frames still in transport buffers or in an uncommitted batch have uncertain coverage. MBP transport/sequence gaps and recovery markers persist, and snapshots do not restore historical continuity. DBO completeness is constrained by the vendor protocol.
- Capture provenance includes contract/expiry, vendor revision, producer SHA when available, schema/clock metadata, retention and durability settings. The same SQLite transaction advances the durable record ID. An additive metadata table detects retained-prefix loss. Old captures remain readable but are unattested for strict replay and extraction.
- Replay pins a WAL reader snapshot and verifies a selected session and upper durable ID. It uses recorded event clocks. The parity tests compare book, coverage, measurements and cards at that position, with runtime latency and external entitlement assertions excluded. A controlled socket tape verifies both the H-GC-001 cutoff and +30s label against live and replay. Strict replay rejects pruned or truncated prefixes.

## Local validations and benchmark

All data generated in this phase were synthetic. Browser tests exercised modes A and C, four canvases, operations fields, recovery, recorder state and the evidence inspector.

| Command | Exit | Observed result |
| --- | ---: | --- |
| `uv lock --check` with writable uv cache | 0 | Lock verified |
| `PR00_REQUIRE_BROWSER=1 uv run --locked python -m pytest -q` at Phase 02 | 0 | 58 passed |
| Same suite on Phase 03 | 0 | 76 passed, including two Chromium cases |
| `uv run --locked python -m pytest -q tests/test_phase03_capture.py -k abrupt_exit --durations=2` | 0 | Both passed. Each process/restart test took 0.07–0.12 s. The interrupted batch retained zero rows, the committed batch retained five. |
| `node --check observatory/static/dashboard.js` and `git diff --check` | 0 each | JavaScript syntax and whitespace |
| `git push origin feat/phase03-live-capture-reliability` and `git ls-remote --exit-code origin refs/heads/feat/phase03-live-capture-reliability` | 0 each | Remote branch written and read back |
| `gh pr create --draft --base feat/phase02-microstructure-engine --head feat/phase03-live-capture-reliability …` | 1 | GraphQL Forbidden |
| `gh run view 44512109997 --log-failed` and Actions API run/job lookups | 1 each | API Forbidden |

The `scripts.benchmark_phase03` workload runs a real loopback WebSocket through decode, journal, books/features, FastAPI snapshot and WebSocket publication, then reconciles SQLite counts and replay. Metrics report application receipt onward. Publisher speed measures synthetic offer rate and can include buffering.

| Workload | Market records sent / committed | Durable market events/s | Application completion |
| --- | ---: | ---: | ---: |
| Phase 02-like before recorder changes, B 2,000 cycles | 2,128 / 2,128 | 714 | 2.980 s |
| Phase 03 B, batch 1 | 2,128 / 2,128 | 627 | 3.392 s |
| Same Phase 03 B, batch 32 | 2,128 / 2,128 | 5,222 | 0.407 s |
| Mixed C, batch 32 | 4,129 / 4,129 | 5,919 | 0.698 s |
| Paced Trade/BBO A, batch 32 | 1,500 / 1,500 | 4,231 | 0.355 s |
| Paced B, 1,500-level image, 30,000 cycles | 31,878 / 31,878 | 2,529 | 12.607 s |
| B, 240 cycles, gaps every 31, reconnect every 80 | 262 / 262 | 391 | 0.670 s |

The B comparison offered approximately 38,316 versus 32,626 frames/s from the synthetic sender. Batch 1 processing p50/p95/p99 was 990/2,182/3,741 µs. Batch 32 processing was 60/103/176 µs, excluding the subsequent transaction in the batched processing timer. The more comparable receive-to-visibility percentiles were 1.01/5.20/7.55 ms versus 4.47/8.80/10.14 ms. Receive-to-commit was 0.91/5.06/7.31 ms versus 4.43/8.53/10.11 ms. Peak process RSS was approximately 50 MiB for both, database size about 1.21 MiB, and replay saw 2,140 versus 2,136 total records including control and derived rows. Sampled backlog high-water was one versus 33 staged records. Benchmarks recorded zero rejected frames. The reconnect workload had three connections and seven correlated recovery replies, plus explicit gap history. It replayed 303 total records. Controlled crash workload counts and times are in the command table.

These are short Linux runs and do not establish sustained production Windows capacity, latency or full feed completeness.

## Quality, research and outstanding gates

| Gate | Evidence |
| --- | --- |
| G0 Source | PASS, exact base and remote history |
| G1 Windows CI | BLOCKING, local correction and repeated fixtures run, final-SHA Windows job unavailable through API |
| G2 Regression | PASS on Linux, all original 58 and 18 added cases |
| G3 Capture | PASS for limited synthetic workloads with count reconciliation |
| G4 Durability | PASS for tested commit, crash, bounded backlog, rollback and fail-closed cases |
| G5 Replay | PASS for attested synthetic prefixes, including MBP/MBO and H-GC-001 |
| G6 Measurement | PASS for synthetic contracts. Unverified calendar coverage is UNKNOWN, age distribution is sampled while frames arrive, and exact exchange completeness is unavailable. |
| G7 Dataset | PASS for engineering correctness only. Chronological whole-session holdout, purge and 30s embargo, input digests and predeclared paired MSE metric. H-GC-001 remains UNRESOLVED. |
| G8 Windows operator | PARTIAL, scripts and sanitized diagnostics supplied, actual Windows execution pending |
| G9 Dashboard | PASS, Chromium A/C |
| G10 Security | PASS within tested scope, IPv4 loopback, approved read-only RPCs, no arbitrary gateway forwarding, capture outside Git and no tracked captures or standard secret signatures |
| G11 Real gateway | UNVERIFIED |
| G12 Real depth entitlements | UNVERIFIED |

**Major remaining evidence** is the physical Windows A/B/C pilot, vendor entitlement and rights confirmation, and Windows disk/throughput validation. Calendar files require operator verification from supported exchange sources. The dataset uses an offline cutoff with sample times at or before it and records actual feature materialization separately. No online decision-time availability or scientific result is claimed. Follow the [Windows pilot procedure](WINDOWS_LIVE_VALIDATION.md).
