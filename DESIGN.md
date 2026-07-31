# DESIGN.md: Section 2, Rate-Limited Async Job Queue

Scenario: transactional emails, 200/minute hard cap, bursts of 2,000
requests in under 10 seconds during flash sales. Can't lose jobs on a
worker crash, must retry failures, must never exceed the cap.

## 1. Celery + Redis vs. Django-Q vs. custom

| | Celery + Redis | Django-Q(2) | Custom |
|---|---|---|---|
| Delivery guarantee | At-least-once with `acks_late` (config, not automatic) | At-least-once, simpler config | Easy to accidentally get at-most-once |
| Retry / backoff | Built in, exponential backoff native | Built in, less granular | Hand-rolled |
| Broker durability | Redis persistence (AOF/RDB) is opt-in and eventually consistent, not synchronous like RabbitMQ | Same Redis options | Whatever the DB gives you, often the most durable |
| Ops complexity | Highest: worker fleet + broker to run and monitor | Lower, fewer moving parts | Lowest infra, but locking/backoff/dead-lettering become your problem |
| Rate limiting | Built-in `rate_limit` is per-worker and approximate, not the atomicity this needs | Same caveat | Whatever you build |

**Chosen: Celery + Redis**, with rate limiting hand-rolled via a Redis Lua
script (§2) instead of Celery's built-in `rate_limit`, and the durability
gap closed explicitly (§4) instead of assumed.

Django-Q2 is a fine choice for a smaller app: less to operate, decent
retries. It loses here because the assignment wants precise control over
retry, dead-lettering, and exact SIGKILL semantics, and Celery's
`acks_late` / `task_reject_on_worker_lost` / `worker_prefetch_multiplier`
trio gives that directly.

Custom loses because at 200/min and 2,000-in-10s this isn't a scale
problem, so a custom queue buys no performance. It would just
re-implement retry scheduling, backoff, and crash-safe acking, the three
things most likely to have a subtle bug during exactly the burst scenario
in the prompt.

Tradeoff accepted: more operational surface (broker + workers to run and
monitor) and Redis's durability caveat above, mitigated below, not
ignored.

## 2. Rate limiter: sliding window, Redis sorted set, Lua script

`emailqueue/ratelimiter.py`.

**Sliding window over token bucket or fixed window:** fixed window
(`INCR` + `EXPIRE`) lets a burst straddle the boundary and double the cap
(200 at 0:59, 200 more at 1:00, 400 in under 2s). Token bucket is smooth
but needs a background refill process, or refill-on-read math duplicated
at every call site. A sorted set trimmed to the trailing `window_seconds`
gives an exact count with neither problem.

**Atomicity:** admission needs to trim (`ZREMRANGEBYSCORE`), read the
count (`ZCARD`), then conditionally write (`ZADD`). `MULTI`/`EXEC` queues
commands blindly and can't branch on an intermediate result. A Lua script
runs atomically on Redis's single-threaded command loop, so nothing can
interleave between trim, count, and write. That's the answer to "how do
you guarantee atomicity": a server-side script, not a client-side lock.

**Redis failure:** fails closed by default (`fail_open=False`, wired that
way in `emailqueue/tasks.py`). If Redis is unreachable, `try_acquire()`
returns `False` and the task retries with backoff instead of sending.
For a hard, bannable third-party cap, sending without being able to
verify the limit risks the account getting banned during exactly the
outage that broke the limiter, worse than delaying some emails.
`fail_open=True` exists for callers where availability matters more than
the cap; email sending isn't one of them.

## 3. Retry, backoff, dead-lettering

`emailqueue/tasks.py::send_transactional_email`:

- `max_retries=5`, backoff `min(2**retries, 60)` seconds, exponential and
  capped.
- A rate-limit rejection and a transient provider error both go through
  the same `self.retry(countdown=..., exc=...)` path: they're the same
  kind of event from the queue's perspective.
- Dead-letter: `EmailTask.on_failure` (a `celery.Task` subclass set as
  `base=`) pushes task id, args, and reason onto a Redis list
  (`emailqueue:dead_letter`, `emailqueue/dead_letter.py`) via `LPUSH`.
  `on_failure` fires for any terminal failure, including
  `MaxRetriesExceededError` once retries are exhausted, so "permanently
  failed" and "dead-lettered" are the same event with no extra
  bookkeeping. A Redis list rather than a DB table: the dead letter needs
  to survive independently of the task's own DB transaction, and at this
  scale a bounded, inspectable list beats a model + migration for what's
  operationally "things a human re-drives."

## 4. SIGKILL

Celery's default is to ack a task before running it. A SIGKILL mid-task
under that default loses the job silently, nothing retries it.

Two settings change that (`config/settings.py`):

- `CELERY_TASK_ACKS_LATE = True`: ack only after the task finishes. A
  SIGKILL mid-task means the ack never happened.
- `CELERY_TASK_REJECT_ON_WORKER_LOST = True`: if the worker disappears
  holding an unacked task (`WorkerLostError`), reject it back to the
  broker so Redis redelivers it.

**Consequence:** this is at-least-once, not exactly-once. A worker could
be killed after successfully calling the provider but before the ack
reaches Redis, so a task can run twice for the same email. Late-ack
prevents loss, not duplication. `send_transactional_email` closes that
gap itself: before doing anything else it claims `SET
emailqueue:sent:{email_id} NX EX <24h>` in Redis. A redelivered execution
finds the key already set, skips sending, and returns
`{"status": "duplicate-skipped"}`
(`test_duplicate_delivery_is_skipped_not_resent`). Late-ack makes
redelivery happen; the dedupe key makes it safe.

One more pairing: `CELERY_WORKER_PREFETCH_MULTIPLIER = 1`. Without it a
fast worker prefetches many unacked tasks, and a SIGKILL redelivers all
of them at once, a thundering herd hitting the rate limiter
simultaneously. Set to 1, a worker holds at most one unacked task, so a
crash redelivers one task, not a batch.

## 5. Testing at assignment scale vs. suite scale

500 jobs against a 200/min cap is 2.5 real minutes to observe, too slow
for a suite that should run in seconds. The algorithm's correctness
doesn't depend on the specific numbers, so the required "500 jobs, limit
never exceeded" property is proven at the literal count (500) against the
real Lua script, with `limit=20/window=1s` instead of `200/60s`
(`test_ratelimiter.py::test_burst_of_500_calls_never_exceeds_the_limit_in_any_one_second_window`).
"No job lost" and "a failure retried correctly" are proven separately at
the Celery-task level with `Task.apply()` (synchronous, retries recurse
in-process with no real sleep), see `test_tasks.py`. Each test file
explains the scaling decision in its own docstring.
