import logging

from celery import Task, shared_task
from django.conf import settings

from .dead_letter import push_dead_letter
from .provider import TransientProviderError, send_via_provider
from .ratelimiter import RateLimitExceeded, SlidingWindowRateLimiter
from .redis_client import get_redis_client

logger = logging.getLogger(__name__)

RATE_LIMIT_KEY = "emailqueue:rate:send_email"
DEDUPE_TTL_SECONDS = 24 * 60 * 60


class EmailTask(Task):
    """on_failure fires for any terminal failure, including
    MaxRetriesExceededError once retries are exhausted, so dead-lettering
    needs no extra bookkeeping in the task body."""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        push_dead_letter(task_id, args, kwargs, repr(exc))
        logger.error("send_transactional_email dead-lettered: task_id=%s exc=%r", task_id, exc)


def _backoff_seconds(retries):
    return min(2**retries, 60)


@shared_task(
    base=EmailTask,
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=5,
)
def send_transactional_email(self, email_id, recipient, subject, body):
    """Send one transactional email, respecting the provider's 200/min cap
    and surviving worker crashes without losing or double-sending the job.
    See DESIGN.md for the SIGKILL write-up.
    """
    redis_client = get_redis_client()
    dedupe_key = f"emailqueue:sent:{email_id}"

    # acks_late + reject_on_worker_lost mean this body can run twice for
    # the same email. SETNX with a TTL makes the send itself idempotent.
    if not redis_client.set(dedupe_key, "1", nx=True, ex=DEDUPE_TTL_SECONDS):
        logger.info("duplicate delivery skipped email_id=%s", email_id)
        return {"status": "duplicate-skipped", "email_id": email_id}

    limiter = SlidingWindowRateLimiter(
        redis_client,
        key=RATE_LIMIT_KEY,
        limit=settings.EMAIL_RATE_LIMIT,
        window_seconds=settings.EMAIL_RATE_LIMIT_WINDOW,
        fail_open=False,
    )

    if not limiter.try_acquire(member=f"{email_id}:{self.request.retries}:{self.request.id}"):
        # Never called the provider, so release the claim for the retry.
        redis_client.delete(dedupe_key)
        raise self.retry(countdown=_backoff_seconds(self.request.retries), exc=RateLimitExceeded())

    try:
        result = send_via_provider(recipient, subject, body)
    except TransientProviderError as exc:
        redis_client.delete(dedupe_key)
        raise self.retry(countdown=_backoff_seconds(self.request.retries), exc=exc)

    return {"status": "sent", "email_id": email_id, "provider": result}
