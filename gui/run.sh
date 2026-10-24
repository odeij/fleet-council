#!/usr/bin/env bash
# Start the fleet GUI in the background and open it in the browser.
#
#   bash gui/run.sh          poll the real fleet (hub alias from $FLEET_HUB, default "hub")
#   bash gui/run.sh demo     simulated fleet, no ssh needed
#   bash gui/run.sh stop     stop the server
#   bash gui/run.sh log      show the server log
#
# Port: $FLEET_GUI_PORT (default 8787). Poll interval: $FLEET_GUI_INTERVAL (default 20 s).
set -euo pipefail

readonly HERE="$(cd "$(dirname "$0")" && pwd)"
readonly PORT="${FLEET_GUI_PORT:-8787}"
readonly INTERVAL="${FLEET_GUI_INTERVAL:-20}"
readonly LOG="${HERE}/server.log"
readonly URL="http://127.0.0.1:${PORT}"

is_listening() {
  lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN > /dev/null 2>&1
}

open_page() {
  if command -v open > /dev/null; then
    open "${URL}"
  elif command -v xdg-open > /dev/null; then
    xdg-open "${URL}"
  else
    echo "open ${URL}"
  fi
}

start() {
  if is_listening; then
    echo "already running on ${URL}"
  else
    nohup python3 "${HERE}/server.py" --port "${PORT}" --interval "${INTERVAL}" "$@" \
      > "${LOG}" 2>&1 &
    echo "started on ${URL} (log: ${LOG})"
    sleep 2
  fi
  open_page
}

case "${1:-}" in
  stop) pkill -f "${HERE}/server.py" && echo "stopped" || echo "not running" ;;
  log) tail -n 40 "${LOG}" ;;
  demo) start --demo ;;
  "") start ;;
  *) echo "usage: $0 [demo|stop|log]" >&2; exit 2 ;;
esac
