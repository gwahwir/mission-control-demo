#!/usr/bin/env bash
# Run all Mission Control components locally (no containers).
# Usage: bash run-local.sh
#
# Prerequisites:
#   - Python venv activated with: pip install -r requirements.txt
#   - Node modules installed:     cd dashboard && npm install
#
# Optional env vars (set before running):
#   OPENAI_API_KEY   - required for the summarizer agent
#   OPENAI_MODEL     - defaults to gpt-4o-mini
#   DATABASE_URL     - PostgreSQL DSN (omit for in-memory task store)
#   REDIS_URL        - Redis URL (omit for in-memory pub/sub)
#   LOG_LEVEL        - defaults to INFO

set -euo pipefail

ECHO_PORT=8001
SUMMARIZER_PORT=8002
CONTROL_PLANE_PORT=8000
DASHBOARD_PORT=5173

PIDS=()

cleanup() {
  echo ""
  echo "Shutting down all components..."
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null && wait "$pid" 2>/dev/null || true
  done
  echo "All components stopped."
  exit 0
}

trap cleanup SIGINT SIGTERM

wait_for_port() {
  local port=$1
  local name=$2
  local retries=30
  while ! python -c "import socket; s=socket.socket(); s.settimeout(1); s.connect(('127.0.0.1',$port)); s.close()" 2>/dev/null; do
    retries=$((retries - 1))
    if [ "$retries" -le 0 ]; then
      echo "[ERROR] $name failed to start on port $port"
      cleanup
    fi
    sleep 1
  done
  echo "[OK] $name is ready on port $port"
}

echo "=== Mission Control — Local Dev ==="
echo ""

# ── Summarizer Agent (start first — echo-agent forwards to it) ──
echo "[1/4] Starting Summarizer Agent on port $SUMMARIZER_PORT..."
python -m agents.summarizer.server &
PIDS+=($!)
wait_for_port $SUMMARIZER_PORT "Summarizer Agent"

# ── Echo Agent ──────────────────────────────────────────────
echo "[2/4] Starting Echo Agent on port $ECHO_PORT..."
DOWNSTREAM_AGENT_URL="http://127.0.0.1:$SUMMARIZER_PORT" \
  python -m agents.echo.server &
PIDS+=($!)
wait_for_port $SUMMARIZER_PORT "Summarizer Agent"

# ── Control Plane ───────────────────────────────────────────
echo "[3/4] Starting Control Plane on port $CONTROL_PLANE_PORT..."
AGENT_URLS="echo-agent@http://127.0.0.1:$ECHO_PORT,summarizer@http://127.0.0.1:$SUMMARIZER_PORT" \
  python -m control_plane.server &
PIDS+=($!)
wait_for_port $CONTROL_PLANE_PORT "Control Plane"

# ── Dashboard ───────────────────────────────────────────────
echo "[4/4] Starting Dashboard on port $DASHBOARD_PORT..."
cd dashboard
npm run dev -- --host 2>&1 &
PIDS+=($!)
cd ..
wait_for_port $DASHBOARD_PORT "Dashboard"

echo ""
echo "=== All components running ==="
echo "  Dashboard:      http://localhost:$DASHBOARD_PORT"
echo "  Control Plane:  http://localhost:$CONTROL_PLANE_PORT"
echo "  Echo Agent:     http://localhost:$ECHO_PORT"
echo "  Summarizer:     http://localhost:$SUMMARIZER_PORT"
echo ""
echo "Press Ctrl+C to stop all components."
echo ""

# Keep script alive until interrupted
wait
