"""Loopback FastAPI app wired to the synthetic gateway and collector."""

import asyncio
import platform
from functools import lru_cache
import subprocess
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .collector import Collector
from .events import Instrument, MOCK_GC, Observation
from .journal import Journal, JournalError
from .microstructure import Microstructure
from .mock_gateway import MockGateway
from .state import ObservatoryState
from .waves import research_event, research_object
from .quality import SessionCalendar, registry, summarize

STATIC = Path(__file__).with_name("static")


@lru_cache(maxsize=1)
def software_revision():
    try:
        output = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=STATIC.parent.parent,
            stderr=subprocess.DEVNULL, text=True, timeout=2,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return output if len(output) == 40 and all(c in "0123456789abcdef" for c in output) else None


def create_app(
    *, interval: float = 0.16, disconnect_after: int | None = None,
    instrument: Instrument = MOCK_GC, live_gateway_port: int | None = None,
    capture_path: Path | None = None, retention_days: int = 7,
    stale_after: float = 3.0, mode: str = "A", mbo_entitled: bool = False,
    mock_gateway: MockGateway | None = None, batch_size: int = 32,
    calendar: SessionCalendar | None = None,
) -> FastAPI:
    if not 1 <= batch_size <= 64:
        raise ValueError("Capture batch size must be 1–64")
    live = live_gateway_port is not None
    if mode not in ("A", "B", "C"):
        raise ValueError("Unknown market data mode")
    requested_mode = mode
    if live and mode == "C" and not mbo_entitled:
        mode = "B"
    instrument_registry = registry(instrument, calendar)
    state = ObservatoryState(instrument, stale_after=stale_after, live=live)
    journal = Journal(capture_path, {
        "source": state.source, "exchange": instrument.exchange,
        "symbol": instrument.symbol, "expiry": instrument.expiry,
        "measurement_schema_version": 4, "stale_after_ns": state.stale_after_ns,
        "software_revision": software_revision(), "producer_os": platform.system(), "instrument_registry": instrument_registry,
        "timestamp_contract": "utc_ns and process monotonic_ns; source clocks per instrument contract",
        "capture_config": {"mode": mode, "batch_size": batch_size, "durability": "SQLite WAL FULL",
                           "retention_days": retention_days, "batch_residence_ms": 4},
        "protocol_revision": "581cb6b3706c93ccd1667b32234e904654a44728",
    }, retention_days, batch_size=batch_size) if capture_path is not None else None

    micro = Microstructure(mode, instrument, state.source, state.stale_after_ns)
    if mode == "C":
        micro.entitlement = "OPERATOR_REPORTED" if live else "SYNTHETIC"
    elif requested_mode == "C":
        micro.entitlement = "UNVERIFIED"
    micro.capture_id = journal.session if journal else None
    started = Observation.now()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        async with AsyncExitStack() as stack:
            if journal:
                await stack.enter_async_context(journal.running())
            if live:
                endpoint = f"ws://127.0.0.1:{live_gateway_port}/"
            else:
                gateway = mock_gateway or MockGateway(instrument, interval, disconnect_after, mode=mode)
                endpoint = await stack.enter_async_context(gateway.running())
            app.state.collector = Collector(endpoint, state, journal=journal, micro=micro, session_calendar=calendar)
            collector = asyncio.create_task(app.state.collector.run())
            try:
                yield
            finally:
                collector.cancel()
                with suppress(asyncio.CancelledError):
                    await collector
                if journal and not journal.publication_failed:
                    now = Observation.now()
                    ended = state.status(now)
                    ended["requested_market_data_mode"] = requested_mode
                    conclusion = summarize(state, ended, micro, now, started, calendar,
                                           journal.diagnostics(now.monotonic_ns))
                    conclusion["freshness_samples"] = app.state.collector.freshness_samples
                    conclusion["receiver"] = app.state.collector.receiver_diagnostics()
                    conclusion["recorder"] = journal.diagnostics(now.monotonic_ns)
                    conclusion["committed_market_records"] = journal.market_records
                    conclusion["effective_mode"] = micro.mode
                    await journal.append("capture_summary", now.utc_ns, conclusion)

    async def current_snapshot(*, full: bool) -> dict:
        if journal:
            backlog = len(journal.staged or ())
            async with journal.visibility:
                snapshot = capture_snapshot(full=full)
            status = snapshot["status"]
            status["recorder"]["backlog_records"] = backlog
            status["quality"]["local_pressure_observed"] = backlog > 0
            return snapshot
        return capture_snapshot(full=full)

    def capture_snapshot(*, full: bool) -> dict:
        now = Observation.now()
        failed = journal is not None and journal.publication_failed
        source_state = ObservatoryState(instrument, stale_after=stale_after, live=live) if failed else state
        if failed:
            source_state.recorder_error = True
            source_state.issue("capture_publication_invalid")

        snapshot = source_state.snapshot(now) if full else {"status": source_state.status(now)}
        status = snapshot["status"]
        agent = app.state.collector
        status["receiver"] = agent.receiver_diagnostics()
        status["market_data_mode"] = micro.mode
        status["requested_market_data_mode"] = requested_mode
        status["mbp_status"] = "INVALID" if failed else micro.depth.state if micro.depth else "DISABLED"
        status["mbo_entitlement"] = micro.entitlement
        source_micro = Microstructure(mode, instrument, source_state.source) if failed else micro
        if full:
            snapshot["microstructure"] = source_micro.snapshot(now)
            if journal and not failed:
                snapshot["capture_checkpoint"] = {"session_id": journal.session,
                                                  "durable_position": journal.last_record_id,
                                                  "as_of": now.wire(), "mode": micro.mode}
        status["recorder"] = {
            **(journal.diagnostics(now.monotonic_ns) if journal else {}),
            "enabled": journal is not None,
            "session_id": journal.session if journal else None,
            "durable_events": journal.written if journal else 0,
            "failures": journal.failures if journal else 0,
            "average_records_per_second": (
                journal.written * 1e9 / max(1, now.monotonic_ns - started.monotonic_ns) if journal else None
            ),
        }
        status["software_revision"] = software_revision()
        status["instrument_registry"] = instrument_registry
        status["quality"] = summarize(source_state, status, source_micro, now, started, calendar, status["recorder"])
        status["quality"]["freshness_samples"] = agent.freshness_samples
        status["quality"]["freshness_sample_basis"] = "arriving frames, at most ten samples per second"
        return snapshot

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"] if live else ["*"])
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", response_class=FileResponse)
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/status")
    async def status():
        return (await current_snapshot(full=False))["status"]

    @app.get("/api/snapshot")
    async def snapshot():
        return await current_snapshot(full=True)

    @app.get("/api/research")
    async def research():
        view = (await current_snapshot(full=True))["microstructure"]
        objects = [research_object(wave, view["evidence"]) for wave in view["waves"]]
        return {
            "hypothesis": "H-GC-001", "scientific_status": "UNRESOLVED",
            "empirical_evaluation": "NOT_RUN_INSUFFICIENT_DATA",
            "wave_objects": objects,
            "event_objects": [research_event(event) for event in view["evidence"]],
            "population_scope": "retained window only",
            "excluded_observed_candidates": sum(card.get("status") == "INELIGIBLE" for card in view["evidence"]),
            "eligible_observed_candidates": sum(card.get("label") is not None for card in view["evidence"]),
            "coverage": "bounded current observations, no empirical reference loaded",
        }

    @app.get("/api/history")
    async def history(after: int = Query(0, ge=0, le=2**63 - 1),
                      limit: int = Query(100, ge=1, le=250), gaps: bool = False):
        if journal is None:
            raise HTTPException(404, "Local capture is disabled")
        try:
            records = await journal.io(journal.history, after, limit, gaps=gaps)
        except JournalError:
            raise HTTPException(503, "Local capture is unavailable") from None
        return {"records": records}

    @app.websocket("/ws")
    async def updates(websocket: WebSocket):
        origin = websocket.headers.get("origin")
        if live and origin is not None and origin != f"http://{websocket.headers.get('host')}":
            await websocket.close(code=1008)
            return
        await websocket.accept()
        try:
            while True:
                await asyncio.wait_for(websocket.send_json(await current_snapshot(full=True)), timeout=1)
                # The dashboard consumes snapshots, which also refresh freshness
                # states during upstream silence. It cannot send gateway commands.
                try:
                    message = await asyncio.wait_for(websocket.receive(), timeout=0.25)
                except TimeoutError:
                    continue
                if message["type"] != "websocket.disconnect":
                    await websocket.close(code=1008)
                break
        except (WebSocketDisconnect, TimeoutError, OSError):
            return

    return app
