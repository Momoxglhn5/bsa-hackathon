#!/usr/bin/env bash
# Local demo stack: recorded real-market replay (port 8000), complete demo dataset (port 8001)
# and the React dev server (http://localhost:5173). Ctrl+C stops all three.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=backend/.venv/bin/python
if [ ! -x "$PY" ]; then
  echo "backend/.venv absent : suivre la section Développement du README." >&2
  exit 1
fi
if [ ! -d frontend/node_modules ]; then
  npm --prefix frontend ci
fi

pids=()
cleanup() { kill ${pids[@]+"${pids[@]}"} 2>/dev/null || true; }
trap cleanup EXIT INT TERM

ANALYTICS_SNAPSHOT=docs/demo/radar/source/analytics_snapshot.json ANALYTICS_REPLAY=true \
  "$PY" -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 &
pids+=($!)
# Fresh demo dataset at each launch: prices stay inside the engines' 5-minute freshness window.
"$PY" backend/scripts/build_demo_dataset.py
DOMINO_DATA_DIR=backend/data/demo ANALYTICS_INPUTS=backend/data/demo/analytics_inputs.json ANALYTICS_REPLAY=true \
  "$PY" -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8001 &
pids+=($!)

npm --prefix frontend run dev
