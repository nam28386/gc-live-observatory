"""Deterministic messages served over a real, private Fxaurum-shaped socket."""

import asyncio
from contextlib import asynccontextmanager
import json

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from .collector import MAX_MESSAGE_BYTES, MAX_QUEUE_FRAMES
from .events import Instrument

# Virtual exchange time is deliberately unrelated to wall-clock time.
START_MS = 1_763_568_000_000
TRADE_STEP_MS = 43_000


class MockGateway:
    def __init__(
        self, instrument: Instrument, interval: float = 0.16, disconnect_after: int | None = None
    ) -> None:
        self.instrument = instrument
        self.interval = interval
        self.disconnect_after = disconnect_after
        self.index = 0
        self.disconnected_once = False

    @asynccontextmanager
    async def running(self):
        async with serve(
            self.handle, "127.0.0.1", 0, max_size=MAX_MESSAGE_BYTES,
            max_queue=MAX_QUEUE_FRAMES, close_timeout=1,
        ) as server:
            port = server.sockets[0].getsockname()[1]
            yield f"ws://127.0.0.1:{port}/"

    async def handle(self, socket: ServerConnection) -> None:
        publisher: asyncio.Task | None = None
        try:
            async for frame in socket:
                request = self.request(frame)
                request_id = request.get("id")
                params = request.get("params")
                if (
                    request.get("method") != "SUBSCRIBE"
                    or type(request_id) not in (int, str)
                    or not isinstance(params, list)
                    or not all(isinstance(stream, str) for stream in params)
                    or sorted(stream.lower() for stream in params)
                    != sorted(stream.lower() for stream in self.instrument.streams)
                ):
                    response = {"id": request_id, "status": 400, "error": "Unsupported request"}
                else:
                    response = {"id": request_id, "result": None}
                await socket.send(json.dumps(response))
                if "result" in response and publisher is None:
                    publisher = asyncio.create_task(self.publish(socket))
        finally:
            if publisher is not None:
                publisher.cancel()
                await asyncio.gather(publisher, return_exceptions=True)

    @staticmethod
    def request(frame: str | bytes) -> dict:
        try:
            message = json.loads(frame) if isinstance(frame, str) else None
        except (ValueError, RecursionError):
            return {}
        return message if isinstance(message, dict) else {}

    async def publish(self, socket: ServerConnection) -> None:
        try:
            # On each subscribe, the snapshot includes a last price but no trade.
            await self.send(socket, self.instrument.streams[1], self.quote(max(self.index - 1, 0)))
            while True:
                await asyncio.sleep(self.interval)
                index = self.index
                self.index += 1
                await self.send(socket, self.instrument.streams[0], self.trade(index))
                await self.send(socket, self.instrument.streams[1], self.quote(index))
                if not self.disconnected_once and self.index == self.disconnect_after:
                    self.disconnected_once = True
                    # Advance the fixture clock across two unobserved prints.
                    self.index += 2
                    await socket.close(code=1012, reason="synthetic reconnect exercise")
                    return
        except ConnectionClosed:
            return

    @staticmethod
    def price(index: int) -> int:
        # Oscillation, drift, and a stable tick grid.
        phase = index % 18
        return 27_500 + index // 7 + (phase if phase < 9 else 18 - phase) * 2

    def trade(self, index: int) -> dict:
        time_ms = START_MS + index * TRADE_STEP_MS
        return {
            "e": "trade", "s": self.instrument.symbol, "p": self.price(index) / 10,
            "q": 1 + index % 7, "side": ("B", "S", "B", "")[index % 4],
            "T": time_ms, "tu": time_ms * 1000 + 120, "tn": 340, "r": time_ms + 8, "tc": 0,
        }

    def quote(self, index: int) -> dict:
        price = self.price(index)
        period_start = index - index % 15
        return {
            "s": self.instrument.symbol,
            "b": (price - 1) / 10, "B": 7 + index % 5,
            "a": (price + 1) / 10, "A": 9 + index % 4,
            "l": price / 10, "v": 100 + index * 3, "vf": True,
            "vt": (START_MS + index * TRADE_STEP_MS) * 1_000,
            "oi": 1000 + period_start, "oif": True,
            "oit": (START_MS + period_start * TRADE_STEP_MS) * 1_000,
        }

    @staticmethod
    async def send(socket: ServerConnection, stream: str, data: dict) -> None:
        await socket.send(json.dumps({"stream": stream.lower(), "data": data}))
