"""Build and verify a sanitized, fixed-prefix GC field evidence packet."""
import argparse
from collections import Counter
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sqlite3

from .dataset import eligible
from .replay import canonical, capture_prefix, observation, replay, semantic

SCHEMA = "gc-observatory-pilot-evidence"
VERSION = 1
LIMIT = 1_000_000
EVENT_KINDS = {"trade", "bbo", "mbp", "mbo", "depth_snapshot", "ack", "depth_rpc",
               "mbp_control", "evidence", "wave_transition", "capture_summary", "rejected"}
GAP_KINDS = {"gap", "restart_gap", "mbp_gap", "mbp_overflow", "mbo_continuity", "mbp_recovery"}
DIGEST = re.compile(r"[0-9a-f]{64}")
SHA = re.compile(r"[0-9a-f]{40}")
SESSION = re.compile(r"[0-9a-f]{32}")
CONTRACT = re.compile(r"GC[FGHJKMNQUVXZ]\d{1,2}")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
GATE_NAMES = {"gateway_tcp", "subscribe_ack", "trade", "bbo", "mbp", "dbo_observation",
              "actual_gateway_and_entitlement", "capture_io", "requested_mode_ready"}
GATE_VALUES = {"PASS", "FAIL", "PARTIAL", "UNVERIFIED", "NOT_APPLICABLE"}
DEPTH_ASSERTIONS = {"UNAVAILABLE", "UNVERIFIED", "OPERATOR_REPORTED", "SYNTHETIC", "DISABLED",
                    "SUBSCRIPTION_REJECTED"}
RPC_OUTCOMES = {
    "SNAPSHOT_APPLIED", "RPC_RECEIPT_TIMEOUT", "RPC_DISPATCH_DELAY", "RPC_RESPONSE_INVALID",
    "RPC_GENERATION_MISMATCH", "RPC_SEQUENCE_INSUFFICIENT", "RPC_TRANSPORT_INTERRUPTED",
    "RPC_LOCAL_BACKPRESSURE", "RPC_COMMIT_FAILURE", "RPC_RECOVERY_FAILED",
    "RPC_PUBLICATION_DELAY", "RPC_SUPERSEDED_BY_STREAM_IMAGE",
}


def digest(obj):
    return sha256(canonical(obj).encode()).hexdigest()


def numeric_fields(obj, keys):
    """Discard strings and arbitrary fields even when input has a known outer shape."""
    return {key: value for key in keys
            if type(value := obj.get(key)) in (int, float) and math.isfinite(value)}


def operator_summary(report, metadata, session, position, event_counts, snapshot):
    if report is None:
        return None
    if (report.get("schema_version") != 1 or report["git_sha"] != metadata["software_sha"] or report["contract"] != metadata["contract"]
            or report["recorder"]["session_id"] != session
            or report["recorder"]["durable_position"] != position):
        raise ValueError("Operator diagnostic does not match capture identity, contract and chosen prefix")
    expected = "LIVE" if metadata["source"] == "fxaurum_rithmic" else "MOCK"
    gates = report["gates"]
    if (report["source"] != expected or set(gates) - GATE_NAMES
            or not set(gates.values()) <= GATE_VALUES
            or gates.get("actual_gateway_and_entitlement") != "UNVERIFIED"
            or (expected == "MOCK" and report.get("gateway_tcp_reachable") is not None)):
        raise ValueError("Unsupported operator assertions")
    if ((gates.get("gateway_tcp") == "PASS" and report.get("gateway_tcp_reachable") is not True)
            or (gates.get("subscribe_ack") == "PASS" and report.get("matching_ack_observed") is not True)
            or (gates.get("trade") == "PASS" and not positive(report.get("eligible_trade_count")))
            or (gates.get("bbo") == "PASS" and not positive(report.get("eligible_bbo_count")))
            or (gates.get("capture_io") == "PASS" and not positive(report["recorder"].get("durable_events")))):
        raise ValueError("A diagnostic gate is unsupported by its observable fields")
    depth = report.get("depth", {}).get("dbo")
    expiry = report.get("expiry_verification")
    if (depth not in DEPTH_ASSERTIONS or expiry not in ("UNVERIFIED", "OPERATOR_ATTESTED")
            or (expected == "MOCK" and (expiry != "UNVERIFIED" or depth == "OPERATOR_REPORTED"))):
        raise ValueError("Unsupported expiry or entitlement assertion")
    if (report["effective_mode"] != snapshot["mode"]
            or (gates.get("subscribe_ack") == "PASS" and not event_counts["ack"])
            or (gates.get("trade") == "PASS" and report["eligible_trade_count"] > event_counts["trade"])
            or (gates.get("bbo") == "PASS" and report["eligible_bbo_count"] > event_counts["bbo"])
            or (gates.get("mbp") == "PASS" and not (
                report.get("depth", {}).get("mbp") == snapshot["mbp"]["status"] == "VALID_BOOK"
                and event_counts["mbp"]
            ))
            or (gates.get("dbo_observation") == "PASS" and not (
                depth in ("OPERATOR_REPORTED", "SYNTHETIC") and event_counts["mbo"]
            ))):
        raise ValueError("Depth assertions lack supporting diagnostics")
    return {
        "basis": "OPERATOR_DIAGNOSTIC_AT_CAPTURE_POSITION",
        "gates": gates, "dbo": depth, "expiry": expiry, "sanitized_diagnostic_sha256": digest(report),
        "receiver": numeric_fields(report.get("receiver", {}), (
            "receive_queue_high_water", "receive_pressure_count", "uncertain_received_frames",
            "durable_market_records", "depthsnap_send_to_receipt_ms", "depthsnap_receipt_to_publication_ms")),
        "commit_latency_us": numeric_fields(report.get("commit_latency_us") or {}, ("p50", "p95", "p99")),
        "publication_latency_us": numeric_fields(report.get("publication_latency_us") or {}, ("p50", "p95", "p99")),
    }


def positive(value):
    return type(value) is int and value > 0


def build_packet(database, session, *, through_id=None, diagnostic=None, checkpoint=None):
    if not SESSION.fullmatch(session):
        raise ValueError("Invalid capture session ID")
    if through_id is not None and not positive(through_id):
        raise ValueError("Invalid durable position")
    if checkpoint is not None:
        anchor = checkpoint["capture_checkpoint"]
        if anchor["session_id"] != session:
            raise ValueError("Checkpoint belongs to a different session")
        if through_id is not None and through_id != anchor["durable_position"]:
            raise ValueError("Checkpoint describes a different durable position")
        through_id = anchor["durable_position"]
        if not positive(through_id):
            raise ValueError("Checkpoint has no durable position")
    if diagnostic is not None and through_id is None:
        through_id = diagnostic["recorder"]["durable_position"]
        if not positive(through_id):
            raise ValueError("Diagnostic has no durable position")

    event_counts = Counter()
    gaps = Counter()
    rpc = Counter()
    latest = {}
    capture_summary = None
    prefix_digest = sha256()
    first = last = None
    count = 0
    with capture_prefix(database, session, through_id, strict=True) as (source, position, _, records):
        sha = source.get("software_revision")
        symbol, expiry = source.get("symbol", ""), source.get("expiry")
        protocol = source.get("protocol_revision", "")
        if (not isinstance(sha, str) or not SHA.fullmatch(sha) or not SHA.fullmatch(protocol)
                or not CONTRACT.fullmatch(symbol) or source.get("exchange") != "COMEX"
                or source.get("source") not in ("fxaurum_mock", "fxaurum_rithmic")
                or (expiry is not None and not DATE.fullmatch(expiry))):
            raise ValueError("Capture provenance lacks a supported exact software, vendor or contract identity")
        if source.get("measurement_schema_version") != 4:
            raise ValueError("Unsupported measurement schema")
        config = source["capture_config"]
        batch_size, mode = config.get("batch_size"), config.get("mode")
        if type(batch_size) is not int or not 1 <= batch_size <= 64 or config.get("durability") != "SQLite WAL FULL":
            raise ValueError("Unsupported durability declaration")
        if mode not in ("A", "B", "C"):
            raise ValueError("Invalid recorded mode")
        if checkpoint is not None and checkpoint.get("status", {}).get("software_revision") != sha:
            raise ValueError("Checkpoint software SHA does not match capture provenance")
        for row in records:
            count += 1
            if count > LIMIT:
                raise ValueError("Capture prefix exceeds packet processing bound")
            if last is not None and row["id"] != last + 1:
                raise ValueError("Non-contiguous capture record order")
            first = row["id"] if first is None else first
            last = row["id"]
            prefix_digest.update((canonical(row) + "\n").encode())
            kind, data = row["kind"], row["payload"]
            if kind in EVENT_KINDS:
                event_counts[kind] += 1
            if kind in GAP_KINDS:
                gaps[kind] += 1
            if kind == "depth_rpc":
                rpc.update(outcome for outcome in data.get("outcomes", ()) if outcome in RPC_OUTCOMES)
            if kind == "capture_summary":
                capture_summary = data
            if kind == "evidence" and data.get("category") == "aggressive_flow_burst":
                latest[data["id"]] = data
    if not count or last != position:
        raise ValueError("Capture prefix lacks the requested boundary")
    as_of = observation(checkpoint["capture_checkpoint"]["as_of"]) if checkpoint is not None else None
    played = replay(database, session, mode, LIMIT, through_id=position, as_of=as_of, strict=True)
    if played["sha256"] != prefix_digest.hexdigest() or played["records_processed"] != count:
        raise ValueError("Capture changed while calculating evidence")
    if played["snapshot"] is None:
        raise ValueError("No replayable input observations")
    schema_snapshot = semantic(played["snapshot"])
    parity = "UNVERIFIED"
    if checkpoint is not None:
        if schema_snapshot != semantic(checkpoint["microstructure"]):
            raise ValueError("Market-state replay parity failed at the checkpoint")
        parity = "PASS"
    registry = source.get("instrument_registry", {})
    calendar = registry.get("calendar") or {}
    calendar_sha = calendar.get("sha256", "")
    elapsed = capture_summary.get("elapsed_seconds") if capture_summary else None
    counts_summary = Counter(eligible(card) or "eligible_offline" for card in latest.values())
    metadata = {"software_sha": sha, "source": source["source"], "contract": {
        "exchange": "COMEX", "symbol": symbol, "expiry": expiry}}
    recorded_cal = calendar_sha if isinstance(calendar_sha, str) and DIGEST.fullmatch(calendar_sha) else None
    return {
        "schema": SCHEMA, "schema_version": VERSION, "capture": {
            **metadata, "session_id": session, "first_record_id": first, "durable_position": position,
            "records": count, "prefix_sha256": prefix_digest.hexdigest(), "mode": mode,
            "vendor_revision": protocol, "record_schema_version": 1, "measurement_schema_version": 4,
            "calendar_sha256": recorded_cal, "time_basis": "collector_monotonic within session; UTC and source clocks separate",
            "producer_os": source["producer_os"] if source.get("producer_os") in ("Windows", "Linux", "Darwin") else None,
        },
        "coverage": {"record_kinds": dict(event_counts), "gap_markers": dict(gaps), "rpc_outcomes": dict(rpc),
                     "scope": "committed observations only", "graceful_summary": capture_summary is not None},
        "durability": {"sqlite": "WAL FULL", "batch_size": batch_size,
                       "queue": numeric_fields((capture_summary or {}).get("receiver", {}), (
                           "receive_queue_high_water", "receive_pressure_count", "uncertain_received_frames")),
                       "commit_latency_us": numeric_fields(
                           (capture_summary or {}).get("recorder", {}).get("observation_to_commit_us", {}), ("p50", "p95", "p99"))},
        "capacity": {"TESTED_SYNTHETIC_CAPACITY": "UNVERIFIED", "TESTED_LOCAL_WINDOWS_CAPACITY": "UNVERIFIED",
                     "OBSERVED_REAL_CAPTURE_RATE": "UNVERIFIED", "UNVERIFIED_PRODUCTION_CAPACITY": True,
                     "recorded_session_market_records_per_second": (
                         sum(event_counts[k] for k in ("trade", "bbo", "mbp", "mbo")) / elapsed
                         if type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed > 0 else None)},
        "replay": {"state_sha256": digest(schema_snapshot), "prefix_reconstruction": "PASS", "checkpoint_parity": parity,
                   "checkpoint_as_of": as_of.wire() if as_of else None},
        "research": {"hypothesis": "H-GC-001", "scientific_status": "UNRESOLVED", "candidate_counts": dict(counts_summary)},
        "operator": operator_summary(diagnostic, metadata, session, position, event_counts, schema_snapshot),
        "field_gates": {"real_gateway": "UNVERIFIED", "real_depth_entitlement": "UNVERIFIED", "real_capture": "UNVERIFIED"},
    }


def verify_packet(packet, database, *, diagnostic=None, checkpoint=None):
    if packet["schema"] != SCHEMA or packet["schema_version"] != VERSION:
        raise ValueError("Unsupported packet schema")
    expected = build_packet(database, packet["capture"]["session_id"], through_id=packet["capture"]["durable_position"],
                            diagnostic=diagnostic, checkpoint=checkpoint)
    if canonical(packet) != canonical(expected):
        raise ValueError("Packet does not match retained capture or supplied checkpoint evidence")
    return expected["replay"]


def read_json(path):
    if path is None:
        return None
    with path.open(encoding="utf-8-sig") as stream:
        raw = stream.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError("Evidence input too large")
    return json.loads(raw)


def main():
    from .__main__ import local_path

    cli = argparse.ArgumentParser(description=__doc__)
    subparsers = cli.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("build")
    create.add_argument("session")
    create.add_argument("--through-id", type=int)
    create.add_argument("--output", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("packet", type=Path)
    for parser in (create, verify):
        parser.add_argument("database", type=Path)
        parser.add_argument("--diagnostic", type=Path)
        parser.add_argument("--checkpoint", type=Path)
    args = cli.parse_args()
    try:
        diagnostics = read_json(args.diagnostic)
        checkpoint = read_json(args.checkpoint)
        if args.command == "build":
            output = local_path(args.output)
            packet = build_packet(args.database, args.session, through_id=args.through_id,
                                  diagnostic=diagnostics, checkpoint=checkpoint)
            with output.open("x", encoding="utf-8") as stream:
                json.dump(packet, stream, indent=2, allow_nan=False)
                stream.write("\n")
        else:
            result = verify_packet(read_json(args.packet), args.database, diagnostic=diagnostics, checkpoint=checkpoint)
            print(json.dumps(result))
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        cli.exit(1, f"Evidence verification failed: {exc}\n")


if __name__ == "__main__":
    main()
