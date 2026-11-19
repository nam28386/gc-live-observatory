# CODEX MASTER PROMPT — GC Live Observatory Full Project Delivery

## Copy everything from ROLE through END. Execute as one integrated project-completion task.

ROLE
You are Principal Engineer, Windows Release Engineer, Market Data Reliability Engineer, Institutional Finance R&D Architect, and Independent QA Reviewer. Work as an implementation agent, not a planning consultant.

REPOSITORY
nam28386/gc-live-observatory

CURRENT DELIVERY BRANCH
feat/full-project-delivery

VERIFIED ENGINEERING BASE
Phase 05 commit: 5490ed2168e3eb0be07b6d1784225ae1f16bf4fc
This branch was created from that exact SHA. Preserve its history.

BASELINE CI EVIDENCE — DO NOT IGNORE
At Phase 05 SHA, GitHub Actions run 44527725201 completed SUCCESS:
- Ubuntu: 97 non-browser tests passed; 2 Chromium browser tests passed.
- Windows: 97 non-browser tests passed; recovery group 20/20 passed in each of three repeats.
Run URL: https://github.com/nam28386/gc-live-observatory/actions/runs/44527725201
This is CI verification, NOT physical Windows Fxaurum LIVE verification.
Do not waste the task re-fixing Windows CI that has already passed. Investigate only if the new branch's actual CI fails.

MISSION
Finish the existing GC Live Observatory into one complete, installable, documented, reliable, tested, read-only Windows-local COMEX GC observation and research workstation. Commit and push the implementation to GitHub.

This is the FINAL INTEGRATED DELIVERY TASK, not another speculative Phase 06/07 chain.
Avoid redesign, fragmentation, repeated roadmaps, unrelated features, and stopping at documents.

SUCCESS CRITERION
A normal Windows user can install dependencies, perform synthetic smoke verification, launch read-only Fxaurum/Rithmic observations in mode A, optionally enable entitled mode B/C, inspect M1/M5/M15/H1 and market microstructure/wave dashboards, capture data locally when authorized, shut down cleanly, reconstruct an attested capture, and generate a sanitized evidence packet. A GitHub Release Candidate can be built from a known SHA with successful CI evidence.

INTEGRITY RULES
- Real gateway/entitlement/session tests cannot be performed by Codex Cloud. Do not claim they passed.
- LIVE is Windows-local and IPv4 loopback only; never use tunnels or public exposure.
- No order entry, account operations, brokerage RPC passthrough, or live trading.
- No raw licensed data, account identifiers, tokens or credentials in Git/CI/Cloud/evidence outputs.
- H-GC-001 remains UNRESOLVED without permitted real out-of-sample evidence.
- Do not identify institutional actors from anonymous flow.
- Keep timestamps, measurement eligibility, known gaps, control RPC ordering, exact/inferred Fill, and replay parity.
- No unauthorized migrations, deletes, production deployments, auto-merge, or auto-tag.

============================================================
A. PREFLIGHT AND MINIMUM CHANGE DESIGN
============================================================

1. Inspect the actual branch, remote, ancestry, working tree and AGENTS.md if present.
2. Read the real project code and docs before implementation:
   - README.md, pyproject.toml, uv.lock
   - observatory/__main__.py, collector.py, books.py, rpc.py
   - journal.py, replay.py, evidence.py, diagnose.py, quality.py
   - microstructure.py, waves.py, dataset.py, evaluate.py, web.py
   - observatory/static/
   - scripts/windows/
   - .github/workflows/pr00.yml
   - docs/PHASE05_EVIDENCE.md
   - docs/WINDOWS_LIVE_VALIDATION.md
   - docs/MEASUREMENT_INTEGRITY.md
   - existing regression, browser and security tests.
3. Confirm the correct base ancestry and baseline CI evidence.
4. Verify actual command flags rather than inventing commands.
5. Run the complete current regression suite before editing.
6. Produce a short internal gap assessment: what is missing for a finished Windows deliverable? Implement the gaps in this task; do not stop at the assessment.

PRESERVE THE WORKING SYSTEM
- Trade/BBO mode A.
- MBP depth, sequence/recovery, permanent gap evidence, mode B.
- Optional MBO/DBO exact/inferred Fill handling and mode C.
- SQLite WAL synchronous FULL, batching and committed-prefix semantics.
- Bounded receipt queue, RPC lifecycle and no silent loss.
- Four charts: M1/M5/M15/H1, plus feature panels.
- Research wave engine and evidence cards.
- H-GC-001 frozen cutoff t0+2s, label t0+30s, dataset/evaluator.
- Windows PowerShell installer/preflight/run-live/smoke/stop.
- Existing sanitized diagnose/evidence packet CLI.
- Existing FastAPI and lightweight browser frontend.

Avoid new frontend frameworks, Kafka/Redis, microservices, LLM agents, Options, cross-assets, speculative ML, footprint and heavy DOM replay. Add dependencies only if required for a measured deliverable.

============================================================
B. MAKE THE WINDOWS PRODUCT WORK END TO END
============================================================

Build a coherent Windows operator experience using existing PowerShell and uv tools.

For a fresh checkout on a normal Windows machine:
- Verify Python 3.12 and uv requirements.
- Install using the locked project configuration.
- Verify isolated local paths and write permissions.
- Run preflight for gateway and dashboard ports.
- Start synthetic mode without access to the vendor gateway.
- Run actual fake-WebSocket integration, not merely unit mocks.
- Start authorized LIVE mode on loopback with explicit contract/expiry.
- Validate mode A, then B when entitled, then C only on positive mbo-probe.
- Verify 4 charts and operational status.
- Enable local capture only after retention/data-use authorization.
- Stop cleanly, flush admitted commits, write a capture summary.
- Replay a committed prefix and produce a sanitized evidence packet.
- Reopen the workstation and confirm it does not falsely bridge prior coverage.

Keep existing script names. Repair confusing exit codes, inconsistent arguments, misleading status, missing-file behavior, and poor error messages based on tests.

Target commands (inspect actual flags, do not blindly copy):
  .\scripts\windows\install.ps1
  .\scripts\windows\preflight.ps1
  .\scripts\windows\run-live.ps1 -Symbol $Symbol -Expiry $Expiry -Mode A
  .\scripts\windows\smoke.ps1 -Symbol $Symbol -Expiry $Expiry -Mode A -DiagnosticPath $Diagnostic -ExpiryAttested
  .\scripts\windows\stop.ps1

The expiry-attestation flag is allowed only after independent operator confirmation.

Operator failure messages must distinguish:
- gateway absent
- wrong port / non-loopback bind
- actual contract or expiry mismatch
- rejected subscription / wrong ACK
- market closed or session UNKNOWN
- no eligible trades, stale trade, stale BBO
- MBP unavailable or recovery failure
- DBO not entitled
- bounded capture pressure / recorder failure
- storage exhaustion / permission failure
- missing licensed data retention authorization.

Make each scenario diagnostically useful. Never replace absence of events with fabricated data.

============================================================
C. WINDOWS SYNTHETIC FULL-STACK ACCEPTANCE
============================================================

Add ONE deterministic Windows-local integration/smoke workflow exercising an actual subprocess stack:
- local fake Fxaurum-shaped WebSocket gateway
- project collector and API
- mode A, B, and C with synthetic entitlement
- optional FULL durable recording
- dashboard status
- controlled reconnect/recovery
- graceful shutdown
- replay and evidence verification.

This may be a small integration command or a test harness using existing tools. Do not rebuild the application or create a separate runtime.

The Windows CI runner should validate PowerShell scripts and actually exercise the relevant Windows operator path in synthetic mode. Do not equate this with real gateway validation.

Acceptance must check:
- exact request IDs and source contract
- subscription and entitlement fallback
- valid MBP reconstruction and recovery with permanent gap
- authoritative Fill with no double counting
- no silent receipt loss
- bounded queue and correct RPC receive/dispatch/commit order
- API/chart readiness
- correct exit status and absence of orphan processes
- no open non-loopback listener
- no raw market data exported from CI.

No test flakiness from arbitrary sleeps. Prefer finite event tapes, explicit readiness states, bounded timers and cleanup.

============================================================
D. RELEASE CANDIDATE PACKAGING
============================================================

Produce a simple, reproducible, Windows-compatible release candidate without a new architecture.

Preferred deliverable:
- self-contained source/launcher ZIP suitable for a normal Windows machine that has or can install uv/Python 3.12
- pinned uv.lock and exact source SHA
- install/preflight/run/mock/live/smoke/stop scripts
- release README / quickstart with explicit configuration
- documented third-party dependency installation requirement
- SHA256 checksum and version metadata
- local app data location and opt-in capture policy
- safe upgrade and rollback instructions.

Do not promise an offline standalone EXE unless it is genuinely built and tested. Do not require code signing or admin privileges to claim a source/launcher ZIP is usable. Do not bundle broker credentials or licensed market data.

If packaging existing scripts is sufficient, do not add PyInstaller/Electron/Docker just for appearance.

Add a packaging/build command that CI can execute and validate on Windows. Verify artifact structure in a clean directory and run at least a synthetic startup smoke from the packaged checkout. Avoid absolute workspace paths in shipped files.

Keep the application version and internal Git SHA coherent. Do not call this a production release before field gates pass.

============================================================
E. OPERATIONS, CAPTURE, REPLAY, EVIDENCE
============================================================

Maintain exact Phase 05 data contracts.

For each capture report:
- requested/effective market-data mode and source identity
- COMEX GC contract and operator-verified expiry status
- process/session identity, software SHA and schema versions
- receive/admitted/settled/normalized/durable/visible counters with separate denominators
- queue high-water, queue residence, commit/publish latency percentiles
- RPC request/response timing and classification
- immutable history gaps and incomplete coverage intervals
- normalized and journal record counts
- recorded durable checkpoint, strict replay digest and state parity
- data retention/license attestation status, not invented rights.

Limit packet data to allowlisted non-sensitive metadata. Keep source payloads, prices, account material and private checkpoints strictly local and untracked.

Ensure the verifier rejects:
- wrong SHA or instrument
- mismatched session or durable position
- truncated/pruned prefix
- missing replay digest
- forged PASS/entitlement assertions
- version mismatch with no documented migration.

A successful verification means the saved evidence is internally coherent; it does not prove the full exchange feed was received.

Maintain full-replay parity for eligible MBP/MBO, wave transitions and H-GC-001 features at the same durable prefix. Do not allow historical gap recovery to rewrite invalid past windows.

============================================================
F. PERFORMANCE AND FAILURE RECOVERY
============================================================

Do not chase a throughput target at the expense of integrity.

Benchmark the full synthetic:
WebSocket -> receiver -> collector -> SQLite FULL -> features -> API -> replay.

Baseline from Phase 05 Linux:
- B burst 2,128 / 2,128; ~4,449 durable market records/s
- mixed C 4,129 / 4,129; ~4,784/s
- paced B 31,878 / 31,878; ~2,593/s
These are short Linux synthetic measurements, NOT Windows production capacity.

Measure the release candidate on Windows CI with deterministic bounded workloads. Report setup, event counts, p50/p95/p99, memory, CPU, queue pressure and anomalies. Prefer comparative results to arbitrary headline thresholds.

Create or reuse controlled crash tests:
- terminate before commit
- terminate after commit
- reconnect while a recovery RPC is pending
- queue saturation and fail-closed gap
- disk full / permission denied
- restart, replay and retention.
Verify durable-prefix correctness, no silent losses, no duplication of acknowledged Fill and no false repaired OFI.

Do not add concurrent writers, brokers, or new database engines unless tests show the current design cannot meet field requirements.

============================================================
G. DASHBOARD READY FOR ACTUAL USE
============================================================

Keep the existing frontend and data interfaces. Make only needed usability refinements.

Required operator-visible information:
- large LIVE versus MOCK/SYNTHETIC distinction
- exact source, contract and expiry attestation
- mode A/B/C and source entitlement state
- ACK/Trade/BBO/MBP/MBO freshness separately
- session OPEN/CLOSED/UNKNOWN based on a sourced calendar
- recorder state, backlog, durable position and storage status
- gap timeline and RPC recovery state
- M1/M5/M15/H1 synchronized charts
- flow, OFI, liquidity, refill, intensity, MBO and wave panels
- validity/coverage flag on any research measurement
- evidence card and wave detail.

Never fabricate absent observations, expected-response baselines, underlying actor identity or trading recommendations. Keep rendering bounded, no footprint or heavy DOM replay.

Improve the user-facing first-run path if it is currently confusing. Test browser behaviors, not just HTML text existence.

============================================================
H. SECURITY AND LICENSE GUARDS
============================================================

Read-only local market observation only.
- Fxaurum WS stays on Windows 127.0.0.1.
- Browser API stays on 127.0.0.1.
- No arbitrary RPC proxy or trading/account methods.
- Explicit allowed read-only RPC methods only.
- No public tunnels, remote gateways or cloud data uploads.
- No license-violating data distribution.
- No raw captures or credentials in repository, CI logs or release artifacts.
- Retention/capture remains opt-in and external to Git.

Run secret scan/allowlist tests and inspect final diff and ZIP content. Do not auto-upload private diagnostics.

============================================================
I. CI, GATES AND ACTUAL EVIDENCE
============================================================

The Phase 05 baseline is GREEN. Preserve it.
The current workflow has explicit feature-branch triggers; update it to include feat/full-project-delivery (or a narrowly scoped equivalent) so the actual delivery branch runs GitHub Actions.

Required GitHub jobs:
- Ubuntu Python 3.12 non-browser suite.
- Ubuntu Chromium browser suite.
- Windows Python 3.12 non-browser suite.
- Windows repeated recovery tests.
- Windows PowerShell parsing and synthetic operator startup.
- Windows packaging, clean-artifact validation and smoke test.
- Security/secret/artifact checks.

Do not remove existing tests, mark failures xfail, or drop a CI OS to produce a green badge.

Observe the real Actions run for the EXACT final SHA.
A prior successful run is evidence of the base, not of modified code.

Gates:
G0 source/ancestry PASS
G1 full Ubuntu CI PASS
G2 full Windows CI PASS
G3 Chromium PASS
G4 Windows synthetic operator smoke PASS
G5 packaged artifact startup and checksum PASS
G6 read-only/security PASS
G7 persistence/crash and replay parity PASS
G8 docs/quickstart and diagnostics PASS
G9 synthetic full-path performance measured
G10 actual user Windows Fxaurum mode A UNVERIFIED until operator evidence
G11 actual MBP mode B UNVERIFIED until operator evidence
G12 actual DBO mode C UNVERIFIED until entitlement and observed events
G13 real capture capacity UNVERIFIED until authorized local field capture
G14 H-GC-001 scientific result UNRESOLVED until authorized real OOS validation

Classification:
GC_RC_ENGINEERING_VERIFIED only when G0-G9 pass on the final SHA.
GC_FIELD_VALIDATED_A/B/C only with independent physical Windows operator evidence.
Do not block engineering release solely because the user has not yet run the field pilot.

============================================================
J. ONE EXECUTION TASK, COMMITS, GITHUB DELIVERY
============================================================

Use the EXISTING feat/full-project-delivery branch.
Do not create another phase branch.

Work in internal milestones only:
1. Gap audit and operator completion.
2. Windows synthetic subprocess acceptance and packaging.
3. Durability, evidence, and user experience hardening.
4. CI verification and benchmark.
5. Release-candidate review.

Implement all achievable engineering work in this one Codex task.

Commit tested changes in coherent increments.
Push to origin/feat/full-project-delivery.
Verify git ls-remote points to the same final SHA.
Keep the working tree clean.

Do not force-push, rewrite history, auto-merge, create a production release, tag or deploy.
Try a Draft PR once against feat/phase05-windows-live-commissioning if permissions permit.
If Forbidden, report PR_CREATION_BLOCKED and give the exact compare URL without repeated retries.

If Actions are inaccessible, report the exact blocker; still commit tested code, but do not label engineering release verified.
If execution budget is exhausted, commit the last coherent tested state with a precise PARTIAL report. Never fabricate completion.

============================================================
K. FINAL RESPONSE — MANDATORY
============================================================

Report:
- full final commit SHA, branch and remote verification
- exact source changes and entrypoint commands
- what a Windows user can install/run now
- package artifact name, version, size and SHA256 if actually built
- exact test commands and counts
- Ubuntu/Windows/Chromium final-SHA CI URLs and conclusions
- synthetic Windows operator result
- throughput/capture/replay consistency evidence
- security/rights audit
- real Windows A/B/C state as PASS/PARTIAL/UNVERIFIED based on evidence
- H-GC-001 status, keep UNRESOLVED without permitted real evaluation
- blockers, limitations, and exact human pilot steps.

Do not finish with only plans or new prompts.
Make code changes, run tests, package, commit and PUSH.
Avoid speculative additions.
Optimize for a working complete local workstation and defensible evidence.

END OF MASTER PROMPT.
