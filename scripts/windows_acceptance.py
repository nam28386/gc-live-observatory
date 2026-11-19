"""Exercise the installed launcher, dashboard, capture, replay and restart on a finite mock tape."""

import argparse
from contextlib import ExitStack, closing, contextmanager
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.request import ProxyHandler, build_opener

from websockets.sync.client import connect

from observatory.diagnose import NoRedirect
from observatory.replay import replay

ROOT = Path(__file__).resolve().parents[1]
OPENER = build_opener(ProxyHandler({}), NoRedirect())
CYCLES = 23
DISCONNECT = 16


def fetch(port, route):
    with OPENER.open(f"http://127.0.0.1:{port}{route}", timeout=2) as response:
        return response.read()


def polling(server, port, ready):
    deadline = time.monotonic() + 25
    last_snapshot = None
    last_fetch_error = None
    while time.monotonic() < deadline and server.poll() is None:
        try:
            snapshot = json.loads(fetch(port, "/api/snapshot"))
        except (OSError, ValueError) as exc:
            last_fetch_error = type(exc).__name__
        else:
            last_snapshot = snapshot
            if ready(snapshot):
                return snapshot
        time.sleep(.05)
    # Report bounded, synthetic, non-price state from the last snapshot.
    summary = {"server_exit": server.poll(), "fetch_error": last_fetch_error}
    if last_snapshot is not None:
        status = last_snapshot.get("status", {})
        micro = last_snapshot.get("microstructure", {})
        receiver = status.get("receiver", {})
        book = micro.get("mbp", {})
        ofi = micro.get("ofi", {})
        summary.update({
            "mode": status.get("market_data_mode"),
            "request_id": status.get("request_id"),
            "last_ack_request_id": status.get("last_ack_request_id"),
            "eligible_trade": status.get("counters", {}).get("eligible_trade"),
            "transport_gap_count": status.get("quality", {}).get("transport_gap_count"),
            "queue_admitted": receiver.get("admitted_queue_frames"),
            "queue_settled": receiver.get("settled_queue_frames"),
            "queue_uncertain": receiver.get("uncertain_received_frames"),
            "rpc_counts": receiver.get("depthsnap_rpc", {}).get("counts", {}),
            "rpc_recent_outcomes": [
                item["outcomes"]
                for item in receiver.get("depthsnap_rpc", {}).get("recent", [])[-4:]
            ],
            "ofi_status": ofi.get("status"),
            "book_status": book.get("status"),
            "book_gaps": book.get("counts", {}).get("gaps"),
            "recorder_error": status.get("recorder_error"),
            "recorder_failures": status.get("recorder", {}).get("failures"),
        })
    raise RuntimeError(
        "Synthetic subprocess did not reach the requested readiness state: "
        + json.dumps(summary, sort_keys=True)
    )


def powershell(shell, script, *args):
    return [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
            str(ROOT / "scripts" / "windows" / f"{script}.ps1"), *map(str, args)]


def python(module, *args):
    return [sys.executable, "-m", module, *map(str, args)]


def check(command, env, expected=0):
    run = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=50)
    if run.returncode != expected:
        raise RuntimeError(f"Operator command returned {run.returncode}, expected {expected}\n{run.stderr}")
    return run.stdout


@contextmanager
def launch(command, env, log):
    with log.open("w+", encoding="utf-8") as output:
        server = subprocess.Popen(command, cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT)
        try:
            yield server
        finally:
            if server.poll() is None:
                # Windows PowerShell and uv may have spawned child processes.
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(server.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                else:
                    server.terminate()
                server.wait(timeout=10)


def run_case(root, shell, port, requested, effective, *, database=None, previous_session=None):
    started = time.monotonic()
    capture = database is not None
    restart = previous_session is not None
    case = f"{requested}{effective}{'-restart' if restart else ''}"
    env = {**os.environ, "LOCALAPPDATA": str(root)}
    runtime = root / "GCLiveObservatory"
    runtime.mkdir(exist_ok=True)
    marker = runtime / f"stop-{port}"
    db = database
    assert not marker.exists()
    if shell:
        command = powershell(shell, "run-mock", "-Port", port, "-Mode", requested,
                             "-Interval", "0.04", "-TapeCycles", CYCLES, "-DisconnectAfter", DISCONNECT,
                             "-StaleAfter", 30)
        if capture:
            command += ["-Capture", "-CapturePath", str(db)]
        if requested != effective:
            command.append("-NoSyntheticDbo")
    else:
        command = python("observatory", "mock", "--port", port, "--mode", requested,
                         "--interval", ".04", "--tape-cycles", CYCLES, "--disconnect-after", DISCONNECT,
                         "--stale-after", 30, "--stop-file", marker)
        if capture:
            command += ["--capture", str(db)]
        if requested != effective:
            command.append("--no-synthetic-dbo")

    trades = CYCLES - 2  # The intentional transport gap omits two cycles.
    market = 2 * trades + 2  # Trade/BBO pairs and one initial BBO per connection.
    if effective in ("B", "C"):
        market += trades + 4  # Depth updates and a Reset/Image per connection.
    if effective == "C":
        market += trades + 2  # Four Reset/Image frames, minus two unseeded order updates.

    def ready(snapshot):
        s = snapshot["status"]
        micro = snapshot["microstructure"]
        receive = s["receiver"]
        return (
            s["counters"].get("eligible_trade") == trades
            and receive["visible_market_frames"] == market
            and (not capture or receive["durable_market_records"] == market)
            and receive["admitted_queue_frames"] == receive["settled_queue_frames"]
            and s["subscribe_ack"] and s["request_id"] == s["last_ack_request_id"]
            and (effective == "A" or (
                micro["ofi"]["status"] == "VALID"
                and receive["depthsnap_rpc"]["counts"].get("SNAPSHOT_APPLIED") == 1
            ))
        )

    with launch(command, env, root / f"{case}.log") as server:
        snapshot = polling(server, port, ready)
        status = snapshot["status"]
        micro = snapshot["microstructure"]
        receiver = status["receiver"]
        assert (status["mode"], status["exchange"], status["symbol"], status["expiry"]) == ("MOCK", "COMEX", "GCZ6", None)
        assert status["requested_market_data_mode"] == requested and status["market_data_mode"] == effective
        assert status["request_id"] == (2 if effective == "A" else 3)
        assert status["quality"]["transport_gap_count"] == 1
        assert not status["recorder_error"] and receiver["uncertain_received_frames"] == 0
        assert receiver["receive_queue_high_water"] <= receiver["receive_queue_capacity"]
        assert all(snapshot["candles"][frame] for frame in ("M1", "M5", "M15", "H1"))
        assert fetch(port, "/").count(b"<canvas") == 4
        with connect(f"ws://127.0.0.1:{port}/ws", proxy=None, open_timeout=3, max_size=8 * 1024 * 1024) as ws:
            assert json.loads(ws.recv(timeout=3))["status"]["request_id"] == status["request_id"]
        if effective != "A":
            rpc = receiver["depthsnap_rpc"]["recent"][-1]
            assert (rpc["request_id"], rpc["connection_epoch"], rpc["instrument"]) == (2, 1, "GCZ6")
            assert micro["mbp"]["status"] == "VALID_BOOK" and micro["mbp"]["counts"]["gaps"] == 2
            stages = rpc["stages_ms"]
            ordered = ["REQUEST_SENT", "RESPONSE_RECEIVED", "RESPONSE_DISPATCHED"]
            if capture:
                ordered.append("RESPONSE_COMMITTED")
            ordered.append("SNAPSHOT_APPLIED")
            stamps = [stages[key] for key in ordered]
            assert stamps == sorted(stamps)
        if effective == "C":
            counts = micro["mbo"]["counts"]
            assert status["mbo_entitlement"] == "SYNTHETIC"
            assert counts["exact_fill"] == counts["exact_fill_quantity"] == 2
            assert counts["inferred_fill"] == counts["inferred_fill_quantity"] == 3
            assert counts["left_censored"] == 2
        if requested != effective:
            assert status["mbo_entitlement"] == "UNVERIFIED" and micro["mbo"]["status"] == "DISABLED"

        if shell:
            check(powershell(shell, "preflight", "-Port", port, "-Synthetic", "-Running"), env)
            assert "PASS MOCK" in check(powershell(shell, "smoke", "-Port", port, "-Synthetic",
                                                  "-Mode", effective, "-TimeoutSeconds", 5), env)
            if effective != requested:
                check(powershell(shell, "smoke", "-Port", port, "-Synthetic", "-Mode", requested), env, expected=1)
        if capture:
            assert receiver["normalized_market_frames"] == receiver["durable_market_records"] == receiver["visible_market_frames"]
            session = snapshot["capture_checkpoint"]["session_id"]
            if restart:
                records = json.loads(fetch(port, "/api/history?limit=30&gaps=true"))["records"]
                assert session != previous_session
                assert any(row["kind"] == "restart_gap" and row["session_id"] == session
                           and row["payload"]["previous_session"] == previous_session
                           and row["payload"]["termination"] == "clean" for row in records)
            diagnostic = root / f"{case}-diagnostic.json"
            checkpoint = root / f"{case}-private.json"
            check(python("observatory.diagnose", "--port", port, "--require-mode", effective,
                         "--output", diagnostic, "--checkpoint-output", checkpoint), env)
        if shell:
            assert "Stopped" in check(powershell(shell, "stop", "-Port", port, "-Synthetic"), env)
        else:
            marker.touch(exist_ok=False)
        exit_code = server.wait(timeout=22)
        assert exit_code == 0 and not marker.exists()

    with socket.socket() as listener:
        listener.settimeout(2)
        assert listener.connect_ex(("127.0.0.1", port)) != 0
    if capture:
        packet_path = root / f"{case}-evidence.json"
        evidence_inputs = ("--diagnostic", diagnostic, "--checkpoint", checkpoint)
        check(python("observatory.evidence", "build", session, db, "--output", packet_path, *evidence_inputs), env)
        verified = check(python("observatory.evidence", "verify", packet_path, db, *evidence_inputs), env)
        assert json.loads(verified)["checkpoint_parity"] == "PASS"
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        assert all(value == "UNVERIFIED" for value in packet["field_gates"].values())
        assert packet["coverage"]["gap_markers"]["mbp_gap"] == 1
        final = replay(db, session, effective, 10_000, strict=True)
        assert final["capture_summary"]["receiver"]["uncertain_received_frames"] == 0
        assert final["capture_summary"]["committed_market_records"] == receiver["durable_market_records"]
        with closing(sqlite3.connect(db)) as connection:
            assert connection.execute("SELECT termination FROM sessions WHERE id=?", (session,)).fetchone() == ("clean",)
    else:
        session = None
    return session, status["software_revision"], {
        "scenario": case, "duration_seconds": round(time.monotonic() - started, 3),
        "launcher_exit": exit_code, "listener_closed": True, "stop_marker_removed": True,
        "settled_market_frames": market, "checkpoint_parity": "PASS" if capture else "NOT_APPLICABLE",
    }


def select_ports(count):
    with ExitStack() as stack:
        ports = []
        while len(ports) < count:
            listener = stack.enter_context(socket.socket())
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            if port != 9001:
                ports.append(port)
        return iter(ports)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--powershell", help="PowerShell executable for the operator path")
    parser.add_argument("--expected-sha")
    parser.add_argument("--restart-repeats", type=int, choices=range(1, 7), default=1)
    parser.add_argument("--restart-only", action="store_true")
    args = parser.parse_args()
    shell = args.powershell or (shutil.which("pwsh") if os.name == "nt" else None)
    if os.name == "nt" and not shell:
        parser.error("PowerShell 7 (pwsh) is required for Windows operator acceptance")
    with TemporaryDirectory(prefix="gc-acceptance-") as directory:
        root = Path(directory)
        if shell:
            env = {**os.environ, "LOCALAPPDATA": directory}
            check(powershell(shell, "install"), env)
            check(powershell(shell, "run-live", "-Symbol", "GCZ6", "-Expiry", "2026-12-29", "-Capture"),
                  env, expected=1)
            with socket.socket() as nonlocal_listener:
                nonlocal_listener.bind(("0.0.0.0", 0))
                nonlocal_listener.listen(1)
                check(powershell(shell, "preflight", "-Port", nonlocal_listener.getsockname()[1],
                                 "-Synthetic", "-Running"), env, expected=1)
        ports = select_ports(2 * args.restart_repeats + (0 if args.restart_only else 3))
        results = []
        if not args.restart_only:
            for requested, effective in (("A", "A"), ("B", "B"), ("C", "B")):
                _, _, result = run_case(root, shell, next(ports), requested, effective)
                results.append(result)
        for repeat in range(1, args.restart_repeats + 1):
            pair_root = root / f"capture-{repeat}"
            pair_root.mkdir()
            database = pair_root / "mock.sqlite"
            first, revision, started = run_case(pair_root, shell, next(ports), "C", "C", database=database)
            _, reopened_revision, restarted = run_case(
                pair_root, shell, next(ports), "C", "C", database=database, previous_session=first,
            )
            assert revision == reopened_revision
            if args.expected_sha:
                assert revision == args.expected_sha
            results.extend({**result, "pair": repeat} for result in (started, restarted))
    print(json.dumps({"source": "SYNTHETIC", "platform": sys.platform,
                      "launcher": "PowerShell" if shell else "Python", "restart_pairs": args.restart_repeats,
                      "capture_replay_evidence_restart": "PASS", "sha": revision, "scenarios": results}))


if __name__ == "__main__":
    main()
