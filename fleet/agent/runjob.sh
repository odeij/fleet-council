#!/usr/bin/env bash
# runjob.sh <job>  — runs ~/rig-runs/<job>/cmd.sh inside ~/env, logs + exit code.
job="$1"; dir="$HOME/rig-runs/$job"
cd "$dir" || exit 2
rm -f exit_code finished_at
date -Iseconds > started_at
[ -f "$HOME/env/bin/activate" ] && source "$HOME/env/bin/activate"
export RIG_JOB="$job" RIG_DIR="$dir" RIG_OUT="$dir/out" RIG_HOST="$(hostname)"
mkdir -p "$RIG_OUT" "$dir/code"
[ -f "$dir/env.sh" ] && source "$dir/env.sh"
{
  echo "=== rig job '$job' on $(hostname) started $(date -Iseconds)"
  echo "=== python: $(command -v python)  CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-<all>}"
  echo "=== cmd: $(cat cmd.sh)"
  nvidia-smi -L 2>/dev/null
  echo
} >> stdout.log
cd "$dir/code"
# per-job GPU trace every 5 s (experience data; harvested by `rig learn`)
nvidia-smi --query-gpu=timestamp,index,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,clocks.sm \
  --format=csv,noheader,nounits -l 5 > "$dir/gpu_trace.csv" 2>/dev/null &
tracer=$!
bash "$dir/cmd.sh" >> "$dir/stdout.log" 2>&1
rc=$?
kill $tracer 2>/dev/null
echo "$rc" > "$dir/exit_code"; date -Iseconds > "$dir/finished_at"
echo "=== rig job '$job' exit $rc at $(date -Iseconds)" >> "$dir/stdout.log"
exit $rc
