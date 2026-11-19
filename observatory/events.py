"""The PR00 subset of the Fxaurum wire contract."""

from dataclasses import asdict, dataclass
from decimal import Decimal
from enum import StrEnum
import time

GC_TICK = Decimal("0.1")
MAX_UTC_MS = 253_402_300_799_999


class ParseError(ValueError):
    pass


class WrongInstrument(ParseError):
    pass


class Aggressor(StrEnum):
    BUY = "B"
    SELL = "S"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class Instrument:
    exchange: str
    symbol: str
    expiry: str | None = None

    @property
    def streams(self) -> tuple[str, str]:
        prefix = f"{self.exchange}.{self.symbol}"
        return f"{prefix}@trade", f"{prefix}@bookTicker"


# A named, fixed fixture contract, not an automatic front-month selection.
MOCK_GC = Instrument("COMEX", "GCZ6")


@dataclass(frozen=True, slots=True)
class Observation:
    utc_ns: int
    monotonic_ns: int

    @classmethod
    def now(cls) -> "Observation":
        return cls(time.time_ns(), time.monotonic_ns())

    def wire(self) -> dict[str, str]:
        # Decimal strings preserve nanoseconds even in JavaScript.
        return {"utc_ns": str(self.utc_ns), "monotonic_ns": str(self.monotonic_ns)}


@dataclass(frozen=True, slots=True)
class MarketEvent:
    instrument: Instrument
    observed: Observation
    quality_flags: tuple[str, ...]
    source: str

    def wire_metadata(self) -> dict:
        return {
            "exchange": self.instrument.exchange,
            "symbol": self.instrument.symbol,
            "expiry": self.instrument.expiry,
            "observed": self.observed.wire(),
            "source": self.source,
            "quality_flags": list(self.quality_flags),
        }


@dataclass(frozen=True, slots=True)
class Trade(MarketEvent):
    exchange_time_ms: int
    match_time_us: int | None
    nanosecond_remainder: int | None
    gateway_receive_ms: int | None
    price_ticks: int
    size: int
    aggressor: Aggressor
    conditional: bool

    @property
    def signed_size(self) -> int:
        if self.aggressor == Aggressor.BUY:
            return self.size
        if self.aggressor == Aggressor.SELL:
            return -self.size
        return 0

    def wire(self) -> dict:
        return {
            **self.wire_metadata(),
            "exchange_time_ms": self.exchange_time_ms,
            "match_time_us": self.match_time_us,
            "nanosecond_remainder": self.nanosecond_remainder,
            "gateway_receive_ms": self.gateway_receive_ms,
            "price_ticks": self.price_ticks,
            "size": self.size,
            "aggressor": self.aggressor.value,
            "conditional": self.conditional,
        }


@dataclass(frozen=True, slots=True)
class Quote:
    bid_ticks: int
    bid_size: int
    ask_ticks: int
    ask_size: int

    @property
    def quality_flag(self) -> str | None:
        if not (self.bid_ticks and self.ask_ticks and self.bid_size and self.ask_size):
            return "BBO_UNAVAILABLE"
        if self.bid_ticks > self.ask_ticks:
            return "CROSSED_BBO"
        return None


@dataclass(frozen=True, slots=True)
class BookTicker(MarketEvent):
    quote: Quote | None
    reported_last_ticks: int | None
    session_volume: int | None
    volume_valid: bool
    volume_time_us: int | None
    open_interest: int | None
    open_interest_valid: bool
    open_interest_time_us: int | None

    def wire(self) -> dict:
        return {
            **self.wire_metadata(),
            "quote": asdict(self.quote) if self.quote else None,
            "quote_time_basis": "collector_observation",
            "reported_last_ticks": self.reported_last_ticks,
            "session_volume": self.session_volume,
            "volume_valid": self.volume_valid,
            "volume_time_us": self.volume_time_us,
            "open_interest": self.open_interest,
            "open_interest_valid": self.open_interest_valid,
            "open_interest_time_us": self.open_interest_time_us,
        }


def integer(value: object, field: str, low: int = 0, high: int = 2**63 - 1) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ParseError(f"Invalid integer field {field}")
    return value


def optional_int(
    payload: dict, field: str, high: int = 2**63 - 1
) -> int | None:
    value = payload.get(field)
    return None if value is None else integer(value, field, high=high)


def price_ticks(value: object, field: str, *, allow_zero: bool = False) -> int:
    if type(value) not in (int, Decimal):
        raise ParseError(f"Invalid price field {field}")
    price = Decimal(value)
    minimum = 0 if allow_zero else GC_TICK
    if not price.is_finite() or not minimum <= price <= 10_000_000:
        raise ParseError(f"Invalid price field {field}")
    if price % GC_TICK:
        raise ParseError(f"Price field {field} is off the GC tick grid")
    return int(price / GC_TICK)


def validity(payload: dict, field: str, value: int | None) -> bool:
    flag = payload.get(field, False)
    if type(flag) is not bool or (flag and value is None):
        raise ParseError(f"Invalid validity field {field}")
    return flag


def parse_event(
    envelope: dict, instrument: Instrument, observed: Observation, source: str
) -> Trade | BookTicker:
    stream = envelope.get("stream")
    data = envelope.get("data")
    if not isinstance(stream, str) or not isinstance(data, dict):
        raise ParseError("Invalid stream envelope")
    channels = dict(zip(map(str.lower, instrument.streams), ("trade", "bookticker")))
    channel = channels.get(stream.lower())
    symbol = data.get("s")
    if not isinstance(symbol, str):
        raise ParseError("Missing payload symbol")
    if channel is None or symbol.upper() != instrument.symbol:
        raise WrongInstrument("Unexpected stream or symbol")
    if channel == "trade":
        return parse_trade(data, instrument, observed, source)
    return parse_book_ticker(data, instrument, observed, source)


def parse_trade(
    data: dict, instrument: Instrument, observed: Observation, source: str
) -> Trade:
    if data.get("e") != "trade":
        raise ParseError("Unexpected trade event type")
    side = data.get("side")
    if side not in ("B", "S", "", None):
        raise ParseError("Invalid trade aggressor")
    aggressor = Aggressor(side or "UNKNOWN")
    conditional = bool(integer(data.get("tc"), "tc", high=1))
    flags = []
    if conditional:
        flags.append("CONDITIONAL")
    if aggressor == Aggressor.UNKNOWN:
        flags.append("UNKNOWN_AGGRESSOR")
    return Trade(
        instrument, observed, tuple(flags), source,
        exchange_time_ms=integer(data.get("T"), "T", high=MAX_UTC_MS),
        match_time_us=optional_int(data, "tu", MAX_UTC_MS * 1_000 + 999),
        nanosecond_remainder=optional_int(data, "tn", 999),
        gateway_receive_ms=optional_int(data, "r", MAX_UTC_MS),
        price_ticks=price_ticks(data.get("p"), "p"),
        size=integer(data.get("q"), "q", low=1),
        aggressor=aggressor,
        conditional=conditional,
    )


def parse_book_ticker(
    data: dict, instrument: Instrument, observed: Observation, source: str
) -> BookTicker:
    flags = ["QUOTE_SOURCE_TIME_UNAVAILABLE"]
    quote = None
    if any(field in data for field in ("b", "B", "a", "A")):
        quote = Quote(
            price_ticks(data.get("b"), "b", allow_zero=True),
            integer(data.get("B"), "B"),
            price_ticks(data.get("a"), "a", allow_zero=True),
            integer(data.get("A"), "A"),
        )
        if quote.quality_flag:
            flags.append(quote.quality_flag)
    volume = optional_int(data, "v")
    oi = optional_int(data, "oi")
    last = data.get("l")
    return BookTicker(
        instrument, observed, tuple(flags), source, quote,
        reported_last_ticks=None if last is None else price_ticks(last, "l", allow_zero=True),
        session_volume=volume,
        volume_valid=validity(data, "vf", volume),
        volume_time_us=optional_int(data, "vt"),
        open_interest=oi,
        open_interest_valid=validity(data, "oif", oi),
        open_interest_time_us=optional_int(data, "oit"),
    )
