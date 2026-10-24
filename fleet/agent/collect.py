#!/usr/bin/env python3
"""Fleet monitor collector: runs on a host (pushed to ~/rig-runs/.bin by `rig setup`) and reports its state.
`--json` prints one JSON object for the fleet GUI; otherwise plain text. Stdlib + nvidia-smi only."""
import json, os, re, subprocess, sys, time
from collections import OrderedDict
from pathlib import Path

HOME = Path.home()
RUNS = HOME / "rig-runs"


def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return ""


def gpus():
    out = sh("nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv,noheader,nounits")
    rows = []
    for row in out.strip().splitlines():
        try:
            i, name, util, used, total, temp = [c.strip() for c in row.split(",")]
            rows.append({"index": int(i), "name": name.replace("NVIDIA GeForce ", ""), "util": int(util),
                         "mem_used": int(used), "mem_total": int(total), "temp": int(temp)})
        except ValueError:
            continue
    return rows


def processes():
    """This user's GPU-relevant processes, grouped (a training's data-loader workers collapse into one row)."""
    out = sh(f"ps -u {os.getuid()} -o pid=,etime=,pcpu=,rss=,args=")
    groups = OrderedDict()
    for line in out.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        pid, etime, cpu, rss, args = parts
        if "collect.py" in args:
            continue
        if "dispatch.py" in args:
            m = re.search(r"(\S+\.json)", args); key = f"dispatcher: {os.path.basename(m.group(1)) if m else '?'}"
        elif "rig-runs/" in args and "runjob.sh" in args:
            key = "rig job: " + args.split("runjob.sh", 1)[1].strip()
        elif re.search(r"python[0-9.]* .*\.py", args) and "/env/" in args:
            m = re.search(r"(\S+\.py)", args); key = f"python: {os.path.basename(m.group(1))}"
        elif args.startswith(("blender", "ray::")) or "/blender" in args:
            key = args.split()[0].rsplit("/", 1)[-1]
        else:
            continue
        g = groups.setdefault(key, {"name": key, "pid": int(pid), "etime": etime, "n": 0, "cpu": 0.0, "rss_mb": 0.0})
        g["n"] += 1; g["cpu"] += float(cpu); g["rss_mb"] += int(rss) / 1024
    return list(groups.values())


def logs():
    """Log files the GUI may tail on this host (an allowlist: the server tails only paths listed here)."""
    found = []
    for p in RUNS.glob("*/stdout.log") if RUNS.exists() else []:
        try:
            st = p.stat()
        except OSError:
            continue
        found.append({"name": str(p.relative_to(HOME)), "path": str(p), "mtime": int(st.st_mtime), "size": st.st_size})
    found.sort(key=lambda r: -r["mtime"])
    return found[:60]


def fleet_jobs():
    """Jobs started with `rig run` on this host (~/rig-runs/<job>)."""
    jobs = []
    if not RUNS.exists():
        return jobs
    now = time.time()
    for d in sorted(RUNS.iterdir()):
        log = d / "stdout.log"
        if d.name.startswith(".") or not log.exists():
            continue
        tail = ""
        try:
            with open(log, "rb") as fh:
                fh.seek(max(0, log.stat().st_size - 400)); t = fh.read().decode(errors="ignore").strip().splitlines()[-1:]
                tail = t[0] if t else ""
        except OSError:
            pass
        rc = (d / "exit_code").read_text().strip() if (d / "exit_code").exists() else None
        jobs.append({"job": d.name, "state": f"exit {rc}" if rc is not None else ("running" if now - log.stat().st_mtime < 120 else "idle"),
                     "age_s": int(now - log.stat().st_mtime), "last": tail[:140]})
    jobs.sort(key=lambda j: j["age_s"])
    return jobs[:30]


def snapshot():
    disk = sh("df -h ~ | tail -n 1").split()
    return {"host": os.uname().nodename, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "epoch": int(time.time()),
            "load": [f"{x:.2f}" for x in os.getloadavg()], "disk_free": disk[3] if len(disk) > 3 else "?",
            "gpus": gpus(), "processes": processes(), "logs": logs(), "fleet_jobs": fleet_jobs()}


def text(s):
    out = [f"== {s['host']:<10} {s['time'][11:]}   load {' '.join(s['load'])}   disk free {s['disk_free']}"]
    for g in s["gpus"]:
        bar = int(round(g["util"] / 10))
        out.append(f"  GPU{g['index']} {g['name']:<14} [{'#' * bar}{'.' * (10 - bar)}] {g['util']:>3} %   {g['mem_used']:>6} / {g['mem_total']:<6} MiB  {g['temp']}°C")
    out += [f"  {p['name']:<50} up {p['etime']:>11}  {p['n']:>2} proc  cpu {p['cpu']:5.0f}%  rss {p['rss_mb']:6.0f} MB" for p in s["processes"]] or ["  idle"]
    return "\n".join(out)


if __name__ == "__main__":
    s = snapshot()
    print(json.dumps(s) if "--json" in sys.argv else text(s))
