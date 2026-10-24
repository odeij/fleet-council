#!/usr/bin/env bash
job="$1"; s="rig-$job"; dir="$HOME/rig-runs/$job"
if tmux list-sessions -F "#S" 2>/dev/null | grep -qx "$s"; then
  tmux kill-session -t "$s"; sleep 1
  [ -f "$dir/exit_code" ] || { echo 137 > "$dir/exit_code"; date -Iseconds > "$dir/finished_at"; echo "=== killed $(date -Iseconds)" >> "$dir/stdout.log"; }
  echo "killed '$job' on $(hostname)"
else echo "'$job' not running on $(hostname)"; fi
