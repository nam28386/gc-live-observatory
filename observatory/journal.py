"""Local append-only capture records with SQLite transaction recovery."""

import asyncio
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, closing
import json
from pathlib import Path
import sqlite3
import sys
import time
from uuid import uuid4

DAY_NS = 86_400 * 10**9
SCHEMA = 1
APPLICATION_ID = 0x47434C4F


class JournalError(RuntimeError):
    pass


class Journal:
    def __init__(self, path: Path, provenance: dict, retention_days: int = 7, *, batch_size: int = 1):
        self.path = path
        self.provenance = provenance
        self.retention_days = retention_days
        self.session = uuid4().hex
        self.written = 0
        self.market_records = 0
        self.failures = 0
        self.next_prune = 0
        self.batch_size = batch_size
        self.visibility = asyncio.Lock()
        self.publication_failed = False
        self.staged = None
        self.high_water = 0
        self.transactions = 0
        self.last_record_id = None
        self.last_commit_ns = None
        self.commit_latency_ns = deque(maxlen=4096)
        self.writer = None
        self.executor = None

    @asynccontextmanager
    async def running(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # The OS releases the writer lease even after a process crash.
        with self.path.with_suffix(self.path.suffix + ".writer-lock").open("a+b") as lease:
            if sys.platform == "win32":
                import msvcrt
                lease.seek(0)
                msvcrt.locking(lease.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            await self.io(self.start, time.time_ns())
            # Retain one idle WAL connection so each event commit doesn't
            # checkpoint solely because its short-lived writer is the last handle.
            with closing(self.db()):
                if self.batch_size > 1:
                    self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="capture-writer")
                try:
                    yield self
                finally:
                    try:
                        if self.executor:
                            if self.writer:
                                await asyncio.get_running_loop().run_in_executor(self.executor, self.writer.close)
                            self.executor.shutdown()
                            self.executor = self.writer = None
                    finally:
                        await self.io(self.finish, time.time_ns())

    def db(self):
        connection = sqlite3.connect(self.path, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    async def io(self, operation, *args, **kwargs):
        try:
            return await asyncio.to_thread(operation, *args, **kwargs)
        except (OSError, sqlite3.Error) as exc:
            self.failures += 1
            raise JournalError("Local capture I/O failed") from exc

    def start(self, now):
        with closing(self.db()) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            app_id = db.execute("PRAGMA application_id").fetchone()[0]
            has_schema = db.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone()
            if (version, app_id) != (SCHEMA, APPLICATION_ID) and (version or app_id or has_schema):
                raise sqlite3.DatabaseError("File is not a supported capture database")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, provenance TEXT NOT NULL, started INTEGER NOT NULL,
                    durable INTEGER NOT NULL, ended INTEGER, termination TEXT
                );
                CREATE TABLE IF NOT EXISTS records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id),
                    utc_ns INTEGER NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS capture_boundaries (
                    session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
                    first_id INTEGER, last_id INTEGER, pruned INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS records_time ON records(utc_ns);
                CREATE INDEX IF NOT EXISTS records_kind ON records(kind, id);
                PRAGMA user_version=1;
                PRAGMA application_id=1195592783;
            """)
            with db:
                previous = db.execute(
                    """SELECT * FROM sessions
                       WHERE json_extract(provenance, '$.source') IS ?
                         AND json_extract(provenance, '$.exchange') IS ?
                         AND json_extract(provenance, '$.symbol') IS ?
                         AND json_extract(provenance, '$.expiry') IS ?
                       ORDER BY rowid DESC LIMIT 1""",
                    tuple(self.provenance.get(key) for key in ("source", "exchange", "symbol", "expiry")),
                ).fetchone()
                db.execute("UPDATE sessions SET ended = durable, termination = 'unclean' WHERE ended IS NULL")
                db.execute(
                    "INSERT INTO sessions VALUES (?, ?, ?, ?, NULL, NULL)",
                    (self.session, json.dumps(self.provenance, sort_keys=True), now, now),
                )
                db.execute("INSERT INTO capture_boundaries (session_id) VALUES (?)", (self.session,))
                if previous:
                    start = previous["ended"] if previous["ended"] is not None else previous["durable"]
                    self.insert(db, now, "restart_gap", {
                        "previous_session": previous["id"], "from_utc_ns": str(start),
                        "detected_utc_ns": str(now), "termination": previous["termination"] or "unclean",
                    })
                self.prune(db, now)

    def prune(self, db, now):
        cutoff = now - self.retention_days * DAY_NS
        db.execute(
            "UPDATE capture_boundaries SET pruned=1 WHERE session_id IN "
            "(SELECT DISTINCT session_id FROM records WHERE utc_ns < ?)", (cutoff,),
        )
        db.execute("DELETE FROM records WHERE utc_ns < ?", (cutoff,))
        db.execute(
            """DELETE FROM sessions WHERE ended < ?
               AND NOT EXISTS (SELECT 1 FROM records WHERE session_id = sessions.id)""",
            (cutoff,),
        )
        self.next_prune = now + 3_600 * 10**9

    def insert(self, db, utc_ns, kind, payload):
        cursor = db.execute("INSERT INTO records (session_id, utc_ns, kind, payload) VALUES (?, ?, ?, ?)",
                   (self.session, utc_ns, kind, json.dumps(payload)))
        position = cursor.lastrowid
        db.execute("UPDATE capture_boundaries SET first_id=coalesce(first_id, ?), last_id=? WHERE session_id=?",
                   (position, position, self.session))
        db.execute("UPDATE sessions SET durable = max(durable, ?) WHERE id = ?", (utc_ns, self.session))
        return position

    def write(self, kind, utc_ns, payload, coverage):
        return self.write_records([(kind, utc_ns, payload, coverage, None)])

    def write_records(self, records):
        if self.executor and self.writer is None:
            self.writer = self.db()
        db = self.writer or self.db()
        try:
            with db:
                for kind, utc_ns, payload, coverage, _ in records:
                    self.insert(db, utc_ns, kind, payload)
                    if coverage is not None:
                        self.insert(db, utc_ns, "coverage_start", coverage)
                newest = max(record[1] for record in records)
                if newest >= self.next_prune:
                    self.prune(db, newest)
                position = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            return position
        finally:
            if self.writer is None:
                db.close()

    async def commit(self, records):
        if not records:
            return
        self.high_water = max(self.high_water, len(records))
        if len(records) == 1:
            operation = self.write
            args = records[0][:4]
        else:
            operation, args = self.write_records, (records,)

        async def run():
            try:
                if self.executor:
                    position = await asyncio.get_running_loop().run_in_executor(self.executor, operation, *args)
                else:
                    position = await asyncio.to_thread(operation, *args)
            except (OSError, sqlite3.Error) as exc:
                self.failures += 1
                self.publication_failed = True
                raise JournalError("Local capture transaction failed") from exc
            committed = time.monotonic_ns()
            self.last_record_id = position
            self.last_commit_ns = committed
            self.written += sum(1 + (record[3] is not None) for record in records)
            self.market_records += sum(record[0] in ("trade", "bbo", "mbp", "mbo") for record in records)
            self.transactions += 1
            self.commit_latency_ns.extend(committed - record[4] for record in records if record[4] is not None)

        task = asyncio.create_task(run())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    @asynccontextmanager
    async def stage(self):
        # In-memory effects of this batch are tentative until the writer
        # confirms its FULL-synchronous commit. Readers use the same lock.
        async with self.visibility:
            self.staged = []
            try:
                yield
                await self.commit(self.staged)
            except BaseException:
                # A partially applied batch may no longer back a valid UI view.
                self.publication_failed = True
                raise
            finally:
                self.staged = None

    async def append(self, kind, utc_ns, payload, *, coverage=None):
        event = payload.get("event")
        observed = event.get("observed") if isinstance(event, dict) else None
        sampled = int(observed["monotonic_ns"]) if observed else None
        row = kind, utc_ns, payload, coverage, sampled
        if self.staged is not None:
            if len(self.staged) >= self.batch_size * 8:
                self.failures += 1
                raise JournalError("Capture batch record bound exceeded")
            self.staged.append(row)
            self.high_water = max(self.high_water, len(self.staged))
        else:
            await self.commit([row])

    def diagnostics(self, now):
        latencies = sorted(self.commit_latency_ns)
        return {
            "batch_size": self.batch_size,
            "backlog_records": len(self.staged or ()),
            "high_water_records": self.high_water,
            "transactions": self.transactions,
            "durable_position": self.last_record_id,
            "commit_age_ms": (now - self.last_commit_ns) / 1e6 if self.last_commit_ns else None,
            "observation_to_commit_us": {
                f"p{p}": latencies[(len(latencies) - 1) * p // 100] / 1000 if latencies else None
                for p in (50, 95, 99)
            },
        }

    def finish(self, now):
        with closing(self.db()) as db, db:
            db.execute("UPDATE sessions SET ended = ?, termination = ? WHERE id = ?",
                       (now, "recorder_error" if self.failures else "clean", self.session))

    def history(self, after=0, limit=100, *, gaps=False, session_id=None, read_only=False,
                through_id=None, reader=None):
        sql = """SELECT r.*, s.provenance FROM records r JOIN sessions s ON r.session_id = s.id
                 WHERE r.id > ?"""
        if gaps:
            sql += " AND r.kind IN ('gap', 'restart_gap', 'coverage_start', 'mbp_gap', 'mbp_recovery', 'mbp_overflow', 'mbo_continuity')"
        arguments = [after]
        if session_id is not None:
            sql += " AND r.session_id = ?"
            arguments.append(session_id)
        if through_id is not None:
            sql += " AND r.id <= ?"
            arguments.append(through_id)
        sql += " ORDER BY r.id DESC LIMIT ?" if gaps else " ORDER BY r.id LIMIT ?"
        arguments.append(limit)
        if reader is None:
            connection = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True) if read_only else self.db()
            with closing(connection) as db:
                db.row_factory = sqlite3.Row
                records = db.execute(sql, arguments).fetchall()
        else:
            records = reader.execute(sql, arguments).fetchall()
        return [{
            "schema_version": SCHEMA, "id": row["id"], "session_id": row["session_id"],
            "utc_ns": str(row["utc_ns"]), "kind": row["kind"], "payload": json.loads(row["payload"]),
            "provenance": json.loads(row["provenance"]),
        } for row in records]
