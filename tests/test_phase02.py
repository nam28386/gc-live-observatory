from decimal import Decimal
import json

from fastapi.testclient import TestClient
import pytest

from observatory.books import DepthBook, OrderBook, parse_depth, parse_order
from observatory.events import MOCK_GC, Observation, ParseError, parse_event
from observatory.microstructure import Microstructure, rank_ofi
from observatory.web import create_app


def stamp(seconds=0):
    return Observation(1_800_000_000_000_000_000 + int(seconds * 1e9), int(seconds * 1e9))


def depth(book, kind, seq=0, *, gen=1, src=0, t=None, snapshot=False, **fields):
    payload = {"type": kind, "gen": gen, "seq": seq, "src": src}
    if kind == "Image":
        payload["rows"] = [[0, Decimal("2749.9"), 10, 2], [1, Decimal("2750.1"), 10, 2]]
    if kind in ("Update", "Delete"):
        payload.update({"side": "S", "p": Decimal("2750.1"), "q": 5, "n": 1})
    event = parse_depth({**payload, **fields}, stamp(seq if t is None else t))
    if isinstance(book, Microstructure):
        return book.depth_event(event, snapshot=snapshot)
    return book.apply(event, snapshot=snapshot)


def init(book):
    depth(book, "Reset")
    depth(book, "Image")


def test_mbp_absolute_updates_deletions_and_duplicate_sequences():
    book = DepthBook()
    init(book)
    assert book.valid
    depth(book, "Update", 1)
    depth(book, "Update", 1, q=700)
    assert book.levels["S"][27501] == (5, 1)
    depth(book, "Delete", 2)
    assert not book.levels["S"]
    assert book.counts["duplicate_or_late"] == 1


def test_mbp_recovery_rejects_stale_image_and_replays_only_contiguous_later_deltas():
    book = DepthBook()
    init(book)
    depth(book, "Update", 1)
    depth(book, "Update", 4, q=9)
    depth(book, "Update", 3, q=8)
    assert not book.valid
    depth(book, "Image", 0, src=1, snapshot=True, t=5)
    assert book.counts["stale_image"] == 1
    depth(book, "Image", 1, src=1, snapshot=True, t=6)
    assert not book.valid and len(book.gaps) == 1
    depth(book, "Image", 2, src=1, snapshot=True, t=7)
    assert book.valid and book.seq == 4
    assert book.levels["S"][27501] == (9, 1)
    assert book.gaps[0]["resumed"] == stamp(7).wire()
    depth(book, "Image", 5, src=1, t=8)
    assert book.valid and book.gaps[-1]["reason"] == "gateway_image"
    book.disconnect(stamp(9))
    depth(book, "Image", 6)
    assert not book.valid and not book.pending


def test_bad_image_shape_and_generation_mismatch_never_publish_valid_book():
    book = DepthBook()
    init(book)
    with pytest.raises(ParseError):
        depth(book, "Image", rows=[[0, 2700, -5, 1]])
    depth(book, "Update", 1, gen=2)
    assert not book.valid
    depth(book, "Reset", gen=2)
    depth(book, "Image", gen=1)
    assert not book.valid
    depth(book, "Image", gen=2)
    assert book.valid


def mbo(book, kind, t=0, **fields):
    return book.apply(parse_order({"type": kind, "oid": "order", **fields}, stamp(t)))


def test_mbo_partial_change_censoring_and_authoritative_fill_no_double_decrement():
    book = OrderBook()
    mbo(book, "Reset")
    mbo(book, "Image", side="S", p=2750, q=10, k=3, ki=1, pri="9007199254740993")
    assert book.orders["order"].left_censored
    mbo(book, "Change", t=1, q=8)
    fill = {"side": "S", "p": 2750, "q": 2, "k": 5, "ki": 1, "src": 0}
    assert mbo(book, "Fill", t=2, **fill) == "exact_fill"
    assert mbo(book, "Fill", t=2, **fill) is None
    assert mbo(book, "Fill", t=3, **{**fill, "q": 1, "k": 6, "ki": 2, "src": 1}) == "inferred_fill"
    order = book.orders["order"]
    assert (order.quantity, order.cumulative, order.inferred, order.priority) == (8, 6, 2, "9007199254740993")
    assert mbo(book, "Delete", t=4) == "order_removal_unattributed"
    assert not book.durations_ms
    mbo(book, "Image", t=5, side="S", p=2750, q=8)
    assert not book.orders
    mbo(book, "Reset", t=6)
    assert not book.orders


def test_mbo_fill_before_order_is_observation_not_synthesized_resting_order():
    book = OrderBook()
    mbo(book, "Reset")
    assert mbo(book, "Fill", side="B", p=2750, q=1, k=1, ki=0, src=0) == "exact_fill"
    assert not book.orders
    mbo(book, "New", t=1, side="B", p=2750, q=5)
    mbo(book, "Delete", t=2)
    assert list(book.durations_ms) == [1000.0]


def test_ofi_golden_examples_distinguish_size_and_migration():
    assert rank_ofi([(100, 8, 1), (102, 12, 1)], [(100, 10, 1), (102, 7, 1)]) == 7
    assert rank_ofi([(100, 8, 1), (102, 12, 1)], [(101, 11, 1), (103, 7, 1)]) == 23


def event(engine, channel, t, **data):
    payload = {"stream": f"comex.gcz6@{channel}", "data": {"s": "GCZ6", **data}}
    normalized = parse_event(payload, MOCK_GC, stamp(t), "fxaurum_mock")
    (engine.trade if channel == "trade" else engine.quote)(normalized)


def setup_candidate():
    engine = Microstructure("B", MOCK_GC, "fxaurum_mock", freshness_ns=40 * 10**9)
    engine.connect(stamp())
    init(engine)
    event(engine, "bookTicker", 0.5, b=Decimal("2749.9"), a=Decimal("2750.1"), B=10, A=10)
    event(engine, "trade", 1, e="trade", T=1_800_000_000_000, p=2750, q=12, side="B", tc=0)
    return engine


def test_hgc_fixed_grid_cutoff_target_and_provenance():
    engine = setup_candidate()
    depth(engine, "Update", 1, t=1.2, q=3)
    depth(engine, "Update", 2, t=1.5, p=Decimal("2750.2"), q=100)
    depth(engine, "Update", 3, t=1.8, q=7)
    event(engine, "bookTicker", 2.9, b=2750, a=Decimal("2750.2"), B=8, A=8)
    event(engine, "bookTicker", 3.1, b=2752, a=Decimal("2752.2"), B=8, A=8)
    card = engine.active.card
    assert card["prediction"]["refill_contracts"] == 4
    assert card["prediction"]["depletion_contracts"] == 7
    assert card["prediction"]["impact_ticks"] == 1
    assert "label" not in card
    event(engine, "bookTicker", 30.9, b=2751, a=Decimal("2751.2"), B=8, A=8)
    event(engine, "bookTicker", 31.1, b=2760, a=Decimal("2760.2"), B=8, A=8)
    assert card["label"]["impact_ticks"] == 11
    assert card["source"] == "fxaurum_mock"


def test_hgc_gap_stays_ineligible_after_recovery():
    engine = setup_candidate()
    depth(engine, "Update", 2, t=1.3)
    assert engine.active.card["status"] == "INELIGIBLE"
    depth(engine, "Image", 2, src=1, t=1.5)
    assert engine.depth.valid
    assert engine.active.card["label"] is None
    assert engine.snapshot(stamp(1.6))["ofi"]["status"] == "PARTIAL"


@pytest.mark.parametrize("mode", ["A", "B", "C"])
def test_actual_mock_websocket_to_api_with_modes_and_durable_recovery(mode, tmp_path):
    with TestClient(create_app(mode=mode, interval=0.01, capture_path=tmp_path / "p2.sqlite")) as client:
        with client.websocket_connect("/ws") as ws:
            for _ in range(18):
                data = ws.receive_json()
                micro = data["microstructure"]
                if data["trade"] and (
                    mode == "A" or micro["mbp"]["counts"].get("recoveries", 0) > 0
                ):
                    break
            else:
                pytest.fail("No initialized or recovered mode")
        assert all(data["candles"].values())
        assert data["status"]["market_data_mode"] == mode
        if mode != "A":
            assert micro["mbp"]["status"] == "VALID_BOOK"
            assert micro["mbp"]["gaps"]
            rows = client.get("/api/history?gaps=true").json()["records"]
            assert any(row["kind"] == "mbp_gap" for row in rows)
            assert any(row["kind"] == "mbp_recovery" for row in rows)
        if mode == "C":
            assert micro["mbo"]["counts"]["exact_fill"] > 0
            assert micro["mbo"]["counts"]["inferred_fill"] > 0
        else:
            assert micro["mbo"]["status"] == "DISABLED"


def test_mode_c_live_without_entitlement_degrades_to_b():
    with TestClient(create_app(mode="C", live_gateway_port=1), base_url="http://127.0.0.1") as client:
        status = client.get("/api/status").json()
        assert status["market_data_mode"] == "B"
        assert status["mbo_entitlement"] == "UNVERIFIED"
