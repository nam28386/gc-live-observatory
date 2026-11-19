import asyncio
from contextlib import suppress
import json
import threading

from websockets.asyncio.server import serve

from observatory.collector import Collector, RECEIVE_QUEUE
from observatory.events import MOCK_GC
from observatory.journal import Journal
from observatory.microstructure import Microstructure
from observatory.state import ObservatoryState


def test_depthsnap_receipt_is_not_timed_out_while_durable_writer_is_blocked(tmp_path):
    async def run():
        release = threading.Event()
        entered = threading.Event()
        response_sent = asyncio.Event()
        requests = []
        journal = Journal(tmp_path / "pressure.sqlite", {}, batch_size=1)
        write = journal.write

        def slow_write(kind, *args):
            if kind == "trade":
                entered.set()
                assert release.wait(5)
            return write(kind, *args)

        journal.write = slow_write

        async def gateway(socket):
            request = json.loads(await socket.recv())
            await socket.send(json.dumps({"id": request["id"], "result": None}))
            for record in ({"type": "Reset", "gen": 1},
                           {"type": "Image", "gen": 1, "seq": 0, "rows": []},
                           {"type": "Update", "gen": 1, "seq": 2, "side": "B", "p": 2750, "q": 1, "n": 1}):
                await socket.send(json.dumps({"stream": "comex.gcz6@depthraw", "data": record}))
            request = json.loads(await socket.recv())
            requests.append(request)
            await socket.send(json.dumps({
                "stream": "comex.gcz6@trade",
                "data": {"s": "GCZ6", "e": "trade", "T": 1_800_000_000_000, "p": 2750, "q": 1,
                         "side": "B", "tc": 0},
            }))
            await socket.send(json.dumps({
                "id": request["id"], "result": {"type": "Image", "gen": 1, "seq": 2, "rows": [], "src": 1},
            }))
            response_sent.set()
            await socket.wait_closed()

        state = ObservatoryState(MOCK_GC)
        micro = Microstructure("B", MOCK_GC, "fxaurum_mock")
        async with journal.running(), serve(gateway, "127.0.0.1", 0) as server:
            endpoint = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/"
            collector = Collector(endpoint, state, micro=micro, journal=journal, ack_timeout=.20)
            task = asyncio.create_task(collector.run())
            try:
                async with asyncio.timeout(6):
                    await response_sent.wait()
                    assert await asyncio.to_thread(entered.wait, 3)
                    # Let the request deadline expire while the durable writer is blocked.
                    await asyncio.sleep(.26)
                    assert not micro.depth.valid
                    release.set()
                    while not micro.depth.valid or collector.recovery_publication_ms is None:
                        await asyncio.sleep(.005)
                assert len(requests) == 1 and requests[0]["method"] == "depthSnap"
                assert micro.depth.counts["rpc_timeout"] == 0
                assert collector.recovery_receipt_ms < 200 < collector.recovery_dispatch_ms
                assert collector.recovery_publication_ms >= collector.recovery_dispatch_ms
                assert collector.receive_high_water <= RECEIVE_QUEUE
                rows = journal.history()
                assert [row["kind"] for row in rows].index("trade") < [row["kind"] for row in rows].index("depth_snapshot")
            finally:
                release.set()
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
    asyncio.run(run())


def test_saturated_receipt_queue_preserves_explicit_unknown_and_durable_gap(tmp_path, monkeypatch):
    monkeypatch.setattr("observatory.collector.RECEIVE_QUEUE", 2)
    monkeypatch.setattr("observatory.collector.RECEIVE_PRESSURE_TIMEOUT", .05)

    async def run():
        release = threading.Event()
        entered = threading.Event()
        journal = Journal(tmp_path / "overflow.sqlite", {}, batch_size=1)
        original = journal.write

        def slow(kind, *args):
            if kind == "trade" and not release.is_set():
                entered.set()
                assert release.wait(5)
            return original(kind, *args)

        journal.write = slow
        connects = 0

        async def gateway(socket):
            nonlocal connects
            connects += 1
            req = json.loads(await socket.recv())
            await socket.send(json.dumps({"id": req["id"], "result": None}))
            if connects == 1:
                for i in range(12):
                    await socket.send(json.dumps({
                        "stream": "comex.gcz6@trade",
                        "data": {"s": "GCZ6", "e": "trade", "T": 1_800_000_000_000 + i,
                                 "p": 2750, "q": 1, "side": "B", "tc": 0},
                    }))
            await socket.wait_closed()

        state = ObservatoryState(MOCK_GC)
        async with journal.running(), serve(gateway, "127.0.0.1", 0) as server:
            endpoint = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/"
            collector = Collector(endpoint, state, journal=journal, retry_delay=.01)
            task = asyncio.create_task(collector.run())
            try:
                async with asyncio.timeout(6):
                    assert await asyncio.to_thread(entered.wait, 3)
                    while collector.uncertain_receipts == 0:
                        await asyncio.sleep(.005)
                    release.set()
                    while not state.counters["local_receive_backpressure"] or state.last_ack_request_id == 1:
                        await asyncio.sleep(.005)
                assert collector.receive_high_water == 2
                assert collector.receive_pressure > 0
                rows = journal.history()
                assert len([row for row in rows if row["kind"] == "trade"]) == 3
                assert any(row["kind"] == "gap" and row["payload"]["reason"] == "local_receive_backpressure"
                           and row["payload"]["receiver_uncertain_min"] >= 1 for row in rows)
            finally:
                release.set()
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
    asyncio.run(run())
