"""
Pre-fix /api/orders/summary/ queryset, kept only so the regression stays
reproducible and testable. Not wired into any URL. Used by
orders/management/commands/query_count_demo.py and
orders/tests/test_n_plus_one.py to prove the N+1 diagnosis in
INCIDENT_LOG.md.

The fixed queryset lives in OrderSummaryView.get_queryset().
"""
from .models import Order


def broken_order_queryset():
    return Order.objects.all()
