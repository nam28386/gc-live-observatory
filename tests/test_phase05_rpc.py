"""Finite socket tapes for store pressure and correlated RPC outcomes."""

import asyncio
from contextlib import suppress
import json
import time

import pytest
from websockets.asyncio.server import serve

from observatory.collector import Collector
from observatory.events import MOCK_GC
from observatory.journal import Journal
from observatory.microstructure import Microstructure
from observatory.state import ObservatoryState


async def market(socket, channel, data):
    await socket.send(json.dumps({"stream": f"comex.gcz6@{channel}", "data": data}))


async def seed(socket):
    req = json.loads(await socket.recv())
    await socket.send(json.dumps({"id": req["id"], "result": None}))
    for item in ({"type": "Reset", "gen": 1}, {"type": "Image", "gen": 1, "seq": 0, "rows": []},
                 {"type": "Update", "gen": 1, "seq": 2, "side": "B", "p": 2750, "q": 1, "n": 1}):
        await market(socket, "depthraw", item)


def test_finite_burst_recovery_under_full_commit_pressure(tmp_path, monkeypatch):
    monkeypatch.setattr("observatory.collector.RECEIVE_QUEUE", 4)
    # Preserve SQLite FULL and recorder/transport paths. Add cross-platform,
    # fixed storage cost per transaction in the full-path pressure fixture.
    write = Journal.write_records

    def slow(self, records):
        if any(row[0] == "bbo" for row in records):
            time.sleep(.034)
        return write(self, records)

    monkeypatch.setattr(Journal, "write_records", slow)

    async def scenario():
        requests = []
        sent = 32

        async def gateway(socket):
            await seed(socket)
            first = json.loads(await socket.recv())
            requests.append(first)
            for _ in range(sent):
                await market(socket, "bookTicker", {"s": "GCZ6", "b": 2750, "a": 2751, "B": 1, "A": 1})
            await socket.send(json.dumps({
                "id": first["id"], "result": {"type": "Image", "gen": 1, "seq": 2, "src": 1, "rows": []},
            }))
            async for raw in socket:
                requests.append(json.loads(raw))

        book = Microstructure("B", MOCK_GC, "fxaurum_mock")
        journal = Journal(tmp_path / "burst.sqlite", {}, batch_size=32)
        async with journal.running(), serve(gateway, "127.0.0.1", 0) as listener:
            endpoint = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}/"
            collector = Collector(endpoint, ObservatoryState(MOCK_GC), micro=book, journal=journal, ack_timeout=.6)
            task = asyncio.create_task(collector.run())
            try:
                async with asyncio.timeout(12):
                    while not book.depth.valid or book.depth.counts.get("recoveries", 0) != 1:
                        if book.depth.state == "RECOVERY_FAILED":
                            break
                        await asyncio.sleep(.01)
                    assert book.depth.valid, (book.depth.status(), collector.receiver_diagnostics())
                    while collector.recovery_publication_ms is None:
                        await asyncio.sleep(.01)
                assert len(requests) == 1
                assert book.depth.counts["rpc_timeout"] == 0
                assert collector.receive_high_water <= 4
                assert collector.uncertain_receipts == 0
                rows = journal.history()
                kinds = [row["kind"] for row in rows]
                assert kinds.count("bbo") == sent
                assert kinds.count("depth_snapshot") == kinds.count("mbp_recovery") == 1
                assert max(i for i, kind in enumerate(kinds) if kind == "bbo") < kinds.index("depth_snapshot")
                assert any(gap["reason"] == "sequence" for gap in book.depth.gaps)
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    asyncio.run(scenario())


@pytest.mark.parametrize("reply,expected,attempts", [
    ("ok", "SNAPSHOT_APPLIED", 1),
    ("none", "RPC_RECEIPT_TIMEOUT", 3),
    ("wrong_id", "RPC_RECEIPT_TIMEOUT", 3),
    ("wrong_generation", "RPC_GENERATION_MISMATCH", 3),
    ("malformed", "RPC_RESPONSE_INVALID", 3),
    ("insufficient", "RPC_SEQUENCE_INSUFFICIENT", 3),
    ("late", "RPC_RECEIPT_TIMEOUT", 3),
    ("close", "RPC_TRANSPORT_INTERRUPTED", 1),
])
def test_correlated_rpc_outcomes_and_permanent_gap(reply, expected, attempts, tmp_path):
    async def scenario():
        sent = []
        first_socket = asyncio.Event()
        journ = Journal(tmp_path / "rpc.sqlite", {}, batch_size=4)

        async def gateway(socket):
            if first_socket.is_set():
                await socket.wait_closed()
                return
            first_socket.set()
            await seed(socket)
            late = None
            async for raw in socket:
                rpc = json.loads(raw)
                assert rpc["method"] == "depthSnap"
                sent.append(rpc["id"])
                if reply == "close":
                    await socket.close()
                    return
                if reply == "none":
                    continue
                result = {"id": rpc["id"] + (1000 if reply == "wrong_id" else 0),
                          "result": {"type": "Image", "gen": 2 if reply == "wrong_generation" else 1,
                                     "seq": 0 if reply == "insufficient" else 2, "src": 1, "rows": []}}
                if reply == "malformed":
                    result["result"].pop("rows")
                if reply == "late":
                    if late:
                        await socket.send(json.dumps(late))
                    late = result
                    continue
                await socket.send(json.dumps(result))

        book = Microstructure("B", MOCK_GC, "fxaurum_mock")
        async with journ.running(), serve(gateway, "127.0.0.1", 0) as listener:
            endpoint = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}/"
            collector = Collector(endpoint, ObservatoryState(MOCK_GC), micro=book, journal=journ, ack_timeout=.18)
            task = asyncio.create_task(collector.run())
            try:
                async with asyncio.timeout(8):
                    while True:
                        result = collector.rpc.view()
                        done = result["counts"].get(expected, 0)
                        if done == attempts and (attempts == 1 or book.depth.state == "RECOVERY_FAILED"):
                            break
                        if book.depth.state == "RECOVERY_FAILED" and done != attempts:
                            pytest.fail(f"Unexpected recovery outcome: {result}; {book.depth.status()}")
                        await asyncio.sleep(.01)
                assert len(sent) == attempts
                assert result["active"] is None
                assert len(book.depth.gaps) >= 1
                assert any(item["reason"] == "sequence" for item in book.depth.gaps)
                for item in result["recent"]:
                    assert item["time_basis"] == "same_process_monotonic_offsets_from_creation"
                    assert item["connection_epoch"] == 1 and item["instrument"] == "GCZ6"
                    assert item["generation"] == 1
                rows = journ.history()
                snapshots = [row for row in rows if row["kind"] == "depth_snapshot"]
                if reply == "ok":
                    assert book.depth.valid and len(snapshots) == 1
                    assert list(result["recent"][0]["stages_ms"]) == [
                        "REQUEST_CREATED", "REQUEST_SENT", "RESPONSE_RECEIVED", "RESPONSE_QUEUED",
                        "RESPONSE_DISPATCHED", "RESPONSE_VALIDATED", "RESPONSE_COMMITTED", "SNAPSHOT_APPLIED",
                    ]
                    assert [r["kind"] for r in rows].index("mbp_gap") < [r["kind"] for r in rows].index("depth_snapshot")
                else:
                    assert not book.depth.valid and not book.depth.counts.get("recoveries")
                assert collector.uncertain_receipts == 0
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    asyncio.run(scenario())


def test_snapshot_commit_failure_does_not_publish_or_claim_application(tmp_path):
    import sqlite3

    async def scenario():
        journal = Journal(tmp_path / "failure.sqlite", {}, batch_size=8)
        write = journal.write_records

        def fail_on_snapshot(records):
            if any(row[0] == "depth_snapshot" for row in records):
                raise sqlite3.OperationalError("synthetic commit fault")
            return write(records)

        journal.write_records = fail_on_snapshot

        async def gateway(socket):
            await seed(socket)
            rpc = json.loads(await socket.recv())
            await socket.send(json.dumps({"id": rpc["id"],
                                          "result": {"type": "Image", "gen": 1, "seq": 2, "src": 1, "rows": []}}))
            await socket.wait_closed()

        state = ObservatoryState(MOCK_GC)
        micro = Microstructure("B", MOCK_GC, "fxaurum_mock")
        async with journal.running(), serve(gateway, "127.0.0.1", 0) as listener:
            endpoint = f"ws://127.0.0.1:{listener.sockets[0].getsockname()[1]}/"
            collector = Collector(endpoint, state, micro=micro, journal=journal)
            await asyncio.wait_for(collector.run(), 10)
            rpc = collector.rpc.view()
            assert rpc["counts"]["RPC_COMMIT_FAILURE"] == 1
            assert "RESPONSE_COMMITTED" not in rpc["recent"][-1]["stages_ms"]
            assert rpc["active"] is None
            assert state.recorder_error and journal.publication_failed
            rows = journal.history()
            assert not any(row["kind"] in ("depth_snapshot", "mbp_recovery") for row in rows)
            assert any(row["kind"] == "mbp_gap" for row in rows)
    asyncio.run(scenario())


def test_reconnect_during_batch_does_not_count_committed_frames_as_uncertain(tmp_path):
    from argparse import Namespace
    from scripts.benchmark_phase03 import run

    result = run(Namespace(mode="B", cycles=90, levels=20, pace=0, burst=64, reconnect_every=30,
                           gap_every=18), tmp_path / "reconnect.sqlite", batch_size=32)
    receiver = result["receiver"]
    assert result["planned_connections"] == 3
    assert result["recovery_replies"] == 5
    assert receiver["uncertain_received_frames"] == 0
    assert receiver["admitted_queue_frames"] == receiver["settled_queue_frames"]
    assert result["durable_gap_records"] >= 7
