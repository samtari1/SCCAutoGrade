from pathlib import Path
import platform

from dotenv import load_dotenv
from rq import SimpleWorker, Worker


ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env", override=True)
load_dotenv(ROOT_DIR / "backend" / ".env", override=True)

from backend.app.queueing import get_redis
from backend.app.settings import QUEUE_NAME


def main() -> None:
    worker_cls = SimpleWorker if platform.system() == "Darwin" else Worker
    worker = worker_cls([QUEUE_NAME], connection=get_redis())
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
