#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

echo "[setup] Checking toolchain..."
command -v docker >/dev/null 2>&1 || { echo "[setup] docker is required"; exit 1; }
command -v npm >/dev/null 2>&1 || { echo "[setup] npm is required"; exit 1; }

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "[setup] python is required"
  exit 1
fi

echo "[setup] Creating virtual environment..."
"$PYTHON_BIN" -m venv .venv

echo "[setup] Installing backend dependencies..."
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r backend/requirements.txt

echo "[setup] Installing frontend dependencies..."
cd "$ROOT_DIR/frontend"
npm install
cd "$ROOT_DIR"

if [[ ! -f "$ROOT_DIR/.env" && -f "$ROOT_DIR/example.env" ]]; then
  echo "[setup] Creating .env from example.env"
  cp "$ROOT_DIR/example.env" "$ROOT_DIR/.env"
fi

if [[ ! -f "$ROOT_DIR/frontend/.env" && -f "$ROOT_DIR/frontend/.env.example" ]]; then
  echo "[setup] Creating frontend/.env from frontend/.env.example"
  cp "$ROOT_DIR/frontend/.env.example" "$ROOT_DIR/frontend/.env"
fi

if [[ ! -f "$ROOT_DIR/backend/.env" && -f "$ROOT_DIR/backend/.env.example" ]]; then
  echo "[setup] Creating backend/.env from backend/.env.example"
  cp "$ROOT_DIR/backend/.env.example" "$ROOT_DIR/backend/.env"
fi

echo "[setup] Done. Next: ./start.sh"
