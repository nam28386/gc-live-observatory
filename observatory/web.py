"""Loopback FastAPI app wired to the synthetic gateway and collector."""

import asyncio
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

STATIC = Path(__file__).with_name("static")


def create_app(
    *, interval: float = 0.16, disconnect_after: int | None = None,
    instrument: Instrument = MOCK_GC, live_gateway_port: int | None = None,
    capture_path: Path | None = None, retention_days: int = 7,
    stale_after: float = 3.0, mode: str = "A", mbo_entitled: bool = False,
) -> FastAPI:
    live = live_gateway_port is not None
    if mode not in ("A", "B", "C"):
        raise ValueError("Unknown market data mode")
    requested_mode = mode
    if live and mode == "C" and not mbo_entitled:
        mode = "B"
    state = ObservatoryState(instrument, stale_after=stale_after, live=live)
    journal = Journal(capture_path, {
        "source": state.source, "exchange": instrument.exchange,
        "symbol": instrument.symbol, "expiry": instrument.expiry,
        "measurement_schema_version": 2, "stale_after_ns": state.stale_after_ns,
        "protocol_revision": "581cb6b3706c93ccd1667b32234e904654a44728",
    }, retention_days) if capture_path is not None else None

    micro = Microstructure(mode, instrument, state.source, state.stale_after_ns)
    if mode == "C":
        micro.entitlement = "OPERATOR_REPORTED" if live else "SYNTHETIC"
    elif requested_mode == "C":
        micro.entitlement = "UNVERIFIED"
    micro.capture_id = journal.session if journal else None
    started = Observation.now().monotonic_ns

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        async with AsyncExitStack() as stack:
            if journal:
                await stack.enter_async_context(journal.running())
            if live:
                endpoint = f"ws://127.0.0.1:{live_gateway_port}/"
            else:
                gateway = MockGateway(instrument, interval, disconnect_after, mode=mode)
                endpoint = await stack.enter_async_context(gateway.running())
            collector = asyncio.create_task(Collector(endpoint, state, journal=journal, micro=micro).run())
            try:
                yield
            finally:
                collector.cancel()
                with suppress(asyncio.CancelledError):
                    await collector

    async def current_snapshot(*, full: bool) -> dict:
        now = Observation.now()
        snapshot = state.snapshot(now) if full else {"status": state.status(now)}
        status = snapshot["status"]
        status["market_data_mode"] = micro.mode
        status["requested_market_data_mode"] = requested_mode
        status["mbp_status"] = micro.depth.state if micro.depth else "DISABLED"
        status["mbo_entitlement"] = micro.entitlement
        if full:
            snapshot["microstructure"] = micro.snapshot(now)
        status["recorder"] = {
            "enabled": journal is not None,
            "session_id": journal.session if journal else None,
            "durable_events": journal.written if journal else 0,
            "failures": journal.failures if journal else 0,
            "average_records_per_second": (
                journal.written * 1e9 / max(1, now.monotonic_ns - started) if journal else None
            ),
        }
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
