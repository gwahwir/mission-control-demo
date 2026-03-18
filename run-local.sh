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
RELEVANCY_PORT=8003
EXTRACTION_PORT=8004
LEAD_ANALYST_PORT=8005
SPECIALIST_PORT=8006
PROBABILITY_PORT=8007
CONTROL_PLANE_PORT=8000
DASHBOARD_PORT=5173

PIDS=()

cleanup() {
  echo ""
  echo "Shutting down all components (agents first, then control plane)..."
  # Shut down in reverse order so agents can deregister while the control plane is still running
  for (( i=${#PIDS[@]}-1; i>=0; i-- )); do
    kill "${PIDS[$i]}" 2>/dev/null && wait "${PIDS[$i]}" 2>/dev/null || true
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

# ── Control Plane (start first so agents can self-register) ──
echo "[1/9] Starting Control Plane on port $CONTROL_PLANE_PORT..."
python -m control_plane.server &
PIDS+=($!)
wait_for_port $CONTROL_PLANE_PORT "Control Plane"

CP_URL="http://127.0.0.1:$CONTROL_PLANE_PORT"

# ── Summarizer Agent ────────────────────────────────────────
echo "[2/9] Starting Summarizer Agent on port $SUMMARIZER_PORT..."
CONTROL_PLANE_URL="$CP_URL" \
SUMMARIZER_AGENT_URL="http://127.0.0.1:$SUMMARIZER_PORT" \
  python -m agents.summarizer.server &
PIDS+=($!)
wait_for_port $SUMMARIZER_PORT "Summarizer Agent"

# ── Relevancy Agent ─────────────────────────────────────────
echo "[3/9] Starting Relevancy Agent on port $RELEVANCY_PORT..."
CONTROL_PLANE_URL="$CP_URL" \
RELEVANCY_AGENT_URL="http://127.0.0.1:$RELEVANCY_PORT" \
  python -m agents.relevancy.server &
PIDS+=($!)
wait_for_port $RELEVANCY_PORT "Relevancy Agent"

# ── Echo Agent ──────────────────────────────────────────────
echo "[4/9] Starting Echo Agent on port $ECHO_PORT..."
CONTROL_PLANE_URL="$CP_URL" \
ECHO_AGENT_URL="http://127.0.0.1:$ECHO_PORT" \
DOWNSTREAM_AGENT_URL="http://127.0.0.1:$SUMMARIZER_PORT" \
  python -m agents.echo.server &
PIDS+=($!)
wait_for_port $ECHO_PORT "Echo Agent"

# ── Extraction Agent ───────────────────────────────────────
echo "[5/9] Starting Extraction Agent on port $EXTRACTION_PORT..."
CONTROL_PLANE_URL="$CP_URL" \
EXTRACTION_AGENT_URL="http://127.0.0.1:$EXTRACTION_PORT" \
  python -m agents.extraction_agent.server &
PIDS+=($!)
wait_for_port $EXTRACTION_PORT "Extraction Agent"

# ── Lead Analyst Agent ─────────────────────────────────────
echo "[6/9] Starting Lead Analyst Agent on port $LEAD_ANALYST_PORT..."
CONTROL_PLANE_URL="$CP_URL" \
LEAD_ANALYST_AGENT_URL="http://127.0.0.1:$LEAD_ANALYST_PORT" \
  python -m agents.lead_analyst.server &
PIDS+=($!)
wait_for_port $LEAD_ANALYST_PORT "Lead Analyst Agent"

# ── Specialist Agent ──────────────────────────────────────────
echo "[7/9] Starting Specialist Agent on port $SPECIALIST_PORT..."
CONTROL_PLANE_URL="$CP_URL" \
SPECIALIST_AGENT_URL="http://127.0.0.1:$SPECIALIST_PORT" \
  python -m agents.specialist_agent.server &
PIDS+=($!)
wait_for_port $SPECIALIST_PORT "Specialist Agent"

# ── Probability Forecasting Agent ────────────────────────────
echo "[8/9] Starting Probability Agent on port $PROBABILITY_PORT..."
CONTROL_PLANE_URL="$CP_URL" \
PROBABILITY_AGENT_URL="http://127.0.0.1:$PROBABILITY_PORT" \
  python -m agents.probability_agent.server &
PIDS+=($!)
wait_for_port $PROBABILITY_PORT "Probability Agent"

# ── Dashboard ───────────────────────────────────────────────
echo "[9/9] Starting Dashboard on port $DASHBOARD_PORT..."
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
echo "  Relevancy:      http://localhost:$RELEVANCY_PORT"
echo "  Extraction:     http://localhost:$EXTRACTION_PORT"
echo "  Lead Analyst:   http://localhost:$LEAD_ANALYST_PORT"
echo "  Specialist:     http://localhost:$SPECIALIST_PORT"
echo "  Probability:    http://localhost:$PROBABILITY_PORT"
echo ""
echo "Press Ctrl+C to stop all components."
echo ""

# Keep script alive until interrupted
wait
