"""Reconstruct one capture session in durable journal order without a gateway."""

from contextlib import closing
import argparse
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

from .books import parse_depth, parse_order
from .events import Instrument, Observation, Trade, parse_event
from .journal import Journal
from .microstructure import Microstructure


def observation(fields):
    return Observation(int(fields["utc_ns"]), int(fields["monotonic_ns"]))


def replay(path: Path, session: str, mode: str, limit: int):
    journal = Journal(path, {})
    engine = None
    last = None
    last_trade = None
    cursor = processed = 0
    # A read-only SQLite URI avoids opening or modifying an arbitrary capture file.
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        if not db.execute("SELECT 1 FROM sessions WHERE id = ?", (session,)).fetchone():
            raise ValueError("Unknown capture session")
    while processed < limit:
        batch = journal.history(cursor, min(250, limit - processed), session_id=session, read_only=True)
        if not batch:
            break
        for row in batch:
            cursor = row["id"]
            processed += 1
            source = row["provenance"]
            if engine is None:
                instrument = Instrument(source["exchange"], source["symbol"], source.get("expiry"))
                engine = Microstructure(mode, instrument, source["source"],
                                        source.get("stale_after_ns", 3_000_000_000))
                engine.capture_id = session
            payload = row["payload"]
            kind = row["kind"]
            if kind in ("ack", "gap"):
                clocks = payload.get("observed")
                if clocks:
                    last = observation(clocks)
                    if kind == "ack":
                        engine.connect(last)
                    else:
                        engine.disconnect(last)
                continue
            if kind == "mbp_gap":
                if engine.depth and payload["reason"] in ("malformed_mbp", "snapshot_timeout"):
                    last = observation(payload["detected"])
                    engine.depth.invalidate(payload["reason"], last)
                    engine.advance(last)
                continue
            if kind not in ("trade", "bbo", "mbp", "depth_snapshot", "mbo"):
                continue
            last = observation(payload["event"]["observed"])
            if not engine.transport:
                engine.connect(last)
            raw = json.loads(payload["raw"], parse_float=Decimal)
            if kind == "trade" or kind == "bbo":
                event = parse_event(raw, instrument, last, source["source"])
                if isinstance(event, Trade):
                    if not event.conditional and (last_trade is None or event.exchange_time_ms >= last_trade):
                        last_trade = event.exchange_time_ms
                        engine.trade(event)
                else:
                    engine.quote(event)
            elif kind == "mbo" and engine.orders:
                engine.order_event(parse_order(raw["data"], last))
            elif kind in ("mbp", "depth_snapshot") and engine.depth:
                data = raw["result"] if kind == "depth_snapshot" else raw["data"]
                engine.depth_event(parse_depth(data, last), snapshot=kind == "depth_snapshot")
            engine.durable_cards.clear()
    return {"records_processed": processed, "after": cursor, "cap_reached": processed == limit,
            "snapshot": engine.snapshot(last) if engine and last else None}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("database", type=Path)
    cli.add_argument("session")
    cli.add_argument("--mode", choices=("A", "B", "C"), default="C")
    cli.add_argument("--limit", type=int, default=100_000)
    args = cli.parse_args()
    if not 1 <= args.limit <= 1_000_000:
        cli.error("--limit must be between 1 and 1000000")
    try:
        print(json.dumps(replay(args.database, args.session, args.mode, args.limit), indent=2))
    except (OSError, ValueError, sqlite3.Error) as exc:
        cli.exit(1, f"Replay failed: {exc}\n")


if __name__ == "__main__":
    main()
