#!/bin/bash
# Start CivicWatch.command — launches the local server and opens the studio.
#
#   ./Start\ CivicWatch.command --no-browser   start without opening a window
#   ./Start\ CivicWatch.command --foreground   run in this Terminal (Ctrl-C stops)

cd "$(dirname "$0")" || exit 1
# shellcheck source=launchers/lib/common.sh
source launchers/lib/common.sh

OPEN_BROWSER=1
FOREGROUND=0
for arg in "$@"; do
  case "$arg" in
    --no-browser) OPEN_BROWSER=0 ;;
    --foreground) FOREGROUND=1 ;;
    -h|--help)
      echo "Usage: Start CivicWatch.command [--no-browser] [--foreground]"
      exit 0
      ;;
    *) die "Unknown option: $arg" ;;
  esac
done

ensure_dirs
cw_banner
require_macos

# ---------------------------------------------------------------------------
# Already running?
# ---------------------------------------------------------------------------
if civicwatch_running; then
  log_ok "CivicWatch is already running at $BASE_URL"
  if [ "$OPEN_BROWSER" = "1" ]; then open "$BASE_URL"; fi
  echo
  echo "  Stop it with:  \"Stop CivicWatch.command\""
  exit 0
fi

if port_in_use; then
  log_err "Port $((${CIVICWATCH_PORT:-8000})) is already used by another application:"
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"${CIVICWATCH_PORT:-8000}" -sTCP:LISTEN | sed 's/^/    /'
  fi
  echo
  echo "  Quit that application, or start CivicWatch on another port:"
  echo "    CIVICWATCH_PORT=8010 ./Start\\ CivicWatch.command"
  exit 1
fi

# ---------------------------------------------------------------------------
# Installation checks — fail early with a clear instruction, not a stack trace.
# ---------------------------------------------------------------------------
log_step "Checking the installation"
[ -x "$PY" ] || die "The virtual environment is missing. Run \"Setup CivicWatch.command\" first."
[ -f "$CIVICWATCH_ROOT/frontend/dist/index.html" ] \
  || die "The interface has not been built. Run \"Setup CivicWatch.command\" first."
if ! "$PY" - "$CIVICWATCH_ROOT" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[1]) / "backend"))
from civicwatch.detect import registry

installed = [m["id"] for m in registry.list_builtin_models() if registry.weights_path(m["id"]).exists()]
if not installed:
    print("  No model weights are installed.")
    raise SystemExit(1)
print(f"  Model ready: {', '.join(installed)}")
PY
then
  die "No AI model is installed. Run \"Setup CivicWatch.command\" (it downloads and validates one)."
fi

# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------
log_step "Starting the local server on $BASE_URL"
cd "$CIVICWATCH_ROOT/backend" || die "backend/ directory is missing."
export PYTHONPATH="$CIVICWATCH_ROOT/backend"
export CIVICWATCH_HOST="${CIVICWATCH_HOST:-127.0.0.1}"

if [ "$FOREGROUND" = "1" ]; then
  log_info "Foreground mode — press Ctrl-C to stop."
  exec "$PY" -m civicwatch
fi

: >"$LOG_DIR/backend.log"
nohup "$PY" -m civicwatch >>"$LOG_DIR/backend.log" 2>&1 &
SERVER_PID=$!
echo "$SERVER_PID" >"$PID_FILE"
disown "$SERVER_PID" 2>/dev/null || true
log_info "Server process $SERVER_PID · log: $LOG_DIR/backend.log"

wait_for_health 90 "$SERVER_PID"
HEALTH=$?
if [ "$HEALTH" -ne 0 ]; then
  if [ "$HEALTH" = "2" ]; then
    log_err "The server process exited during startup."
  else
    log_err "The server did not become ready within 90 seconds."
  fi
  tail_log "$LOG_DIR/backend.log" 30
  kill "$SERVER_PID" 2>/dev/null
  rm -f "$PID_FILE"
  echo
  echo "Common causes:"
  echo "  · a firewall blocking the loopback port"
  echo "  · insufficient memory (close other apps and retry)"
  echo "  · a previous run left a half-installed environment — re-run Setup"
  echo "Full log: $LOG_DIR/backend.log"
  exit 1
fi

DEVICE="$("$PY" - "$BASE_URL" <<'PY' 2>/dev/null || true
import json, sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1] + "/api/system/info", timeout=5) as response:
        data = json.load(response)
    device = data.get("device", {})
    print(f"{device.get('device', 'cpu')} · {device.get('reason', '')}".strip(" ·"))
except Exception:
    pass
PY
)"
log_ok "CivicWatch is running${DEVICE:+ (inference device: $DEVICE)}"

if [ "$OPEN_BROWSER" = "1" ]; then
  open "$BASE_URL"
  log_ok "Opened $BASE_URL in your browser"
fi

cat <<EOF

  Studio:      $BASE_URL
  Data:        $DATA_DIR
  Logs:        $LOG_DIR
  Stop:        double-click "Stop CivicWatch.command"
               (or run: kill \$(cat "$PID_FILE"))

  Footage is processed on this Mac only. The server listens on 127.0.0.1.

EOF
if [ -t 0 ]; then
  read -r -p "Press Return to close this window (CivicWatch keeps running)… " _
fi
