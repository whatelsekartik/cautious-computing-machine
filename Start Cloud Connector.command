#!/bin/bash
# Start Cloud Connector.command — runs local inference and syncs incidents to
# the hosted CivicWatch dashboard. Keep this window open while cameras are live.
#
# Configuration (in order of precedence):
#   1. CIVICWATCH_CLOUD_URL / CIVICWATCH_CONNECTOR_TOKEN in the environment
#   2. config/connector.env in this folder
#   3. prompts below (saved to config/connector.env, mode 600)

cd "$(dirname "$0")" || exit 1
# shellcheck source=launchers/lib/common.sh
source launchers/lib/common.sh

ensure_dirs
cw_banner
require_macos

ENV_FILE="$CIVICWATCH_ROOT/config/connector.env"

if [ ! -x "$PY" ]; then
  die "CivicWatch is not installed yet. Run \"Setup CivicWatch.command\" first."
fi

if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  set -a; . "$ENV_FILE"; set +a
fi

CLOUD_URL="${CIVICWATCH_CLOUD_URL:-}"
TOKEN="${CIVICWATCH_CONNECTOR_TOKEN:-}"

if [ -z "$CLOUD_URL" ] && [ -t 0 ]; then
  echo "Cloud dashboard URL (for example https://civicwatch-city.vercel.app):"
  read -r CLOUD_URL
fi

if [ -z "$TOKEN" ] && [ -t 0 ]; then
  echo "Connector token (shown once when you create it in Settings → Local connectors):"
  read -r -s TOKEN
  echo
fi

if [ -z "$CLOUD_URL" ] || [ -z "$TOKEN" ]; then
  echo
  log_err "Both the cloud URL and a connector token are required."
  echo "  Create a connector in the dashboard: Settings → Local connectors → Create."
  echo "  Then either export the two variables or write them to:"
  echo "    $ENV_FILE"
  exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
  umask 077
  cat >"$ENV_FILE" <<ENVEOF
# CivicWatch connector credentials — keep private, never commit.
CIVICWATCH_CLOUD_URL=$CLOUD_URL
CIVICWATCH_CONNECTOR_TOKEN=$TOKEN
ENVEOF
  chmod 600 "$ENV_FILE"
  log_ok "Saved credentials to config/connector.env (mode 600, git-ignored)"
fi

log_step "Checking the local installation"
"$PY" tools/diagnostics.py --quick >/dev/null 2>&1 \
  && log_ok "Local runtime healthy" \
  || log_warn "Diagnostics reported problems — run tools/diagnostics.py for details"

echo
log_step "Starting the connector (Ctrl-C stops it; queued incidents are kept)"
echo "  Footage and RTSP credentials never leave this Mac."
echo "  Only incidents, metadata and selected evidence are uploaded."
echo

exec "$PY" tools/connector.py \
  --cloud-url "$CLOUD_URL" \
  --token "$TOKEN" \
  --interval "${CIVICWATCH_CONNECTOR_INTERVAL:-20}"
