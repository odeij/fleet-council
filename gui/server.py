#!/usr/bin/env python3
"""Fleet GUI server (controller): polls every rig in the fleet over ssh (~/rig-runs/.bin/collect.py --json on each host),
keeps the latest snapshot per rig, and serves the page + a small JSON API on http://127.0.0.1:8787.
    python3 gui/server.py [--port 8787] [--interval 20] [--demo]
--demo serves a simulated three-machine fleet (badged "Demo data" in the page) so the UI can be tried without a cluster.
FLEET_COLLECT overrides the remote collector command (default: python3 rig-runs/.bin/collect.py --json).
The rig list comes from the hub's ~/fleet/hosts.conf (name  ssh-alias; "local" = the hub itself), re-read every few
minutes, so a rig added to the fleet appears by itself; a rig that does not answer shows as offline. Only the paths the
collector listed for a rig can be tailed through /api/log (no arbitrary remote commands). Stdlib only."""
import argparse
import json
import os
import random
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
HUB_ALIAS = os.environ.get("FLEET_HUB", "hub")   # ssh alias of the hub
SSH = ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=10"]
STATE = {"rigs": {}, "hosts": [], "started": int(time.time()), "poll_interval": 20, "last_poll": None, "demo": False, "hist": {}}
HIST_N = 40   # utilization samples kept per GPU for the page's sparklines
COLLECT = os.environ.get("FLEET_COLLECT", "python3 rig-runs/.bin/collect.py --json")
LOCK = threading.Lock()


def ssh(alias, cmd, timeout=40):
    try:
        r = subprocess.run(SSH + [alias, cmd], capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as e:  # noqa: BLE001
        return 1, "", str(e)


def read_hosts():
    """[(name, alias, is_hub)] from the hub's ~/fleet/hosts.conf; falls back to the last known list / the hub alone."""
    rc, out, _ = ssh(HUB_ALIAS, "cat ~/fleet/hosts.conf", timeout=20)
    hosts = []
    if rc == 0:
        for line in out.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2:
                name, alias = parts[0], parts[1]
                hosts.append((name, HUB_ALIAS if alias == "local" else alias, alias == "local"))
    if not hosts:
        hosts = STATE["hosts"] or [(HUB_ALIAS, HUB_ALIAS, True)]
    return hosts


def remember(name, snap, ts):
    for g in snap.get("gpus", []):
        h = STATE["hist"].setdefault(f"{name}:{g['index']}", [])
        if not h or h[-1][0] != ts:
            h.append([ts, g["util"]]); del h[:-HIST_N]


def poll_one(name, alias, is_hub):
    t0 = time.time()
    rc, out, err = ssh(alias, COLLECT)
    entry = {"name": name, "alias": alias, "hub": is_hub, "fetched": int(time.time()), "rtt_s": round(time.time() - t0, 1)}
    if rc == 0:
        try:
            entry["snapshot"] = json.loads(out.strip().splitlines()[-1])
            entry["online"] = True
        except (ValueError, IndexError) as e:
            entry.update(online=False, error=f"bad collector output: {e}; {out[-200:]} {err[-200:]}")
    else:
        entry.update(online=False, error=(err.strip() or f"ssh exit {rc}")[-300:])
    with LOCK:
        prev = STATE["rigs"].get(name, {})
        if not entry.get("online") and prev.get("snapshot"):
            entry["snapshot"] = prev["snapshot"]          # keep the last good snapshot, flagged offline
            entry["last_online"] = prev.get("last_online", prev.get("fetched"))
        elif entry.get("online"):
            entry["last_online"] = entry["fetched"]
            remember(name, entry["snapshot"], entry["fetched"])
        STATE["rigs"][name] = entry


DEMO_FLEET = [("hub", True, [("RTX 5090", 32607)]), ("gpu-box-a", False, [("RTX 2080 SUPER", 8192), ("RTX 2080 SUPER", 8192)]),
              ("gpu-box-b", False, [("RTX 2080 Ti", 11264)])]
DEMO_JOBS = {"hub": ["train-vit-s", "dispatch-tiles"], "gpu-box-a": ["sweep-lr-3e-4", "sweep-lr-1e-4"], "gpu-box-b": ["eval-ckpt-12"]}
DEMO_UTIL = {}


def poll_demo():
    now = int(time.time())
    hosts = [(n, n, hub) for n, hub, _ in DEMO_FLEET]
    for name, hub, cards in DEMO_FLEET:
        gpus = []
        for i, (gname, total) in enumerate(cards):
            k = f"{name}:{i}"; u = DEMO_UTIL.get(k, random.uniform(55, 95))
            u = min(100, max(20, u + random.uniform(-9, 9))); DEMO_UTIL[k] = u
            gpus.append({"index": i, "name": gname, "util": int(u), "mem_used": int(total * (0.45 + u / 250)), "mem_total": total,
                         "temp": int(48 + u * 0.3)})
        jobs = [{"job": j, "state": "running" if i < len(cards) else "exit 0", "age_s": 4 if i < len(cards) else 3600 + 600 * i,
                 "last": f"epoch {random.randint(3, 40)}/50  loss {random.uniform(0.2, 0.9):.3f}  {random.randint(900, 2400)} img/s"}
                for i, j in enumerate(DEMO_JOBS[name])]
        snap = {"host": name, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "epoch": now, "load": [f"{random.uniform(2, 9):.2f}"] * 3,
                "disk_free": f"{random.choice([412, 96, 38])}G", "gpus": gpus, "fleet_jobs": jobs,
                "processes": [{"name": f"rig job: {j['job']}", "pid": 4000 + i, "etime": "01:12:09", "n": 5, "cpu": 340.0, "rss_mb": 6100.0}
                              for i, j in enumerate(jobs) if j["state"] == "running"],
                "logs": [{"name": f"rig-runs/{j['job']}/stdout.log", "path": f"/demo/{j['job']}", "mtime": now, "size": 20000} for j in jobs]}
        with LOCK:
            remember(name, snap, now)
            STATE["rigs"][name] = {"name": name, "alias": name, "hub": hub, "fetched": now, "rtt_s": 0.3, "online": True,
                                   "last_online": now, "snapshot": snap}
    with LOCK:
        STATE["hosts"] = hosts; STATE["last_poll"] = now


def poll_all():
    if STATE["demo"]:
        return poll_demo()
    hosts = read_hosts()
    with LOCK:
        STATE["hosts"] = hosts
    threads = [threading.Thread(target=poll_one, args=h, daemon=True) for h in hosts]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    with LOCK:
        STATE["last_poll"] = int(time.time())


def poller(interval):
    n = 0
    while True:
        poll_all()
        n += 1
        time.sleep(interval)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quiet
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text") else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            return self._send(200, (HERE / "index.html").read_bytes(), "text/html")
        if u.path == "/api/state":
            with LOCK:
                body = {"now": int(time.time()), "last_poll": STATE["last_poll"], "poll_interval": STATE["poll_interval"],
                        "hosts": [h[0] for h in STATE["hosts"]], "rigs": STATE["rigs"], "demo": STATE["demo"], "hist": STATE["hist"]}
            return self._send(200, body)
        if u.path == "/api/refresh":
            threading.Thread(target=poll_all, daemon=True).start()
            return self._send(202, {"ok": True})
        if u.path == "/api/log":
            rig = q.get("rig", [""])[0]; path = q.get("path", [""])[0]
            n = max(10, min(int(q.get("n", ["80"])[0]), 400))
            with LOCK:
                entry = STATE["rigs"].get(rig)
                allowed = {l["path"] for l in (entry or {}).get("snapshot", {}).get("logs", [])} if entry else set()
            if STATE["demo"] and path in allowed:
                lines = [f"epoch {e}/50  loss {0.9 - e * 0.012:.3f}  val_acc {60 + e * 0.6:.1f}%" for e in range(1, 41)]
                return self._send(200, {"rig": rig, "path": path, "n": n, "ok": True, "text": "\n".join(lines), "fetched": int(time.time())})
            if not entry or path not in allowed:
                return self._send(404, {"error": "unknown rig or log (only logs listed by the collector can be tailed)"})
            rc, out, err = ssh(entry["alias"], f"tail -n {n} {json.dumps(path)}", timeout=25)
            return self._send(200, {"rig": rig, "path": path, "n": n, "ok": rc == 0, "text": out if rc == 0 else err, "fetched": int(time.time())})
        return self._send(404, {"error": "not found"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--interval", type=int, default=20)
    ap.add_argument("--demo", action="store_true", help="serve a simulated fleet (no ssh)")
    a = ap.parse_args()
    STATE["poll_interval"] = 5 if a.demo else a.interval
    STATE["demo"] = a.demo
    if a.demo:   # pre-roll a few minutes so the sparklines are populated on first load
        t = int(time.time()) - HIST_N * 5
        for k in range(HIST_N):
            for name, _, cards in DEMO_FLEET:
                for i in range(len(cards)):
                    key = f"{name}:{i}"; u = DEMO_UTIL.get(key, random.uniform(55, 95))
                    DEMO_UTIL[key] = min(100, max(20, u + random.uniform(-9, 9)))
                    STATE["hist"].setdefault(key, []).append([t + 5 * k, int(DEMO_UTIL[key])])
    threading.Thread(target=poller, args=(STATE["poll_interval"],), daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"fleet GUI on http://127.0.0.1:{a.port}  (polling every {a.interval} s; Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
