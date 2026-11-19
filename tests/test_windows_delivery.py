import asyncio
from contextlib import closing
import errno
import sqlite3
from threading import Event
import time

import pytest

from observatory.events import MOCK_GC
from observatory.journal import Journal, JournalError
from observatory.mock_gateway import MockGateway
from observatory.web import create_app


@pytest.mark.asyncio
async def test_orderly_shutdown_drains_already_admitted_frames(tmp_path, monkeypatch):
    entered, released = Event(), Event()
    insert = Journal.insert

    def hold_first_trade(journal, db, stamp, kind, payload):
        position = insert(journal, db, stamp, kind, payload)
        if kind == "trade" and not entered.is_set():
            entered.set()
            if not released.wait(10):
                raise TimeoutError("Test did not release the transaction")
        return position

    monkeypatch.setattr(Journal, "insert", hold_first_trade)
    db = tmp_path / "drain.sqlite"
    app = create_app(capture_path=db, mock_gateway=MockGateway(MOCK_GC, interval=.001, tape_cycles=20))
    lifespan = app.router.lifespan_context(app)
    await lifespan.__aenter__()
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        async with asyncio.timeout(5):
            while app.state.collector.admitted_frames < 30:
                await asyncio.sleep(.01)
        before_stop = app.state.collector.admitted_frames
        shutdown = asyncio.create_task(lifespan.__aexit__(None, None, None))
        await asyncio.wait_for(app.state.collector.stopping.wait(), timeout=3)
    finally:
        released.set()
        if not app.state.collector.stopping.is_set():
            await lifespan.__aexit__(None, None, None)
    await shutdown
    agent = app.state.collector
    assert agent.admitted_frames >= before_stop
    assert agent.admitted_frames == agent.accounted_queue_frames
    assert agent.uncertain_receipts == 0
    with closing(sqlite3.connect(db)) as connection:
        records = connection.execute("SELECT kind, count(*) FROM records GROUP BY kind").fetchall()
        assert dict(records)["capture_summary"] == 1
        assert dict(records)["trade"] + dict(records)["bbo"] == agent.journal.market_records


@pytest.mark.asyncio
@pytest.mark.parametrize("error,expected", [
    (OSError(errno.ENOSPC, "fixture"), "STORAGE_FULL"),
    (PermissionError(errno.EACCES, "fixture"), "STORAGE_PERMISSION_DENIED"),
])
async def test_storage_failures_rollback_and_are_classified(tmp_path, monkeypatch, error, expected):
    journal = Journal(tmp_path / "failure.sqlite", {})
    async with journal.running():
        await journal.append("fixture", time.time_ns(), {"i": 1})
        first = journal.last_record_id
        insert = journal.insert

        def unavailable(db, stamp, kind, payload):
            insert(db, stamp, kind, payload)
            raise error

        monkeypatch.setattr(journal, "insert", unavailable)
        with pytest.raises(JournalError, match="transaction failed"):
            await journal.append("fixture", time.time_ns(), {"i": 2})
        assert journal.publication_failed and journal.last_record_id == first
        assert journal.diagnostics(time.monotonic_ns())["storage_status"] == expected
        records = await journal.io(journal.history)
        assert [row["payload"]["i"] for row in records] == [1]
