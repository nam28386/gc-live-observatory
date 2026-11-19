"""Receive-time measurements over the shared transaction and book observations."""

from collections import Counter, deque
from dataclasses import dataclass, field
from hashlib import sha256

from .books import DepthBook, OrderBook
from .events import BookTicker, Observation, Trade

SECOND = 10**9
WINDOW = 2 * SECOND
LIMIT = 8192
TOP = 5
BURST_CONTRACTS = 12


def rank_ofi(before, after):
    """Bid contribution minus ask contribution at one contemporaneous rank."""
    total = 0
    for side, previous, current in zip((1, -1), before, after):
        if previous is None or current is None:
            total += side * ((current or (0, 0))[1] - (previous or (0, 0))[1])
            continue
        old_price, old_size, _ = previous
        price, size, _ = current
        improvement = side * (price - old_price)
        total += side * (size if improvement > 0 else -old_size if improvement < 0 else size - old_size)
    return total


def depth_moves(before, after):
    def at_rank(book, side, rank):
        return book[side][rank] if rank < len(book[side]) else None

    ranks = [rank_ofi(
        [at_rank(before, side, rank) for side in range(2)],
        [at_rank(after, side, rank) for side in range(2)],
    ) for rank in range(TOP)]
    boundaries = sum(
        (at_rank(before, side, rank) or (None,))[0] != (at_rank(after, side, rank) or (None,))[0]
        for rank in range(TOP) for side in range(2)
    )
    return ranks, boundaries


@dataclass(slots=True)
class Candidate:
    card: dict
    start: int
    epoch: int
    direction: int
    anchors: dict
    initial_mid: float | None
    signed_at_start: int
    mid2: tuple[int, float] | None = None
    mid30: tuple[int, float] | None = None
    refill: Counter = field(default_factory=Counter)
    depletion: Counter = field(default_factory=Counter)
    current: dict = field(default_factory=dict)
    recovered_at: int | None = None
    invalid: bool = False
    cutoff_recorded: bool = False
    terminal: bool = False


class Microstructure:
    def __init__(self, mode, instrument, source, freshness_ns=3 * SECOND):
        self.mode, self.instrument, self.source = mode, instrument, source
        self.freshness_ns = freshness_ns
        self.depth = DepthBook() if mode in ("B", "C") else None
        self.orders = OrderBook() if mode == "C" else None
        self.entitlement = "UNVERIFIED" if self.orders else "DISABLED"
        self.events = deque()
        self.ofi = deque()
        self.flow = deque()
        self.saturated_until = {}
        self.cards = deque(maxlen=32)
        self.durable_cards = []
        self.active = None
        self.mid = None
        self.bbo = None
        self.last_time = None
        self.capture_id = None
        self.session_started = None
        self.flow_coverage_since = None
        self.depth_coverage_since = None
        self.previous_valid = False
        self.transport = False
        self.last_detection = {}
        self.samples = deque()
        self.last_trade_seen = None
        self.last_intensity_check = None
        self.cvd = 0
        self.processed = 0
        self.processing_ns = deque(maxlen=256)

    def connect(self, observed):
        self.transport = True
        self.session_started = observed.monotonic_ns
        self.flow_coverage_since = observed.monotonic_ns

    def disconnect(self, observed):
        self.transport = False
        self.mid = self.bbo = self.last_trade_seen = None
        self.events.clear()
        self.samples.clear()
        self.ofi.clear()
        self.flow.clear()
        self.depth_coverage_since = None
        if self.depth:
            self.depth.disconnect(observed)
        if self.orders:
            self.orders.disconnect()
        if self.active and not self.active.terminal:
            self.finish_invalid(observed, "transport interruption")

    def append(self, series, row):
        stamp = row[0]
        q = getattr(self, series)
        while q and q[0][0] < stamp - WINDOW:
            q.popleft()
        if len(q) == LIMIT:
            self.saturated_until[series] = q.popleft()[0] + WINDOW
        q.append(row)

    def advance(self, observed):
        stamp = observed.monotonic_ns
        if self.last_time is not None and stamp < self.last_time:
            return False
        self.last_time = stamp
        if not self.samples or stamp - self.samples[-1][0] >= SECOND // 10:
            self.append("samples", (stamp, self.depth_eligible(stamp)))
        if self.last_intensity_check is None or stamp - self.last_intensity_check >= SECOND // 4:
            self.last_intensity_check = stamp
            if stamp >= self.saturated_until.get("events", 0):
                rates = Counter(category for seen, category in self.events if seen >= stamp - SECOND)
                if rates:
                    category, count = rates.most_common(1)[0]
                    if count >= 100:
                        self.detect("high_observed_event_intensity", observed,
                                    {"event_category": category, "count_1s": count})
        c = self.active
        if c and not c.terminal:
            elapsed = stamp - c.start
            if self.depth and (c.epoch != self.depth.epoch or not self.depth_eligible(stamp)):
                self.finish_invalid(observed, "MBP coverage interrupted")
            elif self.mid and stamp - self.mid[0] > self.freshness_ns:
                self.finish_invalid(observed, "BBO freshness interrupted")
            elif elapsed > WINDOW and not c.cutoff_recorded:
                self.cutoff(c, observed)
            if not c.terminal and elapsed > 30 * SECOND:
                sample = c.mid30
                if sample and self.transport and stamp - c.start >= 30 * SECOND and (
                    c.start + 30 * SECOND - sample[0] <= self.freshness_ns
                ) and c.initial_mid is not None and not c.invalid:
                    c.card["label"] = {"impact_ticks": c.direction * (sample[1] - c.initial_mid),
                                       "sample_receive_ns": str(sample[0]),
                                       "available_receive_ns": str(stamp)}
                else:
                    c.card["label"] = None
                    c.card["missing_evidence"].append("eligible +30s BBO or uninterrupted coverage")
                c.card["end"] = observed.wire()
                c.terminal = True
                self.durable_cards.append({**c.card, "missing_evidence": list(c.card["missing_evidence"]), "phase": "outcome"})
        return True

    def finish_invalid(self, observed, reason):
        c = self.active
        c.invalid = c.terminal = True
        c.card["status"] = "INELIGIBLE"
        c.card["end"] = observed.wire()
        c.card["missing_evidence"].append(reason)
        c.card["label"] = None
        self.durable_cards.append({**c.card, "missing_evidence": list(c.card["missing_evidence"]), "phase": "invalidation"})

    def cutoff(self, c, observed):
        c.cutoff_recorded = True
        side = "S" if c.direction > 0 else "B"
        opposite = "B" if side == "S" else "S"
        usable = c.initial_mid is not None and c.mid2 is not None and not c.invalid and bool(c.anchors)
        usable = usable and c.start + WINDOW - c.mid2[0] <= self.freshness_ns
        c.card["status"] = "ELIGIBLE_AT_CUTOFF" if usable else "INELIGIBLE"
        c.invalid = not usable
        if not usable:
            c.card["missing_evidence"].append("fresh BBO, anchored depth or continuous MBP coverage")
        c.card["prediction"] = {
            "cutoff_receive_ns": str(c.start + WINDOW),
            "available_receive_ns": str(self.last_time),
            "impact_ticks": c.direction * (c.mid2[1] - c.initial_mid) if usable else None,
            "refill_contracts": c.refill[side] if usable else None,
            "opposite_refill_contracts": c.refill[opposite] if usable else None,
            "depletion_contracts": c.depletion[side] if usable else None,
            "depth_retention": (
                sum(c.current[k] for k in c.anchors if k[0] == side)
                / sum(c.anchors[k] for k in c.anchors if k[0] == side)
                if usable and any(k[0] == side for k in c.anchors) else None
            ),
            "partial_recovery_ms": (
                (c.recovered_at - c.start) / 1e6 if usable and c.recovered_at is not None else None
            ),
        }
        self.durable_cards.append({**c.card, "missing_evidence": list(c.card["missing_evidence"]), "phase": "prediction"})
        if usable:
            facts = {"parent_event": c.card["id"], "signed_contracts_1s": c.signed_at_start,
                     **c.card["prediction"]}
            if c.refill[side] >= 8:
                self.detect("high_anchored_replenishment", observed, facts)
            if c.depletion[side] >= 8 and c.recovered_at is None:
                self.detect("no_half_recovery_by_cutoff", observed, facts)
            if abs(facts["impact_ticks"]) <= 1:
                self.detect("high_flow_weak_mid_response", observed, facts)


    def trade(self, event: Trade):
        if not self.advance(event.observed):
            return
        stamp = event.observed.monotonic_ns
        self.last_trade_seen = stamp
        self.cvd += event.signed_size
        self.append("events", (stamp, "trade"))
        self.append("flow", (stamp, event.signed_size, event.size, event.price_ticks))
        signed = sum(row[1] for row in self.flow if row[0] >= stamp - SECOND)
        if abs(signed) >= BURST_CONTRACTS and (not self.active or self.active.terminal) and (
            stamp >= self.saturated_until.get("flow", 0)
        ):
            self.start_candidate(event, signed)

    def start_candidate(self, event, signed):
        stamp = event.observed.monotonic_ns
        top = self.depth.top() if self.depth_eligible(stamp) else [[], []]
        anchors = {(side, price): size for side, rows in zip("BS", top) for price, size, _ in rows}
        if not all(top):
            anchors.clear()
        mid = self.mid if self.mid and stamp - self.mid[0] <= self.freshness_ns else None
        card = {
            "id": sha256(f"{self.capture_id}:{self.instrument.symbol}:{stamp}:{event.exchange_time_ms}".encode()).hexdigest()[:24], "instrument": self.instrument.symbol, "exchange": self.instrument.exchange,
            "expiry": self.instrument.expiry, "source": self.source, "capture_session": self.capture_id,
            "category": "aggressive_flow_burst", "epistemic_status": "MEASUREMENT",
            "alternative_status": "HYPOTHESIS", "scientific_status": "UNRESOLVED",
            "status": "PENDING", "start": event.observed.wire(), "trade_exchange_ms": event.exchange_time_ms,
            "start_price_ticks": event.price_ticks,
            "clock": "collector_monotonic", "signed_contracts_1s": signed,
            "pre_mid_ticks": mid[1] if mid else None, "pre_depth": top,
            "feature_schema_version": 2,
            "anchor_depth_generation": self.depth.gen if anchors else None,
            "anchor_depth_sequence": self.depth.seq if anchors else None,
            "pre_spread_ticks": self.bbo[2] - self.bbo[1] if mid else None,
            "direction": "BUY" if signed > 0 else "SELL",
            "baseline_utc_ns": event.observed.wire()["utc_ns"],
            "utc_hour": (event.observed.utc_ns // (3600 * SECOND)) % 24,
            "market_session": "UNKNOWN",
            "missing_evidence": [], "alternatives": [
                "displayed replenishment", "cross-stream timing uncertainty", "trade-side classification",
            ], "limitations": "Anonymous observed flow; no actor or causal attribution.",
        }
        self.active = Candidate(card, stamp, self.depth.epoch if self.depth else -1,
                                1 if signed > 0 else -1, anchors, mid[1] if mid else None, signed,
                                mid2=mid, mid30=mid, current=dict(anchors))
        self.cards.append(card)
        self.durable_cards.append({**card, "missing_evidence": [], "phase": "candidate"})

    def quote(self, event: BookTicker):
        if not self.advance(event.observed) or event.quote is None:
            return
        if event.quote.quality_flag:
            self.mid = self.bbo = None
            if self.active and not self.active.terminal:
                self.finish_invalid(event.observed, "invalid BBO within event window")
            return
        stamp = event.observed.monotonic_ns
        bid, ask = event.quote.bid_ticks, event.quote.ask_ticks
        previous = self.mid
        self.mid = stamp, (bid + ask) / 2
        if previous and stamp - previous[0] <= self.freshness_ns and abs(self.mid[1] - previous[1]) >= 3:
            signed = sum(row[1] for row in self.flow if row[0] >= stamp - SECOND)
            if abs(signed) <= 3:
                self.detect("price_move_with_weak_net_flow", event.observed,
                            {"prior_mid_ticks": previous[1], "mid_change_ticks": self.mid[1] - previous[1],
                             "net_contracts_1s": signed})
        self.bbo = stamp, bid, ask
        c = self.active
        if c and not c.terminal:
            if stamp <= c.start + WINDOW:
                c.mid2 = self.mid
            if stamp <= c.start + 30 * SECOND:
                c.mid30 = self.mid

    def depth_eligible(self, timestamp):
        if not self.depth or not self.depth.valid or self.depth.last is None:
            return False
        bids, asks = self.depth.top(1)
        return bool(bids and asks and bids[0][0] < asks[0][0] and (
            timestamp - self.depth.available.monotonic_ns <= self.freshness_ns
        ))

    def depth_event(self, event, *, snapshot=False):
        if self.depth.valid and event.kind in ("Update", "Delete") and (
            self.depth.seq is not None and event.gen == self.depth.gen and event.seq <= self.depth.seq
        ):
            self.depth.counts["duplicate_or_late"] += 1
            return
        # Process coverage breaks before a received frame can close an evidence window.
        if self.active and not self.active.terminal and (
            event.kind == "Reset" or (event.kind == "Image" and (event.src == 1 or snapshot))
            or (self.depth.valid and (
                event.gen != self.depth.gen or event.seq != self.depth.seq + 1
            ))
        ):
            self.finish_invalid(event.observed, "MBP continuity interrupted")
        if not self.advance(event.observed):
            self.depth.invalidate("late_receive_clock", event.observed)
            return
        before_eligible = self.depth_eligible(event.observed.monotonic_ns)
        if not before_eligible:
            self.depth_coverage_since = None
            self.ofi.clear()
        prior_epoch = self.depth.epoch
        movements = self.depth.apply(event, snapshot=snapshot)
        stamp = event.observed.monotonic_ns
        eligible = self.depth_eligible(stamp)
        if not eligible or prior_epoch != self.depth.epoch:
            self.ofi.clear()
            self.depth_coverage_since = None
            if self.active and not self.active.terminal:
                self.finish_invalid(event.observed, "MBP continuity or quote quality")
        if eligible and self.depth_coverage_since is None:
            self.depth_coverage_since = stamp
        if event.kind in ("Update", "Delete"):
            self.append("events", (stamp, "mbp_" + event.kind.lower()))
        if event.kind == "Image" and not movements and (event.src == 1 or snapshot):
            self.append("events", (stamp, "recovery"))
        for before, after, (side, price, size, _count), old_size in movements if eligible and before_eligible else []:
            ranks, boundaries = depth_moves(before, after)
            self.append("ofi", (stamp, ranks, boundaries, side, size - old_size))
            if old_size >= 8 and size <= old_size / 2:
                self.detect("displayed_depth_contraction", event.observed,
                            {"side": side, "price_ticks": price, "before_contracts": old_size,
                             "size_change_contracts": size - old_size})
            c = self.active
            key = side, price
            if c and not c.terminal and key in c.anchors and stamp <= c.start + WINDOW:
                c.current[key] = size
                c.refill[side] += max(0, size - old_size)
                c.depletion[side] += max(0, old_size - size)
                if c.depletion[side] and c.recovered_at is None and size > old_size and (
                    sum(c.current[k] for k in c.current if k[0] == side)
                    >= sum(c.anchors[k] for k in c.anchors if k[0] == side) * 0.5
                ) and side == ("S" if c.direction > 0 else "B"):
                    c.recovered_at = stamp

    def order_event(self, event):
        if not self.advance(event.observed):
            self.orders.status = "INVALID"
            return
        category = self.orders.apply(event)
        if category:
            self.append("events", (event.observed.monotonic_ns, category))

    def detect(self, category, observed, metrics):
        stamp = observed.monotonic_ns
        if category in self.last_detection and stamp - self.last_detection[category] < WINDOW:
            return
        self.last_detection[category] = stamp
        card = {
            "id": sha256(f"{self.capture_id}:{self.instrument.symbol}:{stamp}:{category}".encode()).hexdigest()[:24],
            "category": category, "start": observed.wire(), "end": observed.wire(),
            "source": self.source, "instrument": self.instrument.symbol, "exchange": self.instrument.exchange,
            "expiry": self.instrument.expiry, "capture_session": self.capture_id,
            "clock": "collector_monotonic", "epistemic_status": "MEASUREMENT",
            "status": "OBSERVED", "metrics": metrics,
            "alternatives": ["execution or displayed-size change", "cross-stream timing or migration"],
            "missing_evidence": ["participant identity and cause"],
        }
        self.cards.append(card)
        self.durable_cards.append({**card, "phase": "observation"})

    def snapshot(self, now):
        stamp = now.monotonic_ns
        active = self.transport and self.flow_coverage_since is not None
        valid_depth = active and self.depth_eligible(stamp)
        flow = [row for row in self.flow if row[0] >= stamp - WINDOW]
        buy = sum(max(0, signed) for _, signed, _, _ in flow)
        sell = sum(max(0, -signed) for _, signed, _, _ in flow)
        fresh_trade = self.last_trade_seen is not None and stamp - self.last_trade_seen <= self.freshness_ns
        flow_full = active and fresh_trade and stamp - self.flow_coverage_since >= WINDOW and stamp >= self.saturated_until.get("flow", 0)
        ofi = [row for row in self.ofi if row[0] >= stamp - WINDOW] if valid_depth else []
        ofi_full = valid_depth and self.depth_coverage_since is not None and (
            stamp - self.depth_coverage_since >= WINDOW and stamp >= self.saturated_until.get("ofi", 0)
        )
        top = self.depth.top() if valid_depth else [[], []]
        totals = [sum(level[1] for level in side) for side in top]
        total = sum(totals)
        comparable = bool(valid_depth and self.bbo and (
            abs(self.depth.available.monotonic_ns - self.bbo[0]) <= 250_000_000
        ) and stamp - self.bbo[0] <= self.freshness_ns)
        samples = [available for seen, available in self.samples if seen >= stamp - WINDOW]
        order_fresh = self.orders and self.orders.last and (
            stamp - self.orders.last.monotonic_ns <= self.freshness_ns
        )
        counts = Counter(category for seen, category in self.events if seen >= stamp - WINDOW)
        latencies = sorted(self.processing_ns)
        return {
            "diagnostics": {
                "stream_frames_processed": self.processed,
                "processing_p95_us": latencies[int((len(latencies) - 1) * .95)] / 1000 if latencies else None,
                "measurement_overflow": {name: stamp < until for name, until in self.saturated_until.items()},
            },
            "mode": self.mode, "clock": "collector_monotonic", "window_seconds": 2,
            "trade_flow": {"status": "VALID" if flow_full else "PARTIAL" if active else "UNAVAILABLE",
                           "buy": buy, "sell": sell, "net": buy - sell, "count": len(flow), "cvd_observed": self.cvd,
                           "unknown": sum(size for _, signed, size, _ in flow if not signed),
                           "trades_per_second": len(flow) / 2 if flow_full else None,
                           "price_response_ticks_per_signed_contract": (
                               (flow[-1][3] - flow[0][3]) / (buy - sell)
                               if flow_full and len(flow) > 1 and buy != sell else None
                           )},
            "mbp": self.depth.status() if self.depth else {"status": "DISABLED"},
            "ofi": {"status": "VALID" if ofi_full else "PARTIAL" if valid_depth else "INELIGIBLE",
                    "ranks": [sum(row[1][i] for row in ofi) for i in range(TOP)] if ofi_full else None,
                    "best_level": sum(row[1][0] for row in ofi) if ofi_full else None,
                    "multi_level": sum(sum(row[1]) for row in ofi) if ofi_full else None,
                    "price_fixed_bid_change": sum(row[4] for row in ofi if row[3] == "B") if ofi_full else None,
                    "price_fixed_ask_change": sum(row[4] for row in ofi if row[3] == "S") if ofi_full else None,
                    "normalized_by_current_top_depth": sum(sum(row[1]) for row in ofi) / total if ofi_full and total else None,
                    "boundary_changes": sum(row[2] for row in ofi) if ofi_full else None,
                    "count": len(ofi) if ofi_full else None},
            "liquidity": {"status": "VALID" if valid_depth else "UNAVAILABLE", "top": top,
                          "top_n_depth": totals if valid_depth else None,
                          "sampled_unavailable_fraction": samples.count(False) / len(samples) if samples else None,
                          "recovery_count": self.depth.counts["recoveries"] if self.depth else None,
                          "spread_ticks": top[1][0][0] - top[0][0][0] if valid_depth else None,
                          "distance_ticks": [[abs(p - side[0][0]) for p, _, _ in side] for side in top],
                          "imbalance": (totals[0] - totals[1]) / total if total else None,
                          "concentration": sum((size / total) ** 2 for side in top for _, size, _ in side) if total else None,
                          "bbo_disagreement": (
                              (top[0][0][0], top[1][0][0]) != self.bbo[1:] if comparable else None
                          )},
            "mbo": {
                "status": (
                    self.orders.status if self.orders and (order_fresh or self.orders.status != "OBSERVED_PARTIAL_BOOK")
                    else "STALE" if self.orders else "DISABLED"
                ),
                "entitlement": self.entitlement,
                "epoch": self.orders.epoch if self.orders else None,
                "order_count": len(self.orders.orders) if order_fresh and self.orders.status == "OBSERVED_PARTIAL_BOOK" else None,
                "counts": dict(self.orders.counts) if self.orders else None,
                "observed_resting_ms": list(self.orders.durations_ms)[-12:] if order_fresh else None,
                "cohort_survival": self.orders.survival(stamp) if order_fresh else None,
                "added_size_sample": list(self.orders.added_sizes)[-12:] if order_fresh else None,
            },
            "intensity": {"status": "VALID" if flow_full and stamp >= self.saturated_until.get("events", 0) else "PARTIAL",
                          "rates_per_second": {key: count / 2 for key, count in counts.items()}},
            "evidence": list(self.cards)[-8:],
        }
