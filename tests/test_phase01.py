from contextlib import closing
import json
import sqlite3
import subprocess
import sys
import time

from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect
from websockets.sync.server import serve
from threading import Thread

from observatory.config import gc_contract
from observatory.journal import DAY_NS, Journal
from observatory.mock_gateway import MockGateway
from observatory.web import create_app


def test_contract_month_and_year_are_explicit():
    assert gc_contract("gcz6", "2026-12-29").expiry == "2026-12-29"
    for symbol, expiry in [("MGCZ6", "2026-12-29"), ("GCZ6", "2026-11-29"), ("GCZ6", "2027-12-29")]:
        with pytest.raises(ValueError):
            gc_contract(symbol, expiry)


def test_linux_cli_refuses_live_gateway(monkeypatch):
    from observatory.__main__ import main
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "argv", ["observatory", "live", "--symbol", "GCZ6", "--expiry", "2026-12-29"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2


def test_live_adapter_reads_synthetic_vendor_socket_and_local_history(tmp_path):
    instrument = gc_contract("GCZ6", "2026-12-29")
    fixture = MockGateway(instrument)
    requests = []

    def handler(socket):
        request = json.loads(socket.recv())
        requests.append(request)
        socket.send(json.dumps({"id": request["id"], "result": None}))
        for channel, payload in [
            ("@bookTicker", fixture.quote(0)),
            ("@bookTicker", {**fixture.quote(0), "v": 121, "vt": fixture.quote(1)["vt"]}),
            ("@trade", {**fixture.trade(0), "T": time.time_ns() // 1_000_000 + 30_000}),
            ("@trade", fixture.trade(0)),
        ]:
            socket.send(json.dumps({"stream": f"comex.gcz6{channel}", "data": payload}))
        for _ in socket:
            pass

    with serve(handler, "127.0.0.1", 0) as gateway:
        worker = Thread(target=gateway.serve_forever, daemon=True)
        worker.start()
        try:
            app = create_app(instrument=instrument, live_gateway_port=gateway.socket.getsockname()[1],
                             capture_path=tmp_path / "capture.sqlite", stale_after=0.1)
            with TestClient(app, base_url="http://127.0.0.1") as client:
                with client.websocket_connect("ws://127.0.0.1/ws", headers={"origin": "http://127.0.0.1"}) as ws:
                    for _ in range(12):
                        data = ws.receive_json()
                        if data["trade"] and data["status"]["market_freshness"] == "STALE":
                            break
                    else:
                        pytest.fail("Synthetic LIVE profile did not observe trade then silence")
                assert requests == [{"method": "SUBSCRIBE", "params": list(instrument.streams), "id": 1}]
                assert data["trade"]["source"] == "fxaurum_rithmic"
                assert data["trade"]["expiry"] == instrument.expiry
                assert data["status"]["transport_connected"]
                assert data["status"]["last_ack_request_id"] == 1
                assert data["status"]["last_bbo_change"] is None
                assert data["status"]["last_volume_update"] == data["status"]["last_bbo_message"]
                assert data["status"]["last_oi_update"] != data["status"]["last_volume_update"]
                rows = client.get("/api/history").json()["records"]
                assert [row["kind"] for row in rows][:2] == ["ack", "bbo"]
                assert rows[-1]["kind"] == "trade"
                assert any(row["payload"].get("reason") == "future_trade_time" for row in rows)
                assert json.loads(rows[-1]["payload"]["raw"])["data"]["T"] == fixture.trade(0)["T"]
                assert len(client.get("/api/history?limit=1").json()["records"]) == 1
                assert client.get("/api/history", headers={"Host": "foreign.example"}).status_code == 400
                with pytest.raises(WebSocketDisconnect):
                    with client.websocket_connect("ws://127.0.0.1/ws", headers={"origin": "http://foreign.example"}):
                        pass
        finally:
            gateway.shutdown()
            worker.join(timeout=2)


@pytest.mark.asyncio
async def test_restart_recovers_unclean_session_and_keeps_persistent_timeline(tmp_path):
    path = tmp_path / "capture.sqlite"
    crash = subprocess.run([sys.executable, "-c", """
import asyncio, os, sys, time
from pathlib import Path
from observatory.journal import Journal

async def run():
    journal = Journal(Path(sys.argv[1]), {"source": "fixture"})
    async with journal.running():
        await journal.append("bbo", time.time_ns(), {"fixture": True})
        os._exit(17)

asyncio.run(run())
""", str(path)], timeout=10, check=False)
    assert crash.returncode == 17
    restarted = Journal(path, {"source": "fixture"})
    async with restarted.running():
        rows = await restarted.io(restarted.history)
        assert [row["kind"] for row in rows] == ["bbo", "restart_gap"]
        gap = rows[-1]["payload"]
        assert gap["termination"] == "unclean"
        assert gap["from_utc_ns"] == rows[0]["utc_ns"]
        assert gap["previous_session"] == rows[0]["session_id"]
        assert rows[0]["session_id"] != rows[1]["session_id"]
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT termination FROM sessions ORDER BY rowid").fetchall() == [
            ("unclean",), ("clean",),
        ]


@pytest.mark.asyncio
async def test_retention_and_pagination(tmp_path):
    journal = Journal(tmp_path / "capture.sqlite", {"source": "fixture"}, 1)
    now = time.time_ns()
    await journal.io(journal.start, now - 2 * DAY_NS)
    await journal.append("trade", now - 2 * DAY_NS, {})
    await journal.append("gap", now, {})
    rows = await journal.io(journal.history)
    assert [row["kind"] for row in rows] == ["gap"]
    assert await journal.io(journal.history, rows[0]["id"], 20) == []


def test_capture_failure_stops_collection_and_is_visible(tmp_path, monkeypatch):
    def failed_write(*args):
        raise sqlite3.OperationalError("simulated disk error")

    monkeypatch.setattr(Journal, "write", failed_write)
    with TestClient(create_app(interval=0.01, capture_path=tmp_path / "bad.sqlite")) as client:
        with client.websocket_connect("/ws") as ws:
            for _ in range(12):
                snapshot = ws.receive_json()
                if snapshot["status"]["recorder_error"]:
                    break
            else:
                pytest.fail("Recorder error not surfaced")
        assert snapshot["trade"] is None
        assert not snapshot["status"]["transport_connected"]
        assert snapshot["status"]["recorder"]["failures"] == 1


def test_mock_capture_retains_gap_and_returns_ordered_history_after_restart(tmp_path):
    path = tmp_path / "capture.sqlite"
    with TestClient(create_app(interval=0.01, disconnect_after=2, capture_path=path)) as client:
        with client.websocket_connect("/ws") as ws:
            for _ in range(12):
                data = ws.receive_json()
                if data["status"]["last_ack_request_id"] == 2 and any(segment["id"] == 2 for segment in data["status"]["coverage"]):
                    break
            else:
                pytest.fail("Reconnect and durable gap were not observed")
        kinds = [row["kind"] for row in client.get("/api/history?gaps=true").json()["records"]]
        assert kinds == ["coverage_start", "gap", "coverage_start"]
        assert all(data["candles"][frame] for frame in ("M1", "M5", "M15", "H1"))
    with TestClient(create_app(interval=0.01, capture_path=path)) as client:
        records = client.get("/api/history?gaps=true").json()["records"]
        assert any(row["kind"] == "restart_gap" and row["payload"]["termination"] == "clean" for row in records)
        assert any(row["kind"] == "gap" for row in records)
