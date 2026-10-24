"""Thread-safe, in-memory view of the fleet that the web server publishes."""

from __future__ import annotations

import collections
import dataclasses
import threading
import time
from typing import Any

# Utilization samples kept per GPU for the page's sparklines.
HISTORY_LEN = 40

Snapshot = dict[str, Any]


@dataclasses.dataclass(frozen=True)
class Host:
    """One machine from hosts.conf.

    Attributes:
        name: Display name, the first column of hosts.conf.
        alias: ssh alias used to reach the machine.
        is_hub: True for the hub ("local" in hosts.conf).
    """

    name: str
    alias: str
    is_hub: bool = False


class FleetState:
    """Latest snapshot per machine plus a short utilization history per GPU.

    The poller writes and the HTTP handlers read from different threads, so
    every public method takes the lock.
    """

    def __init__(
        self,
        poll_interval_s: int,
        demo: bool = False,
        history_len: int = HISTORY_LEN,
    ) -> None:
        self.poll_interval_s = poll_interval_s
        self.demo = demo
        self._history_len = history_len
        self._lock = threading.Lock()
        self._hosts: list[Host] = []
        self._machines: dict[str, dict[str, Any]] = {}
        self._history: dict[str, collections.deque[list[int]]] = {}
        self._last_poll: int | None = None

    def set_hosts(self, hosts: list[Host]) -> None:
        """Replaces the list of machines the page shows, in display order."""
        with self._lock:
            self._hosts = list(hosts)

    def record_poll(
        self,
        host: Host,
        snapshot: Snapshot | None,
        error: str | None = None,
        rtt_s: float = 0.0,
        fetched: int | None = None,
    ) -> None:
        """Stores the result of polling one machine.

        A failed poll keeps the machine's last good snapshot, flagged offline,
        so the page can still show what the machine was doing.

        Args:
            host: The machine that was polled.
            snapshot: The collector's output, or None if the poll failed.
            error: Why the poll failed; shown on the machine's card.
            rtt_s: How long the poll took, in seconds.
            fetched: Unix time of the poll. Defaults to now.
        """
        fetched = int(time.time()) if fetched is None else fetched
        entry: dict[str, Any] = {
            "name": host.name,
            "alias": host.alias,
            "hub": host.is_hub,
            "fetched": fetched,
            "rtt_s": round(rtt_s, 1),
            "online": snapshot is not None,
        }
        with self._lock:
            previous = self._machines.get(host.name, {})
            if snapshot is not None:
                entry["snapshot"] = snapshot
                entry["last_online"] = fetched
                self._remember(host.name, snapshot, fetched)
            else:
                entry["error"] = error or "unreachable"
                if "snapshot" in previous:
                    entry["snapshot"] = previous["snapshot"]
                    entry["last_online"] = previous.get(
                        "last_online", previous["fetched"])
            self._machines[host.name] = entry

    def add_sample(self, key: str, timestamp: int, util: int) -> None:
        """Appends one utilization sample for the GPU "<machine>:<index>"."""
        with self._lock:
            self._append_sample(key, timestamp, util)

    def mark_polled(self, timestamp: int | None = None) -> None:
        """Records that a full poll of the fleet finished."""
        with self._lock:
            self._last_poll = (
                int(time.time()) if timestamp is None else timestamp)

    def log_target(self, name: str) -> tuple[Host | None, frozenset[str]]:
        """Returns a machine and the log paths its collector allows tailing.

        Args:
            name: Machine name as shown on the page.

        Returns:
            The machine (None if unknown) and the set of tailable paths.
        """
        with self._lock:
            entry = self._machines.get(name)
            if entry is None:
                return None, frozenset()
            logs = entry.get("snapshot", {}).get("logs", [])
            host = Host(entry["name"], entry["alias"], entry["hub"])
            return host, frozenset(log["path"] for log in logs)

    def to_json(self) -> dict[str, Any]:
        """Returns the body of /api/state."""
        with self._lock:
            return {
                "now": int(time.time()),
                "last_poll": self._last_poll,
                "poll_interval": self.poll_interval_s,
                "demo": self.demo,
                "history_len": self._history_len,
                "hosts": [host.name for host in self._hosts],
                "rigs": dict(self._machines),
                "hist": {
                    key: list(samples)
                    for key, samples in self._history.items()
                },
            }

    def _remember(self, name: str, snapshot: Snapshot, timestamp: int) -> None:
        for gpu in snapshot.get("gpus", []):
            key = f"{name}:{gpu['index']}"
            self._append_sample(key, timestamp, gpu["util"])

    def _append_sample(self, key: str, timestamp: int, util: int) -> None:
        samples = self._history.setdefault(
            key, collections.deque(maxlen=self._history_len))
        if not samples or samples[-1][0] != timestamp:
            samples.append([timestamp, util])
