#!/usr/bin/env python3
"""Turn fleet history into a model and a readable experience report (stdlib only).
Reads experience/data/{tasks,runs,net,samples-*}.jsonl (+ harvested rig jobs); writes experience/model.json and
experience/EXPERIENCE.md. Recent observations weigh more (half-life 14 days)."""
import glob, json, math, os, statistics as st, time
from collections import defaultdict
EXP = os.path.expanduser("~/fleet/experience"); D = f"{EXP}/data"; HALF_LIFE_D = 14.0
def jl(p):
    out = []
    for f in sorted(glob.glob(p)):
        for line in open(f):
            try: out.append(json.loads(line))
            except Exception: pass
    return out
def age_w(ts):
    try: t = time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception: return 1.0
    return 0.5 ** ((time.time() - t) / 86400 / HALF_LIFE_D)
def wmed(vals):  # weighted median
    vals = sorted(vals); tot = sum(w for _, w in vals); acc = 0
    for v, w in vals:
        acc += w
        if acc >= tot / 2: return v
    return None
def wlinfit(xs, ys, ws):  # weighted least squares y = a + b x
    W = sum(ws); mx = sum(w * x for x, w in zip(xs, ws)) / W; my = sum(w * y for y, w in zip(ys, ws)) / W
    sxx = sum(w * (x - mx) ** 2 for x, w in zip(xs, ws)); sxy = sum(w * (x - mx) * (y - my) for x, y, w in zip(xs, ys, ws))
    b = sxy / sxx if sxx > 1e-12 else 0.0; b = max(b, 0.0); a = max(my - b * mx, 0.0); return a, b
def jl_recent_samples(months=2):   # telemetry grows ~3.5 MB/day: learn from the last two monthly files only
    fs = sorted(glob.glob(f"{D}/samples-*.jsonl"))[-months:]; out = []
    for f in fs: out += jl(f)
    return out
tasks = jl(f"{D}/tasks.jsonl"); runs = jl(f"{D}/runs.jsonl"); net = jl(f"{D}/net.jsonl"); samples = jl_recent_samples(); jobs = jl(f"{D}/rig_jobs.jsonl")
M = dict(updated=time.strftime("%Y-%m-%dT%H:%M:%S%z"), counts=dict(tasks=len(tasks), runs=len(runs), net_probes=len(net), samples=len(samples), rig_jobs=len(jobs)))
# network
M["net"] = {}
for h in sorted({r["host"] for r in net if r.get("ok")}):
    rs = [r for r in net if r.get("host") == h and r.get("ok")]
    M["net"][h] = dict(up_MBps=round(wmed([(r["up_MBps"], age_w(r["ts"])) for r in rs]), 1), down_MBps=round(wmed([(r["down_MBps"], age_w(r["ts"])) for r in rs]), 1),
                       ssh_rtt_s=round(wmed([(r["ssh_rtt_s"], age_w(r["ts"])) for r in rs]), 3), n=len(rs),
                       min_up=min(r["up_MBps"] for r in rs), paths=sorted({r.get("path") for r in rs}))
# effective transfer rate seen by real tasks (compressed rsync of task inputs)
eff = defaultdict(list)
for t in tasks:
    if t.get("ok") and t.get("t_push") and t.get("bytes_in"): eff[t["host"]].append((t["bytes_in"] / 1e6 / t["t_push"], age_w(t["ts"])))
for h, v in eff.items(): M["net"].setdefault(h, {})["effective_push_MBps"] = round(wmed(v), 1)
# the 5090 uplink is one shared pipe: per-task push rate x concurrent pushes = link rate actually achieved
lk = [(t["bytes_in"] / 1e6 / t["t_push"] * t["push_concurrency"], age_w(t["ts"])) for t in tasks
      if t.get("ok") and t.get("t_push") and t.get("bytes_in") and t.get("push_concurrency") and t["bytes_in"] > 20e6]
if lk: M["uplink_MBps"] = round(wmed(lk), 1); M["uplink_samples"] = len(lk)
pc = [(t["push_concurrency"], age_w(t["ts"])) for t in tasks if t.get("push_concurrency")]
if pc: M["typical_push_concurrency"] = round(wmed(pc), 2)
# per-lane duration fits and gpu rates
M["lanes"] = {}; M["gpu_rate_Mpts_s"] = {}; M["mem_bytes_per_point"] = {}; M["reliability"] = {}
by = defaultdict(list)
for t in tasks: by[(t["lane"], t.get("script", "task.py"))].append(t)
for (lane, script), ts in sorted(by.items()):
    ok = [t for t in ts if t.get("ok") and t.get("t_total")]
    rel = M["reliability"].setdefault(lane, dict(tasks=0, failures=0, by_class={}))
    rel["tasks"] += len(ts); rel["failures"] += sum(1 for t in ts if not t.get("ok"))
    for t in ts:
        if not t.get("ok"): rel["by_class"][t.get("error_class", "?")] = rel["by_class"].get(t.get("error_class", "?"), 0) + 1
    if len(ok) >= 2:
        _xs = [t["points"] / 1e6 for t in ok]
        if max(_xs) / max(min(_xs), 1e-9) < 2.0: pass
        a, b = wlinfit([t["points"] / 1e6 for t in ok], [t["t_total"] for t in ok], [age_w(t["ts"]) for t in ok])
        errs = [abs(a + b * t["points"] / 1e6 - t["t_total"]) / t["t_total"] for t in ok]
        pe = [abs(t["t_total"] - t["predicted_s"]) / t["t_total"] for t in ok if t.get("predicted_s")]
        M["lanes"].setdefault(lane, {})[script] = dict(a_s=round(a, 3), b_s_per_Mpt=round(b, 4), n=len(ok), fit_mape_pct=round(100 * st.mean(errs), 1),
                                                       past_prediction_mape_pct=round(100 * st.mean(pe), 1) if pe else None,
                                                       gpu=ok[-1].get("gpu"))
    ex = [t for t in ok if t.get("t_exec") is not None]
    if len(ex) >= 2:
        xs = [t["points"] / 1e6 for t in ex]; ys = [t["t_exec"] for t in ex]; ws = [age_w(t["ts"]) for t in ex]
        spread = max(xs) / max(min(xs), 1e-9)
        gr = [t["points"] / 1e6 / t["t_gpu"] for t in ok if t.get("t_gpu")]
        b0 = 1.0 / st.median(gr) if gr else None                      # prior slope from the measured pure GPU rate
        if spread >= 2.0 or b0 is None:
            a2, b2 = wlinfit(xs, ys, ws); how = "fit"
        else:                                                           # sizes too similar to learn a slope: use the prior
            b2 = b0; a2 = max(0.0, sum(w * (y - b0 * x) for x, y, w in zip(xs, ys, ws)) / sum(ws)); how = "prior-slope (narrow size range)"
        pr_ = [(t["bytes_in"] / 1e6 / t["t_push"], age_w(t["ts"])) for t in ex if t.get("t_push") and t.get("bytes_in") and t["bytes_in"] > 20e6]
        if len(pr_) >= 3: M["lanes"].setdefault(lane, {}).setdefault(script, {})["push_MBps"] = round(wmed(pr_), 1)
        M["lanes"].setdefault(lane, {}).setdefault(script, {})["exec"] = dict(a_s=round(a2, 3), b_s_per_Mpt=round(b2, 4), n=len(ex), how=how, size_spread=round(spread, 2))
    g = [(t["points"] / 1e6 / t["t_gpu"], age_w(t["ts"])) for t in ok if t.get("t_gpu")]
    if g and ok[-1].get("gpu"): M["gpu_rate_Mpts_s"].setdefault(ok[-1]["gpu"], {})[script] = round(wmed(g), 1)
    mb = [t["gpu_mem_mb"] * 1e6 / t["points"] for t in ok if t.get("gpu_mem_mb") and t["points"] > 5e6]
    if mb: M["mem_bytes_per_point"][script] = round(max(M["mem_bytes_per_point"].get(script, 0), sorted(mb)[int(0.95 * (len(mb) - 1))]), 1)
# run-level prediction quality
pr = [r for r in runs if r.get("predicted_makespan_s") and r.get("wall_s")]
M["makespan_prediction"] = [dict(run=r["run_id"], predicted_s=r["predicted_makespan_s"], actual_s=r["wall_s"], basis=r.get("model_basis")) for r in pr[-10:]]
# GPU behaviour from telemetry: idle/busy, foreign load by hour, thermals
gb = defaultdict(lambda: dict(n=0, busy=0, util=[], temp_max=0, power_max=0, foreign_mem=[], own_mem=[], hours=defaultdict(list), down=0))
hostdown = defaultdict(lambda: [0, 0])
for s in samples:
    hostdown[s["name"]][1] += 1
    if not s.get("ok"): hostdown[s["name"]][0] += 1; continue
    hr = int(s["ts"][11:13])
    for g in s.get("gpus", []):
        k = f"{s['name']}:{g['index']}"; G = gb[k]; G["n"] += 1; G["name"] = g["name"]
        u = g.get("util") or 0; G["util"].append(u); G["busy"] += u > 10
        G["temp_max"] = max(G["temp_max"], g.get("temp") or 0); G["power_max"] = max(G["power_max"], g.get("power") or 0)
        fm = g.get("holders", {}).get("other_users", {}).get("mem_mb", 0.0); om = sum(v["mem_mb"] for kk, v in g.get("holders", {}).items() if kk != "other_users")
        G["foreign_mem"].append(fm); G["own_mem"].append(om); G["hours"][hr].append((u, g["mem_used"], g["mem_total"]))
M["gpus"] = {}
for k, G in sorted(gb.items()):
    if not G["n"]: continue
    free_by_hour = {h: round(st.median([t - m for _, m, t in v])) for h, v in sorted(G["hours"].items())}
    M["gpus"][k] = dict(name=G["name"], samples=G["n"], busy_pct=round(100 * G["busy"] / G["n"], 1), util_mean=round(st.mean(G["util"]), 1),
                        temp_max=G["temp_max"], power_max=round(G["power_max"]), foreign_mem_mb_median=round(st.median(G["foreign_mem"])),
                        own_mem_mb_median=round(st.median(G["own_mem"])), free_mem_mb_median_by_hour=free_by_hour)
M["host_availability_pct"] = {h: round(100 * (1 - d / max(n, 1)), 2) for h, (d, n) in hostdown.items()}
# rig jobs (harvested)
M["rig_jobs"] = dict(total=len(jobs), failed=sum(1 for j in jobs if j.get("exit") not in (0, None)),
                     error_classes=dict(sorted({c: sum(1 for j in jobs if j.get("error_class") == c) for c in {j.get("error_class") for j in jobs if j.get("error_class")}}.items())))
json.dump(M, open(f"{EXP}/model.json", "w"), indent=1)
# ---------- readable report ----------
L = [f"# Fleet experience (auto-generated {M['updated']} by ~/fleet/lib/learn.py — do not edit; curated lessons live in LESSONS.md)", "",
     f"History: {len(tasks)} dispatched tasks, {len(runs)} runs, {len(jobs)} rig jobs, {len(net)} network probes, {len(samples)} telemetry samples.", ""]
L += ["## Network (hub <-> host)", "", "| host | probe up MB/s | probe down MB/s | real task push MB/s | ssh setup s | probes | paths |", "|---|---|---|---|---|---|---|"]
for h, n in M["net"].items(): L.append(f"| {h} | {n.get('up_MBps','-')} | {n.get('down_MBps','-')} | {n.get('effective_push_MBps','-')} | {n.get('ssh_rtt_s','-')} | {n.get('n',0)} | {','.join(p for p in n.get('paths',[]) if p)} |")
L += ["", f"Shared 5090 uplink achieved by real transfers: {M.get('uplink_MBps','(not measured yet)')} MB/s over {M.get('uplink_samples',0)} transfers; typical concurrent pushes {M.get('typical_push_concurrency','?')}."]
L += ["", "## Lanes: duration = a + b x (million points)", "", "| lane | script | GPU | a (s) | b (s/Mpt) | tasks | fit err % | past prediction err % |", "|---|---|---|---|---|---|---|---|"]
for lane, ss in M["lanes"].items():
    for s, f in ss.items(): L.append(f"| {lane} | {s} | {f['gpu']} | {f['a_s']} | {f['b_s_per_Mpt']} | {f['n']} | {f['fit_mape_pct']} | {f['past_prediction_mape_pct']} |")
L += ["", "## Pure GPU compute rate (Mpts/s)", ""] + [f"- {g}: " + ", ".join(f"{s} {v}" for s, v in d.items()) for g, d in M["gpu_rate_Mpts_s"].items()]
L += ["", f"GPU memory per point (p95): " + ", ".join(f"{s} {v} B" for s, v in M["mem_bytes_per_point"].items()), ""]
L += ["## Reliability", "", "| lane | tasks | failures | classes |", "|---|---|---|---|"] + [f"| {l} | {r['tasks']} | {r['failures']} | {r['by_class']} |" for l, r in M["reliability"].items()]
L += ["", "## Makespan prediction (last runs)", ""] + [f"- {r['run']}: predicted {r['predicted_s']}s, actual {r['actual_s']}s ({r['basis']})" for r in M["makespan_prediction"]]
L += ["", "## GPU behaviour from telemetry", "", "| gpu | model | samples | busy % | mean util | max temp | max W | median foreign mem MB | median own mem MB |", "|---|---|---|---|---|---|---|---|---|"]
for k, g in M["gpus"].items(): L.append(f"| {k} | {g['name']} | {g['samples']} | {g['busy_pct']} | {g['util_mean']} | {g['temp_max']} | {g['power_max']} | {g['foreign_mem_mb_median']} | {g['own_mem_mb_median']} |")
L += ["", "Median free GPU memory (MB) by hour of day:", ""]
for k, g in M["gpus"].items(): L.append(f"- {k}: " + " ".join(f"{h:02d}h:{v}" for h, v in g["free_mem_mb_median_by_hour"].items()))
L += ["", f"Host reachability: {M['host_availability_pct']}", "", f"Rig jobs: {M['rig_jobs']}", ""]
open(f"{EXP}/EXPERIENCE.md", "w").write("\n".join(L) + "\n")
print(f"model.json + EXPERIENCE.md updated ({len(tasks)} tasks, {len(runs)} runs, {len(jobs)} rig jobs, {len(samples)} samples, {len(net)} probes)")
