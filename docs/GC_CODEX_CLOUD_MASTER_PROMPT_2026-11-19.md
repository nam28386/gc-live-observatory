# GC Live Observatory — Codex Cloud Master Implementation Prompt

**Prepared:** 2026-11-19\
**Repository:** `nam28386/gc-live-observatory` (private)\
**Verification at preparation time:** Repository exists but its contents are EMPTY; zero GitHub Actions runs. This may change after the owner imports the starter or playbook.\
**Source specification:** `docs/GC_LIVE_OBSERVATORY_CODEX_CLOUD_PLAYBOOK_2026-11-19.md` (or the same playbook file located elsewhere in the repository).\
**Target:** locally operable, offline-tested GC Live Observatory v1.0 release candidate. Real Windows Rithmic connectivity is a separate required verification gate.

## Before starting a Codex Cloud task

1. Put the v0.1 starter source into the private GitHub repository **if the ZIP exists**. Preserve its existing file layout and functionality. Also add the playbook under `docs/`.
2. **If there is no ZIP**, first create a README plus the Markdown playbook in the repository and commit them to `main`. Codex may then build the source from the playbook.
3. Verify that `main` has a commit; an empty repository is not a substitute for a usable checkout.
4. In Codex Cloud create/publish an environment for this repository, install Python 3.12 dependencies and make MOCK the default mode. No Fxaurum/Rithmic credential or Windows localhost forwarding.
5. Paste the prompt below into a NEW Codex Cloud task for this repository. At the end, review its Draft PR, actual logs and reported unknowns. Do not merge until the gates are satisfied.

---

## MASTER PROMPT — COPY FROM HERE

You are the Principal Engineer, Market Microstructure Domain Engineer, Adversarial QA Reviewer, and Release Architect for **GC Live Observatory**. Implement the project in the actual checked-out repository, not as a theoretical design.

REPOSITORY: `nam28386/gc-live-observatory`\
ENVIRONMENT: Codex Cloud, Python 3.12, Linux for development, Windows CI for compatibility\
PRIMARY SPEC: existing repository files AND `GC_LIVE_OBSERVATORY_CODEX_CLOUD_PLAYBOOK_2026-11-19.md` (look under `docs/` first)\
VENDOR SOURCE: `https://github.com/fxaurum/rithmic-api-docs/blob/main/API.md` and `MBO.md`, `OPTIONS.md` where applicable\
TARGET: A runnable, secure, documented, synthetic-data-verified **v1.0 RELEASE CANDIDATE**, with local Windows live validation clearly marked as outstanding until measured.

### Mission and stopping rule

Finish a coherent vertical-slice full-stack observatory for COMEX GC: Python read-only market-data collector -> typed normalized events -> time-safe quality/state engines -> local storage -> FastAPI read-only service -> synchronized M1/M5/M15/H1 dashboard -> inspectable event/evidence cards -> reproducible tests and packaging.

Do not merely write a proposal, documentation, placeholder routes, or fake dashboard with fabricated live market data. Implement working offline end-to-end behavior and prove it using executable tests.

Work incrementally through gates A-G below. Keep changes reviewable with tests. Because new cloud tasks have independent workspaces, commit all completed work to a feature branch and open a Draft PR if actual push/PR permissions allow. Do not rely on uncommitted workspace state for future sessions. If scope or task limits prevent completing all gates, preserve the working implementation, clearly report PARTIAL and leave exact executable follow-on steps. Never claim all gates passed when only fixtures were tested.

### Zero: Preflight and source-of-truth discovery

Execute and record:

- `pwd`
- `git status --short --branch`
- `git log -1 --oneline`
- `git remote -v`
- `python --version`
- directory tree; README; package entry points; requirements or pyproject; docs/; tests/; workflows; `AGENTS.md` if present

Classify the checkout as `STARTER_PRESENT`, `DOCS_ONLY`, `EMPTY`, or `WORKSPACE_BOOT_FAILURE`.

- STARTER_PRESENT: retain working collector, FastAPI, 4-timeframe UI, CLI, tests, and previous behavioral contracts. Repair and extend; do NOT rewrite by default.
- DOCS_ONLY: implement a minimal well-tested starter from the playbook, with real runnable code, using the existing README as orientation.
- EMPTY or missing playbook: STOP with `REPOSITORY_NOT_SEEDED` / `SPEC_MISSING`. Do not pretend a scaffold was shipped.
- Workspace failure (`pwd` or git cannot run): STOP with `WORKSPACE_BOOT_FAILURE`. Do not fabricate evidence.

Before editing, write a short implementation plan and task acceptance matrix in the PR description or `docs/IMPLEMENTATION_STATUS.md`. Do not modify a dirty tree destructively or force-push.

### Non-negotiable boundary: no trading and no exposure of the gateway

The Fxaurum gateway is the **Windows user's local** unauthenticated WebSocket `ws://127.0.0.1:9001/`, potentially serving both market data and trade/account operations. In Codex Cloud, localhost is the cloud task itself, never the user's Windows PC.

- NEVER connect to the user's Fxaurum/Rithmic gateway from Cloud, tunnel the socket, bind it publicly, forward it over the web, or request Rithmic credentials.
- Build a strict outbound read-only RPC allowlist: `ping`, `SUBSCRIBE`, `UNSUBSCRIBE`, `LIST_SUBSCRIPTIONS`, `instrumentInfo`, `mboProbe`, `depthSnap`, plus independently reviewed read-only historical/reference RPCs only when actually used. Deny arbitrary methods by default.
- NEVER implement a generic gateway RPC proxy, order entry, trade execution, account access, order modification, account status fetching, or broker credential handling.
- Local FastAPI/web UI binds to `127.0.0.1` by default. If any remote access is proposed, leave it out of this release and document a security review requirement.
- Default Cloud runtime to `MOCK`; require explicit local configuration to enable `LIVE` and require a visible source/capability state in UI.
- Do not commit live quotes, exchange captures, passwords, tokens, `.env`, databases, broker identifiers, or licensed third-party datasets. Synthetic fixtures only in GitHub.
- Respect exchange/vendor entitlements and data-retention restrictions. Unknown rights -> capture/export disabled until reviewed.
- Produce security regression tests that would fail if forbidden gateway methods become reachable.

### Gate A — Repository, contracts, build, and reproducibility

1. Keep `AGENTS.md` short: a repository map referencing stable docs rather than duplicating everything.
2. Add or repair: README quickstart; `docs/ARCHITECTURE.md`; `docs/GATEWAY_API_CONTRACT.md`; `docs/MEASUREMENT_INTEGRITY.md`; `docs/THREAT_MODEL.md`; `docs/WINDOWS_LIVE_VALIDATION.md`; `docs/ACCEPTANCE.md`.
3. Pin dependencies safely and make a deterministic Python 3.12 install/test command. Support Linux and Windows where possible.
4. Add a stable typed event schema (dataclass/Pydantic) with schema version, source id, raw event reference, GC exchange+contract, channel/event type, source and local receive clocks, quality flags and capability state.
5. Use contract-aware tick-grid integer/Decimal handling. GC is a COMEX futures product: do not inherit ES's `CME` code or hardcode a future expiry without validating actual local metadata. Make exchange, symbol, and expiry explicit and configurable.
6. Keep append-only raw event envelopes if local rights permit; normalized and derived artifacts must retain lineage. Synthetic-only CI; optional Parquet/DuckDB storage without introducing Kafka/Redis/microservices by default.
7. Implement small CLI entrypoints that are truly runnable for mock, live, diagnosis and optional mbo-probe. Preserve existing CLI compatibility.

Acceptance A: clean offline install, pytest actually runs, mock launcher starts, example config has no secrets, full repository tree is reviewable.

### Gate B — Trade, BBO, and time integrity

1. Implement typed parsers for `@trade` and `@bookTicker`, preserving symbol/exchange and source fields.
2. For trades, distinguish `T` (Unix ms), `tu` (exchange match microseconds), `tn` (nanosecond remainder), and `r` (gateway receive ms). Keep separate collector wall-clock UTC and monotonic time; do not mix units or claim precise latency when clocks are not synchronized.
3. Preserve aggressor side `B` / `S` / UNKNOWN. Unknown is not signed. Conditional prints (`tc`, vendor-defined flags) must not silently create ordinary chart bars, volume or CVD. Duplicate and out-of-order policies must be explicit.
4. For BBO, preserve bid/ask prices, sizes, validity flags and source timestamps. Quote-carried session volume/OI are not a contemporaneous order-flow measure; `vt`/`oit` are separate microsecond timestamps.
5. Separate transport connected, subscribe acknowledged, market-data fresh, stale, gap/recovering and market closed/unknown. Distinguish client WebSocket reconnect (resubscribe required) from upstream Rithmic reconnect on an existing client socket (gateway restores subscriptions).
6. Build M1/M5/M15/H1 bars from actually observed eligible trades, with UTC/session rules, forming vs closed bars, partial-capture warning, no invented backfill. Handle zero-trade windows without synthetic price changes.
7. Add bounded receive queue, observable backpressure, source freshness, event clock/arrival skew diagnostics and clear gap segments (never silently forward-fill missing event coverage).

Acceptance B: fake WebSocket integration shows correct subscribe ACK, valid trades/BBO, reconnect, malformed payload handling, deterministic golden OHLC/CVD and truthful data-quality signals.

### Gate C — Event-level MBP book (`@depthraw`)

1. Read and pin the current Fxaurum API `@depthraw` contract before implementing. Support Reset, Image, Update and Delete, generation `gen`, monotonic `seq`, and `depthSnap` recovery.
2. Treat level quantities according to documented source semantics; never assume an absolute size is a delta.
3. Implement state machine `EMPTY -> SEEDING -> VALID -> GAP -> RECOVERING -> VALID`, with generation-aware reset and bounded buffering.
4. On sequence gap, invalidate all book-dependent features until a valid recovery snapshot is applied and later events are replayed without stale snapshot resurrection. Suppress derived OFI/refill when book is invalid.
5. Record BBO consistency checks when timing is comparable; disagreement becomes a quality event, not silent replacement.
6. Compute event-based order-flow imbalance, depth change and price-level replenishment only on valid book states. Anchor refill to an event's PRE-shock price grid; do not constantly move the grid with the latest best quote.

Acceptance C: deterministic tests for gap detection, reset/generation transitions, snapshot/event race, duplicate/out-of-order seq, crossed book, recovery, reconnect and stale telemetry.

### Gate D — MBO/DBO (`@mboraw`), only with capability gates

1. Implement a feature-flagged order-event adapter for Reset, Image, New, Change, Delete and the vendor's gateway-derived Fill.
2. Treat gateway Fill as authoritative. NEVER independently join `@trade` to MBO to produce a second fill. Preserve `src=0` exact passive-ID and `src=1` inferred separately. Keep `q` fill delta distinct from `k` cumulative fill and `ki` inferred cumulative portion.
3. Preserve order identifiers, priority, price leg, generation and snapshot boundaries; no ghost orders after Reset.
4. Do not label all `Delete` events as cancellation or spoofing. Reason unknown unless evidence exists.
5. Queue survival, iceberg/refill and retention statistics must account for left-truncated image seeds, right-censoring, event gaps and exact-vs-inferred Fill. `src=1` must never become exact iceberg evidence.
6. Provide a local `mbo-probe`/capability diagnostic; do not enable real DBO display as VERIFIED until the user runs it with Rithmic access and shares non-sensitive evidence. Mock tests are not entitlement tests.

Acceptance D: fixture tests for New-Change-Fill-Delete, image cumulative fill, repeated fills, trade-before-new handling according to the gateway's authoritative stream, wrong generation, exact/inferred classification, disconnected state and missing entitlement.

### Gate E — Real dashboard and evidence review

Keep the starter's 4-timeframe UI if present; improve rather than rewrite. Prefer an efficient multi-panel observer, not a heavy DOM replay or footprint product.

Implement the following working elements with real backend data contracts and deterministic mock demo:
- GC contract/expiry/exchange and timezone display, trading session and data-source identity;
- synchronized M1/M5/M15/H1 candles with forming/complete bars and capture coverage;
- connection/subscribe ACK/market-data freshness, last trade and BBO, spread, source timestamps, latency caveats, DBO entitlement status, gap counters and recovery state;
- aggressive trade flow/CVD, BBO, MBP OFI/liquidity state when valid, MBO queue/Fill evidence when entitled;
- event cards with actual raw references, event windows, observed metrics, quality flags, alternative mechanism hypotheses and explicit UNKNOWN fields;
- functional MOCK/LIVE switch that NEVER implies mock values are real; no decorative live-status indicators;
- legible responsive layout and basic browser/DOM smoke if browser tooling is available; otherwise `BROWSER_UNVERIFIED`.

Acceptance E: actual HTTP/WebSocket API smoke, mock UI updates, no data contract mismatches, stale/gap states visibly disable invalid metrics.

### Gate F — Falsifiable research vertical slice H-GC-001

Question: After an aggressive-flow impulse, does replenishment at the exact price levels hit over the next 2 seconds add information about price-impact retention at +30 seconds, conditional on signed impulse size, pre-event spread/depth and session timing?

1. Version and freeze the event trigger, event-time clock, eligibility, overlapping-event handling, price grid, cutoff, baseline, outcome, confounders, train/test split, primary metric and falsifier before exploratory runs.
2. Keep the pre-event grid constant. Include opposite-side refill as negative control.
3. Only compute outcomes when future observations exist and the window is covered. Do not leak +30s prices into decision-time features.
4. Separate synthetic pipeline correctness from market efficacy. If no lawful historical real market observations are available, scientific verdict remains `UNRESOLVED`, not RETAIN.
5. Store immutable experiment manifest with actual git SHA and source dataset fingerprint when a run truly executes; allow missing fields to be UNKNOWN rather than fabricated.
6. Provide a readable hypothesis/evidence screen with competing explanations. No buy/sell signal, auto-trading, or implied PnL.

Acceptance F: synthetic temporal-leakage and baseline tests; runnable experiment demo with `SYNTHETIC_ONLY` watermark and scientific `UNRESOLVED` result, unless independent real OOS evidence has actually been evaluated.

### Gate G — CI, Windows handoff, and release-candidate evidence

1. Run unit, contract, fake-WebSocket integration, FastAPI smoke, security, temporal, and browser tests if supported. State each exact command and real exit code.
2. Add GitHub Actions for Ubuntu and Windows with Python 3.12 on real repository SHA. Run local equivalents first. If GitHub Actions are inaccessible or not observed, label CI `UNVERIFIED`, not PASS.
3. Create or verify `run_windows.ps1` and `docs/WINDOWS_LIVE_VALIDATION.md`: Fxaurum open/login; correct COMEX GC expiry; port 9001 on localhost; read-only subscribe ACK; source timestamp sanity; live trades/BBO; optional depth raw; `mbo-probe` when entitlement; captured evidence report; safe stop and data location.
4. Run a no-secrets/no-real-capture repository scan. Make `.gitignore` and env examples safe. Bind dashboard to localhost.
5. Create a reproducible developer startup path for Linux/mock and Windows/mock, plus Windows/live but NEVER claim the latter ran from Cloud.
6. Build a release acceptance report in `docs/RELEASE_EVIDENCE.md` containing branch, actual SHA, test counts, CI run links for exact SHA, security and licence status, screenshot/browser evidence when available, known issues and next human validation.
7. Create one Draft PR if permissions allow, with reviewable commits and scope/acceptance checklist. Never merge, tag a version or deploy to production automatically.

### Priority and resource rules

Prioritize A -> B -> E (a genuine end-to-end demo) -> C -> D -> F -> G. If the codebase or effort is too large, stop at the last completed coherent slice and save it in git with complete evidence; never fill missing functionality with placeholders and report success.

Make options/XAUUSD a **documented future feature or separate feature-flagged extension** only after A-G are stable. If implemented, start with ONE option expiry and bounded strikes; preserve OI publication/source clocks and independent GC/option/CFD timestamps. Do not infer dealer net gamma from OI alone. Do not introduce an external broker API or trading connector without separate authorization.

### Mandatory final report format

Return exactly these substantive items with **actual** observed values, not forecasts:

- CHECKOUT: STARTER_PRESENT | DOCS_ONLY | EMPTY | WORKSPACE_BOOT_FAILURE
- BRANCH / HEAD SHA / WORKTREE STATUS
- IMPLEMENTED: gate A/B/C/D/E/F/G, each PASS / PARTIAL / FAIL / UNVERIFIED with direct evidence
- FILE CHANGES: concise paths and diff summary
- COMMANDS EXECUTED: command, exit code, pass/fail/skip count and log path
- CI: Ubuntu and Windows workflow run URLs, matching SHA, result, or CI_UNVERIFIED
- UI: real browser smoke PASS/FAIL or BROWSER_UNVERIFIED
- LIVE WINDOWS: LIVE_UNVERIFIED unless non-sensitive local evidence has actually been examined
- DBO: ENTITLED / NOT_ENTITLED / UNVERIFIED, justified by actual local evidence
- RESEARCH H-GC-001: SYNTHETIC_ONLY / UNRESOLVED / REJECT / MODIFY / RETAIN; never claim efficacy from synthetic data
- SECURITY: RPC allowlist tests, loopback boundary, secrets/license audit
- PR: actual Draft PR URL or precise push/permission blocker
- VERDICT: `RC_MOCK_VERIFIED`, `RC_PARTIAL`, `BLOCKED`, or `LIVE_VALIDATED` only when supported
- HUMAN NEXT GATE: exact Windows validation commands and remaining blocker

Avoid claiming any test ran, PR opened, live feed worked, entitlement existed or release was published without tool evidence. Work now on the checked-out code and finish as much as can be objectively proven.

## END MASTER PROMPT

---

## Follow-up prompt for a new task (after saving/merging prior PR)

Read `docs/RELEASE_EVIDENCE.md`, `docs/ACCEPTANCE.md`, the current `AGENTS.md`, the actual latest `main` SHA and all open PRs for `nam28386/gc-live-observatory`. Continue **only the first incomplete gate** of the master implementation contract. Do not recreate earlier code or overwrite existing tests. Make one bounded feature branch/PR and run the relevant regression tests. Report actual SHA, CI, test evidence, risk and next human gate. Do not claim live Rithmic verification from Cloud.

## Local Windows validation reminder

Use the exchange/symbol configuration returned by the user's gateway, not an assumed continuous contract. `GCZ6` is a contractual example, not an automatic current front-month choice. Run `Test-NetConnection 127.0.0.1 -Port 9001`, the appropriate project's `python -m observatory ...` command after verifying the actual CLI, and review subscribe ACK, source/receive timestamps, correct instrument, BBO/trades and optional `mbo-probe` outputs. Never paste credentials, account/order details or a real licensed feed into cloud task logs.

## Source links

- Vendor API: https://github.com/fxaurum/rithmic-api-docs/blob/main/API.md
- Vendor MBO: https://github.com/fxaurum/rithmic-api-docs/blob/main/MBO.md
- Codex Cloud help: https://help.openai.com/en/articles/20001545-using-codex-cloud
- GitHub connection help: https://help.openai.com/en/articles/11145903-connecting-github-to-chatgpt
