#!/bin/bash
# Developer launcher (macOS/Linux). Unlike the .command launchers this runs
# hot-reload servers: FastAPI on :8000 and Vite on :5173.
# Normal users should use "Start CivicWatch.command" instead.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
VENV="$ROOT/.venv"
VENV_PY="$VENV/bin/python"
export CIVICWATCH_PORT="${CIVICWATCH_PORT:-8000}"

if [ ! -x "$VENV_PY" ]; then
  echo "▸ Creating the virtual environment"
  "$PYTHON" -m venv "$VENV"
fi
echo "▸ Installing backend requirements"
"$VENV_PY" -m pip install -q --disable-pip-version-check -r backend/requirements.txt

echo "▸ Installing frontend dependencies"
(cd frontend && npm install --no-audit --no-fund --silent)

echo "▸ Ensuring a model is available"
"$VENV_PY" tools/download_models.py

cleanup() {
  trap - EXIT INT TERM
  kill 0 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "▸ Backend  http://127.0.0.1:${CIVICWATCH_PORT}  (API + OpenAPI docs at /docs)"
(
  cd backend
  PYTHONPATH="$ROOT/backend" "$VENV_PY" -m uvicorn civicwatch.main:app \
    --reload --host 127.0.0.1 --port "$CIVICWATCH_PORT"
) &

echo "▸ Frontend http://127.0.0.1:5173  (hot reload, proxies /api)"
(cd frontend && npm run dev -- --host 127.0.0.1) &

wait
