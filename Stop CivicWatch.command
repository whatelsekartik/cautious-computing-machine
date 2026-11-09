#!/bin/bash
# Stop CivicWatch.command — stops the local server started by Start CivicWatch.

cd "$(dirname "$0")" || exit 1
# shellcheck source=launchers/lib/common.sh
source launchers/lib/common.sh

ensure_dirs
cw_banner
require_macos

stop_pid() {
  local pid="$1"
  local command
  command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  case "$command" in
    *civicwatch*) ;;
    *)
      log_warn "Process $pid is not CivicWatch; leaving it alone."
      return 1
      ;;
  esac
  log_step "Stopping CivicWatch (process $pid)"
  kill -TERM "$pid" 2>/dev/null || true
  local waited=0
  while [ "$waited" -lt 15 ]; do
    if ! kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done
  log_warn "It did not exit cleanly; forcing shutdown."
  kill -KILL "$pid" 2>/dev/null || true
  sleep 1
  ! kill -0 "$pid" 2>/dev/null
}

STOPPED=0

if [ -f "$PID_FILE" ]; then
  PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
    stop_pid "$PID" && STOPPED=1
  fi
  rm -f "$PID_FILE"
fi

# Catch servers started from Terminal or an older launcher version.
for PID in $(pgrep -f "python.*-m civicwatch" 2>/dev/null); do
  stop_pid "$PID" && STOPPED=1
done

# Last resort: whoever owns the loopback port, if it is our app.
if civicwatch_running; then
  for PID in $(lsof -nP -tiTCP:"${CIVICWATCH_PORT:-8000}" -sTCP:LISTEN 2>/dev/null); do
    stop_pid "$PID" && STOPPED=1
  done
fi

if [ "$STOPPED" = "1" ]; then
  if port_in_use; then
    log_warn "Port is still in use by another application."
  else
    log_ok "CivicWatch stopped. Your footage, incidents and settings are still in $DATA_DIR."
  fi
else
  log_info "CivicWatch is not running."
fi

if [ -t 0 ]; then
  read -r -p "Press Return to close this window… " _
fi
