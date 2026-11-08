#!/bin/bash
# Shared helpers for the CivicWatch launchers.
# Sourced by Setup/Start/Stop/Create App. Not meant to be run directly.

set -o pipefail

# Resolve the repository root from this file's location (launchers/lib/../..).
if [ -z "${CIVICWATCH_ROOT:-}" ]; then
  _cw_source="${BASH_SOURCE[0]}"
  while [ -L "$_cw_source" ]; do
    _cw_target="$(readlink "$_cw_source")"
    case "$_cw_target" in
      /*) _cw_source="$_cw_target" ;;
      *) _cw_source="$(dirname "$_cw_source")/$_cw_target" ;;
    esac
  done
  CIVICWATCH_ROOT="$(cd "$(dirname "$_cw_source")/../.." && pwd)"
fi
export CIVICWATCH_ROOT

VENV="${CIVICWATCH_VENV:-$CIVICWATCH_ROOT/.venv}"
PY="$VENV/bin/python"
RUNTIME="$CIVICWATCH_ROOT/tools/runtime"
NODE_DIR="$RUNTIME/node"
PY_STANDALONE_DIR="$RUNTIME/python"
DATA_DIR="${CIVICWATCH_DATA_DIR:-$CIVICWATCH_ROOT/data}"
RUN_DIR="$DATA_DIR/run"
LOG_DIR="$DATA_DIR/logs"
SETUP_LOG="$LOG_DIR/setup.log"
PID_FILE="$RUN_DIR/civicwatch.pid"
BASE_URL="http://127.0.0.1:${CIVICWATCH_PORT:-8000}"

if [ -t 1 ]; then
  CW_RED=$'\033[31m'; CW_GREEN=$'\033[32m'; CW_YELLOW=$'\033[33m'
  CW_BLUE=$'\033[34m'; CW_DIM=$'\033[2m'; CW_BOLD=$'\033[1m'; CW_RESET=$'\033[0m'
else
  CW_RED=""; CW_GREEN=""; CW_YELLOW=""; CW_BLUE=""; CW_DIM=""; CW_BOLD=""; CW_RESET=""
fi

cw_banner() {
  printf "\n%s\n" "${CW_BOLD}CivicWatch AI Studio${CW_RESET}"
  printf "%s\n\n" "${CW_DIM}Local-first civic monitoring · macOS${CW_RESET}"
}

log_step() { printf "%s▸%s %s\n" "$CW_BLUE" "$CW_RESET" "$*"; }
log_ok()   { printf "  %s✓%s %s\n" "$CW_GREEN" "$CW_RESET" "$*"; }
log_warn() { printf "  %s!%s %s\n" "$CW_YELLOW" "$CW_RESET" "$*"; }
log_err()  { printf "  %s✗%s %s\n" "$CW_RED" "$CW_RESET" "$*" >&2; }
log_info() { printf "  %s%s%s\n" "$CW_DIM" "$*" "$CW_RESET"; }

die() {
  log_err "$*"
  echo
  printf "%sSetup cannot continue.%s\n" "$CW_BOLD" "$CW_RESET"
  if [ -f "$SETUP_LOG" ]; then
    printf "Details: %s\n" "$SETUP_LOG"
  fi
  exit 1
}

ensure_dirs() {
  mkdir -p "$RUN_DIR" "$LOG_DIR" "$DATA_DIR"
}

require_macos() {
  if [ "$(uname -s)" != "Darwin" ]; then
    if [ "${CIVICWATCH_ALLOW_NON_MACOS:-0}" = "1" ]; then
      log_warn "Non-macOS host detected — running in developer mode (MPS will be unavailable)."
      return 0
    fi
    die "These launchers target macOS. On other systems use tools/dev.sh instead."
  fi
}

require_disk_space() {
  local need_gb="${1:-6}"
  local avail_kb
  avail_kb="$(df -Pk "$CIVICWATCH_ROOT" | awk 'NR==2 {print $4}')"
  local avail_gb=$((avail_kb / 1024 / 1024))
  if [ "$avail_gb" -lt "$need_gb" ]; then
    die "Only ${avail_gb} GB free. CivicWatch needs about ${need_gb} GB for Python packages and model weights."
  fi
  log_info "Free disk space: ${avail_gb} GB"
}

check_network() {
  if ! curl -fsS --max-time 12 -o /dev/null "https://pypi.org/simple/" >/dev/null 2>&1; then
    die "Cannot reach pypi.org. An internet connection is required for the one-time setup. The app works offline afterwards."
  fi
  log_info "Network reachable (pypi.org)"
}

# ---------------------------------------------------------------------------
# Python discovery
# ---------------------------------------------------------------------------

python_version_ok() {
  local candidate="$1"
  [ -x "$candidate" ] || return 1
  "$candidate" - <<'PY' >/dev/null 2>&1
import sys
sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 13) else 1)
PY
}

find_system_python() {
  local candidate
  for candidate in \
    "$PY_STANDALONE_DIR/bin/python3" \
    python3.12 python3.11 python3.13 python3.10 python3 \
    /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3 \
    /usr/local/bin/python3.12 /usr/local/bin/python3.11 /usr/local/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/3.11/bin/python3
  do
    if command -v "$candidate" >/dev/null 2>&1 || [ -x "$candidate" ]; then
      if python_version_ok "$candidate"; then
        echo "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

download_standalone_python() {
  log_step "Installing a private Python 3.12 runtime (no admin required)"
  local arch="arm64"
  [ "$(uname -m)" = "arm64" ] || arch="x86_64"
  mkdir -p "$RUNTIME"
  local api="https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"
  local json="$RUNTIME/pbs-release.json"
  log_info "Looking up the latest Python build…"
  curl -fsSL --retry 2 --max-time 60 "$api" -o "$json" \
    || die "Could not reach GitHub to download Python. Install Python 3.12 from python.org, then run Setup again."
  local asset
  asset="$(grep -o '"browser_download_url":[[:space:]]*"[^"]*"' "$json" \
    | sed -e 's/.*"\(https[^"]*\)"/\1/' \
    | grep 'cpython-3\.12' | grep 'apple-darwin' | grep "$arch" \
    | grep 'install_only\.tar\.gz$' | head -1)"
  rm -f "$json"
  if [ -z "$asset" ]; then
    die "Could not locate a standalone Python build. Install Python 3.12 from python.org, then run Setup again."
  fi
  log_info "Downloading $(basename "$asset")"
  curl -fL --retry 3 --progress-bar "$asset" -o "$RUNTIME/python.tar.gz" \
    || die "Python runtime download failed. Check your connection and retry."
  rm -rf "$PY_STANDALONE_DIR.tmp"
  mkdir -p "$PY_STANDALONE_DIR.tmp"
  tar -xzf "$RUNTIME/python.tar.gz" -C "$PY_STANDALONE_DIR.tmp" \
    || die "Could not extract the Python runtime."
  rm -f "$RUNTIME/python.tar.gz"
  local inner
  inner="$(find "$PY_STANDALONE_DIR.tmp" -maxdepth 3 -name python3 -type f -perm +111 | head -1)"
  [ -n "$inner" ] || die "The downloaded Python runtime is incomplete."
  local prefix
  prefix="$(cd "$(dirname "$inner")/.." && pwd)"
  rm -rf "$PY_STANDALONE_DIR"
  mv "$prefix" "$PY_STANDALONE_DIR"
  rm -rf "$PY_STANDALONE_DIR.tmp"
  log_ok "Private Python installed at tools/runtime/python"
}

ensure_python() {
  local found
  found="$(find_system_python || true)"
  if [ -z "$found" ]; then
    log_warn "No compatible Python (3.10–3.13) was found."
    download_standalone_python
    found="$(find_system_python || true)"
    [ -n "$found" ] || die "Python installation could not be completed."
  fi
  echo "$found"
}

# ---------------------------------------------------------------------------
# Node discovery
# ---------------------------------------------------------------------------

node_version_ok() {
  command -v node >/dev/null 2>&1 || return 1
  local major
  major="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
  [ "$major" -ge 20 ] 2>/dev/null
}

find_node_bin() {
  if [ -x "$NODE_DIR/bin/node" ]; then
    echo "$NODE_DIR/bin"
    return 0
  fi
  if node_version_ok; then
    dirname "$(command -v node)"
    return 0
  fi
  return 1
}

resolve_node_lts() {
  local version
  version="$(curl -fsSL --max-time 30 https://nodejs.org/dist/index.json 2>/dev/null \
    | tr '}' '\n' | grep '"lts":"' | grep -o '"version":"v[0-9.]*"' | head -5 \
    | sed -e 's/.*"v/v/' -e 's/"//' | while read -r v; do
        major="${v%%.*}"
        if [ "${major:-0}" -ge 20 ] 2>/dev/null; then echo "$v"; break; fi
      done | head -1)"
  if [ -z "$version" ]; then
    version="22.11.0"
    log_warn "Could not query nodejs.org; using known-good Node ${version}."
  fi
  echo "$version"
}

download_node() {
  log_step "Installing a private Node.js runtime (no admin required)"
  local arch="arm64"
  [ "$(uname -m)" = "arm64" ] || arch="x64"
  local version
  version="$(resolve_node_lts)"
  local base="https://nodejs.org/dist/${version}"
  local tarball="node-${version}-darwin-${arch}.tar.gz"
  mkdir -p "$RUNTIME"
  log_info "Downloading ${tarball}"
  curl -fL --retry 3 --progress-bar "${base}/${tarball}" -o "$RUNTIME/${tarball}" \
    || die "Node.js download failed. Check your connection and retry."
  if curl -fsSL --max-time 60 "${base}/SHASUMS256.txt" -o "$RUNTIME/SHASUMS256.txt"; then
    if (cd "$RUNTIME" && grep " ${tarball}\$" SHASUMS256.txt | shasum -a 256 -c - >/dev/null 2>&1); then
      log_ok "Node.js checksum verified"
    else
      rm -f "$RUNTIME/${tarball}"
      die "Node.js download failed checksum verification."
    fi
  else
    log_warn "Could not fetch the Node.js checksum file; continuing with TLS-verified download."
  fi
  mkdir -p "$NODE_DIR"
  tar -xzf "$RUNTIME/${tarball}" -C "$RUNTIME"
  rm -rf "$NODE_DIR"
  mv "$RUNTIME/node-${version}-darwin-${arch}" "$NODE_DIR"
  rm -f "$RUNTIME/${tarball}" "$RUNTIME/SHASUMS256.txt"
  [ -x "$NODE_DIR/bin/node" ] || die "Node.js installation is incomplete."
  log_ok "Private Node.js installed at tools/runtime/node"
}

ensure_node() {
  local dir
  if dir="$(find_node_bin)"; then
    echo "$dir"
    return 0
  fi
  log_warn "Node.js 20+ was not found."
  download_node
  if dir="$(find_node_bin)"; then
    echo "$dir"
    return 0
  fi
  die "Node.js installation could not be completed."
}

# ---------------------------------------------------------------------------
# Health helpers
# ---------------------------------------------------------------------------

# Returns 0 when the server answers, 2 when the process we started has died,
# 1 when the timeout elapsed. Pass the PID to detect an early exit.
wait_for_health() {
  local timeout="${1:-90}"
  local pid="${2:-}"
  local waited=0
  while [ "$waited" -lt "$timeout" ]; do
    if curl -fsS --max-time 2 "$BASE_URL/api/health" >/dev/null 2>&1; then
      return 0
    fi
    if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
      return 2
    fi
    sleep 1
    waited=$((waited + 1))
    if [ $((waited % 10)) -eq 0 ]; then
      log_info "Waiting for the local server… ${waited}s"
    fi
  done
  return 1
}

civicwatch_running() {
  curl -fsS --max-time 3 "$BASE_URL/api/health" 2>/dev/null | grep -q "CivicWatch"
}

port_in_use() {
  local port="${CIVICWATCH_PORT:-8000}"
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1 && return 0
    return 1
  fi
  # Fallback for machines without lsof: try a loopback TCP connect.
  (exec 3<>"/dev/tcp/127.0.0.1/$port") >/dev/null 2>&1
}

tail_log() {
  local file="$1"
  local lines="${2:-30}"
  if [ -f "$file" ]; then
    echo
    printf "%s--- last %s lines of %s ---%s\n" "$CW_DIM" "$lines" "$file" "$CW_RESET"
    tail -n "$lines" "$file"
    printf "%s--- end of log ---%s\n" "$CW_DIM" "$CW_RESET"
  fi
}
