"""Run the synthetic application on loopback."""

import argparse
import math

import uvicorn

from .web import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="GC Live Observatory (synthetic market data)")
    subcommands = parser.add_subparsers(dest="command", required=True)
    mock = subcommands.add_parser("mock", help="run a local synthetic gateway and dashboard")
    mock.add_argument("--port", default=8000, type=int, help="loopback dashboard port")
    mock.add_argument("--interval", default=0.16, type=float, help="seconds between synthetic trades")
    mock.add_argument("--disconnect-after", type=int, help="force one synthetic socket loss after N trades")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or args.port == 9001:
        parser.error("--port must be between 1 and 65535 and cannot be 9001")
    if not math.isfinite(args.interval) or args.interval <= 0:
        parser.error("--interval must be a positive finite number")
    if args.disconnect_after is not None and args.disconnect_after < 1:
        parser.error("--disconnect-after must be positive")
    app = create_app(interval=args.interval, disconnect_after=args.disconnect_after)
    uvicorn.run(
        app, host="127.0.0.1", port=args.port, ws="websockets-sansio",
        ws_max_size=64 * 1024, timeout_graceful_shutdown=2,
    )


if __name__ == "__main__":
    main()
