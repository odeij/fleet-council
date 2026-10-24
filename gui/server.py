#!/usr/bin/env python3
"""Fleet GUI server (controller): polls every rig in the fleet over ssh (~/rig-runs/.bin/collect.py --json on each host),
keeps the latest snapshot per rig, and serves the page + a small JSON API on http://127.0.0.1:8787.
    python3 ~/fleet_gui/server.py [--port 8787] [--interval 20]
The rig list comes from the hub's ~/fleet/hosts.conf (name  ssh-alias; "local" = the hub itself), re-read every few
minutes, so a rig added to the fleet appears by itself; a rig that does not answer shows as offline. Only the paths the
collector listed for a rig can be tailed through /api/log (no arbitrary remote commands). Stdlib only."""
import argparse
import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
HUB_ALIAS = os.environ.get("FLEET_HUB", "hub")   # ssh alias of the hub
SSH = ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=10"]
STATE = {"rigs": {}, "hosts": [], "started": int(time.time()), "poll_interval": 20, "last_poll": None}
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


def poll_one(name, alias, is_hub):
    t0 = time.time()
    rc, out, err = ssh(alias, "python3 rig-runs/.bin/collect.py --json")
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
        STATE["rigs"][name] = entry


def poll_all():
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
                        "hosts": [h[0] for h in STATE["hosts"]], "rigs": STATE["rigs"]}
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
            if not entry or path not in allowed:
                return self._send(404, {"error": "unknown rig or log (only logs listed by the collector can be tailed)"})
            rc, out, err = ssh(entry["alias"], f"tail -n {n} {json.dumps(path)}", timeout=25)
            return self._send(200, {"rig": rig, "path": path, "n": n, "ok": rc == 0, "text": out if rc == 0 else err, "fetched": int(time.time())})
        return self._send(404, {"error": "not found"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--interval", type=int, default=20)
    a = ap.parse_args()
    STATE["poll_interval"] = a.interval
    threading.Thread(target=poller, args=(a.interval,), daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"fleet GUI on http://127.0.0.1:{a.port}  (polling every {a.interval} s; Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
