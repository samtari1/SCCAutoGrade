#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$ROOT_DIR/.logs"
mkdir -p "$LOG_DIR"
REDIS_MODE="unknown"
INMEMORY_MODE="0"
PIDS=()

if [[ ! -x "$ROOT_DIR/.venv/bin/python" ]]; then
  echo "[start] Missing virtual environment. Run ./setup.sh first."
  exit 1
fi

if [[ ! -d "$ROOT_DIR/frontend/node_modules" ]]; then
  echo "[start] Missing frontend dependencies. Run ./setup.sh first."
  exit 1
fi

cd "$ROOT_DIR"
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  echo "[start] Starting Redis via docker compose..."
  docker compose up -d redis >/dev/null
  REDIS_MODE="docker"
elif command -v redis-cli >/dev/null 2>&1 && redis-cli -p 6379 ping >/dev/null 2>&1; then
  echo "[start] Redis already running on port 6379, reusing it."
  REDIS_MODE="external"
elif command -v redis-server >/dev/null 2>&1; then
  echo "[start] Docker is unavailable. Starting local redis-server on port 6379..."
  (
    redis-server --port 6379
  ) >"$LOG_DIR/redis.log" 2>&1 &
  PIDS+=("$!")
  REDIS_MODE="local"
else
  echo "[start] Redis is unavailable. Falling back to in-memory queue mode."
  INMEMORY_MODE="1"
  REDIS_MODE="none"
fi

cleanup() {
  echo ""
  echo "[start] Stopping dev processes..."
  for pid in "${PIDS[@]:-}"; do
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
      pkill -P "$pid" >/dev/null 2>&1 || true
    fi
  done
  if [[ "$REDIS_MODE" == "docker" ]]; then
    echo "[start] Stopped app processes. Redis is still running in Docker."
  elif [[ "$REDIS_MODE" == "external" ]]; then
    echo "[start] Stopped app processes. External Redis was not modified."
  else
    echo "[start] Stopped app processes including local redis-server."
  fi
}

trap cleanup EXIT INT TERM

echo "[start] Launching backend API (http://localhost:8000)..."
(
  cd "$ROOT_DIR"
  if [[ "$INMEMORY_MODE" == "1" ]]; then
    USE_INMEMORY_QUEUE=1 ./.venv/bin/python -m uvicorn backend.app.main:app --reload --port 8000
  else
    ./.venv/bin/python -m uvicorn backend.app.main:app --reload --port 8000
  fi
) >"$LOG_DIR/backend.log" 2>&1 &
PIDS+=("$!")

if [[ "$INMEMORY_MODE" == "1" ]]; then
  echo "[start] In-memory mode enabled: skipping RQ worker."
else
  echo "[start] Launching RQ worker..."
  (
    cd "$ROOT_DIR"
    ./.venv/bin/python backend/worker.py
  ) >"$LOG_DIR/worker.log" 2>&1 &
  PIDS+=("$!")
fi

echo "[start] Launching frontend (http://localhost:5173)..."
(
  cd "$ROOT_DIR/frontend"
  npm run dev -- --host 0.0.0.0 --port 5173
) >"$LOG_DIR/frontend.log" 2>&1 &
PIDS+=("$!")

echo "[start] Dev stack started. Logs:"
echo "  - $LOG_DIR/backend.log"
if [[ "$INMEMORY_MODE" != "1" ]]; then
  echo "  - $LOG_DIR/worker.log"
fi
echo "  - $LOG_DIR/frontend.log"
if [[ "$REDIS_MODE" == "local" ]]; then
  echo "  - $LOG_DIR/redis.log"
fi
if [[ "$INMEMORY_MODE" == "1" ]]; then
  echo "[start] Queue backend: in-memory (single-process dev mode)."
fi
echo "[start] Press Ctrl+C to stop all app processes."

wait
