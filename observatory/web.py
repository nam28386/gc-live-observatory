"""Loopback FastAPI app wired to the synthetic gateway and collector."""

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .collector import Collector
from .events import MOCK_GC, Observation
from .mock_gateway import MockGateway
from .state import ObservatoryState

STATIC = Path(__file__).with_name("static")


def create_app(
    *, interval: float = 0.16, disconnect_after: int | None = None
) -> FastAPI:
    state = ObservatoryState(MOCK_GC)
    gateway = MockGateway(MOCK_GC, interval, disconnect_after)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        async with gateway.running() as endpoint:
            collector = asyncio.create_task(Collector(endpoint, state).run())
            try:
                yield
            finally:
                collector.cancel()
                with suppress(asyncio.CancelledError):
                    await collector

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", response_class=FileResponse)
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/status")
    async def status():
        return state.status(Observation.now())

    @app.get("/api/snapshot")
    async def snapshot():
        return state.snapshot(Observation.now())

    @app.websocket("/ws")
    async def updates(websocket: WebSocket):
        await websocket.accept()
        try:
            while True:
                await asyncio.wait_for(websocket.send_json(state.snapshot(Observation.now())), timeout=1)
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
