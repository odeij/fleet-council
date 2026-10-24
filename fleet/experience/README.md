# ~/fleet/experience — what the GPU fleet has taught me

| file | what | written by |
|---|---|---|
| LESSONS.md | curated understanding: lessons, evidence, confidence, rules | me (Claude), after runs |
| EXPERIENCE.md | numbers learned from history (network, per-lane timing, GPU rates, memory, reliability, telemetry) | `rig learn` |
| model.json | the same, machine-readable; dispatch.py plans with it | `rig learn` |
| data/samples-YYYY-MM.jsonl | every GPU on every host, once a minute (util, memory, temp, power, clocks, who holds it) | cron -> lib/sample.py |
| data/net.jsonl | hourly 128 MB up/down probe to each worker | cron -> lib/sample.py --net |
| data/tasks.jsonl | every dispatched task: lane, size, predicted vs actual, push/exec/pull, GPU memory, errors | lib/dispatch.py |
| data/runs.jsonl | every dispatch run: wall time vs predicted makespan, per-lane stats | lib/dispatch.py |
| data/rig_jobs.jsonl | every finished `rig run` job: command, duration, exit, error class, 5 s GPU trace summary | `rig learn` (harvest) |

Commands: `rig experience` · `rig learn` · `rig sample [--net]` · `rig dispatch MANIFEST [--dry-run]`.
Cron (see ../crontab.example): sample every minute, network probe hourly, learn daily at 04:05.
Telemetry keeps other users anonymous: their GPU use is aggregated as "other_users" (process count and memory only).
