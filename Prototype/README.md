# AutoGrade Full-Stack Setup (FastAPI + React + Redis/RQ)

This workspace now includes:

- `backend/`: FastAPI API + RQ worker
- `frontend/`: React (Vite) UI
- `docker-compose.yml`: Redis service

## Quick Start

From workspace root:

```bash
./setup.sh
./start.sh
```

This starts Redis, backend API, worker, and frontend. Press `Ctrl+C` in the `start.sh` terminal to stop app processes.

If Docker Desktop is not running, `start.sh` will try to reuse an existing Redis on port 6379 or start a local `redis-server` process if available.

If Redis is not available, `start.sh` now fails by default instead of silently falling back to in-memory mode.

This keeps Stop/Resume behavior consistent with the Redis/RQ worker model.

If you explicitly want single-process in-memory mode for development, start it with:

```bash
USE_INMEMORY_QUEUE=1 ./start.sh
```

## 1) Start Redis

From workspace root:

```bash
docker compose up -d redis
```

## 2) Start Backend API

From workspace root:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.app.main:app --reload --port 8000
```

## 3) Start Worker

Open another terminal in workspace root:

```bash
source .venv/bin/activate
python -m backend.worker
```

On macOS, the worker automatically uses `SimpleWorker` (non-forking mode) to avoid `Work-horse terminated unexpectedly; signal 6` crashes related to fork safety.

## 4) Start React Frontend

Open another terminal:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

## API Endpoints

- `GET /api/health`
- `GET /api/evaluators`
- `POST /api/jobs` (multipart upload: `main_zip`, `instructions_html`, optional `evaluator_key=auto|<plugin>`)
- `GET /api/jobs/{job_id}`
- `GET /api/jobs/{job_id}/stream` (SSE live stream of grading output + status packets)
- `GET /api/jobs/{job_id}/artifacts`
- `GET /api/jobs/{job_id}/artifacts/{filename}`

`GET /api/jobs/{job_id}` now includes evaluator metadata and confidence details when available.
When `evaluator_key=auto`, the backend infers `route_type` (code/essay/numeric/mcq) from instructions and maps to the current evaluator plugin.

## Notes

- Grading logic is now hosted under `backend/app/grading/legacy/autograder_core.py` and invoked through backend adapters/evaluators.
- Existing `.env` values from root are used by the grader (provider/model/api key settings).
- Job outputs are stored under `backend/data/jobs/<job_id>/output`.
