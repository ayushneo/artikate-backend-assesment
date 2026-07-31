from django.db import models

from tenants.managers import TenantManager
from tenants.models import Tenant


class Customer(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="customers")
    name = models.CharField(max_length=200)
    email = models.EmailField()

    # objects is tenant-scoped and safe by default. all_tenants is a
    # plain Manager, deliberately separate and loudly named, for
    # admin/background jobs. Never use it from request-serving code.
    objects = TenantManager()
    all_tenants = models.Manager()

    def __str__(self):
        return self.email


class Order(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("paid", "Paid"),
        ("shipped", "Shipped"),
        ("cancelled", "Cancelled"),
    ]

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="orders")
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="orders")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantManager()
    all_tenants = models.Manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["tenant", "created_at"])]

    def __str__(self):
        return f"Order #{self.pk}"
