"""Fxaurum depth normalization and bounded book reconstruction."""

from collections import Counter, deque
from dataclasses import asdict, dataclass
from heapq import nlargest, nsmallest

from .events import Instrument, Observation, ParseError, WrongInstrument, integer, optional_int, price_ticks

DEPTH_LIMIT = 4096
ORDER_LIMIT = 65_536
RECOVERY_BUFFER = 512


@dataclass(frozen=True, slots=True)
class DepthEvent:
    kind: str
    gen: int
    seq: int | None
    src: int
    tu: int | None
    gw: int | None
    observed: Observation
    level: tuple[str, int, int, int] | None = None
    rows: tuple[tuple[str, int, int, int], ...] = ()

    def wire(self):
        return {**asdict(self), "observed": self.observed.wire()}


@dataclass(frozen=True, slots=True)
class OrderEvent:
    kind: str
    observed: Observation
    oid: str | None
    side: str | None
    price: int | None
    quantity: int | None
    priority: str | None
    cumulative: int | None
    inferred: int | None
    src: int | None
    ts: int | None
    tu: int | None
    gw: int | None

    def wire(self):
        return {**asdict(self), "observed": self.observed.wire()}


def stream_payload(payload: dict, instrument: Instrument, channel: str) -> dict:
    data = payload.get("data")
    if payload.get("stream", "").lower() != f"{instrument.exchange}.{instrument.symbol}@{channel}".lower():
        raise WrongInstrument("Wrong depth stream or instrument")
    if not isinstance(data, dict):
        raise ParseError("Missing depth payload")
    if "s" in data and (not isinstance(data["s"], str) or data["s"].upper() != instrument.symbol):
        raise WrongInstrument("Wrong depth payload instrument")
    return data


def parse_depth(data: dict, observed: Observation) -> DepthEvent:
    kind = data.get("type")
    if kind not in ("Reset", "Image", "Update", "Delete"):
        raise ParseError("Unknown MBP event")
    gen = integer(data.get("gen"), "gen")
    seq = optional_int(data, "seq") if kind == "Reset" else integer(data.get("seq"), "seq")
    src = integer(data.get("src", 0), "src", high=1)
    level = None
    rows = ()
    if kind == "Image":
        image = data.get("rows")
        if not isinstance(image, list) or len(image) > DEPTH_LIMIT:
            raise ParseError("Invalid MBP image size")
        parsed = []
        for row in image:
            if not isinstance(row, list) or len(row) != 4:
                raise ParseError("Invalid MBP image row")
            side = "BS"[integer(row[0], "side", high=1)]
            parsed.append((side, price_ticks(row[1], "p"), integer(row[2], "q"), integer(row[3], "n")))
        if len({(side, price) for side, price, _, _ in parsed}) != len(parsed):
            raise ParseError("Duplicate MBP image level")
        rows = tuple(parsed)
    elif kind in ("Update", "Delete"):
        side = data.get("side")
        if side not in ("B", "S"):
            raise ParseError("Invalid MBP side")
        size = 0 if kind == "Delete" else integer(data.get("q"), "q")
        count = 0 if kind == "Delete" else integer(data.get("n"), "n")
        level = (side, price_ticks(data.get("p"), "p"), size, count)
    return DepthEvent(kind, gen, seq, src, optional_int(data, "tu"), optional_int(data, "gw"),
                      observed, level, rows)


def parse_order(data: dict, observed: Observation) -> OrderEvent:
    kind = data.get("type")
    if kind not in ("Reset", "Image", "New", "Change", "Delete", "Fill"):
        raise ParseError("Unknown MBO event")
    if kind == "Reset":
        return OrderEvent(kind, observed, None, None, None, None, None, None, None, None,
                          optional_int(data, "ts"), optional_int(data, "tu"), optional_int(data, "gw"))
    oid = data.get("oid")
    if kind != "Reset" and (not isinstance(oid, str) or not oid or len(oid) > 128):
        raise ParseError("Invalid MBO order ID")
    side, price, priority = data.get("side") or None, data.get("p"), data.get("pri") or None
    if side not in (None, "B", "S") or (priority is not None and (
        not isinstance(priority, str) or len(priority) > 32 or not priority.isascii() or not priority.isdecimal()
    )):
        raise ParseError("Invalid MBO side or priority")
    quantity, cumulative, inferred = (optional_int(data, key) for key in ("q", "k", "ki"))
    src = optional_int(data, "src", 1)
    if inferred is not None and (cumulative is None or inferred > cumulative):
        raise ParseError("Invalid inferred cumulative fill")
    if kind == "Fill" and (
        side is None or price is None or not quantity or cumulative is None or cumulative < quantity
        or inferred is None or src is None or (src == 1 and inferred < quantity)
        or (src == 0 and cumulative - inferred < quantity)
    ):
        raise ParseError("Incomplete MBO Fill")
    return OrderEvent(kind, observed, oid, side, None if price is None else price_ticks(price, "p"),
                      quantity, priority, cumulative, inferred, src, optional_int(data, "ts"),
                      optional_int(data, "tu"), optional_int(data, "gw"))


class DepthBook:
    def __init__(self):
        self.state = "UNINITIALIZED"
        self.gen = self.seq = None
        self.levels: dict[str, dict[int, tuple[int, int]]] = {"B": {}, "S": {}}
        self.pending: dict[int, DepthEvent] = {}
        self.gaps = deque(maxlen=64)
        self.counts = Counter()
        self.epoch = 0
        self.last = None
        self.recovery_ns = None
        self.available = None
        self.last_snapshot = None
        self.lost_through = -1

    @property
    def valid(self):
        return self.state == "VALID_BOOK"

    def invalidate(self, reason, observed):
        if self.state not in ("GAP_DETECTED", "RECOVERING", "OVERFLOW", "RECOVERY_FAILED") or reason in ("transport", "reset"):
            self.gaps.append({"reason": reason, "gen": self.gen, "after_seq": self.seq,
                              "detected": observed.wire(), "resumed": None})
            self.counts["gaps"] += 1
            self.epoch += 1
        self.state = "GAP_DETECTED"

    def disconnect(self, observed):
        if self.gen is not None:
            self.invalidate("transport", observed)
        self.gen = self.seq = None
        self.pending.clear()
        self.lost_through = -1
        self.levels = {"B": {}, "S": {}}
        self.last = self.available = None
        self.state = "UNINITIALIZED"

    def top(self, n=5):
        return [
            [(p, *self.levels[side][p]) for p in select(n, self.levels[side])]
            for side, select in (("B", nlargest), ("S", nsmallest))
        ]

    def apply(self, event: DepthEvent, *, snapshot=False):
        if event.kind == "Reset":
            if self.gen is not None and event.gen <= self.gen:
                self.counts["stale_reset"] += 1
                return []
            if self.gen is not None:
                self.invalidate("reset", event.observed)
            self.gen, self.seq = event.gen, event.seq
            self.levels = {"B": {}, "S": {}}
            self.pending.clear()
            self.lost_through = -1
            self.last = event
            self.state = "SNAPSHOT_PENDING"
            return []
        if self.gen is None or self.gen != event.gen:
            self.counts["generation_mismatch"] += 1
            if self.gen is not None and event.gen > self.gen:
                self.invalidate("missing_reset", event.observed)
            return []
        if event.kind == "Image":
            floor = self.seq if self.seq is not None else -1
            if event.seq < max(floor, self.lost_through):
                self.counts["stale_image"] += 1
                return []
            image_levels = {"B": {}, "S": {}}
            for side, price, quantity, count in event.rows:
                if quantity:
                    image_levels[side][price] = (quantity, count)
            if self.valid and event.seq == floor and image_levels == self.levels:
                self.counts["duplicate_image"] += 1
                return []
            if self.valid:
                if event.src != 1 and not snapshot:
                    self.counts["unexpected_image"] += 1
                    return []
                self.invalidate("gateway_image", event.observed)
            buffered = sorted((seq, delta) for seq, delta in self.pending.items() if seq > event.seq)
            if any(seq != event.seq + i for i, (seq, _) in enumerate(buffered, 1)):
                self.counts["incomplete_image"] += 1
                self.state = "GAP_DETECTED"
                return []
            self.levels = image_levels
            self.seq, self.last = event.seq, event
            self.state = "VALID_BOOK"
            self.pending.clear()
            self.lost_through = -1
            for seq, delta in buffered:
                if seq > self.seq:
                    self.apply(delta)
            self.available = self.last_snapshot = event.observed
            if self.valid and self.gaps and self.gaps[-1]["resumed"] is None:
                resumed = self.last.observed
                # Buffered observations preceded the RPC reply, so availability is the image or later.
                resumed = max((resumed, event.observed), key=lambda stamp: stamp.monotonic_ns)
                self.gaps[-1]["resumed"] = resumed.wire()
                self.recovery_ns = resumed.monotonic_ns - int(self.gaps[-1]["detected"]["monotonic_ns"])
                self.counts["recoveries"] += 1
            # Buffered transitions are historical recovery state, not observed event flow.
            return []
        if self.seq is not None and event.seq <= self.seq:
            self.counts["duplicate_or_late"] += 1
            return []
        if self.state == "OVERFLOW" or self.lost_through >= 0:
            self.lost_through = max(self.lost_through, event.seq)
            self.counts["discarded_while_invalid"] += 1
            return []
        if self.state == "RECOVERY_FAILED":
            self.counts["discarded_while_invalid"] += 1
            return []
        if not self.valid or self.seq is None or event.seq != self.seq + 1:
            self.invalidate("sequence" if self.seq is not None else "missing_image", event.observed)
            if event.seq not in self.pending and len(self.pending) >= RECOVERY_BUFFER:
                self.counts["buffer_overflow"] += 1
                self.lost_through = max(event.seq, *self.pending)
                self.pending.clear()
                self.state = "OVERFLOW"
            elif self.state != "OVERFLOW":
                self.pending.setdefault(event.seq, event)
                self.counts["buffer_high_water"] = max(self.counts["buffer_high_water"], len(self.pending))
            return []
        before = self.top()
        side, price, quantity, count = event.level
        old_size = self.levels[side].get(price, (0, 0))[0]
        if quantity and not old_size and sum(map(len, self.levels.values())) >= DEPTH_LIMIT:
            self.invalidate("book_bound", event.observed)
            self.lost_through = event.seq
            self.state = "OVERFLOW"
            return []
        if quantity:
            self.levels[side][price] = (quantity, count)
        else:
            self.levels[side].pop(price, None)
        self.seq, self.last, self.available = event.seq, event, event.observed
        return [(before, self.top(), event.level, old_size)]

    def status(self):
        return {"status": self.state, "generation": self.gen, "sequence": self.seq,
                "coverage_epoch": self.epoch, "buffered": len(self.pending), "counts": dict(self.counts),
                "gaps": list(self.gaps)[-12:], "recovery_duration_ns": self.recovery_ns}


@dataclass(slots=True)
class RestingOrder:
    oid: str
    side: str
    price: int
    quantity: int
    priority: str | None
    first: Observation
    last: Observation
    left_censored: bool
    epoch: int
    leg: int = 0
    cumulative: int = 0
    inferred: int = 0


class OrderBook:
    def __init__(self):
        self.orders: dict[str, RestingOrder] = {}
        self.retired = set()
        self.fills = {}
        self.counts = Counter()
        self.epoch = 0
        self.status = "AWAITING_RESET"
        self.last = None
        self.durations_ms = deque(maxlen=256)
        self.added_sizes = deque(maxlen=512)
        self.cohort = {}

    def disconnect(self):
        self.orders.clear()
        self.retired.clear()
        self.fills.clear()
        self.cohort.clear()
        self.status = "AWAITING_RESET"
        self.last = None

    def apply(self, event: OrderEvent):
        kind, oid = event.kind, event.oid
        if kind == "Reset":
            self.disconnect()
            self.epoch += 1
            self.status = "AWAITING_OBSERVATIONS"
            return None
        if self.status in ("AWAITING_RESET", "OVERFLOW", "INVALID"):
            self.counts["ineligible"] += 1
            return None
        existing = self.orders.get(oid)
        if kind == "Fill":
            key = oid, event.side, event.price
            if existing and (existing.side, existing.price) != (event.side, event.price):
                self.counts["fill_other_leg_unattributed"] += 1
                return None
            prior, prior_inferred = self.fills.get(key, (0, 0))
            if existing:
                if existing.cumulative >= prior:
                    prior, prior_inferred = existing.cumulative, existing.inferred
            if event.cumulative <= prior:
                self.counts["duplicate_or_late_fill"] += 1
                return None
            if event.inferred < prior_inferred:
                self.counts["inconsistent_cumulative_fill"] += 1
                return None
            if event.cumulative - prior != event.quantity:
                self.counts["fill_cumulative_discontinuity"] += 1
            if key not in self.fills and len(self.fills) >= ORDER_LIMIT:
                self.status = "OVERFLOW"
                return None
            self.fills[key] = event.cumulative, event.inferred
            if existing:
                existing.cumulative = event.cumulative
                existing.inferred = event.inferred
                existing.last = event.observed
            else:
                self.counts["fill_without_resting_observation"] += 1
            category = "exact_fill" if event.src == 0 else "inferred_fill"
            self.counts[category + "_quantity"] += event.quantity
        elif kind == "Delete":
            if len(self.retired) >= ORDER_LIMIT and oid not in self.retired:
                self.status = "OVERFLOW"
                return None
            self.retired.add(oid)
            if not existing:
                self.counts["unknown_delete"] += 1
                return None
            del self.orders[oid]
            if oid in self.cohort and self.cohort[oid][1] is None:
                self.cohort[oid] = self.cohort[oid][0], event.observed.monotonic_ns
            if not existing.left_censored:
                self.durations_ms.append((event.observed.monotonic_ns - existing.first.monotonic_ns) / 1e6)
            category = "order_removal_unattributed"
        elif kind == "Image" and oid in self.retired:
            self.counts["stale_image"] += 1
            return None
        elif kind == "New" and existing:
            self.counts["duplicate_new"] += 1
            return None
        else:
            side = event.side if event.side is not None else existing.side if existing else None
            price = event.price if event.price is not None else existing.price if existing else None
            size = event.quantity if event.quantity is not None else existing.quantity if existing else None
            if side is None or price is None or size is None or (kind == "Change" and not existing):
                self.counts["unresolved_patch"] += 1
                self.status = "INVALID"
                return None
            if existing is None:
                if len(self.orders) >= ORDER_LIMIT:
                    self.status = "OVERFLOW"
                    return None
                order = RestingOrder(oid, side, price, size, event.priority, event.observed, event.observed,
                                     kind == "Image", self.epoch)
                order.cumulative, order.inferred = self.fills.get((oid, side, price), (0, 0))
                self.orders[oid] = order
                self.retired.discard(oid)
                if kind == "Image":
                    self.counts["left_censored"] += 1
                else:
                    self.added_sizes.append(size)
                    if len(self.cohort) < 512 and oid not in self.cohort:
                        self.cohort[oid] = event.observed.monotonic_ns, None
                    if event.priority is not None:
                        self.counts["additions_with_priority"] += 1
            else:
                order = existing
                if (side, price) != (order.side, order.price):
                    self.fills.pop((oid, side, price), None)
                    order.leg += 1
                    order.cumulative = order.inferred = 0
                order.side, order.price, order.quantity = side, price, size
                order.last = event.observed
                if event.priority is not None:
                    order.priority = event.priority
            if event.cumulative is not None and event.cumulative >= order.cumulative:
                order.cumulative, order.inferred = event.cumulative, event.inferred or 0
            category = {"Image": "order_image", "New": "order_addition", "Change": "order_change"}[kind]
        self.status = "OBSERVED_PARTIAL_BOOK"
        self.last = event.observed
        self.counts[category] += 1
        return category

    def survival(self, observed_ns):
        if not self.cohort or self.status != "OBSERVED_PARTIAL_BOOK":
            return None
        durations = Counter()
        removals = Counter()
        for started, ended in self.cohort.values():
            age = (ended if ended is not None else observed_ns) - started
            durations[age] += 1
            if ended is not None:
                removals[age] += 1
        points = {}
        at_risk, survival = len(self.cohort), 1.0
        times = iter(sorted(durations))
        age = next(times, None)
        for horizon_ms in (100, 1000, 5000):
            horizon = horizon_ms * 1_000_000
            while age is not None and age <= horizon:
                survival *= 1 - removals[age] / at_risk
                at_risk -= durations[age]
                age = next(times, None)
            points[str(horizon_ms)] = survival if horizon <= max(durations) else None
        return {"population": "first 512 New observations in this epoch", "sample_size": len(self.cohort),
                "right_censored": sum(end is None for _, end in self.cohort.values()),
                "survival_at_ms": points}
