import time

from emailqueue.ratelimiter import SlidingWindowRateLimiter


class TestSlidingWindowRateLimiter:
    def test_allows_up_to_the_limit_within_the_window(self, redis_client):
        limiter = SlidingWindowRateLimiter(redis_client, "test:limit", limit=5, window_seconds=1)
        assert all(limiter.try_acquire(member=str(i)) for i in range(5))

    def test_rejects_beyond_the_limit_within_the_window(self, redis_client):
        limiter = SlidingWindowRateLimiter(redis_client, "test:limit", limit=5, window_seconds=1)
        for i in range(5):
            assert limiter.try_acquire(member=str(i))
        assert limiter.try_acquire(member="overflow") is False

    def test_admits_again_once_the_window_slides_past(self, redis_client):
        limiter = SlidingWindowRateLimiter(redis_client, "test:limit", limit=2, window_seconds=0.3)
        assert limiter.try_acquire(member="a")
        assert limiter.try_acquire(member="b")
        assert limiter.try_acquire(member="c") is False
        time.sleep(0.35)
        assert limiter.try_acquire(member="d") is True

    def test_fails_closed_on_redis_error_by_default(self, redis_client, monkeypatch):
        limiter = SlidingWindowRateLimiter(redis_client, "test:limit", limit=5, window_seconds=1)
        monkeypatch.setattr(
            limiter, "_script", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("redis down"))
        )
        assert limiter.try_acquire() is False

    def test_can_be_configured_to_fail_open(self, redis_client, monkeypatch):
        limiter = SlidingWindowRateLimiter(
            redis_client, "test:limit", limit=5, window_seconds=1, fail_open=True
        )
        monkeypatch.setattr(
            limiter, "_script", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("redis down"))
        )
        assert limiter.try_acquire() is True

    def test_burst_of_500_calls_never_exceeds_the_limit_in_any_one_second_window(self, redis_client):
        """The assignment's required scale (500 submissions against a
        hard cap), exercised directly against the atomic Lua script at
        limit=20/window=1s instead of 200/60s so it runs in under a
        second instead of several real minutes. The algorithm being
        proven is identical at either scale. See test_tasks.py for the
        Celery-task-level test proving retry behaviour and that no job
        is ever silently lost."""
        limiter = SlidingWindowRateLimiter(redis_client, "test:burst", limit=20, window_seconds=1)

        admitted_at = []
        for i in range(500):
            if limiter.try_acquire(member=str(i)):
                admitted_at.append(time.time())

        assert len(admitted_at) < 500, "burst of 500 must actually be throttled, not all admitted"

        admitted_at.sort()
        for start in admitted_at:
            count_in_window = sum(1 for t in admitted_at if start <= t < start + 1)
            assert count_in_window <= 20
