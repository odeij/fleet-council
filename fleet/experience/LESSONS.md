# Fleet lessons (curated; the "understanding" part of the experience)

Written by the agent after each run, from `rig learn` numbers. Each lesson: what was learned, the evidence, the
confidence, the rule. Contradicted lessons are marked RETIRED, not deleted. HIGH = measured repeatedly; MED = a few times.
These are the real lessons from the reference fleet (1x RTX 5090 32 GB hub, 2x RTX 2080 SUPER 8 GB, 1x RTX 2080 Ti 11 GB,
all on one Tailscale tailnet). Replace them with your own as your fleet learns.

## Protocol
1. Before planning GPU work: `rig experience` (these lessons, the learned model, live GPU state).
   For sharded work also `rig dispatch MANIFEST --dry-run` to see the plan and predicted makespan.
2. While running: nothing to do; dispatch.py, rig jobs (5 s GPU trace) and the per-minute sampler record everything.
3. After: `rig learn`, compare prediction vs actual. A systematic error, a new failure class or a surprise becomes a lesson.

## Capacity and hardware
- **GPU memory is the hard cap; check it live.** (HIGH) The point-cloud workload needs ~110-118 B of GPU memory per point
  (p95). 8 GB 2080 SUPER ~60M pts, 11 GB 2080 Ti ~90M. dispatch.py derives caps from live free memory minus a 900 MB
  reserve, and cuts a lane's cap on the spot after an OOM.
- **A shared desktop GPU is rarely idle.** (HIGH, telemetry) At night the 5090 ran ~100% util, ~70 C, 410-470 W with
  ~18 GB held by other jobs: ~13.7 GB free of 32. Plan for what is free, never touch other users' processes.
- **Compute is not the bottleneck for point-cloud tiles; I/O and transfer are.** (HIGH) Pure rates: 5090 ~52,
  2080 Ti ~32, 2080 SUPER ~22 Mpts/s.
- **Results are identical across GPU generations.** (HIGH) The same tile gave byte-identical output on Turing and
  Blackwell, so any lane can take any float32/integer task. Re-verify for training (non-deterministic kernels).
- **Turing (sm_75) has no real bf16.** (HIGH) fp16 autocast + GradScaler on the 2080s, bf16 on the 5090.

## Network
- **One 1 GbE uplink on the hub feeds every remote GPU.** (HIGH) ~107 MB/s each way per host over a direct Tailscale path.
  Three remote lanes share it; rsync -z gets ~140 MB/s effective on compressible data.
- **Measure throughput with big payloads and subtract ssh setup.** (HIGH) A 32 MB probe read 57 MB/s because ~0.21 s of
  ssh session setup dominated; 128 MB minus setup reads the true ~107 MB/s.
- **When remote GPUs are worth it.** (MED) A remote task pays ~MB/100 s to ship its input plus ~2-3 s fixed cost
  (ssh + python/torch start + CUDA init). I/O-bound work: 4 GPUs gave 1.5x over the 5090 alone. Compact inputs with real
  per-task compute: 2.2x (74 tiles of ~15 MB, all four lanes finished together). Ship compact data, stage big data once.

## Scheduling
- **Give the no-transfer lane the big tasks, remote lanes the cheap-to-ship ones.** (HIGH) The list scheduler predicts
  every task on every lane and re-plans whenever a lane frees up; a lane may wait if another will finish a task sooner.
- **Each lane has its own share of the uplink.** (MED) Assuming a 3-way split over-predicted one lane by 40%; using each
  lane's own observed push rate brought its error to 7%.
- **A deterministic scheduler starves the learner.** (MED) The same lane kept getting the same task size, so the slope
  of time vs size could not be fit. learn.py falls back to a prior slope from the measured GPU rate when sizes span < 2x.
- **New scripts have no learned model: calibrate online.** (HIGH) For a script with no history the physics fallback
  predicted 1.3 s for a 150 s render chunk (x117 off). dispatch.py now scales each lane's predictions by the median
  actual/predicted ratio seen in the current run. Result: all four lanes finished within 90 s of each other.
- **CPU-bound GPU tasks: run two lanes on the big GPU.** (HIGH) Large Blender scenes spent ~10 of 11 s/frame in CPU scene
  sync. Two lanes on the one 5090 (`--extra-lanes hub:0b=local:0`) = 1.75x throughput.

## Failures
- **Killing a dispatcher orphans remote work.** (MED) Remote processes kept running. Clean up by PID per host.
- **Smoke-test ONE task of a new job type on one remote lane** (`--lanes gpu-box-b:0`) before the whole manifest.
- **The default task timeout (3600 s) kills long remote tasks.** Raise `FLEET_TASK_TIMEOUT` for long tasks.
