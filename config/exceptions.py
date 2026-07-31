from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from tenants.context import TenantNotSet


def custom_exception_handler(exc, context):
    """TenantNotSet is an ImproperlyConfigured (500 by default) at the
    ORM layer, see tenants/context.py. Map it to a clean 400 at the API
    boundary instead of leaking a stack trace. No rows are ever
    returned either way, only the status code changes."""
    if isinstance(exc, TenantNotSet):
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return drf_exception_handler(exc, context)
