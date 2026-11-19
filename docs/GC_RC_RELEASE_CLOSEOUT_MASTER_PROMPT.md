# GC Live Observatory — Release Closeout Master Prompt
## Codex Cloud — one final, bounded implementation and verification task

### ROLE / MISSION
Act as Principal Windows Release Engineer, Market Data Reliability Engineer, Senior Full-Stack Engineer, and Independent Quant Research Infrastructure Auditor.

Work in the EXISTING repository and branch:
- Repository: nam28386/gc-live-observatory (PUBLIC)
- Head branch: feat/full-project-delivery
- Target Draft PR: https://github.com/nam28386/gc-live-observatory/pull/1
- Verified last engineering source commit: 6623d01504b1c8cdc60c26e9f2a8baf62ac85dff
- Preserve Phase 05 ancestry: 5490ed2168e3eb0be07b6d1784225ae1f16bf4fc
- No new repository, no new large research phase, no auto-merge.

PRIMARY OBJECTIVE: Close out the existing Windows-source release candidate with reproducible cross-platform evidence, investigate the intermittent Windows clean-extraction/restart acceptance failure, harden release artifacts and public-repo safety, and finish an operator-ready Windows LIVE pilot handoff.

Do not stop at a plan. Perform source/test/workflow/docs changes only where evidence shows they are needed. Commit and PUSH coherent changes to feat/full-project-delivery, then review the exact final SHA. Keep the PR as DRAFT pending human review and LIVE authorization.

### VERIFIED BACKGROUND — DO NOT CONFUSE HISTORICAL SHA OR ARTIFACT
1. Older Phase 05 SHA 5490ed2168e3eb0be07b6d1784225ae1f16bf4fc:
   GitHub Actions 44527725201 SUCCESS on Ubuntu and Windows.
2. First delivery SHA f1926d12f70c58d7672a762ad58004484f6a4328:
   GitHub Actions 44539652583 FAILURE:
   Ubuntu SUCCESS; Windows unit tests 100 passed, 2 browser tests deselected.
   Windows ZIP build and SHA256 verification succeeded, but the clean-extraction
   acceptance failed on the SECOND capture (Mode C restart).
   Failure in scripts/windows_acceptance.py run_case(... requested=C,
   effective=C, capture=True, restart=True):
   RuntimeError: Synthetic subprocess did not reach the requested readiness state.
   This is not a proven Fxaurum LIVE gateway defect.
3. Diagnostic follow-up commit 6623d01504b1c8cdc60c26e9f2a8baf62ac85dff:
   Added bounded readiness diagnostics to scripts/windows_acceptance.py.
   GitHub Actions 44541377713 SUCCESS:
   - Ubuntu job SUCCESS (non-browser suite and Chromium).
   - Windows job SUCCESS: 100 non-browser tests passed,
     three repetitions of 20 recovery tests each passed,
     PowerShell syntax accepted,
     clean-extraction synthetic Windows PowerShell subprocess
     modes A/B/C, C-to-B fallback, reconnect, restart,
     capture, evidence and replay all passed,
     B/C synthetic benchmark ran,
     Windows Actions artifact uploaded.
   - Windows-built RC source ZIP at that SHA:
     gc-live-observatory-0.2.0-rc-6623d01504b1-windows.zip
     bytes: 165000
     ZIP SHA256: 131ca26ba828afbccc72f7d365e0dd7a9d048e82a7dca89135ed067b4
   - Actions: https://github.com/nam28386/gc-live-observatory/actions/runs/44541377713
4. The older Linux-produced ZIP at f1926d12 and the Windows-built ZIP at
   f1926d12 had different bytes and hashes. Reproducibility must be
   specified per platform and build conditions. Never reuse an earlier
   checksum for a changed SHA, platform, contents, or archive.

Current classification:
- ENGINEERING CI PASS AT 6623d015 (single successful verified run).
- WINDOWS RESTART FLAKINESS NOT EXCLUDED by one passing run.
- REAL FXAURUM/RITHMIC A/B/C UNVERIFIED.
- REAL CAPTURE CAPACITY UNVERIFIED.
- H-GC-001 SCIENTIFIC STATUS UNRESOLVED.
- PUBLIC REPO REQUIRES SECRET/LICENSE AUDIT.

### A. PREFLIGHT AND REPOSITORY INVENTORY
1. Verify branch HEAD, remote SHA, clean tree, ancestry, current Draft PR #1.
2. Read AGENTS.md if present, docs/MEASUREMENT_INTEGRITY.md,
   docs/WINDOWS_LIVE_VALIDATION.md, README.md, pyproject.toml,
   uv.lock, scripts/build_windows_rc.py,
   scripts/windows_acceptance.py, scripts/windows/*.ps1,
   observatory/collector.py, journal.py, rpc.py, replay.py,
   evidence.py, diagnose.py, web.py, quality.py, dataset.py,
   evaluate.py, .github/workflows/pr00.yml, all related tests.
3. Check if another contributor advanced the branch; never clobber.
4. Preserve Trade/BBO A, MBP B, optional DBO C, exact/inferred Fill,
   permanent historical gaps, SQLite WAL FULL, bounded queues,
   committed-prefix replay, wave engine, H1/M15/M5/M1 frontend,
   H-GC-001 information cutoffs, read-only IPv4 loopback.
5. Establish a deterministic acceptance baseline and record exact evidence.
6. Do not make unsupported claims if an action is blocked.

### B. WINDOWS RESTART/READINESS ROOT CAUSE — TOP PRIORITY
The first Windows RC run failed only on C restart after earlier
A, B, C-to-B, C and capture scenarios. The following run passed
without a demonstrated semantic repair (only diagnostic code changed).
Treat this as a potentially timing-dependent/flaky release gate.

Investigate the real process path in scripts/windows_acceptance.py:
- powershell run-mock.ps1 launching uv and Python
- lifecycle of old and new Uvicorn/collector subprocesses
- same test-selected TCP port across consecutive scenarios
- stop marker deletion, ownership and identity
- SQLite writer lease, WAL checkpoint and session finalization
- synthetic reconnect index and terminal tape boundaries
- capture restart gap versus live source connection gap
- WebSocket ACK and last message state
- collector TaskGroup/lifespan shutdown and admitted frame draining
- MBP Reset/Image/recovery and OFI freshness after a finite source tape
- exact trade-count expectation and mode C exact/inferred counts
- whether the fixed readiness predicate becomes impossible due to
  a valid transient intermediate state, terminal stale-source condition,
  or a persistent defect.
- PowerShell/uv child process exit and orphan process cleanup.

IMPORTANT: a passing rerun does not prove that the root cause is fixed.
Do not patch by merely raising timeout from 25 seconds.
Do not weaken correctness checks, fabricate data, or drop restart from CI.
Use the bounded diagnostic data to classify the failed invariant.
If the missing condition is an acceptance race, introduce an explicit
stable completion barrier in the actual synthetic tape/collector path
that proves the required durable prefix and recovery finished.
If the application behavior is wrong, fix application behavior.
Avoid arbitrary sleeps and unspecified eventual consistency.

Add a distinct, bounded short Windows stress/soak acceptance:
- at least several repeat runs of the previously failing
  clean-extraction C-capture/restart scenario on Windows CI;
- exact outcomes, repeat count, duration and process exit state;
- deterministic new ports or verified closure with proof;
- no orphan subprocesses;
- no stale local database/stop marker contamination;
- no raw price/market payload logs.
A repeat check may be split into fast deterministic tests and a
separate bounded CI subprocess test if runtime becomes excessive.

### C. SOURCE/BUILD/ARTIFACT REPRODUCIBILITY
1. Keep existing versioned source ZIP/launcher model. Do not add
   PyInstaller/Electron/containers unless absolutely necessary.
2. Verify script/build_windows_rc.py includes only allowlisted app
   source, requirements, scripts, README, lock, static assets and docs.
3. Build from exact source SHA and inspect the resulting RELEASE.json.
4. Verify ZIP filename, SHA256 sidecar, archive content digests,
   reproducibility across two builds on the SAME platform and SHA.
5. Explain or remove accidental cross-platform byte drift if feasible,
   but never claim cross-platform bitwise identity without measurement.
6. On Windows CI: build -> verify -> clean extract -> install ->
   fully execute synthetic PowerShell subprocess acceptance ->
   replay/evidence verify -> controlled stop -> benchmarks ->
   upload immutable Actions artifacts.
7. Make artifact names include exact SHA and OS/runtime basis.
   An Actions artifact ZIP wrapping the source ZIP is NOT identical
   to the inner source ZIP. Report hashes of the correct object.
8. Assert a generated ZIP never accidentally includes .git, .venv,
   capture DBs, PRIVATE checkpoints, account material or secrets.
9. Do not publish a GitHub Release or create tags without explicit
   approval; deliver a reviewed Release Candidate via Actions.

### D. PUBLIC REPOSITORY SECURITY AND LICENSE REVIEW
The repo was changed from private to PUBLIC. Treat this as an
explicit additional release risk.

Audit:
- tracked current files and, where Git history access allows, history
  for leaked credentials, tokens, account identifiers, broker/market
  capture files, private diagnostic/checkpoint data or licensed quotes;
- source ZIP, GitHub Actions logs and uploaded artifacts;
- license terms/restrictions on redistribution of raw Fxaurum/Rithmic
  market data and derivatives;
- public README and examples: synthetic fixtures must be clearly
  marked SYNTHETIC and not contain private real-market payloads.
- local storage paths must remain outside Git checkouts.
- gate LIVE capture behind operator authorization/retention attestation.
- local 127.0.0.1 bind and only allowlisted read-only gateway commands.
- HTTP and browser must not expose general RPC, account operations,
  order entry or raw licensed capture export.

If potential secrets are found, STOP disclosing them in logs.
Report file, commit and secret TYPE only, never print the secret.
A history exposure requires a human-coordinated incident response
(rotation, access revocation where relevant, and repository-history
remediation as necessary). Do not force-rewrite public history automatically.

Only call the public release "safe to share" after a bounded audit
documenting what was actually checked; never guarantee unseen
history or future secrets are absent.

### E. DOMAIN, MEASUREMENT AND REPLAY INVARIANTS
Never change frozen scientific semantics solely to pass CI.

Maintain:
- observed exchange, gateway, collector, commit and API availability
  times with correct domains and units;
- independent Trade/BBO last observation and quote-volume/OI clocks;
- strict MBP gen/seq/snapshot and gap continuity;
- exact-ID versus inferred MBO Fill distinction, no double subtraction;
- durable publication only after SQLite FULL commit;
- admitted, processed, recorded and uncertain denominators;
- gap after overflow/reconnect and no reconstructed missing history;
- capture session, contract, expiry provenance, licensed source rights,
  retention, version and exact software SHA;
- same-prefix live/replay feature equivalence;
- wave state and H-GC-001 t0+2s cutoff, t0+30s labels;
- online feature availability distinct from offline as-of extraction;
- science status H-GC-001 = UNRESOLVED.

A synthetic pass is engineering evidence only.

### F. OPERATOR-READY WINDOWS PILOT HANDOFF
Keep LIVE commissioning independent of cloud access.

Create or refine a short, verified quickstart for an authorized
Windows workstation with the actual script switches:

Step 1. Verify Windows ZIP SHA256, extract outside Git if desired,
        install uv/Python 3.12, run scripts/windows/install.ps1.
Step 2. Run synthetic acceptance:
        uv run --locked python -m scripts.windows_acceptance
        --expected-sha <actual package SHA>.
Step 3. Confirm Fxaurum is installed, configured and listening on
        Windows 127.0.0.1:9001; no cloud tunnel or remote access.
Step 4. Independently verify COMEX GC contract and expiry in
        the authorized vendor terminal and exchange specification.
Step 5. Run scripts/windows/preflight.ps1.
Step 6. Start A via run-live.ps1 in terminal one, smoke.ps1 in
        terminal two, save only sanitized diagnostic, stop.ps1.
Step 7. If depth entitled: repeat B and confirm MBP/OFI and
        gap/continuity.
Step 8. Execute local mbo-probe before C; only use -MboEntitled
        after a positive actual gateway response and observe DBO.
        Do not demand C if entitlement is unavailable.
Step 9. Only with confirmed rights: enable capture using
        the actual -Capture / -RetentionAttested flags,
        benchmark the local permitted workload, shutdown cleanly,
        replay the same committed prefix, build and verify the
        sanitized packet. Keep DB and private checkpoint local.
Step 10. Record PASS/PARTIAL/FAIL/UNVERIFIED separately for
         gateway, A, B, C, capture throughput, replay parity.

If real Windows/operator access is unavailable in Codex Cloud,
do not fabricate a LIVE session, entitlement or data rights.

### G. REQUIRED GATES / SCIENTIFIC STATUS
G0: exact head/ancestry and clean remote state.
G1: Ubuntu unit/integration + Chromium CI pass on final SHA.
G2: Windows Python, PowerShell parsing and recovery CI pass.
G3: Windows clean-extraction operator subprocess A/B/C/fallback
    including C capture restart pass on final SHA.
G4: Windows repeated restart stress scenario pass with actual logs.
G5: exact-SHA Windows build/verify/hash/artifact upload pass.
G6: durability, bounded pressure, strict replay/evidence parity pass.
G7: synthetic full-path Windows benchmark measured and reconciled.
G8: public source/history/artifact security/rights review performed,
    limitations explicit.
G9: accurate Windows pilot runbook and release README.
G10: actual authorized Windows Fxaurum A: UNVERIFIED until field evidence.
G11: actual MBP B and DBO C: UNVERIFIED until positive field evidence.
G12: actual local authorized capture capacity: UNVERIFIED until measured.
G13: H-GC-001: UNRESOLVED until permitted real OOS test.

RC_ENGINEERING_VERIFIED requires G0-G9 all PASS.
No single green workflow proves the historical restart path
was never flaky; record repetitions and uncertainty honestly.
FIELD_VALIDATED_A/B/C can only follow independent physical
Windows evidence; C is optional and entitlement-dependent.

### H. GITHUB DELIVERY
Use existing feat/full-project-delivery.
Do not force push, auto merge, change main, rewrite history,
tag or publish a GitHub Release.
Update Draft PR #1 with actual findings and evidence.
Make coherent commits and push.
Verify origin SHA after each last change.
Read exact-SHA workflow runs and job conclusions.
If CI fails, inspect logs, fix measured root cause and iterate.
If unable to finish, commit useful verified changes and
report PARTIAL, not RC_ENGINEERING_VERIFIED.

### I. FINAL OUTPUT CONTRACT
Report:
- exact new SHA, compare link and Draft PR #1 link
- source modules changed and why
- reproduced Windows restart failure / falsification of race theories
- acceptance repeat count and Windows CI evidence URL
- Ubuntu/Windows/browser tests and exact outcomes
- Windows ZIP filename, byte count, inner source ZIP SHA256,
  wrapper artifact identity and provenance
- clean-extraction/stop/restart/evidence results
- full-path Windows capacity and completeness limitations
- public history/source/artifact security audit and licensing caveats
- exact physical Windows A/B/C pilot commands
- remaining human-only field gates
- current H-GC-001 UNRESOLVED
- PASS/FAIL/PARTIAL/UNVERIFIED for every gate
- exact release status (no guesses).

EXECUTE, VERIFY, COMMIT AND PUSH. DO NOT START A NEW PHASE.
