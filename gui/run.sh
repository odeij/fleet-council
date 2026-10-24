#!/bin/bash
# Fleet GUI: start the local server (if it is not running) and open the page in the browser.
#   bash gui/run.sh            open http://127.0.0.1:8787
#   bash gui/run.sh stop       stop the server
#   bash gui/run.sh log        show the server log
# The server polls every rig in the hub's ~/fleet/hosts.conf over ssh every 20 s. Set FLEET_HUB to the hub's ssh alias.
HERE="$(cd "$(dirname "$0")" && pwd)"; PORT=8787
case "${1:-}" in
  stop) pkill -f "$HERE/server.py" && echo "stopped" || echo "not running"; exit 0 ;;
  log) tail -n 40 "$HERE/server.log"; exit 0 ;;
esac
if ! lsof -nP -iTCP:$PORT -sTCP:LISTEN > /dev/null 2>&1; then
  nohup python3 "$HERE/server.py" --port $PORT --interval 20 > "$HERE/server.log" 2>&1 &
  echo "server started (log $HERE/server.log); first poll takes ~15 s"
  sleep 2
else
  echo "server already running"
fi
command -v open >/dev/null && open "http://127.0.0.1:$PORT" || xdg-open "http://127.0.0.1:$PORT"
