from pathlib import Path
import os

from redis import Redis


ROOT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT_DIR / "backend"
DATA_DIR = BACKEND_DIR / "data"
JOBS_DIR = DATA_DIR / "jobs"

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
QUEUE_BASE_NAME = os.getenv("RQ_QUEUE_NAME", "grading").strip() or "grading"
ASSIGNMENT_QUEUE_LANES = ("general", "enum", "array", "variables")
DEFAULT_QUEUE_NAME = f"{QUEUE_BASE_NAME}-general"
PARALLEL_QUEUE_NAMES = tuple(f"{QUEUE_BASE_NAME}-{lane}" for lane in ASSIGNMENT_QUEUE_LANES)


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


def _redis_available() -> bool:
    try:
        client = Redis.from_url(REDIS_URL, socket_connect_timeout=1, socket_timeout=1)
        return bool(client.ping())
    except Exception:
        return False


USE_INMEMORY_QUEUE = _env_truthy("USE_INMEMORY_QUEUE") or not _redis_available()


def ensure_directories() -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
