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


def tray(args: argparse.Namespace) -> None:
    from fdsrouter import tray as tray_module

    raise SystemExit(tray_module.main(base_url=args.url, project_dir=Path.cwd()))


def agent(args: argparse.Namespace) -> None:
    import asyncio
    import logging

    from fdsrouter import agent as agent_module

    # Unlike `start` (uvicorn configures its own logging), nothing sets up a handler here by
    # default -- without this, the agent runs completely silently on the console, including its
    # own errors, which makes a connectivity problem invisible until someone checks the
    # Controller for a stuck job.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx logs one line per request at INFO -- with a 1-2s assignment poll that's a request
    # every couple seconds even while idle, drowning out anything FDSRouter itself logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    config = agent_module.load_agent_config(Path.cwd())
    if args.controller_url is not None:
        config.controller_url = args.controller_url

    # An empty cluster_token means this agent-config.yaml has never been paired -- walk through
    # LAN discovery + token entry once and save the result, rather than making the operator find
    # the Controller's address and edit YAML by hand. --pair repeats this even when already
    # paired (e.g. the Controller's address changed).
    if args.pair or not config.cluster_token:
        _pair_with_controller(config, skip_discovery=args.controller_url is not None)

    print(f"FDSRouter-Agent: verbinde mit {config.controller_url} ...")
    asyncio.run(agent_module.run(config))


def _pair_with_controller(config, skip_discovery: bool) -> None:
    """Interactive first-run setup: find the Controller on the LAN (unless an address was
    already given explicitly) and confirm the pairing with its cluster_token, verified against
    the real /api/agent/register endpoint before it's saved -- a wrong token is caught here,
    not three retries into a background poll loop with no one watching."""
    import httpx

    from fdsrouter import agent as agent_module
    from fdsrouter.core import discovery

    print("\n--- FDSRouter-Agent Einrichtung ---")

    if not skip_discovery:
        print("Suche FDSRouter-Controller im Netzwerk ...")
        found = discovery.discover_controllers()
        if found:
            print("Gefunden:")
            for i, c in enumerate(found, 1):
                print(f"  [{i}] {c.hostname}  ({c.host}:{c.port})")
            choice = input(f"Auswahl, oder Adresse manuell eingeben [1]: ").strip() or "1"
            if choice.isdigit() and 1 <= int(choice) <= len(found):
                chosen = found[int(choice) - 1]
                config.controller_url = f"http://{chosen.host}:{chosen.port}"
            else:
                config.controller_url = choice
        else:
            print("Kein Controller im Netzwerk gefunden (Discovery evtl. dort deaktiviert oder anderes Subnetz).")
            config.controller_url = (
                input(f"Controller-Adresse [{config.controller_url}]: ").strip() or config.controller_url
            )

    print(f"Controller: {config.controller_url}")
    print("Das Cluster-Token steht auf dem Controller unter 'Betrieb' -> 'Cluster'.")

    node_id = agent_module.local_node_id(config)
    payload = agent_module.registration_payload(config, node_id)
    for _ in range(3):
        token = input("Cluster-Token: ").strip()
        if not token:
            continue
        try:
            resp = httpx.post(
                f"{config.controller_url.rstrip('/')}/api/agent/register",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
                timeout=5.0,
            )
        except httpx.HTTPError as exc:
            print(f"Verbindung zum Controller fehlgeschlagen: {exc}")
            continue
        if resp.status_code == 401:
            print("Token wurde vom Controller abgelehnt -- bitte erneut versuchen.")
            continue
        resp.raise_for_status()
        config.cluster_token = token
        break
    else:
        raise SystemExit("Einrichtung abgebrochen: kein gültiges Token erhalten.")

    agent_module.save_agent_config(config)
    print("Verbunden und gespeichert -- weitere Starts fragen nicht erneut.\n")


def main() -> None:
    parser = argparse.ArgumentParser(prog="fdsrouter")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start", help="FDSRouter starten")
    start_parser.add_argument("--port", type=int, default=None)
    start_parser.add_argument("--no-browser", action="store_true")
    start_parser.set_defaults(func=start)

    tray_parser = subparsers.add_parser("tray", help="Tray-Icon im Desktop starten")
    tray_parser.add_argument("--url", default=None, help="z. B. http://127.0.0.1:8000")
    tray_parser.set_defaults(func=tray)

    agent_parser = subparsers.add_parser(
        "agent", help="Als Compute-Node an einen FDSRouter-Controller anbinden"
    )
    agent_parser.add_argument("--controller-url", default=None, help="z. B. http://192.168.1.10:8000")
    agent_parser.add_argument(
        "--pair", action="store_true",
        help="Netzwerksuche nach dem Controller und Token-Eingabe erneut durchlaufen, auch wenn bereits eingerichtet",
    )
    agent_parser.set_defaults(func=agent)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
