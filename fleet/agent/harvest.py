#!/usr/bin/env python3
"""Emit one JSON line per finished rig job under ~/rig-runs (runs on each host; read by `rig learn`)."""
import csv, glob, json, os, re
H = os.path.expanduser("~/rig-runs"); host = os.uname().nodename
def rd(p, n=None):
    try:
        s = open(p, errors="replace").read(); return s if n is None else "\n".join(s.splitlines()[-n:])
    except Exception: return None
ERR = [("oom", r"CUDA out of memory|OutOfMemoryError|cudaErrorMemoryAllocation"), ("killed", r"\bKilled\b|SIGKILL|exit 137"),
       ("module", r"ModuleNotFoundError|ImportError"), ("file", r"FileNotFoundError|No such file"), ("nccl", r"NCCL"),
       ("cuda", r"CUDA error|cudaError|device-side assert"), ("python", r"Traceback \(most recent call last\)")]
for d in sorted(glob.glob(f"{H}/*/")):
    job = os.path.basename(d.rstrip("/"))
    if job.startswith(".") or not os.path.exists(d + "exit_code"): continue
    rec = dict(host=host, job=job, cmd=(rd(d + "cmd.sh") or "").strip()[:500], started=(rd(d + "started_at") or "").strip(),
               finished=(rd(d + "finished_at") or "").strip(), exit=int((rd(d + "exit_code") or "-1").strip() or -1))
    env = rd(d + "env.sh") or ""; m = re.search(r"CUDA_VISIBLE_DEVICES=(\S+)", env); rec["cvd"] = m.group(1) if m else None
    log = rd(d + "stdout.log") or ""
    rec["log_lines"] = log.count("\n")
    if rec["exit"] != 0:
        rec["error_class"] = next((k for k, pat in ERR if re.search(pat, log)), "exit_%d" % rec["exit"])
        rec["error_tail"] = "\n".join(log.splitlines()[-12:])[-1500:]
    tr = {}
    if os.path.exists(d + "gpu_trace.csv"):
        for row in csv.reader(open(d + "gpu_trace.csv")):
            try:
                i = int(row[1]); u, mu, mt, te, pw = (float(row[k]) for k in (2, 3, 4, 5, 6))
            except Exception: continue
            g = tr.setdefault(i, dict(n=0, util_sum=0.0, util_max=0.0, mem_max=0.0, mem_total=mt, temp_max=0.0, power_max=0.0))
            g["n"] += 1; g["util_sum"] += u; g["util_max"] = max(g["util_max"], u); g["mem_max"] = max(g["mem_max"], mu)
            g["temp_max"] = max(g["temp_max"], te); g["power_max"] = max(g["power_max"], pw)
        for g in tr.values(): g["util_mean"] = round(g.pop("util_sum") / max(g["n"], 1), 1)
    rec["gpu_trace"] = {str(k): v for k, v in tr.items()}
    print(json.dumps(rec))
