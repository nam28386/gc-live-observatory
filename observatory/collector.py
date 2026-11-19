"""Collect and normalize the allowlisted Trade/BBO WebSocket streams."""

import asyncio
from dataclasses import replace
from decimal import Decimal, DecimalException
import json
from time import perf_counter_ns
from urllib.parse import urlsplit

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from .books import parse_depth, parse_order, stream_payload
from .microstructure import Microstructure
from .events import Observation, ParseError, Trade, WrongInstrument, parse_event
from .state import ObservatoryState
from .journal import Journal, JournalError

MAX_MESSAGE_BYTES = 512 * 1024
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
        micro: Microstructure | None = None,
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
        self.micro = micro
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
                    await self.receive_market(socket)
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
                if reason == "subscribe_rejected" and self.micro and self.micro.mode != "A":
                    if self.micro.mode == "C":
                        self.micro.entitlement = "SUBSCRIPTION_REJECTED"
                        self.micro.mode = "B"
                        self.micro.orders = None
                    else:
                        self.micro.mode = "A"
                        self.micro.depth = None
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
                    if self.micro:
                        self.micro.disconnect(observed)
                        if not self.state.recorder_error:
                            try:
                                await self.persist_cards(observed)
                            except JournalError:
                                self.state.recorder_error = True
                                self.state.issue("recorder_failed")
                    capture_interrupted = acknowledged or reason in (
                        "subscribe_buffer_overflow", "gateway_frame_overflow",
                    )
                    if capture_interrupted and self.journal and not self.state.recorder_error:
                        try:
                            await self.journal.append("gap", observed.utc_ns, {
                                "reason": reason, "request_id": self.state.request_id, "observed": observed.wire(),
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
            "method": "SUBSCRIBE", "params": self.streams, "id": request_id,
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
                        await self.journal.append("ack", observed.utc_ns, {"request_id": request_id, "response_id": payload["id"], "observed": (pending[0][1] if pending else observed).wire()})
                    self.state.on_ack(observed)
                    if self.micro:
                        self.micro.connect(pending[0][1] if pending else observed)
                    for message in pending:
                        await self.process(*message)
                    return
        except TimeoutError:
            raise SubscriptionFailure("subscribe_ack_timeout") from None

    @property
    def streams(self):
        instrument = self.state.instrument
        base = instrument.streams
        if self.micro and self.micro.depth:
            base += (f"{instrument.exchange}.{instrument.symbol}@depthraw",)
        if self.micro and self.micro.orders:
            base += (f"{instrument.exchange}.{instrument.symbol}@mboraw",)
        return base

    async def receive_market(self, socket):
        pending = None
        recovery_key = None
        attempts = 0
        reset_deadline = None
        loop = asyncio.get_running_loop()
        while True:
            depth = self.micro.depth if self.micro else None
            if depth:
                if depth.state == "SNAPSHOT_PENDING" and reset_deadline is None:
                    reset_deadline = loop.time() + self.ack_timeout
                if depth.state != "SNAPSHOT_PENDING":
                    reset_deadline = None
                elif loop.time() >= reset_deadline:
                    observed = Observation.now()
                    depth.invalidate("snapshot_timeout", observed)
                    if self.journal:
                        await self.journal.append("mbp_gap", observed.utc_ns, depth.gaps[-1])
                    reset_deadline = None
                key = depth.gen, depth.epoch
                if key != recovery_key:
                    pending, attempts, recovery_key = None, 0, key
                if pending and (depth.valid or loop.time() >= pending[1]):
                    if not depth.valid:
                        depth.counts["rpc_timeout"] += 1
                    pending = None
                if depth.state in ("GAP_DETECTED", "RECOVERING", "OVERFLOW") and depth.gen is not None and pending is None:
                    if attempts < 3:
                        attempts += 1
                        self.next_request_id += 1
                        request_id = self.next_request_id
                        await socket.send(json.dumps({
                            "method": "depthSnap", "id": request_id,
                            "params": {"exchange": self.state.instrument.exchange, "symbol": self.state.instrument.symbol},
                        }))
                        pending = request_id, loop.time() + self.ack_timeout
                        depth.state = "RECOVERING"
                    else:
                        depth.state = "RECOVERY_FAILED"
                        depth.counts["retry_exhausted"] += 1
            try:
                deadline = pending[1] if pending else reset_deadline
                if deadline is not None:
                    frame = await asyncio.wait_for(socket.recv(), max(0, deadline - loop.time()))
                else:
                    frame = await socket.recv()
            except TimeoutError:
                continue
            except ConnectionClosed as exc:
                if exc.rcvd and exc.rcvd.code in (1000, 1001):
                    return
                raise
            decoded = self.decode(frame)
            if decoded is None:
                continue
            payload, observed, raw = decoded
            if "stream" in payload:
                await self.process(*decoded)
            elif pending and type(payload.get("id")) is int and payload["id"] == pending[0]:
                pending = None
                try:
                    data = payload.get("result")
                    if ("error" in payload or payload.get("status", 200) != 200
                        or not isinstance(data, dict) or data.get("type") != "Image"
                        or ("s" in data and str(data["s"]).upper() != self.state.instrument.symbol)):
                        raise ParseError("Unexpected depthSnap response")
                    event = parse_depth(data, observed)
                except (ParseError, DecimalException):
                    depth.counts["invalid_rpc_response"] += 1
                else:
                    await self.apply_book(event, "depth_snapshot", raw)

    async def apply_book(self, event, kind, raw):
        depth = self.micro.depth
        gaps_before = depth.counts["gaps"] if depth else 0
        recoveries_before = depth.counts["recoveries"] if depth else 0
        overflows_before = depth.counts["buffer_overflow"] if depth else 0
        order_status_before = self.micro.orders.status if self.micro.orders else None
        if self.journal:
            await self.journal.append(kind, event.observed.utc_ns, {
                "event_schema_version": 2, "event": event.wire(), "raw": raw,
                "request_id": self.state.request_id,
            })
        if kind == "mbo":
            self.micro.order_event(event)
        else:
            self.micro.depth_event(event, snapshot=kind == "depth_snapshot")
        if self.journal and depth:
            if depth.counts["gaps"] != gaps_before:
                await self.journal.append("mbp_gap", event.observed.utc_ns, depth.gaps[-1])
            if depth.counts["buffer_overflow"] != overflows_before:
                await self.journal.append("mbp_overflow", event.observed.utc_ns, {
                    "reason": "recovery_buffer_overflow", "lost_through_seq": depth.lost_through,
                    "observed": event.observed.wire(), "generation": depth.gen,
                })
            if depth.counts["recoveries"] != recoveries_before:
                await self.journal.append("mbp_recovery", event.observed.utc_ns, depth.gaps[-1])
        if self.journal and self.micro.orders and self.micro.orders.status in ("INVALID", "OVERFLOW") and (
            self.micro.orders.status != order_status_before
        ):
            await self.journal.append("mbo_continuity", event.observed.utc_ns, {
                "reason": self.micro.orders.status, "observed": event.observed.wire(),
            })
        await self.persist_cards(event.observed)

    async def persist_cards(self, observed):
        if self.micro:
            cards, self.micro.durable_cards = self.micro.durable_cards, []
            if self.journal:
                for card in cards:
                    await self.journal.append("evidence", observed.utc_ns, {"event_schema_version": 2, **card})

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

    async def process(self, payload, observed, frame):
        started = perf_counter_ns()
        await self.apply(payload, observed, frame)
        if self.micro:
            self.micro.processed += 1
            self.micro.processing_ns.append(perf_counter_ns() - started)

    async def apply(self, payload: dict, observed: Observation, frame: str) -> None:
        try:
            stream = payload.get("stream")
            channel = stream.lower().partition("@")[2] if isinstance(stream, str) else ""
            if self.micro and self.micro.depth and channel == "depthraw":
                await self.apply_book(parse_depth(stream_payload(payload, self.state.instrument, channel), observed),
                                      "mbp", frame)
                return
            if self.micro and self.micro.orders and channel == "mboraw":
                await self.apply_book(parse_order(stream_payload(payload, self.state.instrument, channel), observed),
                                      "mbo", frame)
                return
            event = parse_event(payload, self.state.instrument, observed, self.state.source)
        except WrongInstrument:
            await self.reject("wrong_instrument", observed)
        except (ParseError, DecimalException):
            await self.reject("malformed_message", observed)
            if self.micro and channel == "depthraw" and self.micro.depth:
                if stream.lower() == self.streams[2].lower():
                    self.micro.depth.invalidate("malformed_mbp", observed)
                    if self.journal:
                        await self.journal.append("mbp_gap", observed.utc_ns, self.micro.depth.gaps[-1])
                    self.micro.advance(observed)
                    await self.persist_cards(observed)
            if self.micro and channel == "mboraw" and self.micro.orders:
                if stream.lower() == self.streams[-1].lower():
                    self.micro.orders.status = "INVALID"
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
                        "request_id": self.state.request_id,
                    }
                await self.journal.append("trade" if trade else "bbo", observed.utc_ns, {
                    "event": event.wire(), "raw": frame, "request_id": self.state.request_id,
                }, coverage=coverage)
            if isinstance(event, Trade):
                self.state.on_trade(event)
                if self.micro and self.state.last_trade is event:
                    self.micro.trade(event)
            else:
                self.state.on_ticker(event)
                if self.micro:
                    self.micro.quote(event)
            await self.persist_cards(observed)

    async def reject(self, reason: str, observed: Observation) -> None:
        self.state.issue(reason)
        if self.journal:
            await self.journal.append("rejected", observed.utc_ns, {
                "reason": reason, "request_id": self.state.request_id, "observed": observed.wire(),
            })
