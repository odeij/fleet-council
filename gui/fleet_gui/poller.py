"""Background polling of every machine in the fleet."""

from __future__ import annotations

import concurrent.futures
import logging
import threading

from fleet_gui import sources
from fleet_gui import state


def poll_once(source: sources.Source, fleet_state: state.FleetState) -> None:
    """Polls every machine in parallel and stores the results."""
    hosts = source.discover()
    fleet_state.set_hosts(hosts)
    if hosts:
        with concurrent.futures.ThreadPoolExecutor(len(hosts)) as pool:
            for host, result in zip(hosts, pool.map(source.poll, hosts)):
                fleet_state.record_poll(
                    host, result.snapshot, result.error, result.rtt_s)
    fleet_state.mark_polled()


class Poller:
    """Polls the fleet on a fixed interval in a daemon thread."""

    def __init__(
        self,
        source: sources.Source,
        fleet_state: state.FleetState,
        interval_s: float,
    ) -> None:
        self._source = source
        self._state = fleet_state
        self._interval_s = interval_s
        self._wake = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, name="poller", daemon=True).start()

    def refresh(self) -> None:
        """Starts the next poll now instead of at the end of the interval."""
        self._wake.set()

    def _run(self) -> None:
        while True:
            try:
                poll_once(self._source, self._state)
            except Exception:  # pylint: disable=broad-except
                # One bad poll must not stop the dashboard.
                logging.exception("fleet poll failed")
            self._wake.wait(self._interval_s)
            self._wake.clear()
