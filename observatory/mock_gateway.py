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
        self, instrument: Instrument, interval: float = 0.16, disconnect_after: int | None = None, *, mode: str = "A"
    ) -> None:
        self.instrument = instrument
        self.interval = interval
        self.disconnect_after = disconnect_after
        self.index = 0
        self.disconnected_once = False
        self.mode = mode
        self.generation = 0
        self.seq = 0
        self.ask_size = 10

    @property
    def streams(self):
        prefix = f"{self.instrument.exchange}.{self.instrument.symbol}"
        streams = self.instrument.streams
        if self.mode in ("B", "C"):
            streams += (f"{prefix}@depthraw",)
        if self.mode == "C":
            streams += (f"{prefix}@mboraw",)
        return streams

    def image(self, src=0):
        return {"type": "Image", "gen": self.generation, "seq": self.seq, "src": src,
                "rows": [[0, 2749.9, 8, 2], [0, 2749.8, 7, 2],
                         [1, 2750.1, self.ask_size, 3], [1, 2750.2, 9, 2]]}

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
                    self.mode != "A" and publisher is not None and request.get("method") == "depthSnap"
                    and type(request_id) in (str, int)
                    and params == {"exchange": self.instrument.exchange, "symbol": self.instrument.symbol}
                ):
                    await self.reply_snapshot(socket, request_id)
                    continue
                if (
                    request.get("method") != "SUBSCRIBE"
                    or type(request_id) not in (int, str)
                    or not isinstance(params, list)
                    or not all(isinstance(stream, str) for stream in params)
                    or sorted(stream.lower() for stream in params)
                    != sorted(stream.lower() for stream in self.streams)
                ):
                    response = {"id": request_id, "status": 400, "error": "Unsupported request"}
                else:
                    response = {"id": request_id, "result": None}
                await socket.send(json.dumps(response))
                if "result" in response and publisher is None:
                    publisher = asyncio.create_task(self.publish(socket))
        except ConnectionClosed:
            # The peer may leave while an ACK or recovery reply is being sent.
            return
        except OSError as exc:
            # Windows Proactor can surface an aborted peer connection before
            # websockets translates it into ConnectionClosed.
            if getattr(exc, "winerror", None) not in (64, 995, 10053, 10054):
                raise
        finally:
            if publisher is not None:
                publisher.cancel()
                await asyncio.gather(publisher, return_exceptions=True)

    async def reply_snapshot(self, socket, request_id):
        await socket.send(json.dumps({"id": request_id, "result": self.image(1)}))

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
            if self.mode != "A":
                self.generation += 1
                self.seq = 0
                await self.send(socket, self.streams[2], {"type": "Reset", "gen": self.generation})
                await self.send(socket, self.streams[2], self.image())
            if self.mode == "C":
                await self.send(socket, self.streams[3], {"type": "Reset"})
                await self.send(socket, self.streams[3], {"type": "Image", "oid": "seed",
                                "p": 2750.1, "side": "S", "q": 4, "pri": "31", "k": 0, "ki": 0})
            initial_index = self.index
            while True:
                await asyncio.sleep(self.interval)
                index = self.index
                self.index += 1
                await self.send(socket, self.instrument.streams[0], self.trade(index))
                await self.send(socket, self.instrument.streams[1], self.quote(index))
                if self.mode != "A":
                    self.seq += 2 if index == 12 else 1
                    self.ask_size = 5 + index % 9
                    await self.send(socket, self.streams[2], {
                        "type": "Update", "gen": self.generation, "seq": self.seq, "src": 0,
                        "side": "S", "p": 2750.1, "q": self.ask_size, "n": 3,
                        "tu": self.trade(index)["tu"], "gw": self.trade(index)["r"],
                    })
                if self.mode == "C":
                    oid = str(index // 4)
                    kind = ("New", "Change", "Fill", "Delete")[index % 4]
                    if kind == "New" or index == 0:
                        order = {"type": kind, "oid": oid, "side": "S", "p": 2750.1, "q": 4, "pri": str(index)}
                    elif kind == "Change":
                        order = {"type": kind, "oid": oid, "q": 3}
                    elif kind == "Fill":
                        inferred = (index // 4) % 2
                        order = {"type": kind, "oid": oid, "side": "S", "p": 2750.1,
                                 "q": 1, "k": 1, "ki": inferred, "src": inferred}
                    else:
                        order = {"type": kind, "oid": oid}
                    if index // 4 != initial_index // 4 or initial_index % 4 == 0:
                        await self.send(socket, self.streams[3], order)
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
            "b": (price - 1) / 10 if self.mode == "A" else 2749.9, "B": 7 + index % 5,
            "a": (price + 1) / 10 if self.mode == "A" else 2750.1, "A": 9 + index % 4,
            "l": price / 10, "v": 100 + index * 3, "vf": True,
            "vt": (START_MS + index * TRADE_STEP_MS) * 1_000,
            "oi": 1000 + period_start, "oif": True,
            "oit": (START_MS + period_start * TRADE_STEP_MS) * 1_000,
        }

    @staticmethod
    async def send(socket: ServerConnection, stream: str, data: dict) -> None:
        await socket.send(json.dumps({"stream": stream.lower(), "data": data}))
