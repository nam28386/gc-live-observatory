import asyncio
import json
import logging

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from observatory.collector import Collector
from observatory.events import MOCK_GC
from observatory.microstructure import Microstructure
from observatory.mock_gateway import MockGateway
from observatory.state import ObservatoryState


def test_mock_depth_snap_when_peer_tears_down(caplog):
    async def scenario():
        request_seen = asyncio.Event()
        release = asyncio.Event()

        class HeldReply(MockGateway):
            async def reply_snapshot(self, socket, request_id):
                request_seen.set()
                await release.wait()
                await super().reply_snapshot(socket, request_id)

        gateway = HeldReply(MOCK_GC, mode="B")
        async with gateway.running() as endpoint:
            async with connect(endpoint, proxy=None) as socket:
                await socket.send(json.dumps({"method": "SUBSCRIBE", "params": gateway.streams, "id": 1}))
                assert json.loads(await socket.recv())["id"] == 1
                await socket.send(json.dumps({"method": "depthSnap", "id": 2,
                                              "params": {"exchange": "COMEX", "symbol": "GCZ6"}}))
                await request_seen.wait()
            release.set()

    asyncio.run(asyncio.wait_for(scenario(), 5))
    assert not any("connection handler failed" in record.message for record in caplog.records
                   if record.levelno >= logging.ERROR)


@pytest.mark.parametrize("response", ["valid", "late", "wrong_generation", "malformed", "disconnect", "repeated_disconnect"])
def test_recovery_reply_boundaries_and_reconnect(response):
    async def scenario():
        attempts = 0
        first_ready = asyncio.Event()

        async def vendor(socket):
            nonlocal attempts
            attempts += 1
            req = json.loads(await socket.recv())
            await socket.send(json.dumps({"id": req["id"], "result": None}))
            for event in ({"type": "Reset", "gen": attempts},
                          {"type": "Image", "gen": attempts, "seq": 0, "rows": []},
                          {"type": "Update", "gen": attempts, "seq": 2,
                           "side": "B", "p": 2750, "q": 1, "n": 1}):
                await socket.send(json.dumps({"stream": "comex.gcz6@depthraw", "data": event}))
            rpc = json.loads(await socket.recv())
            assert rpc["method"] == "depthSnap"
            disconnects = 3 if response == "repeated_disconnect" else 1 if response == "disconnect" else 0
            if attempts <= disconnects:
                await socket.close()
                return
            result = {"type": "Image", "gen": attempts, "seq": 2, "rows": [], "src": 1}
            if response == "late":
                await socket.recv()  # The next request proves the first deadline expired.
            if response == "wrong_generation":
                result["gen"] += 1
            if response == "malformed":
                result.pop("rows")
            await socket.send(json.dumps({"id": rpc["id"], "result": result}))
            first_ready.set()
            await socket.wait_closed()

        state = ObservatoryState(MOCK_GC)
        micro = Microstructure("B", MOCK_GC, "fxaurum_mock")
        async with serve(vendor, "127.0.0.1", 0) as server:
            url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/"
            collector = Collector(url, state, micro=micro, ack_timeout=0.15, retry_delay=0.01)
            task = asyncio.create_task(collector.run())
            try:
                async with asyncio.timeout(5):
                    await first_ready.wait()
                    while not (micro.depth.valid or micro.depth.state == "RECOVERY_FAILED"):
                        await asyncio.sleep(0.005)
                if response in ("valid", "disconnect", "repeated_disconnect"):
                    expected = 4 if response == "repeated_disconnect" else 2 if response == "disconnect" else 1
                    assert micro.depth.counts["recoveries"] == expected
                    assert attempts == expected
                    assert sum(g["reason"] == "transport" for g in micro.depth.gaps) == expected - 1
                else:
                    assert not micro.depth.valid
                    assert micro.depth.counts["retry_exhausted"] == 1
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())
