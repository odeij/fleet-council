"""Where snapshots come from: the real fleet over ssh, or a simulated one."""

from __future__ import annotations

import dataclasses
import json
import random
import re
import shlex
import subprocess
import time
from typing import Protocol

from fleet_gui import state

DEFAULT_COLLECT_COMMAND = "python3 rig-runs/.bin/collect.py --json"

_SSH_OPTIONS = (
    "-o", "ConnectTimeout=10",
    "-o", "BatchMode=yes",
    "-o", "ServerAliveInterval=10",
)
# Aliases go to ssh as an argument, never through a local shell, but one that
# starts with "-" would still be parsed as an ssh option.
_ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]*$")
_POLL_TIMEOUT_S = 40
_HOSTS_TIMEOUT_S = 20
_TAIL_TIMEOUT_S = 25
_MAX_ERROR_CHARS = 300


@dataclasses.dataclass(frozen=True)
class PollResult:
    """Outcome of polling one machine.

    Attributes:
        snapshot: The collector's JSON output, or None if the poll failed.
        error: Why the poll failed, if it did.
        rtt_s: Wall-clock time the poll took, in seconds.
    """

    snapshot: state.Snapshot | None
    error: str | None = None
    rtt_s: float = 0.0


class Source(Protocol):
    """Interface shared by the real and the simulated fleet."""

    def discover(self) -> list[state.Host]:
        """Returns the machines to poll, in display order."""

    def poll(self, host: state.Host) -> PollResult:
        """Collects one snapshot from a machine."""

    def tail(self, host: state.Host, path: str, lines: int) -> tuple[bool, str]:
        """Returns (ok, text): the last lines of a log, or the error."""


def run_ssh(alias: str, command: str, timeout_s: float) -> tuple[int, str, str]:
    """Runs a command on a machine and returns (exit code, stdout, stderr)."""
    try:
        proc = subprocess.run(
            ["ssh", *_SSH_OPTIONS, alias, command],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, "", "ssh timed out"
    except OSError as e:
        return 1, "", str(e)
    return proc.returncode, proc.stdout, proc.stderr


def parse_hosts_conf(text: str, hub_alias: str) -> list[state.Host]:
    """Parses hosts.conf: "name  ssh-alias  [gpu indices]" per line.

    Args:
        text: File contents. "#" starts a comment.
        hub_alias: ssh alias of the hub, substituted for the alias "local".

    Returns:
        The machines in file order. Lines with an unsafe alias are skipped.
    """
    hosts = []
    for line in text.splitlines():
        fields = line.split("#", 1)[0].split()
        if len(fields) < 2:
            continue
        name, alias = fields[0], fields[1]
        is_hub = alias == "local"
        if is_hub:
            alias = hub_alias
        if _ALIAS_PATTERN.match(alias):
            hosts.append(state.Host(name, alias, is_hub))
    return hosts


class SshFleet:
    """The real fleet: machine list from the hub, snapshots over ssh."""

    def __init__(
        self,
        hub_alias: str,
        collect_command: str = DEFAULT_COLLECT_COMMAND,
    ) -> None:
        self._hub_alias = hub_alias
        self._collect_command = collect_command
        self._known_hosts = [state.Host(hub_alias, hub_alias, is_hub=True)]

    def discover(self) -> list[state.Host]:
        """Re-reads the hub's ~/fleet/hosts.conf, so new machines show up.

        Falls back to the last list that could be read.
        """
        code, out, _ = run_ssh(
            self._hub_alias, "cat ~/fleet/hosts.conf", _HOSTS_TIMEOUT_S)
        hosts = parse_hosts_conf(out, self._hub_alias) if code == 0 else []
        if hosts:
            self._known_hosts = hosts
        return list(self._known_hosts)

    def poll(self, host: state.Host) -> PollResult:
        start = time.monotonic()
        code, out, err = run_ssh(
            host.alias, self._collect_command, _POLL_TIMEOUT_S)
        rtt_s = time.monotonic() - start
        if code != 0:
            error = err.strip() or f"ssh exit {code}"
            return PollResult(None, error[-_MAX_ERROR_CHARS:], rtt_s)
        try:
            snapshot = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError) as e:
            return PollResult(None, f"bad collector output: {e}", rtt_s)
        return PollResult(snapshot, None, rtt_s)

    def tail(self, host: state.Host, path: str, lines: int) -> tuple[bool, str]:
        command = f"tail -n {int(lines)} {shlex.quote(path)}"
        code, out, err = run_ssh(host.alias, command, _TAIL_TIMEOUT_S)
        return (True, out) if code == 0 else (False, err)


# (name, is_hub, ((gpu model, memory MiB), ...)) for the simulated fleet.
_DEMO_MACHINES = (
    ("hub", True, (("RTX 5090", 32607),)),
    ("gpu-box-a", False, (("RTX 2080 SUPER", 8192), ("RTX 2080 SUPER", 8192))),
    ("gpu-box-b", False, (("RTX 2080 Ti", 11264),)),
)
# The first job per GPU is running; any further job has finished.
_DEMO_JOBS = {
    "hub": ("train-vit-s", "dispatch-tiles"),
    "gpu-box-a": ("sweep-lr-3e-4", "sweep-lr-1e-4"),
    "gpu-box-b": ("eval-ckpt-12",),
}


class DemoFleet:
    """A simulated three-machine fleet, so the page can be tried without GPUs.

    Utilization follows a bounded random walk per GPU, and everything else is
    derived from it.
    """

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        self._util: dict[str, float] = {}

    def discover(self) -> list[state.Host]:
        return [state.Host(name, name, is_hub) for name, is_hub, _ in
                _DEMO_MACHINES]

    def poll(self, host: state.Host) -> PollResult:
        return PollResult(self._snapshot(host.name), None, rtt_s=0.3)

    def tail(self, host: state.Host, path: str, lines: int) -> tuple[bool, str]:
        del host, path  # Every demo log reads the same.
        rows = [
            f"epoch {epoch}/50  loss {0.9 - epoch * 0.012:.3f}  "
            f"val_acc {60 + epoch * 0.6:.1f}%"
            for epoch in range(1, 41)
        ]
        return True, "\n".join(rows[-lines:])

    def preroll(
        self,
        fleet_state: state.FleetState,
        samples: int,
        step_s: int,
    ) -> None:
        """Fills the utilization history so sparklines show on first load."""
        start = int(time.time()) - samples * step_s
        for sample in range(samples):
            for name, _, gpus in _DEMO_MACHINES:
                for index in range(len(gpus)):
                    key = f"{name}:{index}"
                    fleet_state.add_sample(
                        key, start + sample * step_s, self._step(key))

    def _step(self, key: str) -> int:
        util = self._util.get(key, self._rng.uniform(55, 95))
        util = min(100.0, max(20.0, util + self._rng.uniform(-9, 9)))
        self._util[key] = util
        return int(util)

    def _snapshot(self, name: str) -> state.Snapshot:
        now = int(time.time())
        cards = next(gpus for host, _, gpus in _DEMO_MACHINES if host == name)
        gpus = []
        for index, (model, total_mib) in enumerate(cards):
            util = self._step(f"{name}:{index}")
            gpus.append({
                "index": index,
                "name": model,
                "util": util,
                "mem_used": int(total_mib * (0.45 + util / 250)),
                "mem_total": total_mib,
                "temp": int(48 + util * 0.3),
            })
        jobs = []
        for i, job in enumerate(_DEMO_JOBS[name]):
            running = i < len(cards)
            jobs.append({
                "job": job,
                "state": "running" if running else "exit 0",
                "age_s": 4 if running else 3600 + 600 * i,
                "last": (
                    f"epoch {self._rng.randint(3, 40)}/50  "
                    f"loss {self._rng.uniform(0.2, 0.9):.3f}  "
                    f"{self._rng.randint(900, 2400)} img/s"),
            })
        return {
            "host": name,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "epoch": now,
            "load": [f"{self._rng.uniform(2, 9):.2f}"] * 3,
            "disk_free": f"{self._rng.choice((412, 96, 38))}G",
            "gpus": gpus,
            "fleet_jobs": jobs,
            "processes": [
                {"name": f"rig job: {job['job']}", "pid": 4000 + i,
                 "etime": "01:12:09", "n": 5, "cpu": 340.0, "rss_mb": 6100.0}
                for i, job in enumerate(jobs) if job["state"] == "running"
            ],
            "logs": [
                {"name": f"rig-runs/{job['job']}/stdout.log",
                 "path": f"/demo/{job['job']}", "mtime": now, "size": 20000}
                for job in jobs
            ],
        }
