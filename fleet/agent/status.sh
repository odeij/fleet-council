#!/usr/bin/env bash
# status.sh — one line per job dir under ~/rig-runs plus a GPU summary line
h=$(hostname)
gpu=$(nvidia-smi --query-gpu=index,utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits 2>/dev/null | awk -F', ' '{printf "gpu%s:%s%%/%sM ", $1,$2,$3}')
echo "$h  ${gpu:-no-gpu}"
shopt -s nullglob
for d in "$HOME"/rig-runs/*/; do
  j=$(basename "$d"); [ "$j" = ".bin" ] && continue
  if tmux list-sessions -F "#S" 2>/dev/null | grep -qx "rig-$j"; then st="RUNNING"
  elif [ -f "$d/exit_code" ]; then rc=$(cat "$d/exit_code"); [ "$rc" = 0 ] && st="done" || st="FAILED($rc)"
  else st="?"; fi
  last=$(tail -n 1 "$d/stdout.log" 2>/dev/null | cut -c1-90)
  printf "  %-8s %-22s %s\n" "$st" "$j" "$last"
done
