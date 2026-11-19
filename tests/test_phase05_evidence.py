from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import time

from fastapi.testclient import TestClient
import pytest

from observatory.diagnose import diagnostic
from observatory.evidence import build_packet, verify_packet
from observatory.events import MOCK_GC
from observatory.mock_gateway import MockGateway
from observatory.web import create_app


def test_sanitized_attested_evidence_packet_on_real_socket(tmp_path):
    database = tmp_path / "pilot.sqlite"
    app = create_app(mode="C", capture_path=database,
                     mock_gateway=MockGateway(MOCK_GC, mode="C", interval=.01, tape_cycles=13))
    with TestClient(app) as client:
        deadline = time.monotonic() + 12
        while True:
            snapshot = client.get("/api/snapshot").json()
            rpc = snapshot["status"]["receiver"]["depthsnap_rpc"]
            known_position = (snapshot.get("capture_checkpoint") or {}).get("durable_position") or 0
            if rpc["counts"].get("SNAPSHOT_APPLIED") and any(
                row["kind"] == "depth_rpc" and row["id"] <= known_position
                for row in client.get("/api/history").json()["records"]
            ):
                break
            if time.monotonic() > deadline:
                pytest.fail("Finite tape did not settle")
            time.sleep(.025)
        cp = snapshot["capture_checkpoint"]
        session = cp["session_id"]
        diag = diagnostic(snapshot["status"], features=snapshot["microstructure"])
        assert diag["receiver"]["admitted_queue_frames"] >= diag["receiver"]["settled_queue_frames"]
        assert diag["receiver"]["visible_market_frames"] == diag["receiver"]["durable_market_records"]
        diag["raw"] = {"private_payload": "do not export"}
        diag["account_number"] = "do not export"
        packet = build_packet(database, session, diagnostic=diag, checkpoint=snapshot)
        assert packet["replay"]["checkpoint_parity"] == "PASS"
        assert packet["coverage"]["gap_markers"]["mbp_gap"] == 1
        assert packet["coverage"]["rpc_outcomes"]["SNAPSHOT_APPLIED"] == 1
        assert packet["capture"]["retention_basis"] == "SYNTHETIC"
        assert packet["capture"]["requested_mode"] == packet["capture"]["effective_mode"] == "C"
        assert packet["durability"]["receiver"]["visible_market_frames"] == diag["receiver"]["visible_market_frames"]
        assert packet["field_gates"] == {gate: "UNVERIFIED" for gate in
                                         ("real_gateway", "real_depth_entitlement", "real_capture")}
        assert verify_packet(packet, database, diagnostic=diag, checkpoint=snapshot)["checkpoint_parity"] == "PASS"
        serialized = json.dumps(packet)
        assert "price_ticks" not in serialized and "do not export" not in serialized

        counterfeit = deepcopy(packet)
        counterfeit["field_gates"]["real_gateway"] = "PASS"
        with pytest.raises(ValueError, match="does not match"):
            verify_packet(counterfeit, database, diagnostic=diag, checkpoint=snapshot)
        counterfeit = deepcopy(packet)
        del counterfeit["replay"]["state_sha256"]
        with pytest.raises(ValueError, match="does not match"):
            verify_packet(counterfeit, database, diagnostic=diag, checkpoint=snapshot)
        bad_diag = deepcopy(diag)
        bad_diag["git_sha"] = "f" * 40
        with pytest.raises(ValueError, match="does not match"):
            build_packet(database, session, diagnostic=bad_diag, checkpoint=snapshot)
        bad_cp = deepcopy(snapshot)
        bad_cp["microstructure"]["mbp"]["sequence"] += 1
        with pytest.raises(ValueError, match="parity failed"):
            build_packet(database, session, checkpoint=bad_cp)
    # Also exercise the actual local-only interface on a closed session.
    out = tmp_path / "evidence.json"
    def cli(*args):
        return subprocess.run([sys.executable, "-m", "observatory.evidence", *args], capture_output=True,
                              text=True, cwd=Path(__file__).resolve().parents[1], timeout=15)
    built = cli("build", session, str(database), "--output", str(out))
    assert built.returncode == 0, built.stderr
    checked = cli("verify", str(out), str(database))
    assert checked.returncode == 0, checked.stderr
    assert json.loads(checked.stdout)["checkpoint_parity"] == "UNVERIFIED"
