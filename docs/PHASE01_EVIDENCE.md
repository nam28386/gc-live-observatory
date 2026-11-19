# Phase 01 evidence

The tested source commit is `dfde12140b80ac77e8bbac63c6a06674ae7f31fb` on `feat/phase01-live-ready`. Its exact base is PR00 `8b40dd8d9e48691a13824d9b4c2636c6cb80fe3f`. The original PR00 full bundle verified successfully. A non-force atomic Git push restored the original `main` and PR00 refs. A subsequent non-force Phase 01 push and `git ls-remote` confirmed all three remote refs. The current branch includes this evidence commit after the tested source commit.

| Gate | Observed result |
| --- | --- |
| SOURCE_RECOVERED | Original PR00 ancestry verified. Remote refs agree with the original history. |
| MOCK_REGRESSION_PASS | 30 baseline tests passed, then 37 tests passed with Phase 01 changes. |
| LIVE_CONNECTOR_CODE_READY | Synthetic vendor-profile socket exercised the LIVE adapter, one allowlisted request, correlated ACK, held socket during silence, source/contract, volume/OI clocks and rejected future trade time. |
| LOCAL_RECORDING_VERIFIED | Tested durable event/order history, recorder fail-stop, subprocess crash recovery, transport gaps, restart timeline and retention. |
| WINDOWS_OPERATOR_READY / PARTIAL | Operator scripts and CLI recipe supplied. PowerShell and the physical Windows setup were unavailable here. |
| CI_UNVERIFIED | Ubuntu and Windows Python 3.12 workflow defined. GitHub denied the Actions query, so no run URL or outcome was established. |
| WINDOWS_LIVE_UNVERIFIED | No actual local Fxaurum/Rithmic gateway was accessed. |

## Commands and outcomes

Linux Python 3.12 and system Chromium were used. uv used a local cache directory. All captured fixtures were synthetic.

| Command | Exit | Result |
| --- | ---: | --- |
| `PR00_REQUIRE_BROWSER=1 uv run --locked python -m pytest -q` at PR00 | 0 | 30 passed, 0 skipped |
| Same command at Phase 01 | 0 | 37 passed, 0 skipped, including Chromium |
| `node --check observatory/static/dashboard.js` | 0 | JavaScript syntax validated |
| `uv lock --check` | 0 | Existing dependency lock validated |
| `git diff --cached --check` | 0 | No staged whitespace errors |
| `git bundle verify /workspace/scratch/gc-observatory-pr00.bundle` | 0 | Full original history verified |
| `git push --atomic origin main feat/pr00-mock-vertical-slice` | 0 | Original refs restored |
| `git merge-base --is-ancestor 8b40dd8 HEAD` and `git push origin feat/phase01-live-ready` | 0 each | PR00 lineage preserved and Phase 01 pushed |
| `git ls-remote origin` | 0 | Remote refs observed |
| `gh pr create --draft --base feat/pr00-mock-vertical-slice --head feat/phase01-live-ready …` | 1 | GitHub GraphQL Forbidden |
| `gh run list --branch feat/phase01-live-ready --json headSha,status,conclusion,url --limit 5` | 1 | GitHub Actions API Forbidden |

Chromium exercised the actual mock CLI with recording enabled. It observed a correlated new ACK after a forced reconnect, four updating canvases, recorder status and persistent interruption history. The recovery test abruptly killed a subprocess after a durable write. The restarted writer found the preserved record, marked the previous session unclean and reported a restart gap.

## Changed areas and security

- `observatory/` extends the shared collector, timing and chart pipeline with LIVE configuration, SQLite journaling, read-only history and local diagnostics.
- `scripts/windows/` adds setup, loopback preflight, launch, smoke and graceful stop.
- `tests/` adds synthetic vendor-profile, journal and security checks and extends the browser test.
- `.github/workflows/pr00.yml` runs Ubuntu and Windows Python 3.12 synthetic checks and defines Windows PowerShell syntax validation.
- `docs/` records architecture, measurement assumptions, Windows instructions and release gates.

Loopback endpoint guards, HTTP Host and browser WebSocket Origin checks and forbidden RPC regression were verified. Live capture is opt-in and excluded from Git by path checks and ignore rules. A tracked-path and targeted credential-signature scan found no generated captures or common credential signatures. Real feed rates, rights, local gateway connectivity, Windows operator scripts and exchange expiry confirmation remain separate operator gates.

## Delivery

Stack Phase 01 on PR00 while PR00 is unmerged. Open the GitHub compare view with `feat/pr00-mock-vertical-slice` as base and `feat/phase01-live-ready` as head. Git write access worked. GitHub API rejected Draft PR creation and no PR URL was verified. Follow the [Windows operator procedure](WINDOWS_LIVE_VALIDATION.md) for the next physical validation.
