#!/usr/bin/env bash
gpus=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | sed 's/NVIDIA GeForce //; s/ MiB//' | paste -sd'|' -)
if [ -x "$HOME/env/bin/python" ]; then
  t=$("$HOME/env/bin/python" -c 'import torch;print(torch.__version__, "cuda-ok" if torch.cuda.is_available() else "NO-CUDA")' 2>&1 | tail -1)
else t="no ~/env"; fi
printf "%-9s %-8s gpus=[%s] torch=%s disk=%s\n" "$(hostname)" "$(whoami)" "$gpus" "$t" "$(df -h "$HOME" | awk 'NR==2{print $4}')"
