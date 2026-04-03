from rq import Worker

from app.queueing import get_redis
from app.settings import QUEUE_NAME


def main() -> None:
    worker = Worker([QUEUE_NAME], connection=get_redis())
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
