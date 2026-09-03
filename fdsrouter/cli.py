from __future__ import annotations

import argparse
import threading
import webbrowser
from pathlib import Path

import uvicorn

from fdsrouter.api.app import create_app
from fdsrouter.config import load_config


def start(args: argparse.Namespace) -> None:
    config = load_config(Path.cwd())
    if args.port is not None:
        config.port = args.port
    if args.no_browser:
        config.open_browser = False

    app = create_app(config)

    if config.open_browser:
        url = f"http://{config.host}:{config.port}/"
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    uvicorn.run(app, host=config.host, port=config.port)


def main() -> None:
    parser = argparse.ArgumentParser(prog="fdsrouter")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start", help="FDSRouter starten")
    start_parser.add_argument("--port", type=int, default=None)
    start_parser.add_argument("--no-browser", action="store_true")
    start_parser.set_defaults(func=start)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
