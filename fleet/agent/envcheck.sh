#!/usr/bin/env bash
# envcheck.sh <requirements.txt> — prints MATCH or the diff between ~/env and the file
req="$1"; uv="$HOME/.local/bin/uv"
[ -x "$HOME/env/bin/python" ] || { echo "$(hostname): NO ~/env"; exit 1; }
d=$("$uv" pip freeze --python "$HOME/env/bin/python" 2>/dev/null | sort | diff - <(grep -E '^[A-Za-z0-9]' "$req" | sort))
if [ -z "$d" ]; then echo "$(hostname): MATCH ($(grep -cE '^[A-Za-z0-9]' "$req") packages)"; else echo "$(hostname): DIFF"; echo "$d" | sed 's/^/    /'; exit 1; fi
