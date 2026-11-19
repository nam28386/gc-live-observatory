# Phase 04 commissioning and wave research evidence

Release classification is **PHASE04_PARTIAL**. The branch descends from Phase 03 `177ee756169144793d389a71c3f421464f7559bf` and preserves Phase 02 `546234267b20d5842c0ef99694e440e66c7b5e6e` in its ancestry. Recovery changes began at `64bde1b`. The workstation and evaluation implementation began at `1e4f8c3facc560805cfa345dfaa37968fe0bee78`.

## CI investigation and fixes

The supplied [historical Phase 03 Actions run](https://github.com/nam28386/gc-live-observatory/actions/runs/44516476041) is **FAILURE**. Ubuntu reported 73 passed, one failed and two deselected. Windows reported 69 passed, five failed and two deselected. GitHub API refused direct retrieval of that job log. Local Linux reproduced a passing baseline of 76 tests, so the historical runner conditions were not independently reproduced.

- **Virtual clock.** The failing H-GC fixture subtracted 50 seconds from the host monotonic clock. If the runner clock was below 50 seconds, initial stamps were negative and the default saturation comparison against zero prevented candidate creation. The code now compares against the actual sample when no saturation exists. The fixture uses explicit synthetic receive and API snapshot clocks at both cutoffs. The timeout was not extended.
- **depthSnap.** Phase 03 read the socket and waited for recorder commits in the same ordered loop. The timeout was evaluated on dispatch even when a matching reply could have arrived during a commit. On a controlled loopback tape, Phase 04 received the response before the 200 ms deadline while the writer was blocked beyond it, then applied the reply once, durably, after the preceding trade. Separate receipt, dispatch and publication timings verified the mechanism. The capture path still applies messages in wire order. A receipt queue is capped at 64 frames with a bounded backpressure wait. If intake remains blocked, the connection closes after admitted frames drain and a durable gap records the lower bound on uncertain receipts. A second test forces that path and reconciles the admitted frames. This establishes the starvation mechanism in a deterministic fixture. It does not establish which filesystem or scheduling condition dominated the Windows failure.
- **OI and volume.** Separate logical events can share one clock value. The test now compares independent update counts and durable BBO row identities, source OI/volume timestamps and their respective observation stamps. No timestamps are fabricated.

The branch triggers Ubuntu and Windows Actions and repeats recovery fixtures on Windows. Final-SHA results were unavailable through the GitHub Actions API. No actual Windows workflow URL or success is claimed for Phase 04.

## Tested contracts

- The bounded wave engine uses eligible flow, observed quote response and MBP/OFI coverage from the existing path. Journal records each transition and retains the underlying event IDs. Four timeframes copy only their as-of state. Delayed trades do not rewrite a past wave, and gaps, stale BBO and known exchange-session boundaries invalidate affected active waves.
- `GET /api/research` exposes canonical wave and event records. Facts, measurements, hypotheses, missing evidence and contradictory observations are separate. The workstation reports `EXPECTED_RESPONSE_UNAVAILABLE` with a null residual while no eligible reference is loaded.
- `observatory.dataset` preserves the population ledger and rejects future feature samples. Materialization and consumer publication are separate. `observatory.evaluate` has fixed ridge models, a frozen paired holdout MSE metric, strict provenance/timing checks and a locked output for a successful permitted evaluation. Tests checked mechanics and negative controls with synthetic data. The empirical entry point refused to score synthetic data. `H-GC-001 = UNRESOLVED` and `EMPIRICAL_EVALUATION = NOT_RUN_INSUFFICIENT_DATA`.
- Replay compared attested committed-prefix state, including wave transitions and the original H-GC-001 cutoff/label semantics. Browser validation used real loopback Chromium in A and C. The operator diagnostic was executed as a separate Python process against a loopback synthetic server.
- Security checks preserved IPv4 loopback, read-only allowlists, recorder publication rules and the existing RPC denial tests. Searches found no tracked capture files or standard credential signatures.

| Command | Exit | Result |
| --- | ---: | --- |
| `uv lock --check` | 0 | Dependencies unchanged |
| `PR00_REQUIRE_BROWSER=1 uv run --locked python -m pytest -q --tb=short` on Phase 03 | 0 | 76 passed |
| Same suite on Phase 04 | 0 | 87 passed, zero failures, zero skips, including two Chromium cases |
| `node --check observatory/static/dashboard.js`, `git diff --check` | 0 each | Syntax and whitespace |
| `uv run --locked python -m scripts.benchmark_phase03` with workloads below | 0 | Synthetic capture reconciliation and replay |
| `gh pr create --draft …` and one authorized REST POST to `/pulls` | 1 each | Both Forbidden |
| `gh run view 44516476041 --log-failed` and final-SHA Actions lookup | 1 each | Forbidden |

## Full-path Linux benchmark

Run the same loopback benchmark with `--mode B --cycles 2000`, `--mode C --cycles 2000`, `--mode B --cycles 240 --gap-every 31 --reconnect-every 80`, or `--mode B --cycles 30000 --levels 750 --pace .025 --burst 100`. All use batch 32.

| Scenario | Sent / committed market frames | Phase 03 → 04 durable/s | Phase 04 replay rows/s | Wave transitions/s |
| --- | ---: | ---: | ---: | ---: |
| MBP burst | 2,128 / 2,128 | 5,222 → 4,861 | 9,684 | 18.28 |
| Mixed MBP/DBO | 4,129 / 4,129 | 5,919 → 5,394 | 12,039 | 10.45 |
| Recovery/Reconnect | 262 / 262 | 391 → 385 | 7,812 | 0 |
| Paced 1,500-level MBP | 31,878 / 31,878 | 2,529 → 2,647 | 6,018 | 0.66 |

Phase 04 burst B processed 2,129 received frames including ACK, took 0.438 s to API completion, used 0.675 CPU s and 51,520 KiB peak process RSS. Its observation-to-commit p50/p95/p99 was 13.42/27.53/34.07 ms. Publication was 13.45/27.58/34.16 ms. Mixed C publication was 12.00/23.49/36.49 ms. The burst B and C queues reached the 64-frame bound with zero uncertain or rejected frames. The old Phase 03 benchmark stamped observation when the ordered processor pulled the frame. Phase 04 stamps at independent receipt, so the latency includes the new bounded queue wait. The receiver uses a no-timer fast path when capacity is available. The last recovery in the reconnect run measured 1.62 ms send-to-receipt, 0.52 ms receipt-to-dispatch and 3.96 ms receipt-to-publication. The run reconciled seven recovery replies across three connections.

These synthetic measurements do not establish capacity, latency or entitlement for a physical Windows feed.

## Acceptance matrix

| Gate | Status |
| --- | --- |
| G0 Source | PASS |
| G1 CI | BLOCKING — final-SHA Ubuntu, Windows and Chromium jobs not observable |
| G2 Core regression | PASS on Linux |
| G3 Recovery | PASS for tested synthetic deadline, blocked-writer and bounded-overflow scenarios |
| G4 Capture | PASS for measured synthetic reconciliations and existing durability regression |
| G5 Replay | PASS for tested committed-prefix parity |
| G6 Wave engine | PASS synthetic |
| G7 Multi-timeframe | PASS synthetic forming/as-of and delayed-trade tests |
| G8 Abnormality | PASS contract — observed research records, expected reference explicitly unavailable |
| G9 H-GC-001 | PASS technical — scientific population not available |
| G10 Full stack | PASS Linux Chromium and integration |
| G11 Security | PASS tested scope |
| G12 Windows operator | PARTIAL — loopback CLI checked on Linux, Windows script execution pending |
| G13 Real Fxaurum | UNVERIFIED |
| G14 Real depth entitlement and events | UNVERIFIED |

Git push succeeded. Both Draft PR routes were denied. Next verify the Windows job for the final SHA. Then run the authorized [Windows commissioning sequence](WINDOWS_LIVE_VALIDATION.md) in mode A, B and, only after the local entitlement probe, C. Keep any underlying market data local and share only the sanitized report.
