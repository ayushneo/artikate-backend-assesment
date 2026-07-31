import pytest

from emailqueue.redis_client import get_redis_client


@pytest.fixture
def redis_client():
    """Real Redis (started by docker-compose), flushed before and after
    each test so rate-limit windows, dedupe keys, and the dead-letter list
    never leak state between tests."""
    client = get_redis_client()
    client.flushdb()
    yield client
    client.flushdb()
