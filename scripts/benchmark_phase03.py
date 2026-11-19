"""Measure a deterministic socket-to-durable-capture-to-API synthetic workload."""

import argparse
import asyncio
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import time

from fastapi.testclient import TestClient

from observatory.events import MOCK_GC
from observatory.mock_gateway import MockGateway
from observatory.replay import replay
from observatory.web import create_app


class Workload(MockGateway):
    def __init__(self, mode, cycles, book_levels, pace, burst, reconnect, gaps):
        super().__init__(MOCK_GC, mode=mode)
        self.cycles, self.book_levels, self.pace, self.burst = cycles, book_levels, pace, burst
        self.sent = Counter()
        self.send_started = self.send_ended = None
        self.next_cycle = 1
        self.reconnect, self.gaps = reconnect, gaps
        self.recovery_replies = 0
        self.reply_ready = asyncio.Event()

    def image(self, src=0):
        rows = [[side, (27500 + (level if side else -level)) / 10,
                 self.ask_size if side == 1 and level == 1 else 15, 2]
                for side in (0, 1) for level in range(1, self.book_levels + 1)]
        return {"type": "Image", "gen": self.generation, "seq": self.seq, "src": src, "rows": rows}

    async def reply_snapshot(self, socket, request_id):
        await super().reply_snapshot(socket, request_id)
        self.recovery_replies += 1
        self.reply_ready.set()

    async def emit(self, socket, stream, kind, payload):
        await self.send(socket, stream, payload)
        self.sent[kind] += 1

    async def publish(self, socket):
        if self.send_started is None:
            self.send_started = time.perf_counter()
        self.generation += 1
        self.seq = 0
        if self.mode != "A":
            await self.emit(socket, self.streams[2], "mbp", {"type": "Reset", "gen": self.generation})
            await self.emit(socket, self.streams[2], "mbp", self.image())
        if self.mode == "C":
            await self.emit(socket, self.streams[3], "mbo", {"type": "Reset"})
        for i in range(self.next_cycle, self.cycles + 1):
            if self.mode == "A" or i % 32 == 1:
                await self.emit(socket, self.streams[0], "trade", self.trade(i))
                await self.emit(socket, self.streams[1], "bbo", self.quote(i))
            if self.mode != "A":
                gap = self.gaps and i % self.gaps == 0
                self.seq += 2 if gap else 1
                self.ask_size = 4 + i % 9
                if gap:
                    self.reply_ready.clear()
                await self.emit(socket, self.streams[2], "mbp", {
                    "type": "Update", "gen": self.generation, "seq": self.seq, "side": "S",
                    "p": 2750.1, "q": self.ask_size, "n": 1,
                })
                if gap:
                    async with asyncio.timeout(10):
                        await self.reply_ready.wait()
            if self.mode == "C":
                oid = str((i - 1) // 3)
                event = (
                    {"type": "New", "oid": oid, "q": 4, "side": "S", "p": 2750.1},
                    {"type": "Fill", "oid": oid, "q": 1, "side": "S", "p": 2750.1, "k": 1, "ki": i % 2, "src": i % 2},
                    {"type": "Delete", "oid": oid},
                )[(i - 1) % 3]
                await self.emit(socket, self.streams[3], "mbo", event)
            self.next_cycle = i + 1
            if i < self.cycles:
                if self.reconnect and i % self.reconnect == 0:
                    await socket.close(code=1012, reason="synthetic stress reconnect")
                    return
                if i % self.burst == 0:
                    await asyncio.sleep(self.pace)
        self.send_ended = time.perf_counter()
        await socket.wait_closed()


def run(args, path, **options):
    source = Workload(args.mode, args.cycles, args.levels, args.pace, args.burst, args.reconnect_every, args.gap_every)
    cpu_start = time.process_time()
    count = 0
    connections = 1 + ((args.cycles - 1) // args.reconnect_every if args.reconnect_every else 0)
    expected = (args.cycles * 2 if args.mode == "A" else
                ((args.cycles - 1) // 32 + 1) * 2 + args.cycles + 2 * connections +
                (args.cycles + connections if args.mode == "C" else 0))
    with TestClient(create_app(mode=args.mode, capture_path=path, mock_gateway=source, **options)) as client:
        deadline = time.perf_counter() + 90
        while time.perf_counter() < deadline:
            snapshot = client.get("/api/snapshot").json()
            status = snapshot["status"]
            count = snapshot["microstructure"]["diagnostics"]["stream_frames_processed"]
            if (count >= expected and source.send_ended is not None) or status["recorder_error"]:
                break
            time.sleep(.01)
        elapsed = time.perf_counter() - source.send_started
        if count != expected:
            raise RuntimeError(f"Expected {expected} processed frames, got {count}, {status['counters']}")
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["microstructure"]["diagnostics"]["stream_frames_processed"] == expected
        session = status["recorder"]["session_id"]
    with closing(sqlite3.connect(path)) as db:
        durable = dict(db.execute("SELECT kind,count(*) FROM records GROUP BY kind"))
    assert dict(source.sent) == {kind: durable[kind] for kind in source.sent}
    replay_start = time.perf_counter()
    reprocessed = replay(path, session, args.mode, 1_000_000)
    replay_elapsed = time.perf_counter() - replay_start
    result = {
        "sent": dict(source.sent), "processed_stream_frames": count, "decoded_total_frames": status["receiver"]["decoded_frames"],
        "durable": durable, "durable_gap_records": durable.get("mbp_gap", 0) + durable.get("gap", 0),
        "recovery_replies": source.recovery_replies, "planned_connections": connections,
        "replay_records": reprocessed["records_processed"],
        "replay_records_per_second": round(reprocessed["records_processed"] / replay_elapsed),
        "wave_transitions_committed": durable.get("wave_transition", 0),
        "wave_transitions_per_second": round(durable.get("wave_transition", 0) / elapsed, 2),
        "receiver": status["receiver"],
        "incoming_frames_per_second": round(expected / (source.send_ended - source.send_started)),
        "durable_market_events_per_second": round(expected / elapsed),
        "api_completion_seconds": round(elapsed, 3), "cpu_seconds": round(time.process_time() - cpu_start, 3),
        "database_bytes": path.stat().st_size, "rejected": durable.get("rejected", 0),
        "recorder": status["recorder"], "diagnostics": snapshot["microstructure"]["diagnostics"],
    }
    try:
        import resource
        result["peak_process_rss"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        result["rss_unit"] = "KiB on Linux; bytes on macOS"
    except ImportError:
        pass
    assert reprocessed["records_processed"] == sum(durable.values())
    return result


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--mode", choices=("A", "B", "C"), default="B")
    cli.add_argument("--cycles", type=int, default=2000)
    cli.add_argument("--levels", type=int, default=250)
    cli.add_argument("--batch-size", type=int, default=32)
    cli.add_argument("--pace", type=float, default=0.0)
    cli.add_argument("--burst", type=int, default=64)
    cli.add_argument("--reconnect-every", type=int, default=0)
    cli.add_argument("--gap-every", type=int, default=0)
    args = cli.parse_args()
    if not 1 <= args.cycles <= 100_000 or not 1 <= args.levels <= 2000 or not 1 <= args.burst <= 4096:
        cli.error("Invalid workload bounds")
    if args.reconnect_every < 0 or args.gap_every < 0 or args.pace < 0:
        cli.error("Workload timing options must be nonnegative")
    with TemporaryDirectory() as directory:
        print(json.dumps(run(args, Path(directory) / "benchmark.sqlite", batch_size=args.batch_size), indent=2))


if __name__ == "__main__":
    main()
