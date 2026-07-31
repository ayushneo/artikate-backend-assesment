from rest_framework import serializers

from .models import Order


class OrderSummarySerializer(serializers.ModelSerializer):
    # The regression from INCIDENT_LOG.md: touches obj.customer per row.
    # Safe only if the queryset was built with select_related("customer").
    customer_email = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = ["id", "status", "total_amount", "created_at", "customer_email"]

    def get_customer_email(self, obj):
        return obj.customer.email
