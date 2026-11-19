"""Reproduce a delayed recovery request on the finite release tape."""

import json

import pytest
from websockets.asyncio.client import connect

from observatory.events import MOCK_GC
from observatory.mock_gateway import MockGateway


@pytest.mark.asyncio
async def test_finite_tape_keeps_socket_open_for_delayed_recovery_request():
    gateway = MockGateway(MOCK_GC, interval=.001, disconnect_after=16, mode="C", tape_cycles=23)
    async with gateway.running() as url, connect(url, proxy=None) as socket:
        await socket.send(json.dumps({"id": 1, "method": "SUBSCRIBE", "params": gateway.streams}))
        assert json.loads(await socket.recv()) == {"id": 1, "result": None}

        # Defer the request until the whole first connection's market tail has
        # arrived, as can happen while the collector is committing FIFO work.
        # 5 initial frames and 16 complete cycles of four market frames.
        for _ in range(69):
            assert "stream" in json.loads(await socket.recv())
        await socket.send(json.dumps({
            "id": 2, "method": "depthSnap", "params": {"exchange": "COMEX", "symbol": "GCZ6"},
        }))
        response = json.loads(await socket.recv())
        assert response["id"] == 2 and response["result"]["seq"] == 17
        await socket.wait_closed()
        assert socket.close_code == 1012
