"""Collect and normalize the allowlisted Trade/BBO WebSocket streams."""

import asyncio
from decimal import Decimal, DecimalException
import json
from urllib.parse import urlsplit

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

from .events import Observation, ParseError, Trade, WrongInstrument, parse_event
from .state import ObservatoryState

MAX_MESSAGE_BYTES = 64 * 1024
MAX_QUEUE_FRAMES = 16
MAX_PRE_ACK_EVENTS = 32


class SubscriptionFailure(Exception):
    pass


class Collector:
    def __init__(
        self,
        url: str,
        state: ObservatoryState,
        *,
        retry_delay: float = 0.25,
        ack_timeout: float = 2.0,
    ) -> None:
        endpoint = urlsplit(url)
        if (
            endpoint.scheme != "ws" or endpoint.hostname != "127.0.0.1"
            or endpoint.port in (None, 9001) or endpoint.username is not None
        ):
            raise ValueError("PR00 requires an isolated loopback mock gateway port")
        self.url = url
        self.state = state
        self.retry_delay = retry_delay
        self.ack_timeout = ack_timeout
        self.next_request_id = 0

    async def run(self) -> None:
        while True:
            reason = "collector_stopped"
            try:
                # Bounded frame buffering applies transport backpressure. No event
                # queue is drained into an unbounded task or silently overwritten.
                async with connect(
                    self.url, proxy=None, max_size=MAX_MESSAGE_BYTES,
                    max_queue=MAX_QUEUE_FRAMES, open_timeout=2, close_timeout=1,
                    ping_interval=10, ping_timeout=10,
                ) as socket:
                    self.next_request_id += 1
                    self.state.on_connect(self.next_request_id)
                    await self.subscribe(socket, self.next_request_id)
                    async for frame in socket:
                        decoded = self.decode(frame)
                        if decoded is not None:
                            self.apply(*decoded)
                    reason = "client_socket_closed"
                    self.state.issue(reason)
            except SubscriptionFailure as exc:
                reason = str(exc)
                self.state.issue(reason)
            except (OSError, TimeoutError, WebSocketException):
                reason = "client_socket_error" if self.state.connected else "connect_failed"
                self.state.issue(reason)
            finally:
                if self.state.connected:
                    self.state.on_disconnect(reason, Observation.now())
            await asyncio.sleep(self.retry_delay)

    async def subscribe(self, socket: ClientConnection, request_id: int) -> None:
        await socket.send(json.dumps({
            "method": "SUBSCRIBE", "params": self.state.instrument.streams, "id": request_id,
        }))
        pending: list[tuple[dict, Observation]] = []
        try:
            async with asyncio.timeout(self.ack_timeout):
                while True:
                    decoded = self.decode(await socket.recv())
                    if decoded is None:
                        continue
                    payload, observed = decoded
                    if "stream" in payload:
                        if len(pending) == MAX_PRE_ACK_EVENTS:
                            raise SubscriptionFailure("subscribe_buffer_overflow")
                        pending.append(decoded)
                        continue
                    if type(payload.get("id")) is not int or payload["id"] != request_id:
                        self.state.issue("unexpected_ack_id")
                        continue
                    status = payload.get("status", 200)
                    if (
                        type(status) is not int or not 200 <= status < 300
                        or "error" in payload or "result" not in payload or payload["result"] is not None
                    ):
                        raise SubscriptionFailure("subscribe_rejected")
                    self.state.on_ack(observed)
                    for message in pending:
                        self.apply(*message)
                    return
        except TimeoutError:
            raise SubscriptionFailure("subscribe_ack_timeout") from None

    def decode(self, frame: str | bytes) -> tuple[dict, Observation] | None:
        observed = Observation.now()
        self.state.last_any = observed
        try:
            payload = json.loads(frame, parse_float=Decimal) if isinstance(frame, str) else None
        except (ValueError, RecursionError, DecimalException):
            payload = None
        if not isinstance(payload, dict):
            self.state.issue("malformed_message")
            return None
        return payload, observed

    def apply(self, payload: dict, observed: Observation) -> None:
        try:
            event = parse_event(payload, self.state.instrument, observed, "fxaurum_mock")
        except WrongInstrument:
            self.state.issue("wrong_instrument")
        except (ParseError, DecimalException):
            self.state.issue("malformed_message")
        else:
            if isinstance(event, Trade):
                self.state.on_trade(event)
            else:
                self.state.on_ticker(event)
