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
KG_PORT=8008
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
echo "[1/10] Starting Control Plane on port $CONTROL_PLANE_PORT..."
python -m control_plane.server &
PIDS+=($!)
wait_for_port $CONTROL_PLANE_PORT "Control Plane"

CP_URL="http://127.0.0.1:$CONTROL_PLANE_PORT"

# ── Summarizer Agent ────────────────────────────────────────
echo "[2/10] Starting Summarizer Agent on port $SUMMARIZER_PORT..."
CONTROL_PLANE_URL="$CP_URL" \
SUMMARIZER_AGENT_URL="http://127.0.0.1:$SUMMARIZER_PORT" \
  python -m agents.summarizer.server &
PIDS+=($!)
wait_for_port $SUMMARIZER_PORT "Summarizer Agent"

# ── Relevancy Agent ─────────────────────────────────────────
echo "[3/10] Starting Relevancy Agent on port $RELEVANCY_PORT..."
CONTROL_PLANE_URL="$CP_URL" \
RELEVANCY_AGENT_URL="http://127.0.0.1:$RELEVANCY_PORT" \
  python -m agents.relevancy.server &
PIDS+=($!)
wait_for_port $RELEVANCY_PORT "Relevancy Agent"

# ── Echo Agent ──────────────────────────────────────────────
echo "[4/10] Starting Echo Agent on port $ECHO_PORT..."
CONTROL_PLANE_URL="$CP_URL" \
ECHO_AGENT_URL="http://127.0.0.1:$ECHO_PORT" \
DOWNSTREAM_AGENT_URL="http://127.0.0.1:$SUMMARIZER_PORT" \
  python -m agents.echo.server &
PIDS+=($!)
wait_for_port $ECHO_PORT "Echo Agent"

# ── Extraction Agent ───────────────────────────────────────
echo "[5/10] Starting Extraction Agent on port $EXTRACTION_PORT..."
CONTROL_PLANE_URL="$CP_URL" \
EXTRACTION_AGENT_URL="http://127.0.0.1:$EXTRACTION_PORT" \
  python -m agents.extraction_agent.server &
PIDS+=($!)
wait_for_port $EXTRACTION_PORT "Extraction Agent"

# ── Lead Analyst Agent ─────────────────────────────────────
echo "[6/10] Starting Lead Analyst Agent on port $LEAD_ANALYST_PORT..."
CONTROL_PLANE_URL="$CP_URL" \
LEAD_ANALYST_AGENT_URL="http://127.0.0.1:$LEAD_ANALYST_PORT" \
  python -m agents.lead_analyst.server &
PIDS+=($!)
wait_for_port $LEAD_ANALYST_PORT "Lead Analyst Agent"

# ── Specialist Agent ──────────────────────────────────────────
echo "[7/10] Starting Specialist Agent on port $SPECIALIST_PORT..."
CONTROL_PLANE_URL="$CP_URL" \
SPECIALIST_AGENT_URL="http://127.0.0.1:$SPECIALIST_PORT" \
  python -m agents.specialist_agent.server &
PIDS+=($!)
wait_for_port $SPECIALIST_PORT "Specialist Agent"

# ── Probability Forecasting Agent ────────────────────────────
echo "[8/10] Starting Probability Agent on port $PROBABILITY_PORT..."
CONTROL_PLANE_URL="$CP_URL" \
PROBABILITY_AGENT_URL="http://127.0.0.1:$PROBABILITY_PORT" \
  python -m agents.probability_agent.server &
PIDS+=($!)
wait_for_port $PROBABILITY_PORT "Probability Agent"

# ── Knowledge Graph Agent ────────────────────────────────────────────
echo "[9/10] Starting Knowledge Graph Agent on port $KG_PORT..."
MEM0_NEO4J_URL="${MEM0_NEO4J_URL:-bolt://localhost:7687}" \
MEM0_NEO4J_USER="${MEM0_NEO4J_USER:-neo4j}" \
MEM0_NEO4J_PASSWORD="${MEM0_NEO4J_PASSWORD:-password}" \
MEM0_PG_DSN="${MEM0_PG_DSN:-postgresql://mem0:mem0_password@localhost:5433/mem0_kg}" \
CONTROL_PLANE_URL="$CP_URL" \
KNOWLEDGE_GRAPH_AGENT_URL="http://127.0.0.1:$KG_PORT" \
  python -m agents.knowledge_graph.server &
PIDS+=($!)
wait_for_port $KG_PORT "Knowledge Graph Agent"

# ── Memory Agent ────────────────────────────────────────────────────────────
MEMORY_PORT=8009

echo "[10/11] Starting Memory Agent on port $MEMORY_PORT..."
MEMORY_NEO4J_URL="${MEMORY_NEO4J_URL:-bolt://localhost:7687}" \
MEMORY_NEO4J_USER="${MEMORY_NEO4J_USER:-neo4j}" \
MEMORY_NEO4J_PASSWORD="${MEMORY_NEO4J_PASSWORD:-mc_password}" \
MEMORY_PG_DSN="${MEMORY_PG_DSN:-postgresql://mc:mc_password@localhost:5432/missioncontrol}" \
MEMORY_EMBEDDING_MODEL="${MEMORY_EMBEDDING_MODEL}" \
MEMORY_EMBEDDING_DIMS="${MEMORY_EMBEDDING_DIMS}" \
CONTROL_PLANE_URL="$CP_URL" \
MEMORY_AGENT_URL="http://127.0.0.1:$MEMORY_PORT" \
  python -m agents.memory_agent.server &
PIDS+=($!)
wait_for_port $MEMORY_PORT "Memory Agent"

# ── Dashboard ───────────────────────────────────────────────
echo "[11/11] Starting Dashboard on port $DASHBOARD_PORT..."
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
echo "  Knowledge Graph: http://localhost:$KG_PORT"
echo "  Memory Agent:   http://localhost:$MEMORY_PORT"
echo ""
echo "Press Ctrl+C to stop all components."
echo ""

# Keep script alive until interrupted
wait
