from redis import Redis
from rq import Queue

from .settings import QUEUE_NAME, REDIS_URL


def get_redis() -> Redis:
    return Redis.from_url(REDIS_URL)


def get_queue() -> Queue:
    return Queue(name=QUEUE_NAME, connection=get_redis())
