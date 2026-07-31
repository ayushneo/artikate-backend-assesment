import json

from .redis_client import get_redis_client

DEAD_LETTER_KEY = "emailqueue:dead_letter"


def push_dead_letter(task_id, args, kwargs, reason):
    payload = json.dumps({"task_id": task_id, "args": args, "kwargs": kwargs, "reason": reason})
    get_redis_client().lpush(DEAD_LETTER_KEY, payload)


def dead_letter_count():
    return get_redis_client().llen(DEAD_LETTER_KEY)


def dead_letter_items():
    return [json.loads(item) for item in get_redis_client().lrange(DEAD_LETTER_KEY, 0, -1)]
