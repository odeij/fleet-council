#!/usr/bin/env bash
# startjob.sh <job>  — start runjob.sh in a detached tmux session rig-<job>
job="$1"; s="rig-$job"
if tmux list-sessions -F "#S" 2>/dev/null | grep -qx "$s"; then echo "ERROR: job '$job' already running on $(hostname)"; exit 1; fi
tmux new-session -d -s "$s" "bash $HOME/rig-runs/.bin/runjob.sh $job"
echo "started '$job' on $(hostname) (tmux $s)"
