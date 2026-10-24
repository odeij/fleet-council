#!/usr/bin/env python3
"""Experience-driven multi-GPU task queue for the fleet (stdlib only; runs on the hub or on a controller such as a laptop).

manifest.json = list of tasks:
  {"id", "points" (work size, any unit), "out" (local result path),
   "script" (default ~/fleet/jobs/example/task.py), "inputs" [local files to ship] (default [] = data pre-staged on the hosts),
   "argv" [args with {in0},{in1},..,{out} placeholders] (default "{in0} {in1} {out}" + task["args"])}
Before running: loads ~/fleet/experience/model.json, checks free GPU memory on every lane, and predicts each task's
duration on each lane (learned per-lane fit, or physics fallback: transfer + compute). Scheduling = list scheduling
re-planned whenever a lane frees up: a lane runs the first task the simulated plan gives it, or waits.
Records every task (prediction vs actual) to experience/data/tasks.jsonl and the run to runs.jsonl.
"""
import statistics, argparse, json, os, shlex, subprocess, sys, threading, time, uuid
FLEET = os.path.expanduser("~/fleet"); EXP = f"{FLEET}/experience"; DATA = f"{EXP}/data"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "LogLevel=ERROR"]
DEFAULT_SCRIPT = os.environ.get("FLEET_DEFAULT_SCRIPT", f"{FLEET}/jobs/example/task.py")
def _hosts():  # hosts.conf rows: name  ssh-alias  [gpu indices, e.g. 0,1] ("local" = this machine), so the same code runs on the hub or a controller
    try: return [p for p in (l.split("#", 1)[0].split() for l in open(f"{FLEET}/hosts.conf")) if len(p) >= 2]
    except OSError: return [["hub", "local", "0"]]
ALIAS = {p[0]: p[1] for p in _hosts()}
LANES = [(f"{p[0]}:{g}", p[1], int(g)) for p in _hosts()  # name, ssh alias, gpu index: one lane per GPU
         for g in (p[2] if len(p) > 2 else "0").split(",")]
# physics fallback when the model has no history yet (per-point costs measured on a point-cloud workload; `rig learn` replaces them)
FALLBACK = dict(net_MBps=100.0, rtt_s=0.22, remote_overhead_s=1.2, local_overhead_s=1.3, out_frac=0.15,
                io_Mpts_s=25.0, gpu_Mpts_s={"RTX 5090": 50.0, "RTX 2080 Ti": 28.0, "RTX 2080 SUPER": 24.0}, mem_bpp=110.0)
lock = threading.Lock(); T0 = time.time()
PUSH = {"now": 0}      # transfers currently using the 5090 uplink

def log(msg, fh):
    line = f"[{time.time()-T0:7.1f}s] {msg}"
    with lock: print(line, flush=True); fh.write(line + "\n"); fh.flush()

# Wall-clock limit of one task's command (local or over ssh). FLEET_TASK_TIMEOUT (seconds) overrides it; the default
# 3600 kills and orphans longer remote tasks (LESSONS 2026-09-27), so long tasks must raise it.
TASK_TIMEOUT = int(os.environ.get("FLEET_TASK_TIMEOUT", "3600"))

def run(cmd, timeout=3600, **kw):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw); return p.returncode, p.stdout, p.stderr

def classify(err):
    e = err.lower()
    for k, pats in [("oom", ["out of memory", "outofmemory"]), ("push", ["push failed"]), ("pull", ["pull failed"]),
                    ("ssh", ["connection", "timed out", "could not resolve", "permission denied"]), ("missing_file", ["no such file"]),
                    ("module", ["modulenotfounderror", "importerror"]), ("python", ["traceback", "error"])]:
        if any(p in e for p in pats): return k
    return "other"

class Model:
    def __init__(self):
        try: self.m = json.load(open(f"{EXP}/model.json"))
        except Exception: self.m = {}
    def net(self, host):
        n = self.m.get("net", {}).get(host, {}); return n.get("up_MBps", FALLBACK["net_MBps"]), n.get("down_MBps", FALLBACK["net_MBps"])
    def bpp(self, script):
        return self.m.get("mem_bytes_per_point", {}).get(script, FALLBACK["mem_bpp"])
    def predict(self, lane, gpu_name, task, remote_lanes=3):
        script = os.path.basename(task.get("script", DEFAULT_SCRIPT)); mp = task["points"] / 1e6
        host = lane.split(":")[0]; mb = task.get("_bytes_in", 26 * task["points"]) / 1e6
        fit = self.m.get("lanes", {}).get(lane, {}).get(script, {})
        if ALIAS.get(host) == "local":   # no push/pull: inputs are already on this machine
            if fit.get("n", 0) >= 3: return max(0.2, fit["a_s"] + fit["b_s_per_Mpt"] * mp), "learned"
            rate = self.m.get("gpu_rate_Mpts_s", {}).get(gpu_name, {}).get(script) or FALLBACK["gpu_Mpts_s"].get(gpu_name, 20.0)
            return FALLBACK["local_overhead_s"] + mp / FALLBACK["io_Mpts_s"] + mp / rate, "physics"
        # remote: the 5090 uplink is shared by every remote lane that is pushing at the same time
        link = self.m.get("uplink_MBps") or FALLBACK["net_MBps"]
        conc = max(1.0, min(remote_lanes, self.m.get("typical_push_concurrency", remote_lanes)))
        push = mb / fit["push_MBps"] if fit.get("push_MBps") else mb / (link / conc)   # this lane's own observed rate beats the generic model
        ex = fit.get("exec")
        if ex and ex.get("n", 0) >= 3: execs, basis = ex["a_s"] + ex["b_s_per_Mpt"] * mp, "learned"
        else:
            rate = self.m.get("gpu_rate_Mpts_s", {}).get(gpu_name, {}).get(script) or FALLBACK["gpu_Mpts_s"].get(gpu_name, 20.0)
            execs, basis = FALLBACK["remote_overhead_s"] + mp / rate, "physics"
        down = self.net(host)[1]
        return push + execs + FALLBACK["out_frac"] * mb / down + 3 * FALLBACK["rtt_s"], basis

def lane_probe(lane):
    name, host, gpu = lane
    q = f"nvidia-smi -i {gpu} --query-gpu=name,memory.used,memory.total --format=csv,noheader,nounits"
    rc, out, _ = run(["bash", "-c", q] if host == "local" else SSH + [host, q], timeout=30)
    if rc: return None
    n, u, t = [x.strip() for x in out.strip().split(",")]
    return dict(gpu_name=n.replace("NVIDIA GeForce ", ""), mem_used=float(u), mem_total=float(t))

class Sched:
    def __init__(self, tasks, lanes, info, model, reserve_mb):
        self.q = list(tasks); self.lanes = lanes; self.info = info; self.model = model
        self.busy_until = {l[0]: time.time() for l in lanes}; self.inflight = 0
        self.cap = {}
        for name, _, _ in lanes:
            i = info[name]; free = i["mem_total"] - i["mem_used"] - reserve_mb
            self.cap[name] = max(0.0, free) * 1e6 / model.bpp(os.path.basename(DEFAULT_SCRIPT))
        self.pred_cache = {}; self.n_remote = sum(1 for l in lanes if l[1] != "local")
        self.cal = {}   # (lane, script) -> [actual/predicted] seen in THIS run: online calibration for new scripts / stale models
    def pred(self, lane, t):
        k = (lane, t["id"])
        if k not in self.pred_cache: self.pred_cache[k] = self.model.predict(lane, self.info[lane]["gpu_name"], t, self.n_remote)
        return self.pred_cache[k][0] * self.ratio(lane, t)
    def ratio(self, lane, t):
        sc = os.path.basename(t.get("script", DEFAULT_SCRIPT)); r = self.cal.get((lane, sc))
        if r: return statistics.median(r)
        other = [v for (l, s_), vs in self.cal.items() if s_ == sc for v in vs]      # uncalibrated lane: borrow this run's median
        return statistics.median(other) if other else 1.0
    def fits(self, lane, t): return t["points"] <= self.cap[lane] and lane not in t.get("failed_on", [])
    def plan(self, now):
        """Simulated list scheduling (longest first, earliest finish). Returns {lane: [task,...]}."""
        free = {l: max(now, b) for l, b in self.busy_until.items() if l in self.active}
        out = {l: [] for l in free}
        for t in sorted(self.q, key=lambda t: -t["points"]):
            opts = [(free[l] + self.pred(l, t), l) for l in free if self.fits(l, t)]
            if not opts: continue
            fin, l = min(opts); out[l].append(t); free[l] = fin
        return out
    def take(self, lane):
        with lock:
            if not self.q: return None, "empty"
            p = self.plan(time.time()).get(lane, [])
            if not p:
                if not any(self.fits(l, t) for t in self.q for l in self.active): return None, "unplaceable"
                return None, "wait"
            t = p[0]; self.q.remove(t); self.inflight += 1
            self.busy_until[lane] = time.time() + self.pred(lane, t)
            return t, "ok"

def exec_task(lane, t, work):
    name, host, gpu = lane
    script = t.get("script", DEFAULT_SCRIPT); inputs = t.get("inputs", [])   # [] is valid (data pre-staged on the hosts)
    argv = t.get("argv") or (["{in0}", "{in1}", "{out}"] + t.get("args", []))
    tim = dict(t_push=0.0, t_pull=0.0); t0 = time.time(); stats = None
    if host == "local":
        subs = {f"in{i}": p for i, p in enumerate(inputs)}; subs["out"] = t["out"]
        os.makedirs(os.path.dirname(os.path.abspath(t["out"])), exist_ok=True)
        rc, out, se = run([os.path.expanduser("~/env/bin/python"), script, *[a.format(**subs) for a in argv]],
                          env=dict(os.environ, CUDA_VISIBLE_DEVICES=str(gpu)), timeout=TASK_TIMEOUT)
    else:
        rdir = f"{work}/{t['id']}"; ra = f"$HOME/{rdir}"
        rc, _, se = run(SSH + [host, f"mkdir -p {rdir}"])
        with lock: PUSH["now"] += 1; conc = [PUSH["now"]]
        tp = time.time()
        try:
            stop = threading.Event()
            def watch():
                while not stop.wait(0.2): conc.append(PUSH["now"])
            w = threading.Thread(target=watch, daemon=True); w.start()
            rc2, _, se2 = run(["rsync", "-az", "-e", " ".join(SSH), script, *inputs, f"{host}:{rdir}/"])
            stop.set()
        finally:
            with lock: PUSH["now"] -= 1
        if rc or rc2: raise RuntimeError(f"push failed: {se}{se2}")
        tim["t_push"] = time.time() - tp; tim["push_concurrency"] = round(sum(conc) / len(conc), 2)
        subs = {f"in{i}": f"{ra}/{os.path.basename(p)}" for i, p in enumerate(inputs)}; subs["out"] = f"{ra}/out{os.path.splitext(t['out'])[1]}"
        cmd = (f"cd {ra} && CUDA_VISIBLE_DEVICES={gpu} $HOME/env/bin/python {ra}/{os.path.basename(script)} "
               + " ".join(a.format(**subs) if "{" in a else shlex.quote(a) for a in argv))
        rc, out, se = run(SSH + [host, cmd], timeout=TASK_TIMEOUT)
        if rc == 0:
            tq = time.time(); od = os.path.dirname(os.path.abspath(t["out"])); pdir = os.path.join(od, f".pull_{t['id']}")
            os.makedirs(pdir, exist_ok=True)
            rc3, _, se3 = run(["rsync", "-az", "-e", " ".join(SSH), f"{host}:{rdir}/out.*", pdir + "/"])
            if rc3: raise RuntimeError(f"pull failed: {se3}")
            ext = os.path.splitext(t["out"])[1]
            for f in os.listdir(pdir):
                dst = t["out"] if f == "out" + ext else os.path.splitext(t["out"])[0] + os.path.splitext(f)[1]
                os.replace(os.path.join(pdir, f), dst)
            os.rmdir(pdir); tim["t_pull"] = time.time() - tq
        run(SSH + [host, f"rm -rf {rdir}"])
    if rc != 0: raise RuntimeError((se or "")[-800:] or f"exit {rc}")
    for line in out.splitlines():
        if line.startswith("STATS "): stats = json.loads(line[6:])
    tim["t_total"] = time.time() - t0
    return stats or {}, tim

def lane_worker(lane, S, fh, rec_f, run_id, work, manifest):
    name, host, gpu = lane; fails = 0
    while True:
        t, why = S.take(name)
        if t is None:
            with lock: finished = (not S.q and S.inflight == 0) or (why == "unplaceable" and S.inflight == 0)
            if finished: break
            time.sleep(0.3); continue
        pred, basis = S.pred_cache[(name, t["id"])]
        err = None
        try: stats, tim = exec_task(lane, t, work)
        except Exception as e: err = str(e); stats, tim = {}, {"t_total": None}
        r = dict(ts=time.strftime("%Y-%m-%dT%H:%M:%S%z"), run_id=run_id, manifest=manifest, task=t["id"], lane=name, host=name.split(":")[0],
                 gpu=S.info[name]["gpu_name"], script=os.path.basename(t.get("script", DEFAULT_SCRIPT)), points=t["points"],
                 bytes_in=t.get("_bytes_in"), predicted_s=round(pred, 2), prediction_basis=basis, ok=err is None)
        if err:
            r.update(error_class=classify(err), error=err.strip()[-400:])
        else:
            r.update({k: round(v, 2) for k, v in tim.items()}); r["t_exec"] = round(tim["t_total"] - tim["t_push"] - tim["t_pull"], 2); r.update(t_gpu=stats.get("t_gpu_s"), t_read=stats.get("t_read_s"),
                     t_write=stats.get("t_write_s"), gpu_mem_mb=stats.get("gpu_mem_mb"), result_size=stats.get("result_count", stats.get("ground_out")),
                     bytes_out=os.path.getsize(t["out"]) if os.path.exists(t["out"]) else None)
        with lock:
            S.inflight -= 1; S.busy_until[name] = time.time(); rec_f.write(json.dumps(r) + "\n"); rec_f.flush()
            if err:
                t.setdefault("failed_on", []).append(name); t["attempts"] = t.get("attempts", 0) + 1
                if t["attempts"] < 3: S.q.append(t)
                else: S.failed.append((t, err))
                if r["error_class"] == "oom": S.cap[name] = min(S.cap[name], t["points"] * 0.8)   # learn on the spot
            else:
                S.done.append(r); S.cal.setdefault((name, r["script"]), []).append(r["t_total"] / max(pred, 1e-6))
        if err:
            fails += 1
            log(f"{name:11} FAIL {t['id']} [{r['error_class']}] {err.strip().splitlines()[-1][:160] if err.strip() else ''}", fh)
            if fails >= 3:
                log(f"{name:11} retiring lane after 3 consecutive failures", fh)
                with lock: S.active.discard(name)
                break
            continue
        fails = 0
        a = r["t_total"]; e = (a - pred) / max(pred, 1e-6) * 100; cal = S.ratio(name, t)
        log(f"{name:11} done {t['id']:>12} {t['points']/1e6:7.1f}M  {a:5.1f}s (pred {pred:5.1f}s {basis[0]}, {e:+4.0f}%)  "
            f"push {r['t_push']:4.1f} gpu {r.get('t_gpu') or 0:4.1f} pull {r['t_pull']:4.1f}  queue {len(S.q)}  cal x{cal:.1f}", fh)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("manifest"); ap.add_argument("--log")
    ap.add_argument("--lanes", default="all"); ap.add_argument("--extra-lanes", default="",
        help="extra lanes sharing a GPU, e.g. hub:0b=local:0 (useful when tasks are CPU-bound, like Blender scene sync)"); ap.add_argument("--work", default="rig-runs/fleet-work")
    ap.add_argument("--reserve-mb", type=float, default=900, help="GPU memory kept free for other users / CUDA context")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and predicted makespan, run nothing")
    a = ap.parse_args()
    tasks = json.load(open(a.manifest)); run_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    for t in tasks:
        ins = t.get("inputs", [])
        t["_bytes_in"] = sum(os.path.getsize(p) for p in ins if p and os.path.exists(p))
    lanes = [l for l in LANES if a.lanes == "all" or l[0] in a.lanes.split(",")]
    for spec in filter(None, a.extra_lanes.split(",")):
        nm, hg = spec.split("="); hst, g = hg.rsplit(":", 1); lanes.append((nm, hst, int(g)))
    info = {}
    for l in lanes:
        i = lane_probe(l)
        if i: info[l[0]] = i
    lanes = [l for l in lanes if l[0] in info]
    model = Model(); S = Sched(tasks, lanes, info, model, a.reserve_mb); S.active = {l[0] for l in lanes}; S.done = []; S.failed = []
    fh = open(a.log or os.path.splitext(a.manifest)[0] + ".log", "a")
    for l in lanes:
        i = info[l[0]]
        log(f"lane {l[0]:11} {i['gpu_name']:15} free {i['mem_total']-i['mem_used']:6.0f} MB -> cap {S.cap[l[0]]/1e6:6.1f}M pts", fh)
    plan = S.plan(time.time()); now = time.time()
    makespan = max((sum(S.pred(l, t) for t in ts) for l, ts in plan.items()), default=0)
    unplaced = len(tasks) - sum(len(v) for v in plan.values())
    log(f"run {run_id}: {len(tasks)} tasks, {sum(t['points'] for t in tasks)/1e6:.1f}M units; predicted makespan {makespan:.0f}s; "
        f"plan: " + ", ".join(f"{l} {len(v)}" for l, v in plan.items()) + (f"; UNPLACEABLE {unplaced}" if unplaced else "")
        + f"; model {'loaded' if model.m else 'absent (physics fallback)'}", fh)
    if a.dry_run: return
    os.makedirs(DATA, exist_ok=True); rec_f = open(f"{DATA}/tasks.jsonl", "a")
    th = [threading.Thread(target=lane_worker, args=(l, S, fh, rec_f, run_id, a.work, os.path.abspath(a.manifest)), daemon=True) for l in lanes]
    for t in th: t.start()
    for t in th: t.join()
    wall = time.time() - T0
    per = {}
    for r in S.done:
        L = per.setdefault(r["lane"], dict(tasks=0, points=0, busy_s=0.0, push_s=0.0, gpu_s=0.0, abs_err=0.0))
        L["tasks"] += 1; L["points"] += r["points"]; L["busy_s"] += r["t_total"]; L["push_s"] += r["t_push"]; L["gpu_s"] += r.get("t_gpu") or 0
        L["abs_err"] += abs(r["t_total"] - r["predicted_s"]) / max(r["predicted_s"], 1e-6)
    for L in per.values():
        L["mape_pct"] = round(L.pop("abs_err") / L["tasks"] * 100, 1); L["Mpts_per_busy_s"] = round(L["points"] / 1e6 / max(L["busy_s"], 1e-9), 2)
        for k in ("busy_s", "push_s", "gpu_s"): L[k] = round(L[k], 1)
    rr = dict(ts=time.strftime("%Y-%m-%dT%H:%M:%S%z"), run_id=run_id, kind="dispatch", manifest=os.path.abspath(a.manifest), tasks=len(tasks),
              ok=len(S.done), failed=len(S.failed), unfinished=len(S.q), wall_s=round(wall, 1), predicted_makespan_s=round(makespan, 1),
              points=sum(r["points"] for r in S.done), lanes={l[0]: info[l[0]] for l in lanes}, per_lane=per,
              model_basis="learned" if model.m else "physics")
    with open(f"{DATA}/runs.jsonl", "a") as f: f.write(json.dumps(rr) + "\n")
    json.dump(dict(rr, failed_tasks=[{"id": t["id"], "error": e[-300:]} for t, e in S.failed]), open(os.path.splitext(a.manifest)[0] + "_summary.json", "w"), indent=1)
    log(f"SUMMARY {run_id}: wall {wall:.1f}s (predicted {makespan:.0f}s)  ok {len(S.done)} failed {len(S.failed)} unfinished {len(S.q)}", fh)
    for n, L in per.items():
        log(f"   {n:11} {L['tasks']:3d} tasks {L['points']/1e6:8.1f}M  busy {L['busy_s']:6.1f}s push {L['push_s']:6.1f}s gpu {L['gpu_s']:5.1f}s  "
            f"{L['Mpts_per_busy_s']} M/s  prediction error {L['mape_pct']}%", fh)
    sys.exit(1 if S.failed or S.q else 0)

if __name__ == "__main__":
    main()
