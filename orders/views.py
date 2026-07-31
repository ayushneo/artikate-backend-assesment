from rest_framework.generics import ListAPIView

from .models import Order
from .serializers import OrderSummarySerializer


class OrderSummaryView(ListAPIView):
    """GET /api/orders/summary/, the mobile dashboard endpoint from
    Section 1's incident. See INCIDENT_LOG.md for the investigation and
    ANSWERS.md for the database-level explanation of the fix below.
    """

    serializer_class = OrderSummarySerializer

    def get_queryset(self):
        # select_related("customer") folds the per-row customer lookup
        # into a SQL JOIN instead of firing one SELECT per order. Query
        # count goes from 1 + N to 1, independent of order count.
        return Order.objects.select_related("customer").all()
