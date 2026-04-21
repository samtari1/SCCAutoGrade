from pathlib import Path
import os
import platform

from dotenv import load_dotenv
from rq import SimpleWorker, Worker


ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env", override=True)
load_dotenv(ROOT_DIR / "backend" / ".env", override=True)

from backend.app.queueing import get_redis
from backend.app.settings import DEFAULT_QUEUE_NAME


def main() -> None:
    queue_name = (os.getenv("RQ_WORKER_QUEUE") or DEFAULT_QUEUE_NAME).strip() or DEFAULT_QUEUE_NAME
    worker_cls = SimpleWorker if platform.system() == "Darwin" else Worker
    worker = worker_cls([queue_name], connection=get_redis())
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
