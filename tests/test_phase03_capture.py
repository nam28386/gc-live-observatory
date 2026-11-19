import asyncio
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time

from fastapi.testclient import TestClient
import pytest

from observatory.journal import DAY_NS, Journal, JournalError
from observatory.replay import observation, replay, semantic
from observatory.web import create_app


def test_committed_prefix_semantically_matches_live_after_mbp_recovery_and_mbo(tmp_path):
    path = tmp_path / "parity.sqlite"
    with TestClient(create_app(mode="C", interval=.015, capture_path=path)) as client:
        deadline = time.monotonic() + 12
        while True:
            live = client.get("/api/snapshot").json()
            micro = live["microstructure"]
            if (micro["mbp"]["counts"].get("recoveries") and
                    (micro["mbo"]["counts"] or {}).get("inferred_fill") and micro["evidence"]):
                break
            if time.monotonic() >= deadline:
                pytest.fail("Committed market state did not reach expected parity point")
            time.sleep(.025)
        checkpoint = live["capture_checkpoint"]
        played = replay(path, checkpoint["session_id"], checkpoint["mode"], 50_000, strict=True,
                        through_id=checkpoint["durable_position"], as_of=observation(checkpoint["as_of"]))
        assert played["prefix_complete"]
        assert played["after"] == checkpoint["durable_position"]
        assert semantic(played["snapshot"]) == semantic(micro)
        after = replay(path, checkpoint["session_id"], "C", 50_000,
                       through_id=checkpoint["durable_position"], strict=True)
        assert after["sha256"] == played["sha256"]


@pytest.mark.parametrize("phase,committed", [("before", 0), ("after", 5)])
def test_abrupt_exit_before_or_after_batch_commit(tmp_path, phase, committed):
    script = r"""
import asyncio, os, sys, time
from pathlib import Path
from observatory.journal import Journal
async def main():
    journal = Journal(Path(sys.argv[1]), {"source": "fixture"}, batch_size=8)
    async with journal.running():
        if sys.argv[2] == "before":
            insert = journal.insert
            def crash(db, stamp, kind, payload):
                position = insert(db, stamp, kind, payload)
                if payload["i"] == 2:
                    os._exit(9)
                return position
            journal.insert = crash
        async with journal.stage():
            for i in range(5):
                await journal.append("market", time.time_ns(), {"i": i})
        os._exit(9)
asyncio.run(main())
"""
    path = tmp_path / "crash.sqlite"
    result = subprocess.run([sys.executable, "-c", script, str(path), phase], timeout=15)
    assert result.returncode == 9
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT count(*) FROM records WHERE kind='market'").fetchone()[0] == committed
    async def recover():
        journal = Journal(path, {"source": "fixture"}, batch_size=8)
        async with journal.running():
            rows = await journal.io(journal.history, 0, 100)
            assert [row["payload"]["i"] for row in rows if row["kind"] == "market"] == list(range(committed))
            assert rows[-1]["kind"] == "restart_gap"
            assert rows[-1]["payload"]["termination"] == "unclean"
    asyncio.run(recover())


def test_reader_sees_one_committed_prefix_and_backlog_is_bounded(tmp_path):
    async def scenario():
        entered, released = threading.Event(), threading.Event()
        journal = Journal(tmp_path / "concurrent.sqlite", {}, batch_size=4)
        async with journal.running():
            insert = journal.insert
            def hold(db, stamp, kind, payload):
                value = insert(db, stamp, kind, payload)
                if payload["i"] == 1:
                    entered.set()
                    if not released.wait(5):
                        raise TimeoutError("Reader failed to release writer")
                return value
            journal.insert = hold

            async def batch():
                async with journal.stage():
                    for i in range(4):
                        await journal.append("market", time.time_ns(), {"i": i})
            task = asyncio.create_task(batch())
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                assert journal.diagnostics(time.monotonic_ns())["backlog_records"] == 4
                assert await journal.io(journal.history) == []
            finally:
                released.set()
                await task
            assert [r["payload"]["i"] for r in await journal.io(journal.history)] == [0, 1, 2, 3]
            assert journal.transactions == 1
            with pytest.raises(JournalError, match="bound"):
                async with journal.stage():
                    for i in range(33):
                        await journal.append("market", time.time_ns(), {"i": i})
            assert journal.publication_failed
            assert len(await journal.io(journal.history)) == 4
    asyncio.run(scenario())


def test_retention_refuses_attested_prefix(tmp_path):
    from observatory.replay import capture_prefix
    path = tmp_path / "retention.sqlite"
    journal = Journal(path, {}, 1)
    now = time.time_ns()
    journal.start(now - 2 * DAY_NS)
    journal.write("fixture", now - 2 * DAY_NS, {}, None)
    journal.write("fixture", now, {}, None)
    with pytest.raises(ValueError, match="pruned"):
        with capture_prefix(path, journal.session, strict=True):
            pass


def test_source_session_status_is_verified_and_calendar_inactivity_is_separate(tmp_path):
    from observatory.events import MOCK_GC, Observation
    from observatory.microstructure import Microstructure
    from observatory.quality import SessionCalendar, summarize, utc_ns
    from observatory.state import ObservatoryState

    path = tmp_path / "calendar.json"
    path.write_text(json.dumps({
        "schema_version": 1, "version": "synthetic-fixture-1", "provenance": "test fixture",
        "exchange": "COMEX", "product": "GC", "exchange_timezone": "America/New_York",
        "coverage_start": "2026-11-20T00:00:00Z", "coverage_end": "2026-11-21T00:00:00Z",
        "open_sessions": [{"id": "test-open", "open": "2026-11-20T06:00:00Z", "close": "2026-11-20T18:00:00Z"}],
    }))
    calendar = SessionCalendar.load(path)
    assert calendar.at(utc_ns("2026-11-20T18:00:00Z")) == ("CLOSED", None)
    assert calendar.at(utc_ns("2026-11-21T00:00:00Z")) == ("UNKNOWN", None)
    state = ObservatoryState(MOCK_GC, live=True)
    micro = Microstructure("A", MOCK_GC, state.source)
    start = Observation(utc_ns("2026-11-20T06:00:00Z"), 0)
    state.on_connect(1)
    state.on_ack(start)
    for when, expected in [("2026-11-20T07:00:00Z", "OPEN_WITHOUT_OBSERVATIONS"),
                           ("2026-11-20T19:00:00Z", "MARKET_CLOSED"),
                           ("2026-11-22T19:00:00Z", "SILENT_SESSION_UNKNOWN")]:
        current = Observation(utc_ns(when), 10 * 10**9)
        data = {**state.status(current), "requested_market_data_mode": "A"}
        assert summarize(state, data, micro, current, start, calendar, {})["market_observation_state"] == expected


def fixture_card(event_id, utc, *, late=False, invalid=False):
    second = 10**9
    return {
        "id": event_id, "phase": "outcome", "category": "aggressive_flow_burst",
        "status": "INELIGIBLE" if invalid else "ELIGIBLE_AT_CUTOFF",
        "start": {"utc_ns": str(utc), "monotonic_ns": str(second)},
        "exchange": "COMEX", "instrument": "GCZ6", "expiry": None, "source": "fxaurum_mock",
        "pre_mid_ticks": 27500, "pre_spread_ticks": 2, "pre_depth": [[[27499, 8, 1]], [[27501, 8, 1]]],
        "signed_contracts_1s": 12, "utc_hour": 13,
        "prediction": {
            "cutoff_receive_ns": str(3 * second),
            "available_receive_ns": str(3 * second + 1),
            "impact_sample_receive_ns": str((4 if late else 3) * second),
            "last_refill_observation_receive_ns": str(2 * second),
            "impact_ticks": 1, "refill_contracts": 3, "opposite_refill_contracts": 2,
        },
        "label": {"sample_receive_ns": str(31 * second), "available_receive_ns": str(31 * second + 1),
                  "impact_ticks": 2},
    }


def test_hgc001_dataset_excludes_future_invalid_and_overlap(tmp_path):
    from observatory.dataset import build_dataset
    path = tmp_path / "dataset.sqlite"
    now = time.time_ns()

    async def record(cards):
        journal = Journal(path, {"source": "fxaurum_mock"}, batch_size=8)
        async with journal.running():
            async with journal.stage():
                for card in cards:
                    await journal.append("evidence", now, card)
        return journal.session

    first = asyncio.run(record([
        fixture_card("explore", now), fixture_card("overlaps", now + 100 * 10**9),
        fixture_card("leak", now, late=True), fixture_card("gap", now, invalid=True),
    ]))
    last = asyncio.run(record([fixture_card("holdout", now + 120 * 10**9)]))
    rows, manifest = build_dataset(path, [first, last])
    assert [(row["event_id"], row["split"]) for row in rows] == [("explore", "exploration"), ("holdout", "holdout")]
    assert manifest["scientific_status"] == "UNRESOLVED"
    assert len(manifest["capture_prefixes"]) == 2
    assert manifest["exclusions"] == {"clock_or_measurement_contract": 1, "outcome_missing_or_invalid": 1,
                                      "overlap_or_30s_embargo": 1}
    assert rows[0]["baseline"]["impact_at_cutoff_ticks"] == 1
    assert rows[0]["negative_control_refill_contracts"] == 2


def test_sanitized_diagnostic_has_no_quotes_events_or_connector_secrets():
    from observatory.diagnose import diagnostic
    with TestClient(create_app(interval=.005)) as client:
        deadline = time.monotonic() + 4
        while True:
            status = client.get("/api/status").json()
            if status["counters"].get("eligible_trade"):
                break
            assert time.monotonic() < deadline
            time.sleep(.015)
    status["raw"] = "private sample"
    status["account_token"] = "secret sample"
    result = diagnostic(status)
    assert result["gates"]["subscribe_ack"] == "PASS"
    assert result["gates"]["actual_gateway_and_entitlement"] == "UNVERIFIED"
    data = json.dumps(result)
    assert all(value not in data for value in ("private sample", "secret sample", "price_ticks"))


def test_failed_transaction_does_not_publish_tentative_market_state(tmp_path, monkeypatch):
    path = tmp_path / "failure.sqlite"
    write = Journal.write_records

    def fail_on_trade(journal, records):
        if any(record[0] == "trade" for record in records):
            raise sqlite3.OperationalError("synthetic write failure")
        return write(journal, records)

    monkeypatch.setattr(Journal, "write_records", fail_on_trade)
    with TestClient(create_app(mode="A", capture_path=path, interval=.01)) as client:
        deadline = time.monotonic() + 5
        while True:
            snapshot = client.get("/api/snapshot").json()
            if snapshot["status"]["recorder_error"]:
                break
            assert time.monotonic() < deadline
            time.sleep(.015)
        assert snapshot["trade"] is None
        assert "capture_checkpoint" not in snapshot
        assert not snapshot["status"]["transport_connected"]
        assert not any(row["kind"] == "trade" for row in client.get("/api/history").json()["records"])


def test_legacy_capture_can_be_inspected_but_is_not_attested(tmp_path):
    from observatory.replay import capture_prefix

    path = tmp_path / "legacy.sqlite"
    journal = Journal(path, {"source": "fixture"})
    now = time.time_ns()
    journal.start(now)
    journal.write("fixture", now, {"legacy": True}, None)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DROP TABLE capture_boundaries")
    with capture_prefix(path, journal.session) as (_, _, complete, rows):
        assert not complete
        assert list(rows)[0]["payload"] == {"legacy": True}
    with pytest.raises(ValueError, match="unattested"):
        with capture_prefix(path, journal.session, strict=True):
            pass
