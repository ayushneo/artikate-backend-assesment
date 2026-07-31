"""
Section 3 evidence. Every test here proves a negative: that a specific way
of trying to leak or bypass tenant scoping actually fails, not just that
scoped access works.
"""
import pytest

from orders.models import Customer, Order
from tenants.context import (
    TenantMismatch,
    TenantNotSet,
    reset_current_tenant_id,
    set_current_tenant_id,
)
from tenants.models import Tenant


@pytest.fixture
def two_tenants_with_orders(db):
    tenant_a = Tenant.objects.create(slug="tenant-a", name="Tenant A")
    tenant_b = Tenant.objects.create(slug="tenant-b", name="Tenant B")

    token = set_current_tenant_id(tenant_a.id)
    customer_a = Customer.objects.create(name="Alice", email="alice@a.test")
    order_a = Order.objects.create(customer=customer_a, status="paid", total_amount="10.00")
    reset_current_tenant_id(token)

    token = set_current_tenant_id(tenant_b.id)
    customer_b = Customer.objects.create(name="Bob", email="bob@b.test")
    order_b = Order.objects.create(customer=customer_b, status="paid", total_amount="20.00")
    reset_current_tenant_id(token)

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "order_a": order_a,
        "order_b": order_b,
    }


class TestCrossTenantIsolation:
    """(a) tenant A cannot access tenant B's data through any ORM call."""

    def test_all_only_returns_own_tenant(self, two_tenants_with_orders):
        token = set_current_tenant_id(two_tenants_with_orders["tenant_a"].id)
        try:
            ids = set(Order.objects.all().values_list("id", flat=True))
        finally:
            reset_current_tenant_id(token)

        assert ids == {two_tenants_with_orders["order_a"].id}

    def test_get_by_pk_of_other_tenant_raises_does_not_exist(self, two_tenants_with_orders):
        token = set_current_tenant_id(two_tenants_with_orders["tenant_a"].id)
        try:
            with pytest.raises(Order.DoesNotExist):
                Order.objects.get(pk=two_tenants_with_orders["order_b"].id)
        finally:
            reset_current_tenant_id(token)

    def test_filter_by_pk_of_other_tenant_returns_empty(self, two_tenants_with_orders):
        token = set_current_tenant_id(two_tenants_with_orders["tenant_a"].id)
        try:
            qs = Order.objects.filter(pk=two_tenants_with_orders["order_b"].id)
            assert not qs.exists()
        finally:
            reset_current_tenant_id(token)

    def test_filter_id_in_across_tenants_only_returns_own_rows(self, two_tenants_with_orders):
        both_ids = [
            two_tenants_with_orders["order_a"].id,
            two_tenants_with_orders["order_b"].id,
        ]
        token = set_current_tenant_id(two_tenants_with_orders["tenant_a"].id)
        try:
            returned = list(Order.objects.filter(id__in=both_ids).values_list("id", flat=True))
        finally:
            reset_current_tenant_id(token)

        assert returned == [two_tenants_with_orders["order_a"].id]

    def test_create_for_other_tenant_is_refused(self, two_tenants_with_orders):
        """A single wrong tenant_id kwarg on write must not silently succeed."""
        token = set_current_tenant_id(two_tenants_with_orders["tenant_a"].id)
        try:
            with pytest.raises(TenantMismatch):
                Order.objects.create(
                    tenant_id=two_tenants_with_orders["tenant_b"].id,
                    customer=two_tenants_with_orders["order_a"].customer,
                    total_amount="1.00",
                )
        finally:
            reset_current_tenant_id(token)


class TestBypassFailsClosed:
    """(b) calling .objects.all() does not bypass scoping, including the
    case where no developer remembered to bind a tenant at all."""

    def test_all_with_no_tenant_bound_raises_instead_of_returning_unscoped_data(
        self, two_tenants_with_orders
    ):
        # No set_current_tenant_id() call: simulates a code path
        # (background task, forgotten middleware, misconfigured view)
        # that never bound a tenant.
        with pytest.raises(TenantNotSet):
            list(Order.objects.all())

    def test_get_with_no_tenant_bound_raises(self, two_tenants_with_orders):
        with pytest.raises(TenantNotSet):
            Order.objects.get(pk=two_tenants_with_orders["order_a"].id)

    def test_all_tenants_manager_is_the_only_way_to_see_everything(
        self, two_tenants_with_orders
    ):
        """The unscoped escape hatch exists, is explicit, and isn't what
        objects resolves to, proving scoping isn't opt-out by accident."""
        assert Order.all_tenants.count() == 2
        with pytest.raises(TenantNotSet):
            Order.objects.count()
