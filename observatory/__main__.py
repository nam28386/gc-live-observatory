"""Run the local observatory."""

import argparse
import asyncio
import math
import json
from pathlib import Path
import sys

import uvicorn
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from .config import gc_contract
from .web import create_app


async def serve(config: uvicorn.Config, stop_file: Path) -> None:
    server = uvicorn.Server(config)

    async def wait_for_stop():
        while not stop_file.is_file():
            await asyncio.sleep(0.25)
        stop_file.unlink()
        server.should_exit = True

    watcher = asyncio.create_task(wait_for_stop())
    try:
        await server.serve()
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
    if not server.started:
        raise SystemExit(1)


def local_path(value: Path) -> Path:
    path = value.resolve()
    if str(path).startswith(("\\\\", "//")) or any(
        (parent / ".git").is_file() or (parent / ".git" / "HEAD").is_file()
        for parent in path.parents
    ):
        raise ValueError("Use a local path outside Git checkouts")
    return path


async def probe(instrument, port):
    async with asyncio.timeout(12):
        async with connect(f"ws://127.0.0.1:{port}/", proxy=None, max_size=64 * 1024, max_queue=8) as socket:
            await socket.send(json.dumps({
                "method": "mboProbe", "id": 1,
                "params": {"exchange": instrument.exchange, "symbol": instrument.symbol, "ms": 8000},
            }))
            while True:
                payload = json.loads(await socket.recv())
                if isinstance(payload, dict) and type(payload.get("id")) is int and payload["id"] == 1:
                    result = payload.get("result")
                    status = payload.get("status", 200)
                    if ("error" in payload or status != 200 or not isinstance(result, dict)
                        or type(result.get("entitled")) is not bool):
                        raise ValueError("Gateway did not return an entitlement result")
                    return result["entitled"]


def main() -> None:
    parser = argparse.ArgumentParser(description="GC Live Observatory")
    subcommands = parser.add_subparsers(dest="command", required=True)
    mock = subcommands.add_parser("mock", help="run a local synthetic gateway and dashboard")
    mock.add_argument("--interval", default=0.16, type=float, help="seconds between synthetic trades")
    mock.add_argument("--disconnect-after", type=int, help="force one synthetic socket loss after N trades")
    live = subcommands.add_parser("live", help="observe the local Windows Fxaurum gateway")
    live.add_argument("--symbol", required=True)
    live.add_argument("--expiry", required=True, help="operator-confirmed contract expiry, YYYY-MM-DD")
    live.add_argument("--gateway-port", type=int, default=9001)
    live.add_argument("--stale-after", type=float, default=10)
    live.add_argument("--stop-file", type=Path)
    live.add_argument("--mbo-entitled", action="store_true", help="operator's positive local mboProbe result")
    mbo_probe = subcommands.add_parser("mbo-probe", help="verify DBO entitlement from the Windows gateway")
    mbo_probe.add_argument("--symbol", required=True)
    mbo_probe.add_argument("--expiry", required=True)
    mbo_probe.add_argument("--gateway-port", type=int, default=9001)
    for command in (mock, live):
        command.add_argument("--mode", choices=("A", "B", "C"), default="A")
        command.add_argument("--port", default=8000, type=int)
        command.add_argument("--capture", type=Path, help="opt-in journal database outside all Git checkouts")
        command.add_argument("--retention-days", type=int, default=7)
    args = parser.parse_args()
    if args.command == "mbo-probe":
        if sys.platform != "win32" or not 1 <= args.gateway_port <= 65535:
            parser.error("mbo-probe requires a valid Windows loopback port")
        try:
            entitled = asyncio.run(probe(gc_contract(args.symbol, args.expiry), args.gateway_port))
        except (ValueError, OSError, TimeoutError, WebSocketException):
            parser.exit(2, "DBO entitlement unverified: probe failed or timed out\n")
        print("DBO ENTITLED" if entitled else "DBO UNAVAILABLE")
        raise SystemExit(0 if entitled else 1)
    if not 1 <= args.port <= 65535 or args.port == 9001:
        parser.error("--port must be between 1 and 65535 and cannot be 9001")
    if args.retention_days < 1:
        parser.error("--retention-days must be positive")
    try:
        capture = local_path(args.capture) if args.capture else None
        options = dict(capture_path=capture, retention_days=args.retention_days, mode=args.mode)
        stop_file = None
        if args.command == "mock":
            if not math.isfinite(args.interval) or args.interval <= 0:
                parser.error("--interval must be a positive finite number")
            if args.disconnect_after is not None and args.disconnect_after < 1:
                parser.error("--disconnect-after must be positive")
            app = create_app(interval=args.interval, disconnect_after=args.disconnect_after, **options)
        else:
            if sys.platform != "win32":
                parser.error("LIVE is available only on Windows")
            if not 1 <= args.gateway_port <= 65535 or args.gateway_port == args.port:
                parser.error("Specify a valid gateway port distinct from the dashboard port")
            if not math.isfinite(args.stale_after) or args.stale_after <= 0:
                parser.error("--stale-after must be a positive finite number")
            stop_file = local_path(args.stop_file) if args.stop_file else None
            if stop_file and stop_file.exists():
                parser.error("The stop marker already exists")
            app = create_app(instrument=gc_contract(args.symbol, args.expiry),
                             live_gateway_port=args.gateway_port, stale_after=args.stale_after,
                             mbo_entitled=args.mbo_entitled, **options)
    except ValueError as exc:
        parser.error(str(exc))
    config = uvicorn.Config(
        app, host="127.0.0.1", port=args.port, ws="websockets-sansio",
        ws_max_size=64 * 1024, timeout_graceful_shutdown=2, proxy_headers=False,
    )
    if stop_file:
        asyncio.run(serve(config, stop_file))
    else:
        uvicorn.Server(config).run()


if __name__ == "__main__":
    main()
