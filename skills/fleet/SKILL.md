---
name: fleet
description: Run training, eval, inference or many-task jobs on the GPU fleet (every host in ~/fleet/hosts.conf) over Tailscale, in parallel, with the `rig` CLI, from the hub or from a laptop controller. Use whenever the user asks to train/evaluate/run something on "the rigs", "the GPUs", "all GPUs", "the cluster", or to dispatch jobs to other machines. Also covers checking GPU status, syncing the shared ~/env, and collecting results back.
---

# Fleet: multi-host GPU jobs from one agent

The `rig` CLI (`~/fleet/bin/rig`, on PATH) launches and watches jobs on any host over Tailscale with passwordless ssh.
Run `rig help` for the full command list. Prefer `rig` over hand-written ssh/tmux commands.

## Where am I: hub or controller

- **Hub** = the always-on GPU box. It owns the per-minute GPU telemetry cron, the nightly `rig learn`, and the experience
  store `~/fleet/experience/`. In its `hosts.conf` the hub is `local`.
- **Controller** = a laptop, or any machine with `~/fleet/controller.conf` (`RIG_HUB=<hub>`). Every GPU is remote.
  - Start a session with `rig sync` (pulls code, this skill and the learned model from the hub). Edit fleet code on the
    hub, not on the controller: `rig sync` overwrites the controller's copy.
  - `rig experience`, `rig learn`, `rig sample` go through the hub automatically.
  - **Big data or long runs: run the dispatcher on the hub**, so nothing depends on the laptop staying awake:
    `rig run hub disp-<name> -- \$HOME/fleet/bin/rig dispatch /path/on/hub/manifest.json`, then `rig logs hub disp-<name> -f`.
    `rig run` jobs already run in tmux on the hosts and survive the laptop sleeping; only `rig wait` needs it awake.

## Experience loop (do this every time)

The fleet keeps a record of how its GPUs actually behave; learn from it like an engineer gaining experience.
1. **Before** planning GPU work: run `rig experience` and read it (curated lessons, learned timings/capacities, live state).
   For many-task jobs, `rig dispatch MANIFEST --dry-run` shows the learned plan and predicted makespan.
2. **During**: recording is automatic (dispatch.py -> tasks/runs, `rig run` jobs -> 5 s GPU trace, cron -> per-minute
   telemetry of every GPU and hourly network probes).
3. **After**: run `rig learn`, compare predicted vs actual, then edit `~/fleet/experience/LESSONS.md`: add a dated lesson
   for any surprise, failure class or systematic prediction error (with evidence and confidence); correct or RETIRE
   lessons the data contradicts. Keep lessons short and actionable.

## Hosts

Hosts live in `~/fleet/hosts.conf` (`name  ssh-alias  gpu-indices`). Describe yours here so the agent sizes work right:

| name      | GPUs                     | notes                                          |
|-----------|--------------------------|------------------------------------------------|
| hub       | 1x <big GPU>             | may be shared: run `rig gpu hub` first         |
| gpu-box-a | 2x <8 GB GPU>            | pick a card with `-g 0` / `-g 1`, or run two jobs |
| gpu-box-b | 1x <11 GB GPU>           | small disk: keep datasets small                |

## Environment (identical on every host)

- venv at `~/env` on every host, pinned by `~/fleet/requirements.txt`. `rig` activates it for `run` and `exec`.
  Never `pip install` on one host: add the pin, then `rig env-sync all` and `rig env-check all`.
- Mixed GPU generations: Turing (sm_75) has no real bf16; use fp16 autocast + GradScaler there, bf16 on Ampere+.

## Job model

`rig run <host|all> <job> [-g GPUS] [-d DIR] -- <cmd>` rsyncs DIR to `~/rig-runs/<job>/code` on the host and starts
`<cmd>` in tmux session `rig-<job>` inside `~/env`. `$RIG_OUT` = `~/rig-runs/<job>/out`. Logs in `stdout.log`, exit code in
`exit_code`, a 5 s GPU trace in `gpu_trace.csv`. Then `rig wait`, `rig collect` (to `~/fleet/results/<host>/<job>/`).

## Many small tasks: `rig dispatch`

`rig dispatch manifest.json [--dry-run] [--lanes a:0,b:1] [--extra-lanes hub:0b=local:0]` runs a list of tasks over every
GPU with a learned list scheduler. Task: `{"id", "points" (work size), "script", "inputs" [files to ship], "argv" with
{in0}..{out}, "out"}`. The script prints `STATS {json}`. Example: `~/fleet/jobs/example/`.
- One lane per GPU. Each lane pulls the next task the simulated plan gives it; remote lanes rsync inputs over, run via ssh
  with CUDA_VISIBLE_DEVICES, rsync results back, delete the remote copy.
- Caps come from live free GPU memory; an OOM cuts that lane's cap on the spot. Failed tasks retry on another lane
  (3 attempts); a lane that fails 3 times in a row retires itself. New scripts are calibrated online within the run.
- Smoke-test ONE task of a new job type on one remote lane (`--lanes gpu-box-b:0`) before the whole manifest.

## Design rules

- Run **independent** jobs per machine (configs, seeds, models, splits). Do not run one synchronous DDP job across
  consumer GPUs over a tailnet: the slow cards and the link throttle the fast one.
- Size batches per card. Always finish with `rig wait` + `rig collect`, then summarize per-host metrics.
- If a job FAILED, show the last 40 log lines, fix, and relaunch under a new job name.

## Smoke test

`rig run all smoke -d ~/fleet/jobs/smoke -- python train.py --epochs 3 && rig wait all smoke && rig collect all smoke`

## Gotchas

- Non-interactive ssh has a minimal PATH; `rig` uses absolute paths, hand-written ssh commands need `export PATH=$HOME/.local/bin:$PATH`.
- After editing anything in `~/fleet/agent/`, run `rig setup all` to push it to the hosts.
- Killing a dispatcher orphans remote work: clean up by PID on each host.
