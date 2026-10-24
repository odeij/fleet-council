#!/usr/bin/env python
"""Fleet smoke test: train -> eval -> checkpoint -> reload -> inference on one GPU.
Writes $RIG_OUT/metrics.json and $RIG_OUT/model.pt. Synthetic data, no downloads."""
import argparse, json, os, socket, time
import torch, torch.nn as nn

p = argparse.ArgumentParser()
p.add_argument("--epochs", type=int, default=3)
p.add_argument("--samples", type=int, default=200_000)
p.add_argument("--dim", type=int, default=512)
p.add_argument("--hidden", type=int, default=2048)
p.add_argument("--batch", type=int, default=1024)
p.add_argument("--out", default=os.environ.get("RIG_OUT", "."))
a = p.parse_args()
os.makedirs(a.out, exist_ok=True)
host = socket.gethostname()
dev = "cuda" if torch.cuda.is_available() else "cpu"
gpu = torch.cuda.get_device_name(0) if dev == "cuda" else "cpu"
print(f"[{host}] device={dev} gpu={gpu} torch={torch.__version__} cuda={torch.version.cuda}", flush=True)
torch.manual_seed(0)

# synthetic 10-class problem: linear teacher + noise
g = torch.Generator().manual_seed(0)
W = torch.randn(a.dim, 10, generator=g)
X = torch.randn(a.samples, a.dim, generator=g)
y = (X @ W + 0.5 * torch.randn(a.samples, 10, generator=g)).argmax(1)
n_train = int(a.samples * 0.9)
Xtr, ytr = X[:n_train].to(dev), y[:n_train].to(dev)
Xte, yte = X[n_train:].to(dev), y[n_train:].to(dev)

def make_model():
    return nn.Sequential(nn.Linear(a.dim, a.hidden), nn.GELU(),
                         nn.Linear(a.hidden, a.hidden), nn.GELU(),
                         nn.Linear(a.hidden, 10)).to(dev)

amp = dict(device_type=dev, dtype=torch.float16, enabled=(dev == "cuda"))
model = make_model()
opt = torch.optim.AdamW(model.parameters(), lr=1e-3)
scaler = torch.amp.GradScaler(enabled=(dev == "cuda"))
loss_fn = nn.CrossEntropyLoss()

@torch.no_grad()
def evaluate(m):
    m.eval(); correct = 0
    for i in range(0, len(Xte), 4096):
        with torch.autocast(**amp):
            correct += (m(Xte[i:i+4096]).argmax(1) == yte[i:i+4096]).sum().item()
    m.train(); return correct / len(Xte)

hist, t0 = [], time.time()
for ep in range(1, a.epochs + 1):
    te = time.time(); perm = torch.randperm(n_train, device=dev); tot, nb = 0.0, 0
    for i in range(0, n_train, a.batch):
        idx = perm[i:i+a.batch]
        with torch.autocast(**amp):
            loss = loss_fn(model(Xtr[idx]), ytr[idx])
        opt.zero_grad(set_to_none=True); scaler.scale(loss).backward(); scaler.step(opt); scaler.update()
        tot += loss.item(); nb += 1
    if dev == "cuda": torch.cuda.synchronize()
    acc, dt = evaluate(model), time.time() - te
    hist.append({"epoch": ep, "loss": tot / nb, "val_acc": acc, "epoch_s": dt, "samples_per_s": n_train / dt})
    print(f"[{host}] epoch {ep}/{a.epochs}  loss {tot/nb:.4f}  val_acc {acc:.4f}  {n_train/dt:,.0f} samples/s  ({dt:.1f}s)", flush=True)
train_s = time.time() - t0

ckpt = os.path.join(a.out, "model.pt"); torch.save(model.state_dict(), ckpt)
m2 = make_model(); m2.load_state_dict(torch.load(ckpt, map_location=dev)); m2.eval()
with torch.no_grad():
    if dev == "cuda": torch.cuda.synchronize()
    ti = time.time()
    with torch.autocast(**amp):
        preds = m2(Xte).argmax(1)
    if dev == "cuda": torch.cuda.synchronize()
    inf_ms = (time.time() - ti) * 1000
inf_acc = (preds == yte).float().mean().item()
print(f"[{host}] reloaded checkpoint; inference on {len(Xte)} samples: {inf_ms:.1f} ms, acc {inf_acc:.4f}", flush=True)

metrics = {"host": host, "gpu": gpu, "torch": torch.__version__, "cuda": torch.version.cuda, "epochs": a.epochs,
           "train_s": round(train_s, 2), "final_val_acc": hist[-1]["val_acc"], "inference_ms": round(inf_ms, 2),
           "inference_acc": inf_acc, "max_mem_mb": round(torch.cuda.max_memory_allocated() / 1e6) if dev == "cuda" else 0,
           "history": hist}
with open(os.path.join(a.out, "metrics.json"), "w") as f: json.dump(metrics, f, indent=2)
print("METRICS " + json.dumps({k: v for k, v in metrics.items() if k != "history"}), flush=True)
