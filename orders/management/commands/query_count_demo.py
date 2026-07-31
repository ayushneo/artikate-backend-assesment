from django.core.management.base import BaseCommand
from django.db import connection
from django.test.utils import CaptureQueriesContext

from orders.buggy_queryset import broken_order_queryset
from orders.models import Customer, Order
from orders.serializers import OrderSummarySerializer
from orders.views import OrderSummaryView
from tenants.context import reset_current_tenant_id, set_current_tenant_id
from tenants.models import Tenant


class Command(BaseCommand):
    help = (
        "Section 1 evidence: seeds 200 orders and prints the SQL query "
        "count for the broken vs. fixed /api/orders/summary/ queryset. "
        "Companion to the automated proof in orders/tests/test_n_plus_one.py "
        "and to live inspection in django-silk at /silk/."
    )

    def handle(self, *args, **options):
        tenant, _ = Tenant.objects.get_or_create(
            slug="query-demo", defaults={"name": "Query Demo Tenant"}
        )
        token = set_current_tenant_id(tenant.id)
        try:
            customers = [
                Customer.objects.get_or_create(
                    email=f"demo{i}@example.com", defaults={"name": f"Demo Customer {i}"}
                )[0]
                for i in range(5)
            ]
            existing = Order.objects.count()
            target = 200
            for i in range(max(0, target - existing)):
                Order.objects.create(
                    customer=customers[i % len(customers)], status="paid", total_amount="10.00"
                )

            with CaptureQueriesContext(connection) as broken_ctx:
                OrderSummarySerializer(list(broken_order_queryset()), many=True).data

            with CaptureQueriesContext(connection) as fixed_ctx:
                OrderSummarySerializer(list(OrderSummaryView().get_queryset()), many=True).data

            order_count = Order.objects.count()
        finally:
            reset_current_tenant_id(token)

        self.stdout.write(f"Orders for tenant '{tenant.slug}': {order_count}")
        self.stdout.write(
            self.style.ERROR(
                f"BEFORE fix, Order.objects.all(), no select_related: "
                f"{len(broken_ctx.captured_queries)} queries"
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"AFTER fix,  select_related('customer'):              "
                f"{len(fixed_ctx.captured_queries)} queries"
            )
        )
