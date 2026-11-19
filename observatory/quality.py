"""Versioned coverage and explicitly sourced exchange-session observations."""

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

from .events import Instrument, Observation

SECOND = 10**9
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def utc_ns(value):
    if not isinstance(value, str):
        raise ValueError("Calendar times must be explicit ISO timestamps")
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() != timedelta(0):
        raise ValueError("Calendar times must be explicit UTC timestamps")
    delta = stamp - EPOCH
    return (delta.days * 86400 + delta.seconds) * SECOND + delta.microseconds * 1000


@dataclass(frozen=True)
class SessionCalendar:
    version: str
    provenance: str
    digest: str
    start_ns: int
    end_ns: int
    opens: tuple[tuple[int, int, str], ...]
    starts: tuple[int, ...]

    @classmethod
    def load(cls, path: Path):
        if path.stat().st_size > 2_000_000:
            raise ValueError("Calendar file exceeds input bound")
        raw = path.read_bytes()
        doc = json.loads(raw)
        if not isinstance(doc, dict) or doc.get("schema_version") != 1 or (
            doc.get("exchange"), doc.get("product"), doc.get("exchange_timezone")
        ) != ("COMEX", "GC", "America/New_York"):
            raise ValueError("Unsupported COMEX GC calendar")
        version, provenance = doc.get("version"), doc.get("provenance")
        if not all(isinstance(x, str) and x.strip() for x in (version, provenance)):
            raise ValueError("Calendar version and provenance are required")
        start, end = utc_ns(doc.get("coverage_start")), utc_ns(doc.get("coverage_end"))
        if start >= end or not isinstance(doc.get("open_sessions"), list):
            raise ValueError("Invalid calendar coverage")
        opens = []
        previous = start
        for row in doc["open_sessions"]:
            if not isinstance(row, dict):
                raise ValueError("Invalid calendar session")
            begin, finish, label = utc_ns(row.get("open")), utc_ns(row.get("close")), row.get("id")
            if not previous <= begin < finish <= end or not isinstance(label, str) or not label:
                raise ValueError("Sessions must be ordered, disjoint and inside calendar coverage")
            opens.append((begin, finish, label))
            previous = finish
        if len({label for _, _, label in opens}) != len(opens):
            raise ValueError("Duplicated calendar session ID")
        return cls(version, provenance, sha256(raw).hexdigest(), start, end, tuple(opens),
                   tuple(begin for begin, _, _ in opens))

    def at(self, now_ns):
        if not self.start_ns <= now_ns < self.end_ns:
            return "UNKNOWN", None
        i = bisect_right(self.starts, now_ns) - 1
        if i >= 0 and now_ns < self.opens[i][1]:
            return "OPEN", self.opens[i][2]
        return "CLOSED", None

    def metadata(self):
        return {"version": self.version, "provenance": self.provenance, "sha256": self.digest,
                "coverage_start_utc_ns": str(self.start_ns), "coverage_end_utc_ns": str(self.end_ns)}


def registry(instrument: Instrument, calendar: SessionCalendar | None):
    return {
        "exchange": instrument.exchange, "product": "GC", "contract": instrument.symbol,
        "expiry": instrument.expiry, "expiry_basis": "operator_configured" if instrument.expiry else "unknown",
        "rollover": "manual_explicit_contract", "exchange_timezone": "America/New_York",
        "display_timezone": "UTC", "calendar": calendar.metadata() if calendar else None,
    }


def summarize(state, data, micro, now: Observation, started: Observation, calendar, recorder):
    market = calendar.at(now.utc_ns) if calendar and state.live else ("UNKNOWN", None)
    if data["recorder_error"]:
        reason = "RECORDER_FAILED"
    elif not data["transport_connected"]:
        reason = "TRANSPORT_DISCONNECTED"
    elif not data["subscribe_ack"]:
        reason = "WAITING_ACK"
    elif market[0] == "CLOSED":
        reason = "MARKET_CLOSED"
    elif data["market_freshness"] == "STALE":
        reason = "FEED_STALE"
    elif data["market_freshness"] == "SILENT":
        reason = "OPEN_WITHOUT_OBSERVATIONS" if market[0] == "OPEN" else "SILENT_SESSION_UNKNOWN"
    else:
        reason = data["market_freshness"]
    missing = []
    if data["trade_freshness"] != "OBSERVED":
        missing.append("trade")
    if data["bbo_freshness"] != "OBSERVED":
        missing.append("bbo")
    if micro.depth and not micro.depth_eligible(now.monotonic_ns):
        missing.append("mbp")
    if data["requested_market_data_mode"] == "C" and (
        not micro.orders or micro.orders.status != "OBSERVED_PARTIAL_BOOK" or not micro.orders.last
        or now.monotonic_ns - micro.orders.last.monotonic_ns > micro.freshness_ns
    ):
        missing.append("mbo")
    ages = {}
    for name, observed in {
        "trade": state.trade_in_session, "bbo": state.last_ticker.observed if state.last_ticker else None,
        "mbp": micro.depth.available if micro.depth else None, "mbo": micro.orders.last if micro.orders else None,
    }.items():
        ages[name] = (now.monotonic_ns - observed.monotonic_ns) / 1e6 if observed else None

    def duration(begin, end):
        return max(0, (end or now).monotonic_ns - begin.monotonic_ns) / SECOND

    skew = None
    if state.live and state.last_trade:
        trade = state.last_trade
        skew = trade.observed.utc_ns // 1_000_000 - trade.exchange_time_ms
    return {
        "schema_version": 1, "session_state": market[0], "session_id": market[1],
        "market_observation_state": reason,
        "capture_started": started.wire(), "elapsed_seconds": duration(started, now),
        "observed_coverage_seconds_retained": sum(duration(c.started, c.ended) for c in state.coverage),
        "gap_seconds_retained": sum(duration(g.detected, g.resumed) for g in state.gaps),
        "transport_gap_count": state.counters["coverage_gaps"],
        "mbp_recovery_count": micro.depth.counts["recoveries"] if micro.depth else 0,
        "invalid_feature_windows": micro.invalid_windows, "missing_sources": missing,
        "source_age_ms": ages, "source_utc_difference_ms": skew,
        "clock_warning": skew is not None and abs(skew) > 5_000,
        "recorder_commit_age_ms": recorder.get("commit_age_ms"),
        "local_pressure_observed": recorder.get("backlog_records", 0) > 0,
        "last_valid_mbp": micro.depth.available.wire() if micro.depth and micro.depth.available else None,
        "last_mbp_snapshot": micro.depth.last_snapshot.wire() if micro.depth and micro.depth.last_snapshot else None,
        "last_valid_dbo": micro.orders.last.wire() if micro.orders and micro.orders.last else None,
        "research_eligibility": "PARTIAL_OR_UNKNOWN" if missing or reason != "OBSERVED" else "OBSERVED_INPUTS",
        "coverage_basis": "local observations only; retained interval totals",
    }
