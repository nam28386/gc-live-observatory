"""Emit a sanitized diagnostic report from the loopback observatory."""

import argparse
from datetime import datetime, timezone
import json
import platform
import socket
from pathlib import Path
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def diagnostic(status, gateway_reachable=None):
    quality = status["quality"]
    recorder = status["recorder"]
    depths = {"mbp": status["mbp_status"], "dbo": status["mbo_entitlement"]}
    live = status["mode"] == "LIVE"
    ack = status["subscribe_ack"] and status["request_id"] == status["last_ack_request_id"]
    return {
        "schema_version": 1, "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "git_sha": status["software_revision"], "os": platform.system(), "python": platform.python_version(),
        "source": status["mode"], "effective_mode": status["market_data_mode"],
        "contract": {key: status[key] for key in ("exchange", "symbol", "expiry")},
        "gateway_tcp_reachable": gateway_reachable, "matching_ack_observed": bool(ack),
        "eligible_trade_count": status["counters"].get("eligible_trade", 0),
        "eligible_bbo_count": status["counters"].get("eligible_bbo", 0),
        "depth": depths,
        "recorder": {key: recorder.get(key) for key in ("enabled", "durable_events", "average_records_per_second",
                                                        "backlog_records", "high_water_records", "failures")},
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
            "capture_io": "FAIL" if status["recorder_error"] else "PASS" if recorder.get("durable_events") else "UNVERIFIED",
        },
        "validation_basis": "live_application_observations_only" if live else "synthetic",
    }


def main():
    from .__main__ import local_path
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--port", type=int, default=8000)
    cli.add_argument("--gateway-port", type=int)
    cli.add_argument("--output", type=Path)
    args = cli.parse_args()
    if any(port is not None and not 1 <= port <= 65535 for port in (args.port, args.gateway_port)):
        cli.error("Invalid IPv4 loopback port")
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
        with build_opener(ProxyHandler({}), NoRedirect()).open(f"http://127.0.0.1:{args.port}/api/status", timeout=5) as response:
            status = json.loads(response.read(1_048_577))
        report = diagnostic(status, reach)
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
