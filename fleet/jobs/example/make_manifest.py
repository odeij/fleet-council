#!/usr/bin/env python
"""Make N random input tensors of varied size and a manifest for them.
  python make_manifest.py DIR [N]  ->  DIR/inputs/*.pt, DIR/manifest.json
Then: rig dispatch DIR/manifest.json --dry-run   (plan + predicted makespan), and without --dry-run to run it."""
import json, os, random, sys
import torch

d = os.path.abspath(sys.argv[1]); n = int(sys.argv[2]) if len(sys.argv) > 2 else 24
os.makedirs(f"{d}/inputs", exist_ok=True); os.makedirs(f"{d}/out", exist_ok=True)
script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "task.py"); tasks = []
for i in range(n):
    rows = random.choice([200_000, 500_000, 1_000_000, 2_000_000])   # varied sizes so the learner can fit time vs size
    p = f"{d}/inputs/t{i:03d}.pt"; torch.save(torch.randn(rows, 64), p)
    tasks.append(dict(id=f"t{i:03d}", points=rows, script=script, inputs=[p], argv=["{in0}", "{out}"], out=f"{d}/out/t{i:03d}.pt"))
json.dump(tasks, open(f"{d}/manifest.json", "w"), indent=1)
print(f"{n} tasks -> {d}/manifest.json")
