"""
Tenant binding uses contextvars, not threading.local(). Async views can
interleave many requests on one OS thread, and sync_to_async runs ORM
code on a worker thread pulled from a pool, not one dedicated to the
request. Thread-locals break both ways: they leak between interleaved
coroutines, or read back empty on a different pool thread. ContextVar is
copied per asyncio Task, so each request gets its own isolated snapshot.
See ANSWERS.md Section 3.
"""
import contextvars

from django.core.exceptions import ImproperlyConfigured

_current_tenant_id: "contextvars.ContextVar[int | None]" = contextvars.ContextVar(
    "current_tenant_id", default=None
)


class TenantNotSet(ImproperlyConfigured):
    """No tenant bound to the current context."""


class TenantMismatch(Exception):
    """Tried to create a row for a tenant other than the one bound."""


def set_current_tenant_id(tenant_id):
    return _current_tenant_id.set(tenant_id)


def reset_current_tenant_id(token):
    _current_tenant_id.reset(token)


def get_current_tenant_id():
    return _current_tenant_id.get()
