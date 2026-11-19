from decimal import Decimal, DecimalException

import pytest

from observatory.events import Aggressor, MOCK_GC, Observation, ParseError, WrongInstrument, parse_event
from observatory.mock_gateway import MockGateway, START_MS
from observatory.state import ObservatoryState, TIMEFRAMES_MS

GATEWAY = MockGateway(MOCK_GC)


def observation(seconds: int) -> Observation:
    return Observation(1_780_000_000_000_000_000 + seconds * 10**9, seconds * 10**9)


def parse(channel: str, data: dict, seconds: int = 1):
    payload = {"stream": f"comex.gcz6@{channel}", "data": data}
    decimal_data = {key: Decimal(str(value)) if type(value) is float else value for key, value in data.items()}
    return parse_event({**payload, "data": decimal_data}, MOCK_GC, observation(seconds), "fxaurum_mock")


def active_state() -> ObservatoryState:
    state = ObservatoryState(MOCK_GC)
    state.on_connect(7)
    state.on_ack(observation(0))
    return state


def test_trade_preserves_clock_units_and_collector_precision():
    data = GATEWAY.trade(0)
    trade = parse("trade", data)
    assert trade.exchange_time_ms == data["T"]
    assert trade.match_time_us == data["tu"] == data["T"] * 1000 + 120
    assert trade.nanosecond_remainder == 340
    assert trade.gateway_receive_ms == data["r"]
    assert trade.instrument == MOCK_GC
    assert trade.wire()["observed"] == observation(1).wire()
    assert trade.wire()["source"] == "fxaurum_mock"
    assert trade.price_ticks == 27500
    assert trade.signed_size == 1


def test_optional_trade_clocks_are_nullable():
    data = GATEWAY.trade(0)
    for field in ("tu", "tn", "r"):
        del data[field]
    trade = parse("trade", data)
    assert (trade.match_time_us, trade.nanosecond_remainder, trade.gateway_receive_ms) == (None, None, None)


def test_conditional_and_unknown_side_are_separated():
    state = active_state()
    normal = parse("trade", GATEWAY.trade(0), 1)
    conditional = parse("trade", {**GATEWAY.trade(1), "tc": 1, "p": 3000}, 2)
    unknown = parse("trade", {**GATEWAY.trade(2), "T": START_MS + 10_000, "side": ""}, 3)
    state.on_trade(normal)
    state.on_trade(conditional)
    assert state.last_trade == normal
    assert state.cvd == 1
    state.on_trade(unknown)
    assert unknown.aggressor == Aggressor.UNKNOWN
    assert conditional.quality_flags == ("CONDITIONAL",)
    assert unknown.quality_flags == ("UNKNOWN_AGGRESSOR",)
    assert state.cvd == 1
    assert state.candles["M1"][-1].volume == normal.size + unknown.size
    assert state.candles["M1"][-1].high_ticks == unknown.price_ticks
    assert state.counters["conditional_trade"] == 1
    assert state.counters["unknown_aggressor"] == 1


@pytest.mark.parametrize("field,value", [
    ("T", START_MS * 1_000), ("q", True), ("p", Decimal("2750.01")),
    ("tn", 1000), ("side", "BUY"), ("tc", 2), ("p", Decimal("NaN")),
])
def test_malformed_trades_are_rejected(field, value):
    with pytest.raises((ParseError, DecimalException)):
        parse("trade", {**GATEWAY.trade(0), field: value})


@pytest.mark.parametrize("stream,symbol", [("cme.gcz6@trade", "GCZ6"), ("comex.gcz6@trade", "GCG7")])
def test_stream_and_payload_must_both_match_the_instrument(stream, symbol):
    with pytest.raises(WrongInstrument):
        parse_event({"stream": stream, "data": {"s": symbol}}, MOCK_GC, observation(1), "fxaurum_mock")


def test_oi_and_bbo_observations_remain_independent():
    state = active_state()
    original = GATEWAY.quote(0)
    size_changed = {**original, "B": original["B"] + 1}
    oi_changed = {**size_changed, "oi": 0, "oit": original["oit"] + 1234}
    state.on_ticker(parse("bookTicker", original, 1))
    assert state.last_bbo_change is None
    state.on_ticker(parse("bookTicker", size_changed, 2))
    state.on_ticker(parse("bookTicker", oi_changed, 3))
    snapshot = state.snapshot(observation(3))
    assert snapshot["status"]["last_bbo_message"] == observation(3).wire()
    assert snapshot["status"]["last_bbo_change"] == observation(2).wire()
    assert snapshot["status"]["last_oi_update"] == observation(3).wire()
    assert snapshot["status"]["quote_timestamp_basis"] == "collector_observation"
    assert snapshot["book_ticker"]["volume_time_us"] == original["vt"]
    assert snapshot["open_interest"] == {"value": 0, "source_time_us": oi_changed["oit"]}
    assert snapshot["trade"] is None
    assert snapshot["candles"]["M1"] == []
    # A replayed OI value must not replace the latest valid observation.
    state.on_ticker(parse("bookTicker", original, 4))
    assert state.snapshot(observation(4))["open_interest"] == snapshot["open_interest"]


def test_crossed_quote_is_flagged_and_spread_is_unavailable():
    state = active_state()
    original = GATEWAY.quote(0)
    state.on_ticker(parse("bookTicker", original))
    state.on_ticker(parse("bookTicker", {**original, "b": 3000}, 2))
    snapshot = state.snapshot(observation(2))
    assert snapshot["bbo"] is None
    assert snapshot["status"]["bbo_freshness"] == "CROSSED_BBO"
    assert snapshot["status"]["last_bbo_message"] == observation(2).wire()
    assert snapshot["status"]["counters"]["CROSSED_BBO"] == 1


def test_utc_candle_boundaries_and_observed_only_volumes():
    state = active_state()
    # UTC midnight; trade exactly on each boundary and skip over empty M1 buckets.
    midnight = START_MS // 86_400_000 * 86_400_000
    offsets = [15_000, 30_000, 59_999, 60_000, 300_000, 900_000, 3_600_000]
    prices = [2750, 2752, 2748, 2751, 2753, 2754, 2755]
    for index, (offset, price) in enumerate(zip(offsets, prices, strict=True)):
        data = {**GATEWAY.trade(index), "T": midnight + offset, "p": price, "q": index + 1}
        state.on_trade(parse("trade", data, index + 1))

    expected_volumes = {
        "M1": [6, 4, 5, 6, 7], "M5": [10, 5, 6, 7],
        "M15": [15, 6, 7], "H1": [21, 7],
    }
    for name, series in state.candles.items():
        assert [bar.volume for bar in series] == expected_volumes[name]
        assert series[0].start_ms == midnight
        assert series[0].partial
        assert series[-1].start_ms == midnight + 3_600_000
        assert not series[-1].partial
        assert all(bar.start_ms % TIMEFRAMES_MS[name] == 0 for bar in series)
        assert all(bar.finalized for bar in list(series)[:-1])
        assert not series[-1].finalized
    first = state.candles["M1"][0]
    assert (first.open_ticks, first.high_ticks, first.low_ticks, first.close_ticks) == (27500, 27520, 27480, 27480)


def test_late_trade_marks_affected_candle_partial_without_rewriting_ohlcv():
    state = active_state()
    state.on_trade(parse("trade", GATEWAY.trade(0)))
    state.on_trade(parse("trade", GATEWAY.trade(5), 2))
    state.on_trade(parse("trade", GATEWAY.trade(1), 3))
    assert state.counters["late_trade"] == 1
    assert state.candles["M1"][0].finalized
    assert state.candles["M1"][0].volume == 1
    assert state.last_trade.exchange_time_ms == GATEWAY.trade(5)["T"]


def test_freshness_uses_monotonic_time_without_inventing_a_disconnect():
    state = active_state()
    assert state.status(observation(0))["market_freshness"] == "WAITING_DATA"
    assert state.status(observation(4))["market_freshness"] == "SILENT"
    state.on_trade(parse("trade", GATEWAY.trade(0), 5))
    status = state.status(Observation(1, observation(9).monotonic_ns))
    assert status["market_freshness"] == "STALE"
    assert status["transport_connected"]
    assert not status["gaps"]
