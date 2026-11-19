"""Local append-only capture records with SQLite transaction recovery."""

import asyncio
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
    def __init__(self, path: Path, provenance: dict, retention_days: int = 7):
        self.path = path
        self.provenance = provenance
        self.retention_days = retention_days
        self.session = uuid4().hex
        self.written = 0
        self.failures = 0
        self.next_prune = 0

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
                try:
                    yield self
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
                if previous:
                    start = previous["ended"] if previous["ended"] is not None else previous["durable"]
                    self.insert(db, now, "restart_gap", {
                        "previous_session": previous["id"], "from_utc_ns": str(start),
                        "detected_utc_ns": str(now), "termination": previous["termination"] or "unclean",
                    })
                self.prune(db, now)

    def prune(self, db, now):
        cutoff = now - self.retention_days * DAY_NS
        db.execute("DELETE FROM records WHERE utc_ns < ?", (cutoff,))
        db.execute(
            """DELETE FROM sessions WHERE ended < ?
               AND NOT EXISTS (SELECT 1 FROM records WHERE session_id = sessions.id)""",
            (cutoff,),
        )
        self.next_prune = now + 3_600 * 10**9

    def insert(self, db, utc_ns, kind, payload):
        db.execute("INSERT INTO records (session_id, utc_ns, kind, payload) VALUES (?, ?, ?, ?)",
                   (self.session, utc_ns, kind, json.dumps(payload)))
        db.execute("UPDATE sessions SET durable = max(durable, ?) WHERE id = ?", (utc_ns, self.session))

    def write(self, kind, utc_ns, payload, coverage):
        with closing(self.db()) as db, db:
            self.insert(db, utc_ns, kind, payload)
            if coverage is not None:
                self.insert(db, utc_ns, "coverage_start", coverage)
            if utc_ns >= self.next_prune:
                self.prune(db, utc_ns)

    async def append(self, kind, utc_ns, payload, *, coverage=None):
        # The collector waits for each durable transaction before consuming another
        # frame. Cancellation waits for the in-flight commit before finalization.
        task = asyncio.create_task(self.io(self.write, kind, utc_ns, payload, coverage))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await task
            except JournalError:
                pass
            raise
        self.written += 1 + (coverage is not None)

    def finish(self, now):
        with closing(self.db()) as db, db:
            db.execute("UPDATE sessions SET ended = ?, termination = ? WHERE id = ?",
                       (now, "recorder_error" if self.failures else "clean", self.session))

    def history(self, after=0, limit=100, *, gaps=False, session_id=None, read_only=False):
        sql = """SELECT r.*, s.provenance FROM records r JOIN sessions s ON r.session_id = s.id
                 WHERE r.id > ?"""
        if gaps:
            sql += " AND r.kind IN ('gap', 'restart_gap', 'coverage_start', 'mbp_gap', 'mbp_recovery', 'mbp_overflow', 'mbo_continuity')"
        arguments = [after]
        if session_id is not None:
            sql += " AND r.session_id = ?"
            arguments.append(session_id)
        sql += " ORDER BY r.id DESC LIMIT ?" if gaps else " ORDER BY r.id LIMIT ?"
        arguments.append(limit)
        connection = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True) if read_only else self.db()
        with closing(connection) as db:
            db.row_factory = sqlite3.Row
            records = db.execute(sql, arguments).fetchall()
        return [{
            "schema_version": SCHEMA, "id": row["id"], "session_id": row["session_id"],
            "utc_ns": str(row["utc_ns"]), "kind": row["kind"], "payload": json.loads(row["payload"]),
            "provenance": json.loads(row["provenance"]),
        } for row in records]
