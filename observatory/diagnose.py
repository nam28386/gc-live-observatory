"""Emit a sanitized diagnostic report from the loopback observatory."""

import argparse
from datetime import datetime, timezone
import json
import platform
import socket
import time
from pathlib import Path
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def diagnostic(status, gateway_reachable=None, *, features=None, expiry_verified=False):
    quality = status["quality"]
    recorder = status["recorder"]
    depths = {"mbp": status["mbp_status"], "dbo": status["mbo_entitlement"]}
    live = status["mode"] == "LIVE"
    features = features or {}
    mbo = features.get("mbo", {}).get("counts") or {}
    mbp = features.get("mbp", {})
    receiver = status.get("receiver", {})
    ready = status["market_data_mode"]
    gate_a = (status["transport_connected"] and status["subscribe_ack"]
              and status["request_id"] == status["last_ack_request_id"]
              and status["trade_freshness"] == status["bbo_freshness"] == "OBSERVED")
    gate_b = bool(gate_a and mbp.get("status") == "VALID_BOOK"
                  and quality["last_mbp_snapshot"] and features.get("ofi", {}).get("status") == "VALID")
    gate_c = bool(gate_b and status["mbo_entitlement"] in ("OPERATOR_REPORTED", "SYNTHETIC")
                  and quality["last_valid_dbo"] and "mbo" not in quality["missing_sources"])
    ack = status["subscribe_ack"] and status["request_id"] == status["last_ack_request_id"]
    receiver_keys = (
        "receive_queue_high_water", "receive_queue_capacity", "receive_pressure_count", "uncertain_received_frames",
        "socket_received_frames", "admitted_queue_frames", "settled_queue_frames", "normalized_market_frames",
        "visible_market_frames", "durable_market_records", "receiver_queue_occupancy", "oldest_queued_ms",
        "queue_residence_us", "depthsnap_send_to_receipt_ms", "depthsnap_receipt_to_dispatch_ms",
        "depthsnap_receipt_to_publication_ms",
    )
    reasons = {
        "gateway_tcp": "IPv4 probe not requested" if gateway_reachable is None else
            "IPv4 loopback accepted the connection" if gateway_reachable else "IPv4 loopback TCP unreachable",
        "subscribe_ack": "Matching ACK" if ack else "No correlated ACK",
        "trade": status["trade_freshness"],
        "bbo": status["bbo_freshness"],
        "mbp": depths["mbp"],
        "dbo_observation": depths["dbo"],
        "actual_gateway_and_entitlement": "Needs independent authorized operator evidence",
        "capture_io": "Capture disabled" if not recorder.get("enabled") else
            "Recorder failed" if status["recorder_error"] else
            "Durable records observed" if recorder.get("durable_events") else "No durable records yet",
    }
    return {
        "schema_version": 1, "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "git_sha": status["software_revision"], "os": platform.system(), "python": platform.python_version(),
        "os_release": platform.release(), "os_version": platform.version(),
        "source": status["mode"], "effective_mode": status["market_data_mode"],
        "requested_mode": status["requested_market_data_mode"],
        "contract": {key: status[key] for key in ("exchange", "symbol", "expiry")},
        "expiry_verification": "OPERATOR_ATTESTED" if live and expiry_verified else "UNVERIFIED",
        "gateway_tcp_reachable": gateway_reachable,
        "stream_readiness": {mode: "PASS" if passed else "UNVERIFIED"
                             for mode, passed in zip("ABC", (gate_a, gate_b, gate_c), strict=True)},
        "effective_stream_ready": {"A": gate_a, "B": gate_b, "C": gate_c}[ready],
        "fill_events": {"exact": mbo.get("exact_fill", 0), "inferred": mbo.get("inferred_fill", 0)},
        "recovery": {key: mbp.get(key) for key in ("status", "generation", "sequence", "counts")},
        "receiver": {key: receiver.get(key) for key in receiver_keys},
        "depthsnap_lifecycle": receiver.get("depthsnap_rpc", {}).get("counts", {}),
        "gate_reasons": reasons,
        "commit_latency_us": recorder.get("observation_to_commit_us"),
        "publication_latency_us": features.get("diagnostics", {}).get("observation_to_availability_us"), "matching_ack_observed": bool(ack),
        "eligible_trade_count": status["counters"].get("eligible_trade", 0),
        "eligible_bbo_count": status["counters"].get("eligible_bbo", 0),
        "depth": depths,
        "recorder": {key: recorder.get(key) for key in ("enabled", "durable_events", "average_records_per_second",
                                                        "backlog_records", "high_water_records", "failures", "session_id", "durable_position")},
        "quality": {key: quality[key] for key in ("session_state", "market_observation_state", "transport_gap_count",
                                                  "mbp_recovery_count", "invalid_feature_windows", "missing_sources")},
        "gates": {
            "gateway_tcp": "UNVERIFIED" if gateway_reachable is None else "PASS" if gateway_reachable else "FAIL",
            "subscribe_ack": "PASS" if ack else "FAIL",
            "trade": "PASS" if status["trade_freshness"] == "OBSERVED" else "UNVERIFIED",
            "bbo": "PASS" if status["bbo_freshness"] == "OBSERVED" else "UNVERIFIED",
            "mbp": "PASS" if depths["mbp"] == "VALID_BOOK" and quality["last_valid_mbp"] else "UNVERIFIED",
            "dbo_observation": "PASS" if quality["last_valid_dbo"] and "mbo" not in quality["missing_sources"] else "UNVERIFIED",
            "actual_gateway_and_entitlement": "UNVERIFIED",
            "capture_io": "FAIL" if status["recorder_error"] else "PASS" if recorder.get("durable_events")
                          else "UNVERIFIED" if recorder.get("enabled") else "NOT_APPLICABLE",
        },
        "validation_basis": "live_application_observations_only" if live else "synthetic",
    }


def main():
    from .__main__ import local_path
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--port", type=int, default=8000)
    cli.add_argument("--gateway-port", type=int)
    cli.add_argument("--output", type=Path)
    cli.add_argument("--require-mode", choices=("A", "B", "C"))
    cli.add_argument("--wait-seconds", type=float, default=0)
    cli.add_argument("--expiry-verified", action="store_true", help="record a local operator's expiry attestation")
    args = cli.parse_args()
    if any(port is not None and not 1 <= port <= 65535 for port in (args.port, args.gateway_port)):
        cli.error("Invalid IPv4 loopback port")
    if not 0 <= args.wait_seconds <= 120:
        cli.error("Readiness wait must be between 0 and 120 seconds")
    if args.gateway_port is not None and platform.system() != "Windows":
        cli.error("Vendor gateway TCP verification is Windows-only")
    reach = None
    if args.gateway_port is not None:
        try:
            with socket.create_connection(("127.0.0.1", args.gateway_port), timeout=2):
                reach = True
        except OSError:
            reach = False
    try:
        opener = build_opener(ProxyHandler({}), NoRedirect())
        deadline = time.monotonic() + args.wait_seconds
        while True:
            try:
                with opener.open(f"http://127.0.0.1:{args.port}/api/snapshot", timeout=3) as response:
                    snapshot = json.loads(response.read(16 * 1024 * 1024 + 1))
                report = diagnostic(snapshot["status"], reach, features=snapshot["microstructure"],
                                    expiry_verified=args.expiry_verified)
                if args.require_mode is None or (
                    report["effective_mode"] == args.require_mode and report["effective_stream_ready"]
                ):
                    break
                if time.monotonic() >= deadline:
                    report["gates"]["requested_mode_ready"] = "FAIL"
                    report["gate_reasons"]["requested_mode_ready"] = (
                        f"Expected mode {args.require_mode}, effective {report['effective_mode']}; "
                        f"readiness {report['stream_readiness'][args.require_mode]}; "
                        f"missing sources {','.join(report['quality']['missing_sources']) or 'none'}"
                    )
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
            time.sleep(.1)
        rendered = json.dumps(report, indent=2) + "\n"
        if args.output:
            with local_path(args.output).open("x", encoding="utf-8") as output:
                output.write(rendered)
        else:
            print(rendered, end="")
    except (OSError, ValueError, KeyError) as exc:
        cli.exit(1, f"Local diagnostics failed: {exc}\n")
    raise SystemExit(1 if "FAIL" in report["gates"].values() else 0)


if __name__ == "__main__":
    main()
