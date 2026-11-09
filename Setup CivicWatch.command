#!/bin/bash
# Setup CivicWatch.command — one-time, self-contained installation.
# Double-click in Finder. Safe to run again; completed steps are skipped.
#
#   Options (Terminal):  ./Setup\ CivicWatch.command --force     reinstall everything
#                        ./Setup\ CivicWatch.command --check     verify only, change nothing
#                        ./Setup\ CivicWatch.command --offline   no downloads (packages must exist)

cd "$(dirname "$0")" || exit 1
# shellcheck source=launchers/lib/common.sh
source launchers/lib/common.sh

FORCE=0
CHECK_ONLY=0
OFFLINE=0

for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --check|--verify) CHECK_ONLY=1 ;;
    --offline) OFFLINE=1 ;;
    -h|--help)
      echo "Usage: Setup CivicWatch.command [--force] [--check] [--offline]"
      echo "  --force    reinstall Python packages, Node modules and model weights"
      echo "  --check    verify the current installation without changing anything"
      echo "  --offline  never touch the network (dependencies must already be installed)"
      exit 0
      ;;
    *) die "Unknown option: $arg" ;;
  esac
done

ensure_dirs
# Mirror everything to a log so failures can be inspected later.
exec > >(tee -a "$SETUP_LOG") 2>&1

TOTAL_STEPS=9
STEP=0
step() {
  STEP=$((STEP + 1))
  printf "\n%s[%d/%d]%s %s\n" "$CW_BOLD" "$STEP" "$TOTAL_STEPS" "$CW_RESET" "$*"
}

cw_banner
echo "Repository: $CIVICWATCH_ROOT"
echo "Log file:   $SETUP_LOG"
echo "Started:    $(date)"

START_TS=$(date +%s)
require_macos

if [ "$CHECK_ONLY" = "0" ]; then
  require_disk_space 6
fi

# Only one setup at a time.
if ! mkdir "$RUN_DIR/setup.lock" 2>/dev/null; then
  if [ -n "$(find "$RUN_DIR/setup.lock" -maxdepth 0 -mmin +60 2>/dev/null)" ]; then
    log_warn "Removing a stale setup lock (older than an hour)."
    rm -rf "$RUN_DIR/setup.lock"
    mkdir "$RUN_DIR/setup.lock" 2>/dev/null || die "Could not create the setup lock."
  else
    die "Another Setup run is already in progress. Wait for it to finish."
  fi
fi
cleanup() {
  rm -rf "$RUN_DIR/setup.lock"
  if [ "${CW_EXIT_OK:-0}" != "1" ]; then
    echo
    echo "Setup stopped early. Full log: $SETUP_LOG"
  fi
}
trap cleanup EXIT INT TERM

# ---------------------------------------------------------------------------
step "Checking network access"
if [ "$OFFLINE" = "1" ]; then
  log_info "Offline mode — skipping network checks and downloads."
elif [ "$CHECK_ONLY" = "0" ]; then
  check_network
else
  log_info "Check-only mode — skipping network probe."
fi

# ---------------------------------------------------------------------------
step "Locating Python"
if [ "$CHECK_ONLY" = "0" ] && { [ "$FORCE" = "1" ] || [ ! -x "$PY" ]; }; then
  if [ "$FORCE" = "1" ] && [ -d "$VENV" ]; then
    log_warn "Removing the existing virtual environment (--force)."
    rm -rf "$VENV"
  fi
  PYTHON_SRC="$(ensure_python)"
  log_ok "Using $("$PYTHON_SRC" -c 'import sys; print(f"Python {sys.version.split()[0]}")') at $PYTHON_SRC"
  log_step "Creating the virtual environment"
  "$PYTHON_SRC" -m venv "$VENV" \
    || die "Could not create the virtual environment at $VENV"
  log_ok "Virtual environment created"
else
  [ -x "$PY" ] || die "The virtual environment is missing. Run Setup without --check."
  log_ok "Existing virtual environment: $("$PY" -c 'import sys; print(sys.version.split()[0])')"
fi

# ---------------------------------------------------------------------------
step "Installing Python packages"
REQ_HASH="$(shasum -a 256 "$CIVICWATCH_ROOT/backend/requirements.txt" | awk '{print $1}')"
DEPS_STAMP="$VENV/.civicwatch-deps-$REQ_HASH"
if [ "$CHECK_ONLY" = "1" ]; then
  [ -f "$DEPS_STAMP" ] || log_warn "Package stamp missing — run Setup to install dependencies."
  log_info "Skipped (check-only)."
elif [ -f "$DEPS_STAMP" ] && [ "$FORCE" = "0" ]; then
  log_ok "Dependencies already installed for this requirements file"
else
  log_info "First run downloads PyTorch and Ultralytics (1–2 GB); this takes several minutes."
  "$PY" -m pip install --disable-pip-version-check --progress-bar off \
    --upgrade pip setuptools wheel >>"$LOG_DIR/pip.log" 2>&1 \
    || die "Could not upgrade pip. See $LOG_DIR/pip.log"
  if ! "$PY" -m pip install --disable-pip-version-check --progress-bar off \
      -r "$CIVICWATCH_ROOT/backend/requirements.txt" >>"$LOG_DIR/pip.log" 2>&1; then
    tail_log "$LOG_DIR/pip.log" 25
    die "Python package installation failed. See $LOG_DIR/pip.log"
  fi
  rm -f "$VENV"/.civicwatch-deps-*
  touch "$DEPS_STAMP"
  log_ok "Python packages installed"
fi

# ---------------------------------------------------------------------------
step "Checking AI runtime (PyTorch device)"
DEVICE_REPORT="$("$PY" - <<'PY'
import json
try:
    import torch
    mps_built = bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_built())
    mps_ok = bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
    print(json.dumps({
        "torch": torch.__version__,
        "mps_built": mps_built,
        "mps_available": mps_ok,
        "device": "mps" if mps_ok else "cpu",
    }))
except Exception as exc:  # noqa: BLE001
    print(json.dumps({"error": str(exc)}))
PY
)"
if echo "$DEVICE_REPORT" | grep -q '"error"'; then
  die "PyTorch failed to import: $DEVICE_REPORT"
fi
log_ok "PyTorch $(echo "$DEVICE_REPORT" | sed -E 's/.*"torch": "([^"]+)".*/\1/')"
if echo "$DEVICE_REPORT" | grep -q '"mps_available": true'; then
  log_ok "Apple Silicon GPU (MPS) available — inference will use the GPU"
else
  log_warn "MPS not available — CivicWatch will use the CPU (works fine, just slower)"
  if [ "$(uname -m)" = "arm64" ]; then
    log_info "On Apple Silicon this usually means a CPU-only PyTorch wheel; re-run Setup --force."
  fi
fi

# ---------------------------------------------------------------------------
step "Preparing the web interface"
if [ "$CHECK_ONLY" = "1" ]; then
  [ -f "$CIVICWATCH_ROOT/frontend/dist/index.html" ] || log_warn "frontend/dist missing."
  log_info "Skipped (check-only)."
else
  NODE_BIN="$(ensure_node)"
  export PATH="$NODE_BIN:$PATH"
  log_ok "Node $(node --version) · npm $(npm --version)"
  pushd "$CIVICWATCH_ROOT/frontend" >/dev/null || die "frontend/ directory is missing."
  if [ "$FORCE" = "1" ]; then
    log_warn "Reinstalling frontend dependencies (--force)."
    rm -rf node_modules
  fi
  NEED_NPM=0
  if [ ! -d node_modules ]; then
    NEED_NPM=1
  elif [ -f package-lock.json ] && [ package-lock.json -nt node_modules/.package-lock.json ]; then
    NEED_NPM=1
  fi
  if [ "$NEED_NPM" = "1" ]; then
    log_step "Installing frontend dependencies"
    npm_ok=0
    if [ -f package-lock.json ] && npm ci --no-audit --no-fund >>"$LOG_DIR/npm.log" 2>&1; then
      npm_ok=1
    elif npm install --no-audit --no-fund >>"$LOG_DIR/npm.log" 2>&1; then
      npm_ok=1
    fi
    if [ "$npm_ok" != "1" ]; then
      tail_log "$LOG_DIR/npm.log" 25
      popd >/dev/null
      die "Frontend dependency installation failed. See $LOG_DIR/npm.log"
    fi
    log_ok "Frontend dependencies installed"
  else
    log_ok "Frontend dependencies already installed"
  fi
  log_step "Building the interface"
  if npm run build >>"$LOG_DIR/npm.log" 2>&1; then
    log_ok "Interface built into frontend/dist"
  else
    tail_log "$LOG_DIR/npm.log" 25
    popd >/dev/null
    die "Frontend build failed. See $LOG_DIR/npm.log"
  fi
  popd >/dev/null
fi

# ---------------------------------------------------------------------------
step "Model weights"
if [ "$CHECK_ONLY" = "1" ]; then
  "$PY" "$CIVICWATCH_ROOT/tools/download_models.py" --check || true
elif [ "$OFFLINE" = "1" ]; then
  "$PY" "$CIVICWATCH_ROOT/tools/download_models.py" --check \
    || log_warn "Model weights missing — connect to the internet and run Setup again."
else
  FORCE_FLAG=""
  [ "$FORCE" = "1" ] && FORCE_FLAG="--force"
  # shellcheck disable=SC2086
  "$PY" "$CIVICWATCH_ROOT/tools/download_models.py" $FORCE_FLAG \
    || die "Model download/validation failed. Re-run Setup once the network is stable."
fi

# ---------------------------------------------------------------------------
step "Initialising the local database"
if [ "$CHECK_ONLY" = "1" ]; then
  log_info "Skipped (check-only)."
else
  PYTHONPATH="$CIVICWATCH_ROOT/backend" "$PY" - <<'PY' || die "Database initialisation failed."
import sys
from civicwatch import db
from civicwatch.config import get_paths

paths = get_paths().ensure()
applied = db.init_db()
print(f"    database: {paths.db}")
print(f"    migrations applied this run: {applied or 'none (already current)'}")
PY
  log_ok "SQLite schema ready"
fi

# ---------------------------------------------------------------------------
step "Verifying the installation"
if [ "$CHECK_ONLY" = "1" ]; then
  "$PY" "$CIVICWATCH_ROOT/tools/diagnostics.py" || die "Verification found problems (see above)."
else
  "$PY" "$CIVICWATCH_ROOT/tools/diagnostics.py" || die "Verification failed. Fix the items above and run Setup again."
fi

# ---------------------------------------------------------------------------
step "Recording setup state"
if [ "$CHECK_ONLY" = "1" ]; then
  log_info "Skipped (check-only) — nothing was installed."
else
"$PY" - "$CIVICWATCH_ROOT" "$DEVICE_REPORT" <<'PY'
import json, platform, sys, time
from pathlib import Path

root = Path(sys.argv[1])
device = json.loads(sys.argv[2])
state = {
    "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
    "python_executable": str(root / ".venv" / "bin" / "python"),
    "device": device,
}
target = root / "data" / "setup-complete.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(state, indent=2))
print(f"    wrote {target}")
PY
log_ok "Setup state saved"
fi

CW_EXIT_OK=1
ELAPSED=$(( $(date +%s) - START_TS ))

printf "\n%s\n" "${CW_GREEN}${CW_BOLD}Setup complete${CW_RESET} (${ELAPSED}s)"
cat <<EOF

  Start CivicWatch:   double-click "Start CivicWatch.command"
                      or open the CivicWatch app icon

  Then visit:         $BASE_URL

  Sample footage:     samples/demo-street-traffic.avi

  Documentation:      README.md · docs/TROUBLESHOOTING.md

  Everything runs on this Mac. No footage, frame or incident leaves it.
  Read docs/PRIVACY_AND_LEGAL.md before using real municipal footage.

EOF
if [ -t 0 ]; then
  read -r -p "Press Return to close this window… " _
fi
