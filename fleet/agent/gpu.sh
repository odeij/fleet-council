#!/usr/bin/env bash
nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv,noheader,nounits 2>/dev/null \
 | awk -F', ' -v h="$(hostname)" '{sub("NVIDIA GeForce ","",$2); printf "%-9s gpu%s %-16s util %3s%%  mem %5s/%5s MiB  %s°C\n", h,$1,$2,$3,$4,$5,$6}'
