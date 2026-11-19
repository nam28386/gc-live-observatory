"""Replay a fixed committed prefix in its original observation clock domain."""

import argparse
from contextlib import closing, contextmanager
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from .books import parse_depth, parse_order
from .events import Instrument, Observation, Trade, parse_event
from .journal import APPLICATION_ID, SCHEMA, Journal
from .microstructure import Microstructure
from .state import ObservatoryState


def observation(fields):
    return Observation(int(fields["utc_ns"]), int(fields["monotonic_ns"]))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


@contextmanager
def capture_prefix(path, session, through_id=None, *, strict=False):
    journal = Journal(path, {})
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")  # Pin one WAL reader snapshot across pages and retention.
        if strict and (db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
                       or db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA):
            raise ValueError("Unsupported capture database schema")
        info = db.execute("SELECT provenance FROM sessions WHERE id = ?", (session,)).fetchone()
        if not info:
            raise ValueError("Unknown capture session")
        bound = None
        if db.execute("SELECT 1 FROM sqlite_schema WHERE name='capture_boundaries'").fetchone():
            bound = db.execute("SELECT * FROM capture_boundaries WHERE session_id=?", (session,)).fetchone()
        complete = bool(bound and bound["first_id"] is not None and not bound["pruned"])
        maximum = db.execute("SELECT max(id) FROM records WHERE session_id=?", (session,)).fetchone()[0]
        position = maximum if through_id is None else through_id
        if position is not None and not db.execute(
            "SELECT 1 FROM records WHERE id=? AND session_id=?", (position, session)
        ).fetchone():
            raise ValueError("Position is outside the retained capture session")
        if complete and position is not None:
            recorded = db.execute("SELECT count(*) FROM records WHERE session_id=? AND id<=?",
                                  (session, position)).fetchone()[0]
            complete = recorded == position - bound["first_id"] + 1
        if strict and not complete:
            raise ValueError("Capture prefix is unattested, pruned or incomplete")

        def rows():
            cursor = 0
            while position:
                batch = journal.history(cursor, 250, session_id=session, through_id=position, reader=db)
                if not batch:
                    break
                yield from batch
                cursor = batch[-1]["id"]

        yield json.loads(info["provenance"]), position, complete, rows()


def semantic(snapshot):
    """Exclude runtime latency and entitlement assertions from market-state comparison."""
    fields = {key: value for key, value in snapshot.items() if key != "diagnostics"}
    fields["mbo"] = {key: value for key, value in fields["mbo"].items() if key != "entitlement"}
    return json.loads(canonical(fields))


def replay(path: Path, session: str, mode: str, limit: int, *, through_id=None, as_of=None, strict=False):
    last = None
    last_trade = None
    cursor = processed = 0
    digest = sha256()
    final_summary = None
    with capture_prefix(path, session, through_id, strict=strict) as (source, position, complete, rows):
        instrument = Instrument(source["exchange"], source["symbol"], source.get("expiry"))
        initial_mode = source.get("capture_config", {}).get("mode", mode)
        engine = Microstructure(initial_mode, instrument, source["source"],
                                source.get("stale_after_ns", 3_000_000_000))
        engine.capture_id = session
        state = ObservatoryState(instrument)
        wave_capture = source.get("measurement_schema_version", 0) >= 4
        for row in rows:
            if processed == limit:
                break
            cursor = row["id"]
            processed += 1
            digest.update((canonical(row) + "\n").encode())
            payload, kind = row["payload"], row["kind"]
            if kind == "capture_summary":
                final_summary = payload
            elif kind in ("ack", "gap"):
                clocks = payload.get("observed")
                if clocks:
                    last = observation(clocks)
                    if kind == "ack":
                        engine.connect(last)
                        state.on_connect(payload["request_id"])
                        state.on_ack(last)
                    else:
                        engine.disconnect(last)
                        state.on_disconnect(payload.get("reason", "gap"), last)
                        engine.last_time = last.monotonic_ns
            elif kind == "mbp_gap" and engine.depth and payload["reason"] in ("malformed_mbp", "snapshot_timeout"):
                last = observation(payload["detected"])
                engine.depth.invalidate(payload["reason"], last)
                if payload["reason"] == "malformed_mbp":
                    engine.advance(last)
            elif kind == "mbp_control" and engine.depth:
                engine.depth.state = payload["state"]
                engine.depth.counts.update(payload.get("increment", {}))
            elif kind == "mbo_continuity" and engine.orders:
                engine.orders.status = payload["reason"]
            elif kind == "mode_transition":
                engine.mode = payload["mode"]
                engine.orders = None
                if engine.mode == "A":
                    engine.depth = None
            elif kind in ("trade", "bbo", "mbp", "depth_snapshot", "mbo"):
                last = observation(payload["event"]["observed"])
                if not engine.transport:
                    engine.connect(last)
                raw = json.loads(payload["raw"], parse_float=Decimal)
                if kind in ("trade", "bbo"):
                    event = parse_event(raw, instrument, last, source["source"])
                    if isinstance(event, Trade):
                        if not event.conditional and (last_trade is None or event.exchange_time_ms >= last_trade):
                            last_trade = event.exchange_time_ms
                            state.on_trade(event)
                            engine.trade(event, candles=state.candles if wave_capture else None,
                                         session=payload.get("market_session"))
                    else:
                        engine.quote(event)
                elif kind == "mbo" and engine.orders:
                    engine.order_event(parse_order(raw["data"], last))
                elif kind in ("mbp", "depth_snapshot") and engine.depth:
                    data = raw["result"] if kind == "depth_snapshot" else raw["data"]
                    engine.depth_event(parse_depth(data, last), snapshot=kind == "depth_snapshot")
            engine.durable_cards.clear()
            engine.waves.durable.clear()
    finished = cursor == position
    if strict and not finished:
        raise ValueError("Replay limit truncated the requested capture prefix")
    if as_of is not None:
        if not finished or (last and as_of.monotonic_ns < last.monotonic_ns):
            raise ValueError("Replay clock falls before the resolved capture position")
        last = as_of
    return {"records_processed": processed, "after": cursor, "cap_reached": not finished and processed == limit,
            "prefix_complete": complete and finished, "sha256": digest.hexdigest(), "capture_summary": final_summary,
            "snapshot": engine.snapshot(last) if last else None}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("database", type=Path)
    cli.add_argument("session")
    cli.add_argument("--mode", choices=("A", "B", "C"), default="C")
    cli.add_argument("--limit", type=int, default=100_000)
    cli.add_argument("--through-id", type=int)
    cli.add_argument("--strict", action="store_true")
    args = cli.parse_args()
    if not 1 <= args.limit <= 1_000_000:
        cli.error("--limit must be between 1 and 1000000")
    try:
        print(json.dumps(replay(args.database, args.session, args.mode, args.limit,
                                through_id=args.through_id, strict=args.strict), indent=2))
    except (OSError, ValueError, sqlite3.Error) as exc:
        cli.exit(1, f"Replay failed: {exc}\n")


if __name__ == "__main__":
    main()
