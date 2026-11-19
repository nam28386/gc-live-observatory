# GC Live Observatory

PR00 runs a synthetic COMEX GC market-data workflow on a developer machine. The browser receives live updates from FastAPI, which reads the state built by a Python collector connected to a real Fxaurum-shaped mock WebSocket. The synthetic exchange clock advances 43 seconds per print. The dashboard marks its source **MOCK / SYNTHETIC** throughout.

## Run locally

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and use the following commands from the repository root on Linux or Windows.

```bash
uv sync --locked --python 3.12
uv run --locked python -m observatory mock
```

Open `http://127.0.0.1:8000/`. To exercise an interruption, start the app with `uv run --locked python -m observatory mock --disconnect-after 25`. The collector should reconnect with a new ACK ID while the dashboard retains the capture gap. `--port` and `--interval` control the dashboard port and real seconds between mock trades. Press Ctrl+C to stop both services.

| Endpoint | Data |
| --- | --- |
| `GET /` | Four UTC candle panels and capture diagnostics |
| `GET /api/status` | Connection, subscription, freshness and capture coverage |
| `GET /api/snapshot` | Status, observed Trade/BBO and candle history |
| `WS /ws` | Read-only snapshots of the same data |

## Test

```bash
uv run --locked python -m pytest -q -m "not browser"
uv run --locked python -m playwright install chromium
uv run --locked python -m pytest -q -m browser -rs
```

On Linux, use `playwright install --with-deps chromium` if Chromium system dependencies are missing. The browser test also accepts system Chromium or an executable path in `PR00_CHROMIUM`. Set `PR00_REQUIRE_BROWSER=1` to make an unavailable browser fail the test.

The acceptance details, measurement policy and Windows handoff are in [PR00 contracts](docs/PR00_CONTRACTS.md). The [implementation playbook](docs/GC_LIVE_OBSERVATORY_CODEX_CLOUD_PLAYBOOK_2026-11-19.md) and [v1.0 acceptance prompt](docs/GC_CODEX_CLOUD_MASTER_PROMPT_2026-11-19.md) describe the broader project.
