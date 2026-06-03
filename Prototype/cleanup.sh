#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$ROOT_DIR/.logs"
STOPPED_ANY="0"

note() {
  echo "[cleanup] $1"
}

mark_stopped() {
  STOPPED_ANY="1"
}

stop_matching_process() {
  local label="$1"
  local pattern="$2"
  local pids

  pids="$(pgrep -f "$pattern" 2>/dev/null || true)"
  if [[ -z "$pids" ]]; then
    return 0
  fi

  note "Stopping $label: $pids"
  pkill -f "$pattern" >/dev/null 2>&1 || true
  mark_stopped
}

stop_listener_on_port() {
  local port="$1"
  local label="$2"
  local pids

  if ! command -v lsof >/dev/null 2>&1; then
    return 0
  fi

  pids="$(lsof -ti tcp:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -z "$pids" ]]; then
    return 0
  fi

  note "Releasing port $port used by $label: $pids"
  kill $pids >/dev/null 2>&1 || true
  mark_stopped
}

stop_repo_redis_container() {
  if ! command -v docker >/dev/null 2>&1; then
    return 0
  fi

  if [[ ! -S /var/run/docker.sock && ! -S "$HOME/.docker/run/docker.sock" ]]; then
    return 0
  fi

  local redis_container_id
  redis_container_id="$(cd "$ROOT_DIR" && docker compose ps -q redis 2>/dev/null || true)"
  if [[ -z "$redis_container_id" ]]; then
    return 0
  fi

  note "Stopping docker compose redis service"
  (cd "$ROOT_DIR" && docker compose stop redis >/dev/null)
  mark_stopped
}

stop_local_redis() {
  local pids

  pids="$(pgrep -f 'redis-server --port 6379' 2>/dev/null || true)"
  if [[ -z "$pids" ]]; then
    return 0
  fi

  note "Stopping local redis-server on port 6379: $pids"
  pkill -f 'redis-server --port 6379' >/dev/null 2>&1 || true
  mark_stopped
}

note "Cleaning up leftover AutoGrade dev processes..."

stop_matching_process "backend API (module pattern)" "uvicorn backend.app.main:app"
stop_matching_process "RQ worker (module pattern)" "backend.worker"

stop_matching_process "backend API" "$ROOT_DIR/.venv/bin/python -m uvicorn backend.app.main:app"
stop_matching_process "RQ worker" "$ROOT_DIR/.venv/bin/python -m backend.worker"
stop_matching_process "frontend dev server" "$ROOT_DIR/frontend/node_modules/.bin/vite --host 0.0.0.0 --port 5173"

stop_listener_on_port 8000 "backend API"
stop_listener_on_port 5173 "frontend dev server"

stop_repo_redis_container
stop_local_redis

if [[ -f "$LOG_DIR/backend.log" ]]; then
  note "Recent logs remain in $LOG_DIR"
fi

if [[ "$STOPPED_ANY" == "1" ]]; then
  note "Cleanup complete. You can run ./start.sh now."
else
  note "No leftover AutoGrade dev processes were found."
fi