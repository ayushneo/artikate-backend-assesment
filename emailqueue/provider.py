"""Stand-in for the real third-party transactional email API. Tests
monkeypatch send_via_provider to simulate transient/permanent failures
without a real network dependency."""


class TransientProviderError(Exception):
    """Retryable: provider 5xx, timeout, connection reset."""


class PermanentProviderError(Exception):
    """Not retryable: invalid recipient, rejected content."""


def send_via_provider(recipient, subject, body):
    return {"provider_message_id": "stub-message-id", "recipient": recipient}
