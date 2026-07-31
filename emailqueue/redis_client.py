import redis
from django.conf import settings


def get_redis_client():
    """Fresh client per call. redis-py pools connections internally,
    and skipping lru_cache means settings overrides in tests take effect
    immediately."""
    return redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
