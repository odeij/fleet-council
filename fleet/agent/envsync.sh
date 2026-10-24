#!/usr/bin/env bash
# envsync.sh <requirements.txt> — make ~/env match the file exactly (uv pip sync)
req="$1"; uv="$HOME/.local/bin/uv"
if [ ! -x "$uv" ]; then curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh || exit 1; fi
[ -x "$HOME/env/bin/python" ] || "$uv" venv "$HOME/env" --python 3.12.14 --managed-python || exit 1
nice -n 10 "$uv" pip sync --python "$HOME/env/bin/python" --index-strategy unsafe-best-match "$req" 2>&1 | grep -Ev '^ [+-] ' | tail -5
bash "$HOME/rig-runs/.bin/envcheck.sh" "$req"
