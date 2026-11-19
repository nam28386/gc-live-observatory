"""Collect and normalize the allowlisted Trade/BBO WebSocket streams."""

import asyncio
from dataclasses import replace
from decimal import Decimal, DecimalException
import json
from urllib.parse import urlsplit

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from .events import Observation, ParseError, Trade, WrongInstrument, parse_event
from .state import ObservatoryState
from .journal import Journal, JournalError

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
        journal: Journal | None = None,
    ) -> None:
        endpoint = urlsplit(url)
        if (
            endpoint.scheme != "ws" or endpoint.hostname != "127.0.0.1"
            or endpoint.port is None or (endpoint.port == 9001 and not state.live)
            or endpoint.netloc != f"127.0.0.1:{endpoint.port}"
            or endpoint.path not in ("", "/") or endpoint.query or endpoint.fragment
        ):
            raise ValueError("Expected a loopback mock gateway port or a configured LIVE loopback endpoint")
        self.url = url
        self.journal = journal
        self.state = state
        self.retry_delay = retry_delay
        self.ack_timeout = ack_timeout
        self.next_request_id = 0

    async def run(self) -> None:
        delay = self.retry_delay
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
                    delay = self.retry_delay
                    async for frame in socket:
                        decoded = self.decode(frame)
                        if decoded is not None:
                            await self.apply(*decoded)
                    reason = "client_socket_closed"
                    self.state.issue(reason)
            except JournalError:
                reason = "recorder_failed"
                self.state.recorder_error = True
                self.state.issue(reason)
                return
            except SubscriptionFailure as exc:
                reason = str(exc)
                self.state.issue(reason)
            except ConnectionClosed as exc:
                reason = (
                    "gateway_frame_overflow" if exc.sent and exc.sent.code == 1009
                    else "client_socket_error"
                )
                self.state.issue(reason)
            except (OSError, TimeoutError, WebSocketException):
                reason = "client_socket_error" if self.state.connected else "connect_failed"
                self.state.issue(reason)
            finally:
                if self.state.connected:
                    observed = Observation.now()
                    acknowledged = self.state.ack is not None
                    self.state.on_disconnect(reason, observed)
                    capture_interrupted = acknowledged or reason in (
                        "subscribe_buffer_overflow", "gateway_frame_overflow",
                    )
                    if capture_interrupted and self.journal and not self.state.recorder_error:
                        try:
                            await self.journal.append("gap", observed.utc_ns, {
                                "reason": reason, "request_id": self.next_request_id,
                            })
                        except JournalError:
                            self.state.recorder_error = True
                            self.state.issue("recorder_failed")
            if self.state.recorder_error:
                return
            await asyncio.sleep(delay)
            delay = min(delay * 2, 5.0)

    async def subscribe(self, socket: ClientConnection, request_id: int) -> None:
        await socket.send(json.dumps({
            "method": "SUBSCRIBE", "params": self.state.instrument.streams, "id": request_id,
        }))
        pending: list[tuple[dict, Observation, str]] = []
        try:
            async with asyncio.timeout(self.ack_timeout):
                while True:
                    decoded = self.decode(await socket.recv())
                    if decoded is None:
                        continue
                    payload, observed, _ = decoded
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
                    if self.journal:
                        await self.journal.append("ack", observed.utc_ns, {"request_id": request_id, "response_id": payload["id"]})
                    self.state.on_ack(observed)
                    for message in pending:
                        await self.apply(*message)
                    return
        except TimeoutError:
            raise SubscriptionFailure("subscribe_ack_timeout") from None

    def decode(self, frame: str | bytes) -> tuple[dict, Observation, str] | None:
        observed = Observation.now()
        self.state.last_any = observed
        try:
            payload = json.loads(frame, parse_float=Decimal) if isinstance(frame, str) else None
        except (ValueError, RecursionError, DecimalException):
            payload = None
        if not isinstance(payload, dict):
            self.state.issue("malformed_message")
            return None
        return payload, observed, frame

    async def apply(self, payload: dict, observed: Observation, frame: str) -> None:
        try:
            event = parse_event(payload, self.state.instrument, observed, self.state.source)
        except WrongInstrument:
            await self.reject("wrong_instrument", observed)
        except (ParseError, DecimalException):
            await self.reject("malformed_message", observed)
        else:
            if (
                self.state.live and isinstance(event, Trade)
                and event.exchange_time_ms > observed.utc_ns // 1_000_000 + 5_000
            ):
                await self.reject("future_trade_time", observed)
                return
            if self.journal:
                trade = isinstance(event, Trade)
                if trade:
                    late = self.state.last_trade is not None and (
                        event.exchange_time_ms < self.state.last_trade.exchange_time_ms
                    )
                    eligible = not event.conditional and not late
                    if late and not event.conditional:
                        event = replace(event, quality_flags=(*event.quality_flags, "LATE_TRADE"))
                else:
                    eligible = event.quote is not None and event.quote.quality_flag is None
                coverage = None
                if eligible and (not self.state.coverage or self.state.coverage[-1].ended):
                    coverage = {
                        "coverage_id": self.state.coverage_id + 1,
                        "request_id": self.next_request_id,
                    }
                await self.journal.append("trade" if trade else "bbo", observed.utc_ns, {
                    "event": event.wire(), "raw": frame, "request_id": self.next_request_id,
                }, coverage=coverage)
            if isinstance(event, Trade):
                self.state.on_trade(event)
            else:
                self.state.on_ticker(event)

    async def reject(self, reason: str, observed: Observation) -> None:
        self.state.issue(reason)
        if self.journal:
            await self.journal.append("rejected", observed.utc_ns, {
                "reason": reason, "request_id": self.next_request_id,
            })
