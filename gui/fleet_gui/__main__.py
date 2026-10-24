"""Command line entry point.

Usage (from the gui/ directory):
    python3 -m fleet_gui                  # poll the real fleet
    python3 -m fleet_gui --demo           # simulated fleet, no ssh
    python3 -m fleet_gui --port 8787 --interval 20 --hub hub
"""

from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Sequence

from fleet_gui import poller
from fleet_gui import sources
from fleet_gui import state
from fleet_gui import web

_DEFAULT_PORT = 8787
_DEFAULT_INTERVAL_S = 20
_DEMO_INTERVAL_S = 5


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="fleet_gui",
        description="Read-only web dashboard for every GPU in the fleet.")
    parser.add_argument(
        "--port", type=int, default=_DEFAULT_PORT,
        help="port on 127.0.0.1 (default: %(default)s)")
    parser.add_argument(
        "--interval", type=int, default=_DEFAULT_INTERVAL_S,
        help="seconds between polls of the fleet (default: %(default)s)")
    parser.add_argument(
        "--hub", default=os.environ.get("FLEET_HUB", "hub"),
        help="ssh alias of the hub that holds ~/fleet/hosts.conf "
             "(env FLEET_HUB, default: %(default)s)")
    parser.add_argument(
        "--collect",
        default=os.environ.get(
            "FLEET_COLLECT", sources.DEFAULT_COLLECT_COMMAND),
        help="collector command run on each machine (env FLEET_COLLECT)")
    parser.add_argument(
        "--demo", action="store_true",
        help="serve a simulated fleet instead of polling over ssh")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    source: sources.Source
    if args.demo:
        interval_s = _DEMO_INTERVAL_S
        fleet_state = state.FleetState(interval_s, demo=True)
        demo = sources.DemoFleet()
        demo.preroll(fleet_state, state.HISTORY_LEN, interval_s)
        source = demo
    else:
        interval_s = args.interval
        fleet_state = state.FleetState(interval_s)
        source = sources.SshFleet(args.hub, args.collect)

    fleet_poller = poller.Poller(source, fleet_state, interval_s)
    fleet_poller.start()
    server = web.make_server(args.port, fleet_state, source, fleet_poller)
    logging.info(
        "fleet GUI on http://127.0.0.1:%d (%s, polling every %d s; "
        "Ctrl-C to stop)",
        args.port, "demo data" if args.demo else f"hub {args.hub}", interval_s)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
