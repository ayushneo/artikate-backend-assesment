"""
API-level proof that tenant scoping holds end-to-end through the real
middleware + view + exception-handling stack, not just at the ORM layer
in isolation (see tenants/tests/test_isolation.py for that).
"""
import pytest
from django.test import Client

from orders.models import Customer, Order
from tenants.context import reset_current_tenant_id, set_current_tenant_id
from tenants.models import Tenant


@pytest.fixture
def tenant_with_one_order(db):
    tenant = Tenant.objects.create(slug="webtest", name="Web Test")
    token = set_current_tenant_id(tenant.id)
    try:
        customer = Customer.objects.create(name="Jo", email="jo@webtest.test")
        order = Order.objects.create(customer=customer, status="paid", total_amount="5.00")
    finally:
        reset_current_tenant_id(token)
    return tenant, order


def test_summary_without_any_tenant_fails_closed_not_500(db):
    response = Client().get("/api/orders/summary/")
    assert response.status_code == 400


def test_summary_returns_only_that_tenants_orders(tenant_with_one_order):
    tenant, order = tenant_with_one_order
    response = Client().get("/api/orders/summary/", HTTP_X_TENANT_ID=str(tenant.id))
    assert response.status_code == 200
    ids = [row["id"] for row in response.json()["results"]]
    assert ids == [order.id]


def test_unknown_tenant_header_is_rejected_by_middleware(db):
    response = Client().get("/api/orders/summary/", HTTP_X_TENANT_ID="999999")
    assert response.status_code == 400
