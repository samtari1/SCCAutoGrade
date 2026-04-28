#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$ROOT_DIR/.." && pwd)"
API_SERVICE="sccautograde-api"
WORKER_TEMPLATE="sccautograde-worker@"
DEFAULT_VITE_API_BASE="https://autograde.dryangai.com"

log() {
  echo "[deploy] $*"
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    log "Required command not found: $1"
    exit 1
  }
}

extract_env_var() {
  local key="$1"
  local file="$2"
  if [[ -f "$file" ]]; then
    grep -E "^${key}=" "$file" | tail -n 1 | sed -E "s/^${key}=//" | tr -d '"' || true
  fi
}

resolve_queue_base() {
  local from_runtime="${RQ_QUEUE_NAME:-}"
  local from_backend_env
  local from_root_env

  if [[ -n "$from_runtime" ]]; then
    echo "$from_runtime"
    return
  fi

  from_backend_env="$(extract_env_var "RQ_QUEUE_NAME" "$ROOT_DIR/backend/.env")"
  if [[ -n "$from_backend_env" ]]; then
    echo "$from_backend_env"
    return
  fi

  from_root_env="$(extract_env_var "RQ_QUEUE_NAME" "$ROOT_DIR/.env")"
  if [[ -n "$from_root_env" ]]; then
    echo "$from_root_env"
    return
  fi

  echo "grading"
}

resolve_vite_api_base() {
  local from_runtime="${DEPLOY_VITE_API_BASE:-}"
  local from_frontend_env

  if [[ -n "$from_runtime" ]]; then
    echo "$from_runtime"
    return
  fi

  from_frontend_env="$(extract_env_var "VITE_API_BASE" "$ROOT_DIR/frontend/.env")"
  if [[ -n "$from_frontend_env" ]]; then
    echo "$from_frontend_env"
    return
  fi

  echo "$DEFAULT_VITE_API_BASE"
}

run_as_root() {
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    log "This step requires root privileges, but sudo is not available."
    exit 1
  fi
}

require_cmd git
require_cmd npm
require_cmd curl
require_cmd systemctl

log "Using repo directory: $REPO_DIR"
log "Using app directory: $ROOT_DIR"

log "Pulling latest code from origin..."
git -C "$REPO_DIR" fetch origin
CURRENT_BRANCH="$(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD)"
git -C "$REPO_DIR" pull --ff-only origin "$CURRENT_BRANCH"

log "Running setup to sync backend/frontend dependencies..."
"$ROOT_DIR/setup.sh"

VITE_API_BASE_VALUE="$(resolve_vite_api_base)"
log "Building frontend with VITE_API_BASE=$VITE_API_BASE_VALUE"
(
  cd "$ROOT_DIR/frontend"
  VITE_API_BASE="$VITE_API_BASE_VALUE" npm run build
)

QUEUE_BASE_NAME="$(resolve_queue_base)"
WORKER_SERVICES=(
  "${WORKER_TEMPLATE}${QUEUE_BASE_NAME}-general"
  "${WORKER_TEMPLATE}${QUEUE_BASE_NAME}-enum"
  "${WORKER_TEMPLATE}${QUEUE_BASE_NAME}-array"
  "${WORKER_TEMPLATE}${QUEUE_BASE_NAME}-variables"
)

log "Restarting API service: $API_SERVICE"
run_as_root systemctl restart "$API_SERVICE"

log "Restarting worker services..."
for svc in "${WORKER_SERVICES[@]}"; do
  run_as_root systemctl restart "$svc"
done

log "Reloading Apache..."
run_as_root systemctl reload apache2

log "Checking service status..."
run_as_root systemctl is-active --quiet "$API_SERVICE"
for svc in "${WORKER_SERVICES[@]}"; do
  run_as_root systemctl is-active --quiet "$svc"
done

log "Running health checks..."
curl --fail --silent --show-error http://127.0.0.1:8010/api/health >/dev/null
curl --fail --silent --show-error "$VITE_API_BASE_VALUE/api/health" >/dev/null

log "Deployment completed successfully."
log "API service: $API_SERVICE"
for svc in "${WORKER_SERVICES[@]}"; do
  log "Worker service: $svc"
done