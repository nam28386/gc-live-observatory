import asyncio
from contextlib import asynccontextmanager
import json

from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from observatory.collector import Collector
from observatory.events import MOCK_GC, Observation
from observatory.mock_gateway import MockGateway
from observatory.state import ObservatoryState
from observatory.web import create_app

GATEWAY = MockGateway(MOCK_GC)


async def eventually(predicate) -> None:
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.005)


@asynccontextmanager
async def collect(handler, *, stale_after=3, ack_timeout=2):
    state = ObservatoryState(MOCK_GC, stale_after)
    async with serve(handler, "127.0.0.1", 0, close_timeout=0.1) as server:
        endpoint = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/"
        collector = Collector(endpoint, state, retry_delay=0.02, ack_timeout=ack_timeout)
        task = asyncio.create_task(collector.run())
        try:
            yield state
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def subscribe(socket) -> int:
    request = json.loads(await socket.recv())
    assert request["method"] == "SUBSCRIBE"
    assert request["params"] == list(MOCK_GC.streams)
    return request["id"]


async def ack(socket, request_id: int) -> None:
    await socket.send(json.dumps({"id": request_id, "status": 200, "result": None}))


@pytest.mark.asyncio
async def test_only_matching_ack_releases_pre_ack_trades():
    release = asyncio.Event()

    async def handler(socket):
        request_id = await subscribe(socket)
        await ack(socket, request_id + 1)
        await GATEWAY.send(socket, MOCK_GC.streams[0], GATEWAY.trade(0))
        await release.wait()
        await ack(socket, request_id)
        await socket.wait_closed()

    async with collect(handler) as state:
        try:
            await eventually(lambda: state.counters["unexpected_ack_id"] == 1)
            assert state.request_id == 1
            assert state.ack is None
            assert not state.candles["M1"]
        finally:
            release.set()
        await eventually(lambda: state.last_trade is not None)
        assert state.last_ack_request_id == 1
        assert state.last_trade.price_ticks == 27500
        assert state.counters["subscribe_ack"] == 1


@pytest.mark.asyncio
async def test_wrong_ack_alone_times_out_and_retries():
    async def handler(socket):
        request_id = await subscribe(socket)
        await ack(socket, request_id + 1)
        await socket.wait_closed()

    async with collect(handler, ack_timeout=0.08) as state:
        await eventually(lambda: state.request_id is not None and state.request_id >= 2)
        assert state.counters["subscribe_ack_timeout"] >= 1
        assert state.ack is None
        assert state.last_ack_request_id is None
        assert not state.coverage


@pytest.mark.asyncio
async def test_disconnect_resubscribes_and_snapshot_is_not_a_trade():
    next_trade = asyncio.Event()
    requests = []

    async def handler(socket):
        request_id = await subscribe(socket)
        requests.append(request_id)
        await ack(socket, request_id)
        if len(requests) == 1:
            await GATEWAY.send(socket, MOCK_GC.streams[0], GATEWAY.trade(0))
            await socket.close()
        else:
            await GATEWAY.send(socket, MOCK_GC.streams[1], GATEWAY.quote(4))
            await next_trade.wait()
            await GATEWAY.send(socket, MOCK_GC.streams[0], GATEWAY.trade(4))
            await socket.wait_closed()

    async with collect(handler) as state:
        try:
            await eventually(lambda: state.last_ack_request_id == 2 and state.last_ticker is not None)
            status = state.status(Observation.now())
            assert requests == [1, 2]
            assert status["transport_connected"] and status["subscribe_ack"]
            assert status["trade_freshness"] == "WAITING_DATA"
            assert state.last_trade.exchange_time_ms == GATEWAY.trade(0)["T"]
            assert state.counters["eligible_trade"] == 1
            assert state.last_ticker.reported_last_ticks == GATEWAY.price(4)
            assert state.last_bbo_change is None
            assert len(status["coverage"]) == 2
            assert status["coverage"][0]["ended"]
            assert status["gaps"][0]["reason"] == "client_socket_closed"
            assert status["gaps"][0]["resumed"]
        finally:
            next_trade.set()
        await eventually(lambda: state.counters["eligible_trade"] == 2)
        assert state.candles["M1"][-1].partial
        assert state.counters["coverage_gaps"] == 1


@pytest.mark.asyncio
async def test_upstream_silence_keeps_socket_and_subscription():
    requests = []

    async def handler(socket):
        requests.append(await subscribe(socket))
        await ack(socket, requests[-1])
        await GATEWAY.send(socket, MOCK_GC.streams[1], GATEWAY.quote(0))
        await socket.wait_closed()

    async with collect(handler, stale_after=0.05) as state:
        await eventually(lambda: state.status(Observation.now())["market_freshness"] == "STALE")
        assert state.connected
        assert state.ack is not None
        assert not state.gaps
        assert requests == [1]


@pytest.mark.asyncio
async def test_bad_frames_are_counted_and_do_not_interrupt_valid_trades():
    async def handler(socket):
        await ack(socket, await subscribe(socket))
        await socket.send("not-json")
        await GATEWAY.send(socket, "cme.gcz6@trade", GATEWAY.trade(0))
        await GATEWAY.send(socket, MOCK_GC.streams[1], {**GATEWAY.quote(0), "b": 3000})
        await GATEWAY.send(socket, MOCK_GC.streams[0], GATEWAY.trade(0))
        await socket.wait_closed()

    async with collect(handler) as state:
        await eventually(lambda: state.last_trade is not None)
        assert state.counters["malformed_message"] == 1
        assert state.counters["wrong_instrument"] == 1
        assert state.counters["CROSSED_BBO"] == 1
        assert state.connected and state.ack
        assert state.last_any == state.last_trade.observed


@pytest.mark.asyncio
async def test_pre_ack_buffer_exhaustion_is_an_explicit_failure():
    async def handler(socket):
        await subscribe(socket)
        for _ in range(33):
            await GATEWAY.send(socket, MOCK_GC.streams[0], GATEWAY.trade(0))
        await socket.wait_closed()

    async with collect(handler) as state:
        await eventually(lambda: state.counters["subscribe_buffer_overflow"] >= 1)
        assert not state.coverage
        assert state.last_trade is None


def test_http_and_websocket_show_real_collector_state_and_each_timeframe():
    with TestClient(create_app(interval=0.04)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/static/dashboard.js").status_code == 200
        with client.websocket_connect("/ws") as ws:
            initial = ws.receive_json()
            for _ in range(8):
                updated = ws.receive_json()
                if updated["trade"] and initial["trade"] != updated["trade"]:
                    break
            else:
                pytest.fail("WebSocket did not deliver a new trade")
        snapshot = client.get("/api/snapshot").json()
        status = client.get("/api/status").json()
        assert status["mode"] == "MOCK"
        assert status["subscribe_ack"]
        assert status["request_id"] == status["last_ack_request_id"]
        assert snapshot["trade"]["gateway_receive_ms"] == snapshot["trade"]["exchange_time_ms"] + 8
        assert snapshot["bbo"]["spread_ticks"] == 2
        assert set(updated["candles"]) == {"M1", "M5", "M15", "H1"}
        assert all(bars[-1]["volume"] > 0 for bars in updated["candles"].values())
        assert snapshot["trade"]["exchange_time_ms"] >= updated["trade"]["exchange_time_ms"]


@pytest.mark.asyncio
async def test_gateway_rejects_account_order_and_other_stream_requests():
    gateway = MockGateway(MOCK_GC)
    async with gateway.running() as endpoint:
        async with connect(endpoint, proxy=None) as socket:
            for request_id, method in enumerate(("getAccounts", "sendOrder", "cancelOrder", "SUBSCRIBE"), 1):
                await socket.send(json.dumps({
                    "method": method, "id": request_id, "params": ["CME.ESU6@trade"],
                }))
                reply = json.loads(await socket.recv())
                assert reply["id"] == request_id
                assert reply["status"] == 400
            assert gateway.index == 0


def test_browser_endpoints_cannot_forward_commands():
    with TestClient(create_app()) as client:
        assert client.post("/api/snapshot", json={"method": "sendOrder"}).status_code == 405
        assert client.post("/gateway_rpc", json={"method": "getAccounts"}).status_code == 404
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["status"]["mode"] == "MOCK"
            ws.send_json({"method": "sendOrder"})
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
            assert exc.value.code == 1008


@pytest.mark.parametrize("url", ["ws://127.0.0.1:9001/", "ws://example.com:9999/", "wss://127.0.0.1:9999/"])
def test_collector_refuses_live_or_non_loopback_endpoints(url):
    with pytest.raises(ValueError, match="loopback mock gateway"):
        Collector(url, ObservatoryState(MOCK_GC))
