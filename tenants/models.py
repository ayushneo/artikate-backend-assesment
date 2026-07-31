from django.db import models


class Tenant(models.Model):
    """A SaaS client. `slug` is matched against the request subdomain."""

    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.slug
