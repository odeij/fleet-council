# fleet-council

**One AI brain, any number of GPUs, several models.** A harness that lets a single Claude Code session on a MacBook
command a heterogeneous Linux GPU cluster over Tailscale, learn how that cluster actually behaves, and pull other
frontier models into a council when a problem is hard.

- **Cross-OS by design:** macOS controller, Linux GPU workers, mixed GPU generations (Blackwell + Turing), one CLI.
- **Scales by config, not code:** every GPU in `hosts.conf` becomes a scheduling lane. Adding a machine is one line.
- No AnyDesk, no remote desktops, no second Claude session per machine: one agent, one context, every GPU.
- A learned list scheduler that predicts each task on each GPU and gets better after every run.
- A council where Claude and OpenAI's Codex answer independently and Claude chairs the synthesis.

```mermaid
flowchart LR
    subgraph Brain["MacBook (macOS) · one Claude Code session"]
        CC["Claude Code<br/>(orchestrator + chairman)"]
        FS["fleet skill"]
        AC["agent-council skill"]
        CC --- FS
        CC --- AC
    end
    subgraph Council["Council (CLIs you are logged into)"]
        CX["Codex CLI · GPT"]
        CL["claude -p"]
    end
    subgraph Tailnet["Linux GPU hosts · Tailscale tailnet · ssh only"]
        HUB["Hub · RTX 5090 32 GB<br/>telemetry cron · rig learn · experience store"]
        A["Host A · 2x RTX 2080 SUPER 8 GB"]
        B["Host B · RTX 2080 Ti 11 GB"]
        C["rig4090 · RTX 4090 24 GB<br/>joining next: +1 line in hosts.conf"]
        S["DGX Spark ×N · GB10 Grace Blackwell<br/>coming soon"]
        N["Host N · any NVIDIA GPU"]
    end
    AC -->|same question, in parallel| CX & CL
    CX & CL -->|independent answers| CC
    FS -->|rig run / rig dispatch| HUB & A & B & C & S & N
    HUB -->|rig learn: model.json + LESSONS.md| FS
    GUI["Fleet GUI :8787"] -.->|read-only ssh poll| HUB & A & B & C & S & N
```

![Fleet dashboard (demo mode)](docs/dashboard.png)

## Why

I had four consumer GPUs of two generations in three machines. The usual options were bad: remote-desktop into each box
and babysit it, or open a separate AI session on every machine and pay for the same context several times over.
Instead, one agent holds the plan and treats the GPUs as tools. It runs jobs, watches them, collects the results, and
writes down what it learned so the next plan is better.

## What is in here

| part | what it does |
|---|---|
| [`fleet/bin/rig`](fleet/bin/rig) | Bash CLI: `run`, `wait`, `collect`, `logs`, `kill`, `gpu`, `exec`, `env-sync` on any host or `all`, in parallel. Jobs run in tmux inside an identical pinned venv, with a 5 s GPU trace. |
| [`fleet/lib/dispatch.py`](fleet/lib/dispatch.py) | Many-task scheduler (stdlib only). One lane per GPU, list scheduling re-planned whenever a lane frees up, live memory caps, OOM-aware cap cuts, retries on another lane, lane retirement, **online calibration** for scripts it has never seen. |
| [`fleet/lib/learn.py`](fleet/lib/learn.py) | Fits per-lane `time = a + b·size`, push rates, GPU throughput, memory per unit, reliability and hourly free-memory profiles from every run → `model.json` + `EXPERIENCE.md`. |
| [`fleet/lib/sample.py`](fleet/lib/sample.py) | Cron telemetry: every GPU on every host each minute (util, memory, temp, power, clocks, who holds it; other users anonymized) + hourly bandwidth probes. |
| [`fleet/experience/LESSONS.md`](fleet/experience/LESSONS.md) | The curated half of the memory: lessons the agent writes after each run, with evidence and confidence. |
| [`gui/`](gui/) | Stdlib web dashboard: live GPUs, jobs, processes and log tails for the whole fleet, read-only. |
| [`skills/fleet`](skills/fleet/SKILL.md) | The Claude Code skill that teaches the agent the protocol: read experience → plan → run → learn. |
| [`skills/agent-council`](skills/agent-council/) | My council setup on top of the open-source agent-council skill. |

## It adapts to its own hardware

Nobody tells the fleet how fast each GPU is or how good the network is. It learns that by running work, in three loops:

1. **In-run (seconds):** every task is predicted on every GPU and the queue is re-planned whenever a GPU frees up.
   Predictions are calibrated live from actual/predicted ratios, an out-of-memory error cuts that GPU's cap on the spot,
   and failed tasks move to another GPU while a GPU that keeps failing retires itself.
2. **Model (nightly):** every task, job, per-minute GPU sample and network probe feeds recency-weighted fits
   (14-day half-life): per-GPU timing, real link sharing, memory per unit, reliability, free VRAM by hour of day.
3. **Lessons (after each run):** the agent compares prediction against reality and writes, corrects or retires lessons
   with evidence and confidence, then reads them before it plans the next job.

Full mechanics and real examples: **[docs/ADAPTATION.md](docs/ADAPTATION.md)**.

## Scaling out

There is no fixed cluster size anywhere in the code. `hosts.conf` is the whole topology:

```
# name        ssh-alias   gpu indices
hub           local       0
gpu-box-a     gpu-box-a   0,1
gpu-box-b     gpu-box-b   0
rig4090       rig4090     0            # next to join (RTX 4090): one line, then `rig setup rig4090 && rig env-sync rig4090`
gpu-box-c     gpu-box-c   0,1,2,3      # any further box, same way
```

- `rig` fans every command out to all hosts in parallel; `all` always means "whatever is in hosts.conf now".
- `dispatch.py` builds one lane per listed GPU, probes live free memory on each, and plans across all of them.
  A host that doesn't answer is simply dropped from the plan; a lane that keeps failing retires itself mid-run.
- A new GPU has no history, so its first run is calibrated online; after `rig learn` it has its own fitted model.
- The sampler, learner and dashboard read the same file, so a new host shows up in telemetry and the GUI by itself.
- The real limit is bandwidth, not lanes: remote GPUs share the hub's uplink, so staging data on the workers
  (`rig push`) or shipping compact inputs is what keeps a large fleet busy. The scheduler models this per lane.

## Roadmap: DGX Spark nodes

Several NVIDIA DGX Spark machines (GB10 Grace Blackwell, 128 GB unified memory) are joining the fleet next. They are
ARM64 (aarch64), which makes them the first non-x86 hosts: `rig` itself is plain bash/ssh and needs no change, and
`rig env-sync <host> <pins>` already takes a per-host pin file for the aarch64 CUDA wheels. Their 128 GB of unified
memory will be the largest lane caps in the fleet, so the scheduler should route the biggest tasks there by itself.

## Measured on the reference fleet

Controlled from a MacBook (macOS). Workers: 1x RTX 5090 (shared desktop, Blackwell) + 2x RTX 2080 SUPER + 1x RTX 2080 Ti
(Turing), all Linux, one tailnet, 1 GbE uplink on the hub.

- **1.5 billion-point city LiDAR cloud**: ground extraction across all 4 GPUs in about 10 minutes.
- **2.2x** over the 5090 alone on compute-heavy tiles with compact inputs; **1.5x** on I/O-bound point clouds, where the
  hub's single 1 GbE uplink is the bottleneck. The learned model tells the agent which case it is in before it starts.
- **Prediction error 40% → 7%** on one lane after replacing a shared-uplink assumption with each lane's own learned push rate.
- **×117 misprediction neutralized**: a never-seen render script was predicted at 1.3 s per 150 s chunk; online
  calibration rescaled it within the run and all four lanes finished within 90 s of each other.
- **1.75x render throughput** from two lanes on one 5090 after telemetry showed Blender frames were ~90% CPU scene sync.
- **Byte-identical outputs** on Turing and Blackwell for the same tile, so any task can go to any lane.

These come from the fleet's own logs (`rig learn`), written up in [LESSONS.md](fleet/experience/LESSONS.md).

## Quick start

```bash
git clone https://github.com/odeij/fleet-council && cd fleet-council
./install.sh hub                       # or: ./install.sh controller  (on the laptop)
$EDITOR ~/fleet/hosts.conf             # name  ssh-alias  gpu-indices
rig setup all && rig env-sync all && rig wait all envsync && rig hosts
rig run all smoke -d ~/fleet/jobs/smoke -- python train.py --epochs 3 && rig wait all smoke && rig collect all smoke
```

Full walkthrough (Tailscale, ssh keys, cron, scheduler demo, dashboard, skills): **[docs/SETUP.md](docs/SETUP.md)**.

## Design choices

- **ssh + tmux + rsync, not a framework.** Nothing to install on a host but a venv. A job survives the laptop sleeping.
- **Independent jobs per GPU, not cross-machine DDP.** Mixed generations over a tailnet would throttle the fast card.
- **Two kinds of memory.** Numbers (`model.json`, rebuilt nightly) and judgment (`LESSONS.md`, written by the agent,
  wrong lessons retired, not deleted). The agent reads both before it plans.
- **Shared GPUs are respected.** Caps come from live free memory minus a reserve; other users' processes are counted, never touched.
- **The dashboard can't run commands.** It only tails log files that each host's collector lists.

## No API keys

Nothing here calls a model API. Claude Code, the Codex CLI and `claude -p` run on the subscriptions you are already
logged into, and the fleet itself is plain ssh. The repo contains no credentials, and `.gitignore` keeps host lists,
telemetry and results out of git.

## Credits

The council scripts are [team-attention/agent-council](https://github.com/team-attention/agent-council), inspired by
[karpathy/llm-council](https://github.com/karpathy/llm-council). Everything under `fleet/`, `gui/` and `skills/fleet/`
is original to this repo (MIT).
