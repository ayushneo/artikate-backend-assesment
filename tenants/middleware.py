from django.http import HttpResponseBadRequest

from .context import reset_current_tenant_id, set_current_tenant_id
from .models import Tenant


class TenantMiddleware:
    """
    Resolves the tenant once per request and binds it to the contextvar
    for the request lifecycle, clearing it in a finally so it never leaks
    into the next request handled on the same thread/task.

    Resolution order: subdomain (acme.api.example.com -> slug "acme"),
    then an X-Tenant-ID header standing in for a tenant claim already
    verified by upstream JWT auth (see ANSWERS.md for why full JWT
    verification isn't reimplemented here).

    No match: request.tenant is None and no tenant id is bound, so any
    TenantManager-backed query raises TenantNotSet instead of serving
    unscoped data.
    """

    EXEMPT_PATH_PREFIXES = ("/admin", "/silk")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith(self.EXEMPT_PATH_PREFIXES):
            return self.get_response(request)

        tenant = self._resolve_tenant(request)
        request.tenant = tenant

        if tenant is None and request.headers.get("X-Tenant-ID") is not None:
            # Header present but didn't match a real tenant: fail closed.
            return HttpResponseBadRequest("Unknown tenant")

        token = set_current_tenant_id(tenant.id if tenant else None)
        try:
            response = self.get_response(request)
        finally:
            reset_current_tenant_id(token)
        return response

    @staticmethod
    def _resolve_tenant(request):
        host = request.get_host().split(":")[0]
        subdomain = host.split(".")[0]
        tenant = Tenant.objects.filter(slug=subdomain).first()
        if tenant is not None:
            return tenant

        header_value = request.headers.get("X-Tenant-ID")
        if header_value:
            return Tenant.objects.filter(pk=header_value).first()

        return None
