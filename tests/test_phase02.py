from decimal import Decimal
import json
import time

from fastapi.testclient import TestClient
import pytest

from observatory.books import DepthBook, OrderBook, parse_depth, parse_order
from observatory.events import MOCK_GC, Observation, ParseError, parse_event
from observatory.microstructure import Microstructure, rank_ofi
from observatory.web import create_app
from observatory.mock_gateway import MockGateway


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


def await_snapshot(client, predicate):
    # Wait for the observable barrier, independently of the dashboard's
    # publication cadence, timer precision and recorder filesystem latency.
    deadline = time.monotonic() + 12
    while True:
        data = client.get("/api/snapshot").json()
        if predicate(data):
            return data
        if time.monotonic() >= deadline:
            pytest.fail(f"Capture did not reach expected barrier: {data['status']}, {data['microstructure']['mbp']}")
        time.sleep(0.025)


@pytest.mark.parametrize("mode", ["A", "B", "C"])
def test_actual_mock_websocket_to_api_with_modes_and_durable_recovery(mode, tmp_path):
    with TestClient(create_app(mode=mode, mock_gateway=MockGateway(MOCK_GC, interval=.01, mode=mode, tape_cycles=13),
                               capture_path=tmp_path / "p2.sqlite")) as client:
        def ready(data):
            micro = data["microstructure"]
            if not data["trade"]:
                return False
            if mode == "A":
                return True
            if micro["mbp"]["status"] != "VALID_BOOK" or not micro["mbp"]["counts"].get("recoveries"):
                return False
            records = client.get("/api/history?gaps=true").json()["records"]
            if not any(record["kind"] == "mbp_recovery" for record in records):
                return False
            counts = micro["mbo"]["counts"] or {}
            return mode != "C" or counts.get("exact_fill") and counts.get("inferred_fill")

        await_snapshot(client, ready)
        with client.websocket_connect("/ws") as ws:
            data = ws.receive_json()
        micro = data["microstructure"]
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


def test_bounded_recovery_buffer_requires_snapshot_after_highest_discarded_sequence(monkeypatch):
    monkeypatch.setattr("observatory.books.RECOVERY_BUFFER", 2)
    book = DepthBook()
    init(book)
    for seq in range(4, 8):
        depth(book, "Update", seq)
    assert len(book.pending) <= 2
    assert book.counts["buffer_high_water"] == 2
    assert book.counts["buffer_overflow"] == 1
    depth(book, "Image", 6, t=8, src=1, snapshot=True)
    assert not book.valid
    depth(book, "Image", 7, t=9, src=1, snapshot=True)
    assert book.valid


def test_replay_uses_durable_order_and_preserves_gap_after_recovery(tmp_path):
    from observatory.replay import replay

    database = tmp_path / "replay.sqlite"
    with TestClient(create_app(mode="C", mock_gateway=MockGateway(MOCK_GC, interval=.01, mode="C", tape_cycles=13),
                               capture_path=database)) as client:
        data = await_snapshot(client, lambda item: (
            item["microstructure"]["mbp"]["counts"].get("recoveries")
            and (item["microstructure"]["mbo"]["counts"] or {}).get("exact_fill")
            and any(row["kind"] == "mbp_recovery"
                    for row in client.get("/api/history?gaps=true").json()["records"])
        ))
        session = data["status"]["recorder"]["session_id"]
        first = replay(database, session, "C", 200)
        # Read the exact same immutable prefix even while the recorder progresses.
        second = replay(database, session, "C", first["records_processed"])
        assert first["snapshot"] == second["snapshot"]
        assert first["snapshot"]["mbp"]["gaps"][0]["reason"] == "sequence"
        assert first["snapshot"]["mbo"]["counts"]["exact_fill"] > 0


def test_recovery_times_out_with_bounded_requests_while_socket_stays_open():
    import asyncio
    from websockets.asyncio.server import serve
    from observatory.collector import Collector
    from observatory.state import ObservatoryState

    async def scenario():
        requests = []

        async def vendor(socket):
            subscribe = json.loads(await socket.recv())
            requests.append(subscribe)
            await socket.send(json.dumps({"id": subscribe["id"], "result": None}))
            await socket.send(json.dumps({"stream": "comex.gcz6@depthraw", "data": {"type": "Reset", "gen": 1}}))
            async for request in socket:
                requests.append(json.loads(request))

        state = ObservatoryState(MOCK_GC)
        micro = Microstructure("B", MOCK_GC, "fxaurum_mock")
        async with serve(vendor, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            collector = Collector(f"ws://127.0.0.1:{port}/", state, micro=micro, ack_timeout=0.1)
            task = asyncio.create_task(collector.run())
            try:
                async with asyncio.timeout(3):
                    while micro.depth.state != "RECOVERY_FAILED":
                        await asyncio.sleep(0.01)
                assert [req["method"] for req in requests] == ["SUBSCRIBE", *["depthSnap"] * 3]
                assert len({req["id"] for req in requests}) == 4
                assert state.connected
                assert state.last_ack_request_id == requests[0]["id"]
                assert micro.depth.gaps[0]["reason"] == "snapshot_timeout"
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_probe_correlates_response_and_uses_only_operator_rpc():
    import asyncio
    from websockets.asyncio.server import serve
    from observatory.__main__ import probe

    async def scenario():
        async def vendor(socket):
            request = json.loads(await socket.recv())
            assert request == {"method": "mboProbe", "id": 1,
                               "params": {"exchange": "COMEX", "symbol": "GCZ6", "ms": 8000}}
            await socket.send(json.dumps({"id": 2, "result": {"entitled": True}}))
            await socket.send(json.dumps({"id": 1, "result": {"entitled": False}}))

        async with serve(vendor, "127.0.0.1", 0) as server:
            assert not await probe(MOCK_GC, server.sockets[0].getsockname()[1])
    asyncio.run(scenario())


def test_stream_rejection_degrades_from_c_to_b_and_preserves_trade_bbo():
    import asyncio
    from websockets.asyncio.server import serve
    from observatory.collector import Collector
    from observatory.state import ObservatoryState

    async def scenario():
        requests = []

        async def vendor(socket):
            request = json.loads(await socket.recv())
            requests.append(request)
            if len(request["params"]) == 4:
                await socket.send(json.dumps({"id": request["id"], "status": 403, "error": "DBO unavailable"}))
                await socket.wait_closed()
                return
            await socket.send(json.dumps({"id": request["id"], "result": None}))
            await socket.send(json.dumps({
                "stream": "comex.gcz6@trade",
                "data": {"s": "GCZ6", "e": "trade", "T": 1_800_000_000_000, "tc": 0,
                         "p": 2750.1, "q": 1, "side": ""},
            }))
            await socket.wait_closed()

        state = ObservatoryState(MOCK_GC)
        micro = Microstructure("C", MOCK_GC, "fxaurum_mock")
        async with serve(vendor, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            collector = Collector(f"ws://127.0.0.1:{port}/", state, micro=micro, retry_delay=0.01)
            task = asyncio.create_task(collector.run())
            try:
                async with asyncio.timeout(3):
                    while state.last_trade is None:
                        await asyncio.sleep(0.01)
                assert micro.mode == "B" and micro.orders is None
                assert micro.entitlement == "SUBSCRIPTION_REJECTED"
                assert [len(request["params"]) for request in requests] == [4, 3]
                assert state.last_trade.signed_size == 0 and state.cvd == 0
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_bounded_measurement_saturation_and_mixed_receive_clock(monkeypatch):
    monkeypatch.setattr("observatory.microstructure.LIMIT", 2)
    engine = setup_candidate()
    for seq, t in [(1, 1.1), (2, 1.2), (3, 1.3)]:
        depth(engine, "Update", seq, t=t)
    view = engine.snapshot(stamp(2.5))
    assert len(engine.ofi) == 2
    assert view["diagnostics"]["measurement_overflow"]["ofi"]
    assert view["ofi"]["ranks"] is None
    depth(engine, "Update", 4, t=1.2)
    assert engine.depth.state == "GAP_DETECTED"
    assert engine.depth.gaps[-1]["reason"] == "late_receive_clock"


def test_same_sequence_gateway_repair_can_change_state_but_never_invents_flow():
    engine = setup_candidate()
    depth(engine, "Update", 1, t=1.1, q=8)
    depth(engine, "Image", 1, t=1.2, src=1)
    assert engine.depth.levels["S"][27501] == (10, 2)
    assert engine.depth.gaps[-1]["reason"] == "gateway_image"
    assert engine.active.card["status"] == "INELIGIBLE"
    assert not engine.ofi
    depth(engine, "Image", 1, t=1.3, src=1)
    assert engine.depth.counts["duplicate_image"] == 1
    assert len(engine.depth.gaps) == 1


def test_receive_time_liquidity_and_intensity_golden_values():
    engine = Microstructure("B", MOCK_GC, "fxaurum_mock")
    engine.connect(stamp())
    init(engine)
    depth(engine, "Update", 1, t=1, q=5)
    event(engine, "bookTicker", 1.2, b=Decimal("2749.9"), a=Decimal("2750.1"), B=10, A=5)
    event(engine, "trade", 1.3, e="trade", T=1_800_000_000_000, p=2750, q=3, side="", tc=0)
    view = engine.snapshot(stamp(2))
    assert view["liquidity"]["top_n_depth"] == [10, 5]
    assert view["liquidity"]["spread_ticks"] == 2
    assert view["liquidity"]["imbalance"] == pytest.approx(1 / 3)
    assert view["liquidity"]["concentration"] == pytest.approx(5 / 9)
    assert view["liquidity"]["bbo_disagreement"] is False
    assert view["ofi"]["best_level"] == 5
    assert view["ofi"]["price_fixed_ask_change"] == -5
    assert view["intensity"]["rates_per_second"] == {"mbp_update": 0.5, "trade": 0.5}
    assert view["trade_flow"]["net"] == 0 and view["trade_flow"]["unknown"] == 3
    assert engine.snapshot(stamp(4.2))["liquidity"]["status"] == "UNAVAILABLE"
