# PR00 evidence

## Checkout

| Item | Observed value |
| --- | --- |
| Repository | `nam28386/gc-live-observatory` |
| Feature branch | `feat/pr00-mock-vertical-slice` |
| Tested implementation HEAD | `81eeb1bc9abe2851ae2ee646f1301b8de73cc504` |
| Local `main` | Docs-only baseline `4ba149f` from the two supplied specifications |
| Runtime | Linux, CPython 3.12.14, uv 0.13.6 |
| Browser | Headless system Chromium 156.0.8078.79 |
| Verdict | v0.2 MOCK verified on Linux |

The original checkout had no commits or application files. The initial `git log -1 --oneline` reported an unborn branch. The tested commit is the source revision referenced by this evidence commit.

- `observatory/events.py`, `collector.py`, `state.py` and `mock_gateway.py` implement the stream contract, capture state and synthetic gateway.
- `observatory/web.py`, `__main__.py` and `static/` provide the loopback launcher, snapshot API and four live charts.
- `tests/` verifies normalization, state, loopback WebSocket recovery, dashboard rendering and the read-only boundary.
- `pyproject.toml`, `uv.lock` and `.github/workflows/pr00.yml` define the locked Python environment and Ubuntu/Windows CI.
- `README.md`, `AGENTS.md`, `.gitignore` and [PR00 contracts](PR00_CONTRACTS.md) document setup, data policy, Windows handoff and future contracts.

## Commands and outcomes

The following were run in the repository root. The shell used a writable `UV_CACHE_DIR=/tmp/gc-observatory-uv` for uv. Test selections below are disjoint.

| Command | Exit | Result |
| --- | ---: | --- |
| `uv lock --python 3.12` | 0 | Resolved the Python lock |
| `uv sync --locked --python 3.12` | 0 | Installed locked dependencies |
| `uv lock --check` and `uv pip check --python .venv/bin/python` | 0 each | Lock matches manifests and installed dependencies are compatible |
| `uv run --locked python -m pytest -q tests/test_events_and_state.py` | 0 | 17 unit passed |
| `uv run --locked python -m pytest -q tests/test_websocket_pipeline.py -k 'not (rejects_account or cannot_forward or refuses_live)'` | 0 | 7 integration passed, 5 deselected |
| `uv run --locked python -m pytest -q tests/test_websocket_pipeline.py -k 'rejects_account or cannot_forward or refuses_live'` | 0 | 5 security passed, 7 deselected |
| `PR00_REQUIRE_BROWSER=1 uv run --locked python -m pytest -q -m browser -rs` | 0 | 1 browser passed, 29 deselected |
| `node --check observatory/static/dashboard.js` | 0 | Browser script parsed |
| `git diff --check main..HEAD` | 0 | No whitespace errors |
| `git ls-remote --exit-code origin refs/heads/main` | 2 | No matching remote main ref |
| `git push --dry-run origin HEAD:refs/heads/feat/pr00-mock-vertical-slice` | 0 | Advertised a new branch in dry-run |
| `gh api repos/nam28386/gc-live-observatory --jq '{default_branch, permissions}'` | 1 | GitHub API returned Forbidden |

Total for the four test selections was **30 passed, 0 failed, 0 skipped**. Chromium launched against the real CLI and verified that all four canvases rendered and updated, the synthetic label and spread appeared, and a new ACK was displayed after reconnect while the historical gap remained visible.

## Security and publication

The launcher and mock gateway bound to loopback. Tests rejected broker/account/order RPCs, unsubscribed instruments, inbound dashboard WebSocket messages, arbitrary external endpoints and the Windows gateway port. A tracked-path scan found no environment files, capture paths, database files, logs or ZIPs. A targeted signature scan returned no common GitHub tokens, AWS access keys or private key headers. The fixtures comprise generated synthetic data.

**Draft PR blocked.** Remote `main` was absent and GitHub API access returned Forbidden. Both branches and all commits are local. The successful Git dry-run advertised a possible branch update, with zero remote writes performed. Publish the reviewed docs-only baseline as `main` and the feature branch when GitHub access allows, then open the Draft PR against `main`.

**CI_UNVERIFIED.** Python 3.12 CI is defined for Ubuntu and Windows. The Linux CI job also defines a required Chromium test. GitHub Actions runs for the commit and Windows execution remain unverified.

## Reproduce and hand off

```bash
uv sync --locked --python 3.12
uv run --locked python -m observatory mock --disconnect-after 25
uv run --locked python -m pytest -q
```

The first test on Windows is the same mock quickstart and suite. Windows Rithmic is **LIVE_UNVERIFIED**. After a local live adapter exists, follow the [Windows live handoff](PR00_CONTRACTS.md#windows-live-handoff) and verify a matched ACK, BBO and a fresh trade on the locally confirmed contract.
