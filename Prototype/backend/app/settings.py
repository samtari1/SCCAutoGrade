from pathlib import Path
import os


ROOT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT_DIR / "backend"
DATA_DIR = BACKEND_DIR / "data"
JOBS_DIR = DATA_DIR / "jobs"

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
QUEUE_BASE_NAME = os.getenv("RQ_QUEUE_NAME", "grading").strip() or "grading"
ASSIGNMENT_QUEUE_LANES = ("general", "enum", "array", "variables")
DEFAULT_QUEUE_NAME = f"{QUEUE_BASE_NAME}-general"
PARALLEL_QUEUE_NAMES = tuple(f"{QUEUE_BASE_NAME}-{lane}" for lane in ASSIGNMENT_QUEUE_LANES)
USE_INMEMORY_QUEUE = os.getenv("USE_INMEMORY_QUEUE", "false").strip().lower() in {"1", "true", "yes", "on"}


def ensure_directories() -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
