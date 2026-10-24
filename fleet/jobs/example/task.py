#!/usr/bin/env python
"""Example dispatch task: the contract every `rig dispatch` script follows.
  argv: <in0> [<in1> ...] <out>     inputs arrive next to the script (remote lanes) or at their local path
  - write the result to <out>; extra files named <out stem>.<ext> are pulled back too
  - print one line `STATS {json}` (t_gpu_s, gpu_mem_mb, result_count, ...) so the dispatcher can learn from it
Here: load a float tensor, run a batch of matmuls on the GPU, save a reduced result."""
import json, sys, time
import torch

*ins, out = sys.argv[1:]
dev = "cuda" if torch.cuda.is_available() else "cpu"
t0 = time.time()
x = torch.load(ins[0]).to(dev) if ins else torch.randn(1_000_000, 64, device=dev)
t_read = time.time() - t0
if dev == "cuda": torch.cuda.synchronize(); torch.cuda.reset_peak_memory_stats()
t1 = time.time()
w = torch.randn(x.shape[1], x.shape[1], device=dev)
for _ in range(20):
    x = torch.tanh(x @ w)
y = x.mean(0)
if dev == "cuda": torch.cuda.synchronize()
t_gpu = time.time() - t1
torch.save(y.cpu(), out)
print("STATS " + json.dumps(dict(t_read_s=round(t_read, 3), t_gpu_s=round(t_gpu, 3), result_count=int(y.numel()),
      gpu_mem_mb=round(torch.cuda.max_memory_allocated() / 2**20) if dev == "cuda" else 0)))
