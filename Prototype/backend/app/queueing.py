from typing import Optional

from redis import Redis
from rq import Queue

from .settings import DEFAULT_QUEUE_NAME, PARALLEL_QUEUE_NAMES, REDIS_URL


def get_redis() -> Redis:
    return Redis.from_url(REDIS_URL)


def get_queue(name: Optional[str] = None) -> Queue:
    queue_name = (name or DEFAULT_QUEUE_NAME).strip() or DEFAULT_QUEUE_NAME
    return Queue(name=queue_name, connection=get_redis())


def get_parallel_queue_names() -> tuple[str, ...]:
    return PARALLEL_QUEUE_NAMES
