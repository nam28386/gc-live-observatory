"""Synthetic decoder/book/measurement rate and SQLite durable write benchmark."""

import argparse
import asyncio
from decimal import Decimal
import json
from pathlib import Path
import tempfile
from time import perf_counter_ns, process_time_ns, time_ns
import tracemalloc

from observatory.books import DepthBook, RECOVERY_BUFFER, parse_depth
from observatory.events import MOCK_GC, Observation
from observatory.journal import Journal
from observatory.microstructure import Microstructure


def book_pass(count):
    engine = Microstructure("B", MOCK_GC, "fxaurum_mock")
    timestamp = Observation(1_800_000_000_000_000_000, 0)
    engine.connect(timestamp)
    for payload in [
        {"type": "Reset", "gen": 1},
        {"type": "Image", "gen": 1, "seq": 0, "rows": [
            [side, Decimal(27500 + (level if side else -level)) / 10, 15, 2]
            for side in (0, 1) for level in range(1, 251)
        ]},
    ]:
        engine.depth_event(parse_depth(payload, timestamp))
    started = perf_counter_ns()
    cpu = process_time_ns()
    durations = []
    for index in range(1, count + 1):
        t = Observation(timestamp.utc_ns + index * 100_000, index * 100_000)
        frame = (
            '{"type":"Update","gen":1,"seq":%d,"side":"S","p":2750.1,"q":%d,"n":2}'
            % (index, 14 + index % 2)
        )
        begin = perf_counter_ns()
        engine.depth_event(parse_depth(json.loads(frame, parse_float=Decimal), t))
        durations.append(perf_counter_ns() - begin)
    duration = perf_counter_ns() - started
    cpu_duration = process_time_ns() - cpu
    durations.sort()
    return {"events": count, "events_per_second": round(count * 1e9 / duration),
            "p95_process_us": round(durations[int((count - 1) * .95)] / 1000, 2),
            "cpu_seconds": round(cpu_duration / 1e9, 3),
            "book_levels": sum(map(len, engine.depth.levels.values())),
            "retained_ofi_events": len(engine.ofi), "recovery_buffer_high_water": engine.depth.counts["buffer_high_water"],
            "rejected": engine.depth.counts["discarded_while_invalid"], "state": engine.depth.state}


def recovery_pressure():
    book = DepthBook()
    start = time_ns()
    observation = Observation(start, 0)
    book.apply(parse_depth({"type": "Reset", "gen": 1}, observation))
    book.apply(parse_depth({"type": "Image", "gen": 1, "seq": 0, "rows": []}, observation))
    first_missing = 2
    cap_seq = first_missing + RECOVERY_BUFFER
    for seq in range(first_missing, cap_seq + 1):
        mark = seq * 100_000
        book.apply(parse_depth({
            "type": "Update", "seq": seq, "gen": 1, "side": "B",
            "p": 2750, "q": 8, "n": 1,
        }, Observation(start + mark, mark)))
    assert not book.valid and not book.pending
    marked = (cap_seq + 1) * 100_000
    began = perf_counter_ns()
    book.apply(parse_depth({
        "type": "Image", "gen": 1, "seq": cap_seq, "src": 1,
        "rows": [[0, 2750, 8, 1], [1, Decimal("2750.1"), 8, 1]],
    }, Observation(start + marked, marked)), snapshot=True)
    assert book.valid and book.gaps[0]["resumed"]
    return {"buffer_high_water": book.counts["buffer_high_water"],
            "overflow_count": book.counts["buffer_overflow"],
            "discarded_buffered_transitions": RECOVERY_BUFFER + 1,
            "modeled_gap_to_recovery_ms": book.recovery_ns / 1e6,
            "snapshot_apply_us": round((perf_counter_ns() - began) / 1000, 2)}


async def durable_writes(count):
    with tempfile.TemporaryDirectory() as directory:
        journal = Journal(Path(directory) / "synthetic.sqlite", {"source": "benchmark"})
        async with journal.running():
            started = perf_counter_ns()
            for index in range(count):
                await journal.append("mbp", time_ns(), {
                    "event_schema_version": 2, "event": {"seq": index, "gen": 1},
                    "raw": '{"type":"Update","gen":1,"side":"S","p":2750.1,"q":15,"n":2}',
                })
            duration = perf_counter_ns() - started
            assert journal.written == count
            return {"events": count, "durable_writes_per_second": round(count * 1e9 / duration),
                    "mean_write_ms": round(duration / count / 1e6, 3)}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--events", type=int, default=10_000)
    cli.add_argument("--writes", type=int, default=1000)
    args = cli.parse_args()
    if not 1 <= args.events <= 100_000 or not 1 <= args.writes <= 10_000:
        cli.error("Use 1–100000 engine events and 1–10000 durable writes")
    rate = book_pass(args.events)
    tracemalloc.start()
    book_pass(args.events)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(json.dumps({"engine": rate, "python_peak_bytes_with_instrumentation": peak,
                      "recovery_pressure": recovery_pressure(), "sqlite": asyncio.run(durable_writes(args.writes))}, indent=2))


if __name__ == "__main__":
    main()
