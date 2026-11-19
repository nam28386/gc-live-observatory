"""Build or reproduce a sanitized Windows commissioning report from local evidence."""

import argparse
from datetime import datetime
from hashlib import file_digest
import json
from pathlib import Path
import re
import sqlite3
from zipfile import BadZipFile, ZipFile

from .config import gc_contract
from .evidence import DIGEST, PERCENTILES, RECEIVER_TOTALS, SESSION, SHA, numeric_fields, read_json, verify_packet
from .replay import canonical

RESOURCE_KEYS = ("cpu_seconds", "peak_sampled_working_set_bytes", "capture_bytes_start",
                 "capture_bytes_end", "volume_free_bytes_start", "volume_free_bytes_end", "samples",
                 "received_frames_delta", "durable_market_records_delta", "uncertain_receipts_delta")


def timestamp(value):
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() is None:
        raise ValueError("Evidence timestamps require a timezone")
    return stamp


def package_identity(archive, checksum):
    if not DIGEST.fullmatch(checksum):
        raise ValueError("Expected the published inner source ZIP SHA256")
    with archive.open("rb") as stream:
        if file_digest(stream, "sha256").hexdigest() != checksum:
            raise ValueError("Inner source ZIP checksum mismatch")
        stream.seek(0)
        with ZipFile(stream) as package:
            sha = json.loads(package.read("RELEASE.json"))["source_sha"]
    if not SHA.fullmatch(sha):
        raise ValueError("Invalid package source SHA")
    return sha


def resources(sample, recorder, sha, observed):
    if sample is None:
        return None
    if (sample["schema_version"] != 1 or sample["source_sha"] != sha
            or sample["session_id"] != recorder["session_id"] or not recorder["session_id"]):
        raise ValueError("Resource sample must belong to the diagnostic capture session and SHA")
    start, end = timestamp(sample["started_utc"]), timestamp(sample["ended_utc"])
    elapsed = numeric_fields(sample, ("elapsed_seconds",)).get("elapsed_seconds", 0)
    values = numeric_fields(sample, RESOURCE_KEYS)
    if start >= end or end > observed or elapsed <= 0 or any(value < 0 for value in values.values()):
        raise ValueError("Invalid resource measurement window or values")
    if set(values) != set(RESOURCE_KEYS) or type(values["samples"]) is not int or values["samples"] < 2:
        raise ValueError("Resource measurement is incomplete")
    return {**values, "elapsed_seconds": elapsed,
            "received_frames_per_second": values["received_frames_delta"] / elapsed,
            "durable_market_records_per_second": values["durable_market_records_delta"] / elapsed,
            "started_utc": start.isoformat(),
            "ended_utc": end.isoformat(), "basis": "SYSTEM_OBSERVED",
            "method": "Windows process counters and capture-file lengths, sampled locally"}


def build_report(report, pilot_id, sha, checksum, *, authorized=False, packet=None, database=None,
                 checkpoint=None, resource_sample=None):
    if not SESSION.fullmatch(pilot_id):
        raise ValueError("Pilot ID must be a generated UUID in 32-character hexadecimal form")
    if report["schema_version"] != 1 or report["git_sha"] != sha:
        raise ValueError("Diagnostic does not match the verified package SHA")
    live = report["source"] == "LIVE"
    if report["source"] not in ("MOCK", "LIVE") or (live and report["os"] != "Windows"):
        raise ValueError("LIVE diagnostic must be from Windows")
    if not re.fullmatch(r"3\.12\.\d+", report["python"]) or report["os"] not in ("Windows", "Linux", "Darwin"):
        raise ValueError("Unsupported operator runtime")
    mode, requested = report["effective_mode"], report["requested_mode"]
    if mode not in ("A", "B", "C") or requested not in ("A", "B", "C"):
        raise ValueError("Invalid pilot mode")
    observed = timestamp(report["timestamp_utc"])
    contract = report["contract"]
    symbol, expiry = contract["symbol"], contract["expiry"]
    if contract["exchange"] != "COMEX" or (live and gc_contract(symbol, expiry).symbol != symbol):
        raise ValueError("Invalid COMEX contract")
    if not live and (symbol, expiry) != ("GCZ6", None):
        raise ValueError("Invalid synthetic contract")
    expiry_basis = report["expiry_verification"]
    if expiry_basis not in ("UNVERIFIED", "OPERATOR_ATTESTED") or (not live and expiry_basis != "UNVERIFIED"):
        raise ValueError("Invalid expiry attestation")
    recorder = report["recorder"]
    session, position = recorder["session_id"], recorder["durable_position"]
    if session is not None and not SESSION.fullmatch(session):
        raise ValueError("Invalid capture session")
    if position is not None and (type(position) is not int or position <= 0 or session is None):
        raise ValueError("Invalid durable position")
    basis = "SYSTEM_OBSERVED" if live else "SYNTHETIC_TESTED"
    unresolved = []
    replay = {"prefix_reconstruction": "UNVERIFIED", "checkpoint_parity": "UNVERIFIED"}
    rights = "UNKNOWN" if live else "NOT_APPLICABLE"
    if packet is not None:
        if database is None or (packet["capture"]["session_id"], packet["capture"]["durable_position"]) != (session, position):
            raise ValueError("Replay packet and diagnostic must describe the same committed prefix")
        replayed = verify_packet(packet, database, diagnostic=report, checkpoint=checkpoint)
        replay = {key: replayed[key] for key in replay}
        if live and packet["capture"].get("retention_basis") == "OPERATOR_ATTESTED":
            rights = "OPERATOR_ATTESTED"
    if live and not authorized:
        unresolved.append("workstation_authorization")
    if live and expiry_basis != "OPERATOR_ATTESTED":
        unresolved.append("contract_expiry")
    reach = report["gateway_tcp_reachable"]
    if (reach is not None and type(reach) is not bool) or (not live and reach is not None):
        raise ValueError("Invalid gateway check")
    if live and reach is not True:
        unresolved.append("loopback_gateway")
    gates = report["gates"]
    observations = {}
    for output, gate in (("subscription_ack", "subscribe_ack"), ("trade_observation", "trade"),
                         ("bbo_observation", "bbo"), ("mbp_status", "mbp"), ("dbo_status", "dbo_observation"),
                         ("recorder_status", "capture_io")):
        state = gates[gate]
        if state not in ("PASS", "FAIL", "PARTIAL", "UNVERIFIED", "NOT_APPLICABLE"):
            raise ValueError("Invalid diagnostic gate")
        observations[output] = {"status": state, "basis": basis}
    required = ["subscription_ack", "trade_observation", "bbo_observation"]
    if requested in ("B", "C"):
        required.append("mbp_status")
    if requested == "C":
        required.append("dbo_status")
    unresolved.extend(key for key in required if observations[key]["status"] != "PASS")
    if mode != requested:
        unresolved.append("requested_mode")
    failure = reach is False or any(observations[key]["status"] == "FAIL" for key in (*required, "recorder_status"))
    if resource_sample is not None and not live:
        raise ValueError("The workstation resource sample is for LIVE capture only")
    sample = resources(resource_sample, recorder, sha, observed)
    if recorder["enabled"]:
        if observations["recorder_status"]["status"] != "PASS":
            unresolved.append("recorder_status")
        if rights == "UNKNOWN":
            unresolved.append("capture_rights")
        if replay["checkpoint_parity"] != "PASS":
            unresolved.append("capture_replay_parity")
        if sample is None:
            unresolved.append("capture_resource_sample")

    receiver = report["receiver"]
    quality = numeric_fields(report["quality"], ("transport_gap_count", "mbp_recovery_count", "elapsed_seconds"))
    return {
        "schema": "gc-observatory-field-pilot", "schema_version": 1, "pilot_id": pilot_id,
        "software_sha": sha, "artifact_sha256": checksum, "artifact_scope": "INNER_SOURCE_ZIP",
        "operator_os": report["os"], "runtime_version": report["python"],
        "source_name": "FXAURUM_RITHMIC" if live else "SYNTHETIC_MOCK",
        "contract_symbol": symbol, "contract_expiry": expiry, "expiry_attestation": expiry_basis,
        "workstation_authorization": "OPERATOR_ATTESTED" if live and authorized else "UNVERIFIED",
        "observed_at": observed.isoformat(), "timestamp_source": "diagnostic workstation clock",
        "market_data_mode": {"requested": requested, "effective": mode},
        "gateway_status": {"status": "PASS" if reach is True else "FAIL" if reach is False else "UNVERIFIED",
                           "basis": basis},
        **observations, "session_id": session, "durable_position": position, "replay_verification": replay,
        "session_elapsed_seconds": quality.get("elapsed_seconds"),
        "gap_count": quality.get("transport_gap_count"), "mbp_recovery_count": quality.get("mbp_recovery_count"),
        "uncertain_receipts": receiver.get("uncertain_received_frames") if type(receiver.get("uncertain_received_frames")) is int else None,
        "queue_high_water": receiver.get("receive_queue_high_water") if type(receiver.get("receive_queue_high_water")) is int else None,
        "receiver": numeric_fields(receiver, RECEIVER_TOTALS),
        "queue_residence_us": numeric_fields(receiver.get("queue_residence_us") or {}, PERCENTILES),
        "commit_latency_us": numeric_fields(report.get("commit_latency_us") or {}, PERCENTILES),
        "publication_latency_us": numeric_fields(report.get("publication_latency_us") or {}, PERCENTILES),
        "resources": sample, "capture_rights_status": rights,
        "pilot_result": "FAIL" if failure else "PARTIAL" if unresolved else "PASS",
        "pilot_result_scope": basis, "independent_field_verification": "UNVERIFIED",
        "production_capacity": "UNVERIFIED", "unresolved_items": unresolved,
    }


def main():
    from .__main__ import local_path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnostic", required=True, type=Path)
    parser.add_argument("--pilot-id", required=True)
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--artifact-sha256", required=True)
    parser.add_argument("--operator-authorized", action="store_true")
    parser.add_argument("--capture-packet", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--resources", type=Path)
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument("--output", type=Path)
    output.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if bool(args.capture_packet) != bool(args.database) or (args.checkpoint and not args.capture_packet):
        parser.error("Supply --capture-packet with --database and the original private --checkpoint when used")
    try:
        sha = package_identity(args.artifact, args.artifact_sha256)
        report = build_report(read_json(args.diagnostic), args.pilot_id, sha, args.artifact_sha256,
                              authorized=args.operator_authorized, packet=read_json(args.capture_packet),
                              database=args.database, checkpoint=read_json(args.checkpoint),
                              resource_sample=read_json(args.resources))
        if args.verify:
            if canonical(read_json(args.verify)) != canonical(report):
                raise ValueError("Pilot report differs from local inputs")
            print("PASS: report reproduced from local inputs")
        else:
            with local_path(args.output).open("x", encoding="utf-8") as file:
                json.dump(report, file, indent=2, allow_nan=False)
                file.write("\n")
    except (BadZipFile, OSError, ValueError, KeyError, TypeError, AttributeError, sqlite3.Error) as exc:
        parser.exit(1, f"Pilot evidence rejected ({type(exc).__name__}). Check package, schema and capture identity.\n")


if __name__ == "__main__":
    main()
