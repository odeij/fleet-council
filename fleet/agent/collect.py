#!/usr/bin/env python3
"""Fleet collector: reports one machine's GPUs, processes, jobs and logs.

`rig setup` pushes this file to ~/rig-runs/.bin on every machine, and the
fleet GUI runs it over ssh:

    python3 ~/rig-runs/.bin/collect.py --json   # one JSON object (the GUI)
    python3 ~/rig-runs/.bin/collect.py          # human-readable summary

Standard library and nvidia-smi only. Never raises: a missing tool just
yields an empty section.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import time
from typing import Any

_HOME = pathlib.Path.home()
_RUNS = _HOME / "rig-runs"
_COMMAND_TIMEOUT_S = 20
_RUNNING_IF_LOG_NEWER_THAN_S = 120
_MAX_LOGS = 60
_MAX_JOBS = 30
_TAIL_BYTES = 400
_MAX_LINE_CHARS = 140
_GPU_FIELDS = ("index", "name", "utilization.gpu", "memory.used",
               "memory.total", "temperature.gpu")


def _run(args: list[str]) -> str:
    """Returns a command's stdout, or "" if it is missing or fails."""
    try:
        return subprocess.run(args, capture_output=True, text=True,
                              timeout=_COMMAND_TIMEOUT_S, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def gpus() -> list[dict[str, Any]]:
    """Every NVIDIA GPU: utilization %, memory MiB, temperature in °C."""
    out = _run(["nvidia-smi", f"--query-gpu={','.join(_GPU_FIELDS)}",
                "--format=csv,noheader,nounits"])
    rows = []
    for line in out.strip().splitlines():
        try:
            index, name, util, used, total, temp = (
                field.strip() for field in line.split(","))
            rows.append({
                "index": int(index),
                "name": name.replace("NVIDIA GeForce ", ""),
                "util": int(util),
                "mem_used": int(used),
                "mem_total": int(total),
                "temp": int(temp),
            })
        except ValueError:
            continue  # "[N/A]" fields on some cards.
    return rows


def _process_group(args: str) -> str | None:
    """Names the group a process belongs to, or None to leave it out."""
    if "collect.py" in args:
        return None
    if "dispatch.py" in args:
        manifest = re.search(r"(\S+\.json)", args)
        name = os.path.basename(manifest.group(1)) if manifest else "?"
        return f"dispatcher: {name}"
    if "rig-runs/" in args and "runjob.sh" in args:
        return "rig job: " + args.split("runjob.sh", 1)[1].strip()
    if re.search(r"python[0-9.]* .*\.py", args) and "/env/" in args:
        script = re.search(r"(\S+\.py)", args)
        return f"python: {os.path.basename(script.group(1))}"
    if args.startswith(("blender", "ray::")) or "/blender" in args:
        return args.split()[0].rsplit("/", 1)[-1]
    return None


def processes() -> list[dict[str, Any]]:
    """This user's fleet processes, grouped.

    A training run's data-loader workers collapse into one row with a count
    and summed CPU and memory.
    """
    out = _run(["ps", "-u", str(os.getuid()),
                "-o", "pid=,etime=,pcpu=,rss=,args="])
    groups: dict[str, dict[str, Any]] = {}
    for line in out.splitlines():
        fields = line.split(None, 4)
        if len(fields) < 5:
            continue
        pid, etime, cpu, rss_kb, args = fields
        key = _process_group(args)
        if key is None:
            continue
        group = groups.setdefault(key, {
            "name": key, "pid": int(pid), "etime": etime,
            "n": 0, "cpu": 0.0, "rss_mb": 0.0})
        group["n"] += 1
        group["cpu"] += float(cpu)
        group["rss_mb"] += int(rss_kb) / 1024
    return list(groups.values())


def logs() -> list[dict[str, Any]]:
    """Logs the GUI may tail, newest first.

    This is the allowlist: the GUI server only tails paths listed here.
    """
    found = []
    for path in _RUNS.glob("*/stdout.log"):
        try:
            stat = path.stat()
        except OSError:
            continue
        found.append({"name": str(path.relative_to(_HOME)), "path": str(path),
                      "mtime": int(stat.st_mtime), "size": stat.st_size})
    found.sort(key=lambda log: -log["mtime"])
    return found[:_MAX_LOGS]


def _last_line(path: pathlib.Path) -> str:
    try:
        with path.open("rb") as f:
            f.seek(max(0, path.stat().st_size - _TAIL_BYTES))
            lines = f.read().decode(errors="ignore").strip().splitlines()
    except OSError:
        return ""
    return lines[-1][:_MAX_LINE_CHARS] if lines else ""


def fleet_jobs() -> list[dict[str, Any]]:
    """Jobs started with `rig run` (~/rig-runs/<job>), most recent first.

    State is "exit <code>" once the job wrote its exit code, "running" while
    its log is still growing, and "idle" otherwise.
    """
    if not _RUNS.is_dir():
        return []
    now = time.time()
    jobs = []
    for job_dir in _RUNS.iterdir():
        log = job_dir / "stdout.log"
        if job_dir.name.startswith(".") or not log.exists():
            continue
        age_s = int(now - log.stat().st_mtime)
        exit_file = job_dir / "exit_code"
        if exit_file.exists():
            job_state = f"exit {exit_file.read_text().strip()}"
        elif age_s < _RUNNING_IF_LOG_NEWER_THAN_S:
            job_state = "running"
        else:
            job_state = "idle"
        jobs.append({"job": job_dir.name, "state": job_state, "age_s": age_s,
                     "last": _last_line(log)})
    jobs.sort(key=lambda job: job["age_s"])
    return jobs[:_MAX_JOBS]


def snapshot() -> dict[str, Any]:
    """Everything the GUI shows for this machine."""
    disk = _run(["df", "-h", str(_HOME)]).strip().splitlines()[-1:]
    disk_fields = disk[0].split() if disk else []
    return {
        "host": os.uname().nodename,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "epoch": int(time.time()),
        "load": [f"{load:.2f}" for load in os.getloadavg()],
        "disk_free": disk_fields[3] if len(disk_fields) > 3 else "?",
        "gpus": gpus(),
        "processes": processes(),
        "logs": logs(),
        "fleet_jobs": fleet_jobs(),
    }


def as_text(snap: dict[str, Any]) -> str:
    """Formats a snapshot for a terminal."""
    lines = [f"== {snap['host']:<10} {snap['time'][11:]}   "
             f"load {' '.join(snap['load'])}   disk free {snap['disk_free']}"]
    for gpu in snap["gpus"]:
        bar = round(gpu["util"] / 10)
        lines.append(
            f"  GPU{gpu['index']} {gpu['name']:<14} "
            f"[{'#' * bar}{'.' * (10 - bar)}] {gpu['util']:>3} %   "
            f"{gpu['mem_used']:>6} / {gpu['mem_total']:<6} MiB  "
            f"{gpu['temp']} °C")
    for proc in snap["processes"]:
        lines.append(
            f"  {proc['name']:<50} up {proc['etime']:>11}  "
            f"{proc['n']:>2} proc  cpu {proc['cpu']:5.0f}%  "
            f"rss {proc['rss_mb']:6.0f} MB")
    if not snap["processes"]:
        lines.append("  idle")
    return "\n".join(lines)


def main() -> None:
    snap = snapshot()
    print(json.dumps(snap) if "--json" in sys.argv[1:] else as_text(snap))


if __name__ == "__main__":
    main()
