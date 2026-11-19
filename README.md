# GC Live Observatory

GC Live Observatory is a read-only COMEX GC observation and research workstation with a synthetic mode for local testing. The browser receives live updates from FastAPI, which reads the state built by a Python collector connected to a real Fxaurum-shaped mock WebSocket. The synthetic exchange clock advances 43 seconds per print. The dashboard marks its source **MOCK / SYNTHETIC** throughout.

## Windows source release candidate

Install uv and Python 3.12 with the locked dependencies, then test the extracted RC using the [Windows quickstart and operator guide](docs/WINDOWS_LIVE_VALIDATION.md). The release ZIP and its SHA256 sidecar are built from an exact Git SHA. For maintainers, run from a Git checkout:

```powershell
$Sha = git rev-parse HEAD
uv run --locked python -m scripts.build_windows_rc --sha $Sha --output "$env:TEMP\gc-release"
```

The archive contains the app and launchers. Dependencies are installed separately. Real Fxaurum access and capture remain local to the authorized Windows workstation.

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

## Windows LIVE

Use an explicitly selected COMEX GC contract and expiry with the loopback Fxaurum adapter. The [Windows operator guide](docs/WINDOWS_LIVE_VALIDATION.md) covers installation, launch, smoke and graceful stop. The [Phase 01 architecture](docs/PHASE01_ARCHITECTURE.md) describes optional local recording and its HTTP history API.

## Phase 02 microstructure

Run the full synthetic workstation from the repo root.

```bash
uv run --locked python -m observatory mock --mode C
uv run --locked python -m scripts.benchmark_phase02 --events 10000 --writes 1000
```

Use mode A for Trade/BBO, B to add MBP and C to add DBO. The default remains A. Open the dashboard on IPv4 loopback port 8000. A local capture can be inspected independently with `uv run --locked python -m observatory.replay <database> <session-id> --mode C`. Use the recorder's session ID and select the captured mode. See [measurement integrity](docs/MEASUREMENT_INTEGRITY.md) for eligibility and [Windows validation](docs/WINDOWS_LIVE_VALIDATION.md) for the entitlement checks.

## Phase 03 capture and validation

Capture remains optional. Supply a new local SQLite file outside Git and choose the batch bound if needed.

```bash
uv run --locked python -m observatory mock --mode C --capture /tmp/gc-pilot.sqlite --batch-size 32
uv run --locked python -m observatory.diagnose --port 8000
uv run --locked python -m scripts.benchmark_phase03 --mode B --cycles 2000 --batch-size 32
```

Use the session and position reported in `GET /api/snapshot` for `observatory.replay <db> <session> --through-id <id> --strict`. Use `observatory.dataset <db> <new-output-directory> --session <id>` to extract H-GC-001 engineering candidates. An input using legacy or pruned capture data is rejected in strict mode. See the [Phase 03 evidence and contracts](docs/PHASE03_EVIDENCE.md) and [Windows operator procedures](docs/WINDOWS_LIVE_VALIDATION.md).

## Phase 04 commissioning and wave research

Start the synthetic dashboard with `uv run --locked python -m observatory mock --mode C`. The intraday wave panel shows the observed H1 → M15 → M5 → M1 hierarchy. The investigator reads the bounded, local `GET /api/research` endpoint for wave and event records. An absent calibrated reference is reported as `EXPECTED_RESPONSE_UNAVAILABLE`.

After extracting a strict local H-GC-001 dataset, run `uv run --locked python -m observatory.evaluate <dataset-directory>` to check eligibility and provenance. Only use `--confirm-permitted-data` with data permitted for empirical evaluation. The handler refuses mock datasets and locks a successful holdout evaluation in that directory.

The [commissioning guide](docs/WINDOWS_LIVE_VALIDATION.md) covers the local physical checks. [Phase 04 evidence](docs/PHASE04_EVIDENCE.md) records synthetic and remote verification separately.

## Phase 05 field evidence

Use the existing Windows scripts to run A, then B, and C after positive local DBO entitlement. [Windows commissioning](docs/WINDOWS_LIVE_VALIDATION.md) documents the sanitized diagnostic and evidence-packet commands. The packet verifier reads an exact local SQLite prefix and can compare it to a private API checkpoint. Its field gates remain unverified without independent evidence. [Phase 05 validation and capacity measurements](docs/PHASE05_EVIDENCE.md)
