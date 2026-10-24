# Setup, step by step

Target: one **hub** (an always-on GPU box), any number of **GPU hosts**, and optionally a **controller** (your laptop)
that drives everything from one Claude Code session. Linux on the GPU hosts; macOS or Linux on the controller.

## 1. Network: Tailscale on every machine

```bash
curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up
tailscale status          # every machine should list the others
```

No port forwarding, no AnyDesk, no public IPs: every hop below is ssh over the tailnet.

## 2. Passwordless ssh from the hub and the controller to every host

```bash
ssh-keygen -t ed25519            # once per machine that drives the fleet (hub, controller)
ssh-copy-id user@gpu-box-a       # repeat for every host (and the hub, from the controller)
```

Give each host an alias in `~/.ssh/config` (the names `hosts.conf` refers to):

```
Host gpu-box-a
  HostName gpu-box-a            # Tailscale MagicDNS name or 100.x address
  User you
```

Check: `ssh -o BatchMode=yes gpu-box-a nvidia-smi -L` must print the GPUs without a prompt.
Each host also needs `tmux`, `rsync` and an NVIDIA driver.

## 3. Install fleet

On the hub:

```bash
git clone https://github.com/odeij/fleet-council && cd fleet-council
./install.sh hub
export PATH="$HOME/fleet/bin:$PATH"           # add to ~/.bashrc
$EDITOR ~/fleet/hosts.conf                     # hub = local; one row per GPU host with its GPU indices
```

On the controller (laptop), the same with `./install.sh controller`, a `hosts.conf` that lists the hub by its ssh
alias instead of `local`, and `RIG_HUB=<hub name>` in `~/fleet/controller.conf`.

## 4. Push the agent scripts and build identical environments

```bash
rig setup all            # copies fleet/agent/* to ~/rig-runs/.bin on every host
rig env-sync all         # uv venv ~/env on every host, pinned by ~/fleet/requirements.txt (runs as a job)
rig wait all envsync && rig env-check all      # every host must say MATCH
rig hosts                # GPUs, torch, CUDA, disk for every host
```

`requirements.txt` pins torch for CUDA 12.8 wheels. Edit the pins for your driver, never `pip install` on one host.

## 5. Smoke test every GPU

```bash
rig run all smoke -d ~/fleet/jobs/smoke -- python train.py --epochs 3
rig wait all smoke && rig collect all smoke
cat ~/fleet/results/*/smoke/out/metrics.json
```

## 6. Turn on the experience loop (on the hub)

```bash
crontab -e      # paste fleet/crontab.example: telemetry every minute, network probe hourly, rig learn nightly
rig sample --net && rig learn && rig experience
```

## 7. Try the learned scheduler

```bash
python ~/fleet/jobs/example/make_manifest.py ~/dispatch-demo 24
rig dispatch ~/dispatch-demo/manifest.json --dry-run    # plan + predicted makespan
rig dispatch ~/dispatch-demo/manifest.json              # run it; per-lane summary at the end
rig learn                                               # the next prediction uses what just happened
```

## 8. Dashboard (controller)

```bash
FLEET_HUB=<hub ssh alias> bash gui/run.sh        # http://127.0.0.1:8787, polls every host every 20 s
```

Read-only by design: it only tails log files each host's collector lists, and runs no other remote commands.
No cluster yet? `bash gui/run.sh demo` serves a simulated fleet (badged "Demo data") to try the UI.
`?theme=light` or `?theme=dark` in the URL forces a theme. Options, API and code layout: [gui/README.md](../gui/README.md).

## 9. Give Claude Code the skills

`install.sh` already copied `skills/fleet/SKILL.md` to `~/.claude/skills/fleet/`. Fill in the Hosts table there with your
real cards. Then install the council (see `skills/agent-council/README.md`). In Claude Code:

- "train these three configs on the rigs and give me the metrics" → fleet skill
- "summon the council: why is lane B 40% slower than predicted?" → agent-council skill

## Optional: Ray

For many tiny tasks you may prefer Ray placement over `rig dispatch`: a Ray head on the hub, workers on the hosts, all
using the same `~/env` (add `ray` to the pins). `rig` and Ray coexist; pin Ray tasks with `resources={"node:<ip>": 0.01}`
when the hub GPU must stay free.
