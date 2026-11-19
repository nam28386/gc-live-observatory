"""Run the local observatory."""

import argparse
import asyncio
import math
from pathlib import Path
import sys

import uvicorn

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
    for command in (mock, live):
        command.add_argument("--port", default=8000, type=int)
        command.add_argument("--capture", type=Path, help="opt-in journal database outside all Git checkouts")
        command.add_argument("--retention-days", type=int, default=7)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or args.port == 9001:
        parser.error("--port must be between 1 and 65535 and cannot be 9001")
    if args.retention_days < 1:
        parser.error("--retention-days must be positive")
    try:
        capture = local_path(args.capture) if args.capture else None
        options = dict(capture_path=capture, retention_days=args.retention_days)
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
                             live_gateway_port=args.gateway_port, stale_after=args.stale_after, **options)
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
