"""
Section 1 evidence: proves the N+1 diagnosis and proves the fix.

Query counts are measured with CaptureQueriesContext instead of
screenshots, so they run as part of pytest and stay true after any
refactor. See README.md for the live django-silk view at /silk/.
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from orders.buggy_queryset import broken_order_queryset
from orders.models import Customer, Order
from orders.serializers import OrderSummarySerializer
from orders.views import OrderSummaryView
from tenants.context import reset_current_tenant_id, set_current_tenant_id
from tenants.models import Tenant

ORDER_COUNT = 25


@pytest.fixture
def tenant_with_orders(db):
    tenant = Tenant.objects.create(slug="acme", name="Acme Inc")
    token = set_current_tenant_id(tenant.id)
    try:
        customers = [
            Customer.objects.create(name=f"Customer {i}", email=f"customer{i}@acme.test")
            for i in range(5)
        ]
        for i in range(ORDER_COUNT):
            Order.objects.create(
                customer=customers[i % len(customers)],
                status="paid",
                total_amount="10.00",
            )
        yield tenant
    finally:
        reset_current_tenant_id(token)


def _serialize(queryset):
    return OrderSummarySerializer(list(queryset), many=True).data


def test_broken_queryset_is_n_plus_one(tenant_with_orders):
    """Root-cause confirmation: 1 query for the order list, then one
    extra query per order for customer_email. Textbook N+1, and why it
    only shows up once a tenant has enough orders."""
    with CaptureQueriesContext(connection) as ctx:
        _serialize(broken_order_queryset())

    assert len(ctx.captured_queries) == 1 + ORDER_COUNT


def test_fixed_view_queryset_is_constant_regardless_of_order_count(tenant_with_orders):
    """The fix: query count stops scaling with the number of orders."""
    view = OrderSummaryView()

    with CaptureQueriesContext(connection) as ctx:
        _serialize(view.get_queryset())

    assert len(ctx.captured_queries) == 1


def test_fixed_and_broken_return_identical_data(tenant_with_orders):
    """The fix must not change the response, only the query cost."""
    broken_data = _serialize(broken_order_queryset())
    fixed_data = _serialize(OrderSummaryView().get_queryset())
    assert broken_data == fixed_data
