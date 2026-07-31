"""
TenantManager scopes every query to the current tenant by overriding
get_queryset(), so .all(), .filter(), .get(), .count() and related
managers all inherit it automatically. No tenant bound raises
TenantNotSet: fail closed instead of returning unscoped rows. Bypassing
scoping means reaching for the explicit, greppable *.all_tenants manager.
"""
from django.db import models

from .context import TenantMismatch, TenantNotSet, get_current_tenant_id


class TenantQuerySet(models.QuerySet):
    def unscoped(self):
        """Backing queryset for *.all_tenants managers. Never use from
        request-serving code."""
        return models.QuerySet(self.model, using=self._db)

    def create(self, **kwargs):
        """Defined here, not just on the manager, because
        get_or_create() calls create() on the queryset too. Defaults
        tenant_id to the bound context and rejects a mismatched one."""
        tenant_id = get_current_tenant_id()
        if tenant_id is None:
            raise TenantNotSet(f"No tenant bound. Refusing to create a {self.model.__name__}.")
        kwargs.setdefault("tenant_id", tenant_id)
        if kwargs["tenant_id"] != tenant_id:
            raise TenantMismatch(
                f"Refusing to create a {self.model.__name__} for tenant "
                f"{kwargs['tenant_id']!r} while bound to tenant {tenant_id!r}."
            )
        return super().create(**kwargs)


class TenantManager(models.Manager.from_queryset(TenantQuerySet)):
    use_in_migrations = False

    def get_queryset(self):
        tenant_id = get_current_tenant_id()
        if tenant_id is None:
            raise TenantNotSet(
                f"No tenant bound. Refusing an unscoped {self.model.__name__} queryset. "
                f"Use {self.model.__name__}.all_tenants for tenant-agnostic code."
            )
        return super().get_queryset().filter(tenant_id=tenant_id)
