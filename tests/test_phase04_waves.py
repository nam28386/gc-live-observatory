from decimal import Decimal
from hashlib import sha256

import pytest

from observatory.evaluate import evaluate, score_models
from observatory.dataset import PRIMARY_METRIC
from observatory.events import MOCK_GC, Observation, parse_event
from observatory.microstructure import Microstructure
from observatory.replay import canonical
from observatory.state import ObservatoryState
from observatory.waves import research_object

NS = 10**9
BASE = 1_800_000_000_000


def obs(seconds):
    return Observation(BASE * 1_000_000 + int(seconds * NS), int(seconds * NS))


def quote(engine, t, mid_ticks):
    quote_data = {"s": "GCZ6", "b": Decimal(mid_ticks - 1) / 10, "a": Decimal(mid_ticks + 1) / 10,
                  "B": 10, "A": 10}
    engine.quote(parse_event({"stream": "comex.gcz6@bookTicker", "data": quote_data},
                             MOCK_GC, obs(t), "fxaurum_mock"))


def trade(engine, state, t, *, source_ms=BASE, session=None, quantity=12):
    event = parse_event({
        "stream": "comex.gcz6@trade", "data": {"s": "GCZ6", "e": "trade", "T": source_ms,
                                              "p": 2750, "q": quantity, "side": "B", "tc": 0}},
        MOCK_GC, obs(t), "fxaurum_mock")
    state.on_trade(event)
    if state.last_trade is event:
        engine.trade(event, candles=state.candles, session=session)


def fixture():
    state = ObservatoryState(MOCK_GC)
    engine = Microstructure("A", MOCK_GC, "fxaurum_mock", freshness_ns=4 * NS)
    engine.connect(obs(0))
    quote(engine, .5, 27500)
    trade(engine, state, 1, session="session-a")
    return state, engine


@pytest.mark.parametrize("tape,expected", [
    ([(1.2, 27503), (1.8, 27504)], "ACCEPTANCE_CANDIDATE"),
    ([(1.2, 27503), (1.8, 27500), (2, 27497)], "REVERSAL_OBSERVED"),
    ([(1.2, 27500), (1.9, 27501)], "STALL_OBSERVED"),
])
def test_wave_observes_price_tape_and_never_relabels_prior_transition(tape, expected):
    state, engine = fixture()
    initial = engine.waves.active["M1"].wire()
    for t, mid in tape:
        quote(engine, t, mid)
    wave = next(item for item in engine.waves.view() if item["wave_id"] == initial["wave_id"])
    assert wave["state"] == expected
    assert wave["transitions"][0] == initial["transitions"][0]
    research = research_object(wave, engine.cards)
    assert research["expected_response"]["status"] == "EXPECTED_RESPONSE_UNAVAILABLE"
    assert research["residual"] is None
    assert research["epistemic"]["INFERENCE"] == []
    assert research["epistemic"]["REJECTED"] == []


def test_hierarchy_is_as_of_forming_and_delayed_trade_cannot_change_wave():
    state, engine = fixture()
    waves = {item["timeframe"]: item for item in engine.waves.view()}
    assert waves["M1"]["parent_wave_id"] == waves["M5"]["wave_id"]
    assert waves["M5"]["parent_wave_id"] == waves["M15"]["wave_id"]
    assert waves["M15"]["parent_wave_id"] == waves["H1"]["wave_id"]
    assert waves["M15"]["bar"]["bar_state"] == "FORMING_AS_OF"
    trade(engine, state, 1.1, source_ms=BASE - 1)
    assert {item["timeframe"]: item for item in engine.waves.view()} == waves
    trade(engine, state, 1.2, source_ms=BASE + 2 * 60_000)
    assert all(item["state"] == "INVALIDATED" for item in engine.waves.view())
    assert waves["M15"]["bar"]["bar_state"] == "FORMING_AS_OF"


@pytest.mark.parametrize("reason", ["session", "stale_bbo", "mbp_gap"])
def test_wave_continuity_gates(reason):
    from observatory.books import DepthBook, DepthEvent
    state, engine = fixture()
    if reason == "session":
        trade(engine, state, 1.1, source_ms=BASE + 1, session="session-b", quantity=1)
    elif reason == "stale_bbo":
        quote(engine, 6, 27508)
    else:
        engine.depth = DepthBook()
        engine.depth_event(DepthEvent("Reset", 1, None, 0, None, None, obs(1.1)))
    assert not engine.waves.active
    assert len([wave for wave in engine.waves.view() if wave["state"] == "INVALIDATED"]) == 4


def row(n, session, split, *, source="fxaurum_mock"):
    secs = {"a": 0, "b": 3600, "c": 7200}[session]
    signal = n % 6
    return {"event_id": session + str(n), "capture_session": session, "split": split,
            "source": source, "t0_utc_ns": str((secs + n * 35) * NS),
            "label": {"impact_ticks": signal * 3},
            "baseline": {"signed_contracts_1s": 12, "spread_ticks": 2,
                         "fixed_pre_depth": [[[27499, 8, 1]], [[27501, 8, 1]]],
                         "pre_mid_ticks": 27500, "utc_hour": 4, "impact_at_cutoff_ticks": 1},
            "added_refill_contracts": signal, "negative_control_refill_contracts": (n * 7) % 5}


def test_synthetic_negative_control_checks_model_mechanics_but_never_empirical_status():
    train = [row(i, group, "exploration") for group in ("a", "b") for i in range(20)]
    holdout = [row(i, "c", "holdout") for i in range(12)]
    score = score_models(train, holdout)
    assert score["holdout_mse"]["anchored"] < score["holdout_mse"]["control"]
    samples = train + holdout
    manifest = {"dataset_sha256": sha256("".join(canonical(item) + "\n" for item in samples).encode()).hexdigest(),
                "primary_metric_predeclared": PRIMARY_METRIC}
    result = evaluate(manifest, samples, confirm_permitted=True)
    assert result["empirical_evaluation"] == "NOT_RUN_INSUFFICIENT_DATA"
    assert "holdout_mse" not in result
    assert result["scientific_status"] == "UNRESOLVED"
    with pytest.raises(ValueError, match="digest"):
        evaluate(manifest, samples[:-1], confirm_permitted=True)
