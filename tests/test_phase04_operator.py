import json
import socket
import subprocess
import sys
import threading

import uvicorn

from observatory.web import create_app


def test_synthetic_windows_compatible_operator_cli_on_real_loopback():
    app = create_app(mode="A", interval=.01)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", ws="websockets-sansio"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            run = subprocess.run(
                [sys.executable, "-m", "observatory.diagnose", "--port", str(port), "--require-mode", "A",
                 "--wait-seconds", "8"], capture_output=True, text=True, timeout=15,
            )
            assert run.returncode == 0, run.stderr
            report = json.loads(run.stdout)
            assert report["source"] == "MOCK"
            assert report["stream_readiness"] == {"A": "PASS", "B": "UNVERIFIED", "C": "UNVERIFIED"}
            assert report["expiry_verification"] == "UNVERIFIED"
            assert report["gateway_tcp_reachable"] is None
            assert "price_ticks" not in run.stdout and "raw" not in report
        finally:
            server.should_exit = True
            thread.join(5)
