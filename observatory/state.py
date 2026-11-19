"""Market observations and bounded, event-time candle history."""

from collections import Counter, deque
from dataclasses import asdict, dataclass

from .events import BookTicker, GC_TICK, Instrument, Observation, Quote, Trade

TIMEFRAMES_MS = {"M1": 60_000, "M5": 300_000, "M15": 900_000, "H1": 3_600_000}
HISTORY_LIMIT = 120
COVERAGE_LIMIT = 64


@dataclass(slots=True)
class Candle:
    start_ms: int
    end_ms: int
    open_ticks: int
    high_ticks: int
    low_ticks: int
    close_ticks: int
    volume: int = 0
    partial: bool = False
    finalized: bool = False

    def add(self, trade: Trade) -> None:
        self.high_ticks = max(self.high_ticks, trade.price_ticks)
        self.low_ticks = min(self.low_ticks, trade.price_ticks)
        self.close_ticks = trade.price_ticks
        self.volume += trade.size


@dataclass(slots=True)
class Gap:
    reason: str
    detected: Observation
    resumed: Observation | None = None

    def wire(self) -> dict:
        return {
            "reason": self.reason,
            "detected": self.detected.wire(),
            "resumed": self.resumed.wire() if self.resumed else None,
        }


@dataclass(slots=True)
class Coverage:
    id: int
    started: Observation
    ended: Observation | None = None

    def wire(self) -> dict:
        return {
            "id": self.id,
            "started": self.started.wire(),
            "ended": self.ended.wire() if self.ended else None,
        }


class ObservatoryState:
    def __init__(self, instrument: Instrument, stale_after: float = 3.0, *, live: bool = False) -> None:
        self.instrument = instrument
        self.live = live
        self.source = "fxaurum_rithmic" if live else "fxaurum_mock"
        self.recorder_error = False
        self.last_volume_update: Observation | None = None
        self.volume: tuple[int, int | None] | None = None
        self.stale_after_ns = int(stale_after * 1_000_000_000)
        self.connected = False
        self.request_id: int | None = None
        self.ack: Observation | None = None
        self.last_ack_request_id: int | None = None
        self.last_any: Observation | None = None
        self.last_trade: Trade | None = None
        self.last_ticker: BookTicker | None = None
        self.last_bbo_change: Observation | None = None
        self.last_oi_update: Observation | None = None
        self.oi: tuple[int, int | None] | None = None
        self.quote: Quote | None = None
        self.quote_problem: str | None = None
        self.market_in_session: Observation | None = None
        self.trade_in_session: Observation | None = None
        self.quote_in_session: Observation | None = None
        self.coverage: deque[Coverage] = deque(maxlen=COVERAGE_LIMIT)
        self.gaps: deque[Gap] = deque(maxlen=COVERAGE_LIMIT)
        self.pending_gap: Gap | None = None
        self.counters: Counter[str] = Counter()
        self.last_issue: str | None = None
        self.candles: dict[str, deque[Candle]] = {
            name: deque(maxlen=HISTORY_LIMIT) for name in TIMEFRAMES_MS
        }
        self.coverage_id = 0
        self.first_trade_in_coverage = True
        self.cvd = 0

    def issue(self, code: str) -> None:
        self.counters[code] += 1
        self.last_issue = code

    def on_connect(self, request_id: int) -> None:
        self.connected = True
        self.request_id = request_id
        self.ack = None
        self.market_in_session = None
        self.trade_in_session = None
        self.quote_in_session = None
        self.quote = None
        self.quote_problem = None

    def on_ack(self, observed: Observation) -> None:
        self.ack = observed
        self.last_ack_request_id = self.request_id
        self.counters["subscribe_ack"] += 1

    def on_disconnect(self, reason: str, observed: Observation) -> None:
        if self.ack is not None:
            if self.coverage and self.coverage[-1].ended is None:
                self.coverage[-1].ended = observed
            if self.pending_gap is None:
                self.pending_gap = Gap(reason, observed)
                self.gaps.append(self.pending_gap)
                self.counters["coverage_gaps"] += 1
            for series in self.candles.values():
                if series:
                    series[-1].partial = True
        self.connected = False
        self.ack = None

    def observe_market(self, observed: Observation) -> None:
        self.market_in_session = observed
        if self.coverage and self.coverage[-1].ended is None:
            return
        self.coverage_id += 1
        self.coverage.append(Coverage(self.coverage_id, observed))
        self.first_trade_in_coverage = True
        if self.pending_gap:
            self.pending_gap.resumed = observed
            self.pending_gap = None

    def on_trade(self, trade: Trade) -> None:
        if trade.conditional:
            self.counters["conditional_trade"] += 1
            return
        if self.last_trade and trade.exchange_time_ms < self.last_trade.exchange_time_ms:
            self.issue("late_trade")
            for name, duration in TIMEFRAMES_MS.items():
                start = trade.exchange_time_ms // duration * duration
                for candle in self.candles[name]:
                    if candle.start_ms == start:
                        candle.partial = True
                        break
            return
        self.observe_market(trade.observed)
        self.last_trade = trade
        self.trade_in_session = trade.observed
        self.counters["eligible_trade"] += 1
        if trade.signed_size == 0:
            self.counters["unknown_aggressor"] += 1
        self.cvd += trade.signed_size
        for name, duration in TIMEFRAMES_MS.items():
            series = self.candles[name]
            start = trade.exchange_time_ms // duration * duration
            if not series or series[-1].start_ms < start:
                if series:
                    series[-1].finalized = True
                series.append(Candle(
                    start_ms=start,
                    end_ms=start + duration,
                    open_ticks=trade.price_ticks,
                    high_ticks=trade.price_ticks,
                    low_ticks=trade.price_ticks,
                    close_ticks=trade.price_ticks,
                    partial=self.first_trade_in_coverage,
                ))
            series[-1].add(trade)
        self.first_trade_in_coverage = False

    def on_ticker(self, ticker: BookTicker) -> None:
        self.last_ticker = ticker
        quote = ticker.quote
        if quote is not None:
            self.quote_problem = quote.quality_flag
            if self.quote_problem:
                self.quote = None
                self.issue(self.quote_problem)
            else:
                if self.quote is not None and quote != self.quote:
                    self.last_bbo_change = ticker.observed
                self.quote = quote
                self.quote_in_session = ticker.observed
                self.counters["eligible_bbo"] += 1
                self.observe_market(ticker.observed)
        if ticker.volume_valid:
            value = (ticker.session_volume, ticker.volume_time_us)
            if self.volume is None or (
                value != self.volume and (
                    self.volume[1] is None or value[1] is None or value[1] >= self.volume[1]
                )
            ):
                self.volume = value
                self.last_volume_update = ticker.observed
        if ticker.open_interest_valid:
            value = (ticker.open_interest, ticker.open_interest_time_us)
            if self.oi is not None:
                old_time, new_time = self.oi[1], value[1]
                if old_time is not None and new_time is not None and new_time < old_time:
                    self.issue("stale_oi")
                    return
            if self.oi != value:
                self.oi = value
                self.last_oi_update = ticker.observed

    def freshness(self, received: Observation | None, now: Observation) -> str:
        if not self.connected:
            return "DISCONNECTED"
        if self.ack is None:
            return "WAITING_ACK"
        anchor = received or self.ack
        if now.monotonic_ns - anchor.monotonic_ns >= self.stale_after_ns:
            return "STALE" if received else "SILENT"
        return "OBSERVED" if received else "WAITING_DATA"

    def status(self, now: Observation) -> dict:
        ticker = self.last_ticker
        book_freshness = self.freshness(self.quote_in_session, now)
        if self.connected and self.ack and self.quote_problem:
            book_freshness = self.quote_problem
        return {
            "mode": "LIVE" if self.live else "MOCK",
            "source": self.source,
            "expiry": self.instrument.expiry,
            "recorder_error": self.recorder_error,
            "exchange": self.instrument.exchange,
            "symbol": self.instrument.symbol,
            "tick_size": str(GC_TICK),
            "transport_connected": self.connected,
            "subscribe_ack": self.ack is not None,
            "request_id": self.request_id,
            "last_ack_request_id": self.last_ack_request_id,
            "ack_observation": wire(self.ack),
            "last_message": wire(self.last_any),
            "last_eligible_trade": wire(self.last_trade.observed if self.last_trade else None),
            "last_bbo_message": wire(ticker.observed if ticker else None),
            "last_bbo_change": wire(self.last_bbo_change),
            "last_oi_update": wire(self.last_oi_update),
            "last_volume_update": wire(self.last_volume_update),
            "market_freshness": self.freshness(self.market_in_session, now),
            "trade_freshness": self.freshness(self.trade_in_session, now),
            "bbo_freshness": book_freshness,
            "quote_timestamp_basis": "collector_observation",
            "coverage": [coverage.wire() for coverage in self.coverage],
            "coverage_basis": "observed_only",
            "gaps": [gap.wire() for gap in self.gaps],
            "counters": dict(self.counters),
            "last_issue": self.last_issue,
        }

    def snapshot(self, now: Observation) -> dict:
        ticker = self.last_ticker
        quote = self.quote if self.connected and self.ack else None
        return {
            "status": self.status(now),
            "trade": self.last_trade.wire() if self.last_trade else None,
            "book_ticker": {
                **ticker.wire_metadata(),
                "session_volume": ticker.session_volume,
                "volume_valid": ticker.volume_valid,
                "volume_time_us": ticker.volume_time_us,
                "open_interest_time_us": ticker.open_interest_time_us,
                "open_interest_valid": ticker.open_interest_valid,
                "reported_last_ticks": ticker.reported_last_ticks,
            } if ticker else None,
            "bbo": {
                **asdict(quote),
                "spread_ticks": quote.ask_ticks - quote.bid_ticks,
            } if quote else None,
            "open_interest": {"value": self.oi[0], "source_time_us": self.oi[1]} if self.oi else None,
            "cvd_observed": self.cvd,
            "candles": {
                name: [asdict(candle) for candle in series]
                for name, series in self.candles.items()
            },
        }


def wire(observed: Observation | None) -> dict[str, str] | None:
    return observed.wire() if observed else None
