from emailqueue.dead_letter import dead_letter_count, dead_letter_items
from emailqueue.provider import TransientProviderError
from emailqueue.tasks import send_transactional_email


class TestSendTransactionalEmail:
    def test_successful_send(self, redis_client):
        result = send_transactional_email.apply(args=(1, "user1@example.com", "Hi", "Body"))
        assert result.successful()
        assert result.result["status"] == "sent"

    def test_duplicate_delivery_is_skipped_not_resent(self, redis_client):
        """Simulates Celery redelivering the same task after acks_late +
        reject_on_worker_lost (e.g. the worker was SIGKILL'd after
        sending but before acking). The second execution must not
        re-send."""
        first = send_transactional_email.apply(args=(2, "user2@example.com", "Hi", "Body"))
        second = send_transactional_email.apply(args=(2, "user2@example.com", "Hi", "Body"))
        assert first.result["status"] == "sent"
        assert second.result["status"] == "duplicate-skipped"

    def test_transient_failure_is_retried_and_eventually_succeeds(self, redis_client, monkeypatch):
        calls = {"n": 0}

        def flaky_send(recipient, subject, body):
            calls["n"] += 1
            if calls["n"] < 3:
                raise TransientProviderError("provider 503")
            return {"provider_message_id": "ok", "recipient": recipient}

        monkeypatch.setattr("emailqueue.tasks.send_via_provider", flaky_send)

        result = send_transactional_email.apply(args=(3, "user3@example.com", "Hi", "Body"))

        assert result.successful()
        assert result.result["status"] == "sent"
        assert calls["n"] == 3  # 2 failures + 1 success proves the retry loop actually ran

    def test_permanently_failing_send_is_dead_lettered_after_max_retries(self, redis_client, monkeypatch):
        def always_fails(recipient, subject, body):
            raise TransientProviderError("provider down")

        monkeypatch.setattr("emailqueue.tasks.send_via_provider", always_fails)

        result = send_transactional_email.apply(args=(4, "user4@example.com", "Hi", "Body"))

        assert result.failed()
        assert dead_letter_count() == 1
        assert dead_letter_items()[0]["args"] == [4, "user4@example.com", "Hi", "Body"]

    def test_rate_limit_exhaustion_eventually_dead_letters_rather_than_hanging(
        self, redis_client, settings
    ):
        settings.EMAIL_RATE_LIMIT = 1
        settings.EMAIL_RATE_LIMIT_WINDOW = 60

        first = send_transactional_email.apply(args=(5, "user5@example.com", "Hi", "Body"))
        assert first.result["status"] == "sent"

        # Second call is over the limit=1/60s cap. With max_retries=5 and
        # no wall-clock wait inside .apply()'s retry recursion, it exhausts
        # retries well before the window would ever clear, proving the
        # task fails safe (dead-letters) instead of retrying forever.
        second = send_transactional_email.apply(args=(6, "user6@example.com", "Hi", "Body"))
        assert second.failed()
        assert dead_letter_count() == 1


class TestBurstSubmission:
    def test_burst_of_jobs_all_accounted_for_with_one_retried(self, redis_client, monkeypatch):
        """The assignment's required test, at a scale that keeps the
        suite fast: submit a burst of jobs, inject one intentional
        transient failure, and assert none are lost and the failing one
        was retried. Rate-limit-never-exceeded is proven separately at
        the assignment's full n=500 in test_ratelimiter.py against the
        same Lua script this task uses."""
        JOB_COUNT = 25
        RETRY_EMAIL_ID = 10
        attempts = {"n": 0}

        def flaky_once(recipient, subject, body):
            if recipient == f"user{RETRY_EMAIL_ID}@example.com" and attempts["n"] == 0:
                attempts["n"] += 1
                raise TransientProviderError("simulated transient failure")
            return {"provider_message_id": "ok", "recipient": recipient}

        monkeypatch.setattr("emailqueue.tasks.send_via_provider", flaky_once)

        results = [
            send_transactional_email.apply(args=(i, f"user{i}@example.com", "Hi", "Body"))
            for i in range(JOB_COUNT)
        ]

        assert all(r.successful() for r in results), "no job may be lost"
        assert attempts["n"] == 1, "the intentional failure must actually have been hit"
        assert dead_letter_count() == 0
