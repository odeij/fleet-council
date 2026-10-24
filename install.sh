#!/usr/bin/env bash
# install.sh [hub|controller]  — install fleet into ~/fleet and the fleet skill into ~/.claude/skills/fleet.
# Never overwrites an existing hosts.conf, controller.conf or experience/LESSONS.md.
set -euo pipefail
ROLE="${1:-hub}"; SRC="$(cd "$(dirname "$0")" && pwd)"; DST="$HOME/fleet"
mkdir -p "$DST/experience/data" "$DST/results" "$HOME/.claude/skills/fleet"
rsync -a --exclude __pycache__ "$SRC/fleet/bin" "$SRC/fleet/agent" "$SRC/fleet/lib" "$SRC/fleet/jobs" "$SRC/fleet/requirements.txt" "$SRC/fleet/README.md" "$DST/"
cp "$SRC/fleet/experience/README.md" "$DST/experience/"
[ -f "$DST/experience/LESSONS.md" ] || cp "$SRC/fleet/experience/LESSONS.md" "$DST/experience/"
[ -f "$DST/hosts.conf" ] || { cp "$SRC/fleet/hosts.conf.example" "$DST/hosts.conf"; echo "edit $DST/hosts.conf"; }
if [ "$ROLE" = controller ]; then [ -f "$DST/controller.conf" ] || { cp "$SRC/fleet/controller.conf.example" "$DST/controller.conf"; echo "edit $DST/controller.conf"; }; fi
cp "$SRC/skills/fleet/SKILL.md" "$HOME/.claude/skills/fleet/SKILL.md"
chmod +x "$DST/bin/rig"
case ":$PATH:" in *":$DST/bin:"*) ;; *) echo "add to your shell rc:  export PATH=\"\$HOME/fleet/bin:\$PATH\"";; esac
echo "installed fleet ($ROLE). Next: rig setup all && rig hosts   (see docs/SETUP.md)"
