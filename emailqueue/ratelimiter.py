"""
Redis sliding-window rate limiter, atomic via a Lua script.

Sliding window over fixed window: fixed window (INCR + EXPIRE) lets a
burst straddle the boundary and double the limit (200 at 0:59, 200 more
at 1:00, 400 in under 2s). Over token bucket: token bucket needs a
background refill process, or refill-on-read math duplicated at every
call site. A sorted set trimmed to the trailing window_seconds gives an
exact count with neither problem.

Lua script over MULTI/EXEC: admission has to trim expired entries
(ZREMRANGEBYSCORE), read the count (ZCARD), then conditionally write
(ZADD). MULTI/EXEC queues commands blindly and can't branch on an
intermediate result. Redis runs a script atomically on its
single-threaded command loop, so nothing can interleave between trim,
count and write.
"""
import time

_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms - window_ms)
local count = redis.call('ZCARD', key)

if count < limit then
    redis.call('ZADD', key, now_ms, member)
    redis.call('PEXPIRE', key, window_ms)
    return 1
else
    return 0
end
"""


class RateLimitExceeded(Exception):
    """Raised by callers when try_acquire() returns False and they want
    it treated as an error."""


class SlidingWindowRateLimiter:
    def __init__(self, redis_client, key, limit, window_seconds, fail_open=False):
        self.redis = redis_client
        self.key = key
        self.limit = limit
        self.window_ms = int(window_seconds * 1000)
        self.fail_open = fail_open
        self._script = self.redis.register_script(_SLIDING_WINDOW_LUA)

    def try_acquire(self, member=None):
        """True if admitted, False if the caller should back off.

        Fails closed on Redis errors unless fail_open=True. For a hard
        third-party cap, a Redis outage should delay emails, not risk an
        unthrottled burst that gets the account banned.
        """
        member = member or f"{time.time_ns()}"
        now_ms = time.time() * 1000
        try:
            allowed = self._script(keys=[self.key], args=[now_ms, self.window_ms, self.limit, member])
        except Exception:
            return self.fail_open
        return bool(allowed)
