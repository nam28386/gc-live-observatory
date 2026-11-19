"""Collect and normalize the allowlisted Trade/BBO WebSocket streams."""

import asyncio
from collections import Counter, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
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
from .rpc import SnapshotRPC, SnapshotRequest
from .journal import Journal, JournalError

MAX_MESSAGE_BYTES = 512 * 1024
MAX_QUEUE_FRAMES = 16
RECEIVE_QUEUE = 64
RECEIVE_PRESSURE_TIMEOUT = 4.0
MAX_PRE_ACK_EVENTS = 32
BATCH_RESIDENCE_SECONDS = 0.004
MAX_BATCH_BYTES = 2 * 1024 * 1024


class SubscriptionFailure(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Received:
    frame: str | bytes
    clock: float
    observed: Observation


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
        session_calendar=None,
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
        self.session_calendar = session_calendar
        self.state = state
        self.retry_delay = retry_delay
        self.ack_timeout = ack_timeout
        self.next_request_id = 0
        self.connection_epoch = 0
        self.rpc = SnapshotRPC()
        self.rpc_transport_outcome = "RPC_TRANSPORT_INTERRUPTED"
        self.received_frames = 0
        self.pre_ack_buffered_frames = 0
        self.admitted_frames = 0
        self.accounted_queue_frames = 0
        self.connection_uncertain_base = 0
        self.queue_residence_ns = deque(maxlen=4096)
        self.queue_arrivals = deque()
        self.normalized_market_frames = 0
        self.visible_market_frames = 0
        self.incoming_frames = 0
        self.decoded_frames = 0
        self.ingest_high_water_frames = 0
        self.last_quality_sample_ns = None
        self.receive_high_water = 0
        self.receive_pressure = 0
        self.uncertain_receipts = 0
        self.recovery_receipt_ms = None
        self.recovery_dispatch_ms = None
        self.recovery_publication_ms = None
        self.freshness_samples = {key: Counter() for key in ("trade", "bbo", "mbp", "mbo")}

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
                    self.connection_epoch += 1
                    self.connection_uncertain_base = self.uncertain_receipts
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
                    if self.journal:
                        stamp = Observation.now()
                        await self.journal.append("mode_transition", stamp.utc_ns, {"mode": self.micro.mode})
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
                    try:
                        async with self.publication():
                            await self.disconnect(reason)
                    except JournalError:
                        self.state.recorder_error = True
                        self.state.issue("recorder_failed")
            if self.state.recorder_error:
                return
            await asyncio.sleep(delay)
            delay = min(delay * 2, 5.0)

    @asynccontextmanager
    async def publication(self):
        if not self.journal or self.journal.publication_failed:
            yield
        elif self.journal.batch_size > 1:
            async with self.journal.stage():
                yield
        else:
            async with self.journal.visibility:
                yield

    async def disconnect(self, reason):
        observed = Observation.now()
        acknowledged = self.state.ack is not None
        self.state.on_disconnect(reason, observed)
        if self.micro:
            self.micro.disconnect(observed)
            self.micro.last_time = observed.monotonic_ns
        capture_interrupted = acknowledged or reason in ("subscribe_buffer_overflow", "gateway_frame_overflow", "local_receive_backpressure")
        if self.rpc.active:
            await self.rpc_outcome("RPC_COMMIT_FAILURE" if self.state.recorder_error else self.rpc_transport_outcome)
        if capture_interrupted and self.journal and not self.state.recorder_error:
            await self.journal.append("gap", observed.utc_ns, {
                "reason": reason, "request_id": self.state.request_id, "observed": observed.wire(),
                "receiver_uncertain_min": self.uncertain_receipts - self.connection_uncertain_base,
                "receiver_uncertain_total": self.uncertain_receipts,
            })
        if not self.state.recorder_error:
            await self.persist_cards(observed)

    async def subscribe(self, socket: ClientConnection, request_id: int) -> None:
        await socket.send(json.dumps({
            "method": "SUBSCRIBE", "params": self.streams, "id": request_id,
        }))
        pending: list[tuple[dict, Observation, str]] = []
        deadline = asyncio.get_running_loop().time() + self.ack_timeout
        while True:
            try:
                async with asyncio.timeout_at(deadline):
                    raw = await socket.recv()
            except TimeoutError:
                raise SubscriptionFailure("subscribe_ack_timeout") from None
            self.received_frames += 1
            decoded = self.decode(raw)
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
            if (type(status) is not int or not 200 <= status < 300
                or "error" in payload or "result" not in payload or payload["result"] is not None):
                raise SubscriptionFailure("subscribe_rejected")
            # The request deadline governs receipt, not the local commit.
            async with self.publication():
                connected = pending[0][1] if pending else observed
                if self.journal:
                    await self.journal.append("ack", observed.utc_ns, {
                        "request_id": request_id, "response_id": payload["id"],
                        "observed": connected.wire(),
                    })
                self.state.on_ack(observed)
                if self.micro:
                    self.micro.connect(connected)
                for message in pending:
                    await self.process(*message)
            return

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
        # Socket receipt keeps progressing across recorder awaits. The consumer
        # still applies every frame in original wire order, including RPC images.
        queue = asyncio.Queue(RECEIVE_QUEUE)
        admitted_before = self.admitted_frames
        accounted_before = self.accounted_queue_frames
        received_unadmitted = False
        loop = asyncio.get_running_loop()

        async def receiver():
            nonlocal received_unadmitted
            terminal = None
            try:
                while True:
                    raw = await socket.recv()
                    frame = Received(raw, loop.time(), Observation.now())
                    self.received_frames += 1
                    received_unadmitted = True
                    match = self.observe_rpc_receipt(frame)
                    if queue.full():
                        self.receive_pressure += 1
                        async with asyncio.timeout(RECEIVE_PRESSURE_TIMEOUT):
                            await queue.put(frame)
                    else:
                        queue.put_nowait(frame)
                    if match is not None and self.rpc.active is match:
                        match.queued = loop.time()
                        self.rpc.mark("RESPONSE_QUEUED", match.queued)
                    self.queue_arrivals.append(frame.clock)
                    received_unadmitted = False
                    self.admitted_frames += 1
                    self.receive_high_water = max(self.receive_high_water, queue.qsize())
            except TimeoutError:
                self.uncertain_receipts += 1
                received_unadmitted = False
                terminal = SubscriptionFailure("local_receive_backpressure")
            except ConnectionClosed as exc:
                if not exc.rcvd or exc.rcvd.code not in (1000, 1001):
                    terminal = exc
            except Exception as exc:
                terminal = exc
            await queue.put(terminal)

        async def receive(deadline=None):
            if not queue.empty():
                item = queue.get_nowait()
            elif deadline is None:
                item = await queue.get()
            else:
                async with asyncio.timeout_at(deadline):
                    item = await queue.get()
            if isinstance(item, Exception):
                raise item
            if isinstance(item, Received):
                self.queue_arrivals.popleft()
                self.queue_residence_ns.append(round((loop.time() - item.clock) * 1e9))
            return item

        self.rpc_transport_outcome = "RPC_TRANSPORT_INTERRUPTED"
        reader = asyncio.create_task(receiver())
        try:
            await self.consume_market(socket, receive)
        except SubscriptionFailure as exc:
            if str(exc) == "local_receive_backpressure":
                self.rpc_transport_outcome = "RPC_LOCAL_BACKPRESSURE"
            raise
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            # Account also for a dequeued lookahead or a cancelled transaction,
            # not just frames still resident in the underlying queue.
            self.uncertain_receipts += (
                self.admitted_frames - admitted_before
                - (self.accounted_queue_frames - accounted_before)
                + received_unadmitted
            )
            self.queue_arrivals.clear()

    def observe_rpc_receipt(self, packet):
        request = self.rpc.active
        raw = packet.frame
        if request is None or not isinstance(raw, str) or '"id"' not in raw:
            return None
        try:
            message = json.loads(raw)
        except (ValueError, RecursionError):
            return None
        if (isinstance(message, dict) and "stream" not in message
                and type(message.get("id")) is int and message["id"] == request.request_id
                and request.received is None):
            request.received = packet.clock
            self.rpc.mark("RESPONSE_RECEIVED", packet.clock)
            return request
        return None

    async def rpc_outcome(self, outcome, *, stage=None):
        request = self.rpc.active
        if request is None:
            return
        if stage:
            self.rpc.mark(stage, asyncio.get_running_loop().time())
        self.rpc.finish(outcome)
        if self.journal and not self.journal.publication_failed:
            stamp = Observation.now()
            await self.journal.append("depth_rpc", stamp.utc_ns, self.rpc.recent[-1])

    async def consume_market(self, socket, receive):
        recovery_key = None
        attempts = 0
        reset_deadline = None
        carried = None
        loop = asyncio.get_running_loop()
        while True:
            depth = self.micro.depth if self.micro else None
            if depth:
                if depth.state == "SNAPSHOT_PENDING" and reset_deadline is None:
                    reset_deadline = loop.time() + self.ack_timeout
                elif depth.state != "SNAPSHOT_PENDING":
                    reset_deadline = None
                key = depth.gen, depth.epoch
                if key != recovery_key or depth.valid:
                    if self.rpc.active:
                        await self.rpc_outcome("RPC_GENERATION_MISMATCH" if key != recovery_key
                                               else "RPC_SUPERSEDED_BY_STREAM_IMAGE")
                    if key != recovery_key:
                        attempts, recovery_key = 0, key
                if (depth.state in ("GAP_DETECTED", "RECOVERING", "OVERFLOW") and depth.gen is not None
                        and self.rpc.active is None):
                    if attempts < 3:
                        attempts += 1
                        self.next_request_id += 1
                        request_id = self.next_request_id
                        await self.depth_control(depth, "RECOVERING")
                        request = SnapshotRequest(request_id, self.connection_epoch, self.state.instrument.symbol,
                                                  depth.gen, depth.epoch, loop.time(),
                                                  pressure_at_send=self.receive_pressure, dispatch_budget=self.ack_timeout,
                                                  publication_budget=self.ack_timeout)
                        self.rpc.start(request)
                        request.sent = loop.time()
                        request.deadline = request.sent + self.ack_timeout
                        await socket.send(json.dumps({
                            "method": "depthSnap", "id": request_id,
                            "params": {"exchange": self.state.instrument.exchange, "symbol": self.state.instrument.symbol},
                        }))
                        self.rpc.mark("REQUEST_SENT", request.sent)
                    else:
                        self.rpc.counts["RPC_RECOVERY_FAILED"] += 1
                        await self.depth_control(depth, "RECOVERY_FAILED", "retry_exhausted")
            pending = self.rpc.active
            deadline = pending.deadline if pending else reset_deadline
            try:
                # An already-received timely response may be waiting behind durable
                # writes. Only its wire-time stamp, never dispatch time, decides expiry.
                wait_deadline = deadline
                if pending and pending.received is not None and pending.received <= deadline:
                    wait_deadline = None
                packet = carried if carried is not None else await receive(wait_deadline)
                carried = None
                if packet is None:
                    return
                if deadline is not None and packet.clock > deadline:
                    carried = packet
                    raise TimeoutError
            except TimeoutError:
                if pending:
                    pressured = self.receive_pressure != pending.pressure_at_send or len(self.queue_arrivals) >= RECEIVE_QUEUE
                    await self.depth_control(depth, increment="rpc_timeout")
                    await self.rpc_outcome("RPC_LOCAL_BACKPRESSURE" if pressured else "RPC_RECEIPT_TIMEOUT")
                elif reset_deadline is not None:
                    observed = Observation.now()
                    async with self.publication():
                        depth.invalidate("snapshot_timeout", observed)
                        if self.journal:
                            await self.journal.append("mbp_gap", observed.utc_ns, depth.gaps[-1])
                    reset_deadline = None
                continue
            decoded = self.decode(packet.frame, observed=packet.observed)
            if decoded is None:
                if self.journal:
                    await self.journal.append("rejected", self.state.last_any.utc_ns, {
                        "reason": "malformed_frame", "observed": self.state.last_any.wire(),
                    })
                self.accounted_queue_frames += 1
                continue
            payload, observed, raw = decoded
            if "stream" in payload:
                if self.journal and self.journal.batch_size > 1:
                    carried, closed = await self.process_market_batch(receive, decoded, deadline=deadline)
                    if closed:
                        return
                else:
                    async with self.publication():
                        await self.process(*decoded)
                    self.visible_market_frames = self.normalized_market_frames
                    self.accounted_queue_frames += 1
            elif pending and type(payload.get("id")) is int and payload["id"] == pending.request_id:
                await self.handle_snapshot(depth, pending, packet, payload, observed, raw)
                self.accounted_queue_frames += 1
            else:
                if type(payload.get("id")) is int:
                    self.rpc.counts["UNMATCHED_RESPONSE"] += 1
                self.accounted_queue_frames += 1

    async def handle_snapshot(self, depth, request, packet, payload, observed, raw):
        loop = asyncio.get_running_loop()
        self.rpc.mark("RESPONSE_RECEIVED", packet.clock)
        self.rpc.mark("RESPONSE_QUEUED", packet.clock)
        request.dispatched = loop.time()
        self.rpc.mark("RESPONSE_DISPATCHED", request.dispatched)
        self.recovery_receipt_ms = (packet.clock - request.sent) * 1000
        self.recovery_dispatch_ms = (request.dispatched - packet.clock) * 1000
        if request.dispatched > packet.clock + request.dispatch_budget:
            request.outcomes.append("RPC_DISPATCH_DELAY")
            self.rpc.counts["RPC_DISPATCH_DELAY"] += 1
        try:
            data = payload.get("result")
            if ("error" in payload or payload.get("status", 200) != 200
                    or not isinstance(data, dict) or data.get("type") != "Image"
                    or ("s" in data and str(data["s"]).upper() != self.state.instrument.symbol)):
                raise ParseError("Unexpected depthSnap response")
            event = parse_depth(data, observed)
        except (ParseError, DecimalException):
            await self.depth_control(depth, increment="invalid_rpc_response")
            await self.rpc_outcome("RPC_RESPONSE_INVALID")
            return
        if (event.gen != request.generation or event.gen != depth.gen
                or request.recovery_epoch != depth.epoch or request.connection_epoch != self.connection_epoch):
            await self.depth_control(depth, increment="generation_mismatch")
            await self.rpc_outcome("RPC_GENERATION_MISMATCH")
            return
        self.rpc.mark("RESPONSE_VALIDATED", loop.time())
        try:
            async with self.publication():
                await self.apply_book(event, "depth_snapshot", raw)
        except JournalError:
            self.rpc.finish("RPC_COMMIT_FAILURE")
            raise
        self.recovery_publication_ms = (loop.time() - packet.clock) * 1000
        if self.journal:
            request.committed = loop.time()
            self.rpc.mark("RESPONSE_COMMITTED", request.committed)
        if loop.time() > request.dispatched + request.publication_budget:
            request.outcomes.append("RPC_PUBLICATION_DELAY")
            self.rpc.counts["RPC_PUBLICATION_DELAY"] += 1
        await self.rpc_outcome("SNAPSHOT_APPLIED" if depth.valid else "RPC_SEQUENCE_INSUFFICIENT",
                               stage="SNAPSHOT_APPLIED" if depth.valid else None)

    def receiver_diagnostics(self):
        samples = sorted(self.queue_residence_ns)
        now = asyncio.get_running_loop().time() if self.queue_arrivals else None
        return {
            "incoming_frames": self.incoming_frames, "decoded_frames": self.decoded_frames,
            "socket_received_frames": self.received_frames, "admitted_queue_frames": self.admitted_frames,
            "pre_ack_buffered_frames": self.pre_ack_buffered_frames,
            "settled_queue_frames": self.accounted_queue_frames,
            "durable_market_records": self.journal.market_records if self.journal else None,
            "normalized_market_frames": self.normalized_market_frames,
            "visible_market_frames": self.visible_market_frames,
            "receiver_queue_occupancy": len(self.queue_arrivals),
            "oldest_queued_ms": (now - self.queue_arrivals[0]) * 1000 if now is not None else None,
            "queue_residence_us": {f"p{p}": samples[(len(samples) - 1) * p // 100] / 1000 if samples else None
                                   for p in (50, 95, 99)},
            "depthsnap_rpc": self.rpc.view(),
            "ingest_high_water_frames": self.ingest_high_water_frames,
            "receive_queue_capacity": RECEIVE_QUEUE, "receive_queue_high_water": self.receive_high_water,
            "receive_pressure_count": self.receive_pressure, "uncertain_received_frames": self.uncertain_receipts,
            "depthsnap_send_to_receipt_ms": self.recovery_receipt_ms,
            "depthsnap_receipt_to_dispatch_ms": self.recovery_dispatch_ms,
            "depthsnap_receipt_to_publication_ms": self.recovery_publication_ms,
        }

    async def depth_control(self, depth, state=None, increment=None):
        async with self.publication():
            if state:
                depth.state = state
            if increment:
                depth.counts[increment] += 1
            if self.journal:
                stamp = Observation.now()
                await self.journal.append("mbp_control", stamp.utc_ns, {
                    "state": depth.state, "increment": {increment: 1} if increment else {},
                    "rpc_outcome": "RPC_RECOVERY_FAILED" if state == "RECOVERY_FAILED" else None,
                })

    async def process_market_batch(self, receive, first, *, deadline):
        frames = [first]
        carried = None
        closed = False
        end = asyncio.get_running_loop().time() + BATCH_RESIDENCE_SECONDS
        size = len(first[2].encode("utf-8"))
        try:
            while len(frames) < self.journal.batch_size and asyncio.get_running_loop().time() < end:
                try:
                    packet = await receive(min(end, deadline) if deadline is not None else end)
                except TimeoutError:
                    break
                if packet is None:
                    closed = True
                    break
                if deadline is not None and packet.clock > deadline:
                    carried = packet
                    break
                frame = packet.frame
                decoded = self.decode(frame, track=False, observed=packet.observed)
                if decoded is None or "stream" not in decoded[0]:
                    carried = packet
                    break
                size += len(frame.encode("utf-8"))
                if size > MAX_BATCH_BYTES:
                    carried = packet
                    break
                frames.append(decoded)
                self.incoming_frames += 1
                self.decoded_frames += 1
        finally:
            self.ingest_high_water_frames = max(self.ingest_high_water_frames, len(frames))
            async with self.journal.stage():
                for decoded in frames:
                    self.state.last_any = decoded[1]
                    await self.process(*decoded)
        self.visible_market_frames = self.normalized_market_frames
        self.accounted_queue_frames += len(frames)
        if self.micro:
            published = Observation.now().monotonic_ns
            self.micro.availability_ns.extend(published - row[1].monotonic_ns for row in frames)
        return carried, closed

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
        if kind in ("mbp", "mbo"):
            self.normalized_market_frames += 1
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
            waves, self.micro.waves.durable = self.micro.waves.durable, []
            if self.journal:
                for card in cards:
                    await self.journal.append("evidence", observed.utc_ns, {"event_schema_version": 2, **card})
                for wave in waves:
                    await self.journal.append("wave_transition", observed.utc_ns, wave)

    def decode(self, frame: str | bytes, *, track=True, observed=None) -> tuple[dict, Observation, str] | None:
        observed = observed or Observation.now()
        if track:
            self.state.last_any = observed
            self.incoming_frames += 1
        try:
            payload = json.loads(frame, parse_float=Decimal) if isinstance(frame, str) else None
        except (ValueError, RecursionError, DecimalException):
            payload = None
        if not isinstance(payload, dict):
            if track:
                self.state.issue("malformed_message")
            return None
        if track:
            self.decoded_frames += 1
        return payload, observed, frame

    async def process(self, payload, observed, frame):
        started = perf_counter_ns()
        await self.apply(payload, observed, frame)
        if self.micro:
            self.micro.processed += 1
            self.micro.processing_ns.append(perf_counter_ns() - started)
            now = observed.monotonic_ns
            if self.last_quality_sample_ns is None or now - self.last_quality_sample_ns >= 100_000_000:
                self.last_quality_sample_ns = now
                self.freshness_samples["trade"][self.state.freshness(self.state.trade_in_session, observed)] += 1
                self.freshness_samples["bbo"][self.state.freshness(self.state.quote_in_session, observed)] += 1
                self.freshness_samples["mbp"][
                    "OBSERVED" if self.micro.depth_eligible(now) else "UNAVAILABLE"
                ] += 1
                mbo = self.micro.orders
                self.freshness_samples["mbo"][
                    "OBSERVED" if mbo and mbo.last and mbo.status == "OBSERVED_PARTIAL_BOOK"
                    and now - mbo.last.monotonic_ns <= self.micro.freshness_ns else "UNAVAILABLE"
                ] += 1
            if not self.journal or self.journal.batch_size == 1:
                self.micro.availability_ns.append(Observation.now().monotonic_ns - observed.monotonic_ns)

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
                    if self.journal:
                        await self.journal.append("mbo_continuity", observed.utc_ns, {
                            "reason": "INVALID", "observed": observed.wire(),
                        })
        else:
            if (
                self.state.live and isinstance(event, Trade)
                and event.exchange_time_ms > observed.utc_ns // 1_000_000 + 5_000
            ):
                await self.reject("future_trade_time", observed)
                return
            session = None
            if self.session_calendar and self.state.live and isinstance(event, Trade):
                session_state, session_id = self.session_calendar.at(observed.utc_ns)
                session = session_id if session_id else ("CLOSED" if session_state == "CLOSED" else None)
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
                    "event": event.wire(), "raw": frame, "request_id": self.state.request_id, "market_session": session,
                }, coverage=coverage)
            self.normalized_market_frames += 1
            if isinstance(event, Trade):
                self.state.on_trade(event)
                if self.micro and self.state.last_trade is event:
                    self.micro.trade(event, candles=self.state.candles, session=session)
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
