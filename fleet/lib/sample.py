#!/usr/bin/env python3
"""Fleet telemetry sampler (stdlib only, runs on the hub from cron).
  sample.py            one snapshot of every GPU on every host -> data/samples-YYYY-MM.jsonl
  sample.py --net      also measure network throughput hub <-> each remote host -> data/net.jsonl
Each GPU record says who holds it: rig jobs, Ray, other processes of ours, or other users (aggregated, no names)."""
import json, os, subprocess, sys, threading, time
FLEET = os.path.expanduser("~/fleet"); EXP = f"{FLEET}/experience/data"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "LogLevel=ERROR"]
REMOTE = r'''
import json, os, subprocess, time
def q(args):
    try: return subprocess.run(["nvidia-smi", *args, "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=20).stdout.strip().splitlines()
    except Exception: return []
F = "index,uuid,name,utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu,power.draw,power.limit,clocks.sm,pstate,fan.speed"
gpus = []
for l in q(["--query-gpu=" + F]):
    v = [x.strip() for x in l.split(",")]
    def num(s):
        try: return float(s)
        except: return None
    gpus.append(dict(index=int(v[0]), uuid=v[1], name=v[2].replace("NVIDIA GeForce ", ""), util=num(v[3]), mem_util=num(v[4]),
                     mem_used=num(v[5]), mem_total=num(v[6]), temp=num(v[7]), power=num(v[8]), power_limit=num(v[9]), sm_clock=num(v[10]),
                     pstate=v[11], fan=num(v[12]), holders={}))
byuuid = {g["uuid"]: g for g in gpus}; me = os.getuid()
def classify(pid):
    try:
        uid = int([l for l in open(f"/proc/{pid}/status") if l.startswith("Uid:")][0].split()[1])
        cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace")
        env = open(f"/proc/{pid}/environ", "rb").read().split(b"\0") if uid == me else []
    except Exception: return "unknown"
    if uid != me: return "other_users"
    for e in env:
        if e.startswith(b"RIG_JOB="): return "rig:" + e[8:].decode(errors="replace")
    if "ray::" in cmd or "/ray/" in cmd or "raylet" in cmd: return "ray"
    if "rig-runs" in cmd: return "rig-task"
    return "own_other"
for l in q(["--query-compute-apps=pid,gpu_uuid,used_memory"]):
    v = [x.strip() for x in l.split(",")]
    if len(v) < 3 or v[1] not in byuuid: continue
    k = classify(v[0]); h = byuuid[v[1]]["holders"]; e = h.setdefault(k, {"procs": 0, "mem_mb": 0.0})
    e["procs"] += 1
    try: e["mem_mb"] += float(v[2])
    except: pass
for g in gpus: g.pop("uuid")
mi = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo") if l.split(":")[0] in ("MemTotal", "MemAvailable")}
st = os.statvfs(os.path.expanduser("~"))
try: tm = subprocess.run(["tmux", "list-sessions", "-F", "#S"], capture_output=True, text=True, timeout=5).stdout.split()
except Exception: tm = []
print(json.dumps(dict(host=os.uname().nodename, load=os.getloadavg(), ncpu=os.cpu_count(), ram_avail_gb=round(mi["MemAvailable"]/1048576, 1),
                      ram_total_gb=round(mi["MemTotal"]/1048576, 1), disk_free_gb=round(st.f_bavail*st.f_frsize/1e9, 1),
                      rig_jobs=[s[4:] for s in tm if s.startswith("rig-")], gpus=gpus)))
'''
def hosts():
    out = []
    for l in open(f"{FLEET}/hosts.conf"):
        p = l.split()
        if p and not p[0].startswith("#") and len(p) >= 2: out.append((p[0], p[1]))
    return out
def snap(name, alias, res):
    t0 = time.time()
    try:
        cmd = ["python3", "-"] if alias == "local" else SSH + [alias, "python3", "-"]
        p = subprocess.run(cmd, input=REMOTE, capture_output=True, text=True, timeout=40)
        rec = json.loads(p.stdout.strip().splitlines()[-1]); rec["ok"] = True
    except Exception as e:
        rec = {"host": name, "ok": False, "error": str(e)[-300:]}
    rec["name"] = name; rec["probe_s"] = round(time.time() - t0, 2); res.append(rec)
def net(name, alias, res, mb=128):
    blob = f"{EXP}/.netblob"
    if not os.path.exists(blob) or os.path.getsize(blob) != mb << 20:
        with open(blob, "wb") as f: f.write(os.urandom(mb << 20))
    r = {"host": name}
    try:
        rtts = []
        for _ in range(3):
            t0 = time.time(); subprocess.run(SSH + [alias, "true"], timeout=30, check=True); rtts.append(time.time() - t0)
        rtt = min(rtts); r["ssh_rtt_s"] = round(rtt, 3)              # session setup cost, subtracted below
        t0 = time.time(); subprocess.run(SSH + [alias, "cat > /dev/null"], stdin=open(blob, "rb"), timeout=120, check=True)
        r["up_MBps"] = round(mb * 1.048576 / max(time.time() - t0 - rtt, 1e-3), 1)          # 5090 -> host
        t0 = time.time(); p = subprocess.run(SSH + [alias, f"head -c {mb << 20} /dev/urandom"], capture_output=True, timeout=120, check=True)
        r["down_MBps"] = round(len(p.stdout) / 1e6 / max(time.time() - t0 - rtt, 1e-3), 1)   # host -> 5090
        ts = subprocess.run(["tailscale", "status"], capture_output=True, text=True, timeout=10).stdout
        line = [l for l in ts.splitlines() if f" {name} " in f" {l} "]
        r["path"] = ("direct" if "direct" in line[0] else "relay" if "relay" in line[0] else "idle") if line else "?"
        r["ok"] = True
    except Exception as e:
        r["ok"] = False; r["error"] = str(e)[-300:]
    res.append(r)
def main():
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z"); res = []
    th = [threading.Thread(target=snap, args=(n, a, res)) for n, a in hosts()]
    for t in th: t.start()
    for t in th: t.join()
    with open(f"{EXP}/samples-{time.strftime('%Y-%m')}.jsonl", "a") as f:
        for r in sorted(res, key=lambda r: r["name"]): r["ts"] = ts; f.write(json.dumps(r) + "\n")
    if "--net" in sys.argv:
        nres = []
        for n, a in hosts():
            if a != "local": net(n, a, nres)
        with open(f"{EXP}/net.jsonl", "a") as f:
            for r in nres: r["ts"] = ts; f.write(json.dumps(r) + "\n")
        if "-v" in sys.argv: print(json.dumps(nres, indent=1))
    if "-v" in sys.argv: print(json.dumps(res, indent=1)[:4000])
if __name__ == "__main__":
    main()
