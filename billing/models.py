import uuid

from django.conf import settings
from django.db import models


class Plan(models.Model):
    """What a photographer pays for (spec FR-26). Prices live in the admin, not in code."""

    code = models.SlugField(unique=True)
    name = models.CharField(max_length=60)
    price_ugx = models.PositiveIntegerField(help_text="Price of one period, in Uganda shillings.")
    period_days = models.PositiveSmallIntegerField(default=30)
    storage_quota_bytes = models.PositiveBigIntegerField()
    is_public = models.BooleanField(default=True, help_text="Offered in the dashboard.")
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "price_ugx"]

    def __str__(self) -> str:
        return self.name


class Subscription(models.Model):
    """Prepaid access: the state (active, due, grace, lapsed) follows from `paid_until`."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="subscription"
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    paid_until = models.DateTimeField()
    # The last lifecycle state we sent a reminder for, so each reminder goes out once.
    reminded_state = models.CharField(max_length=10, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.user} · {self.plan}"


class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        SUCCEEDED = "succeeded"
        FAILED = "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subscription = models.ForeignKey(
        Subscription, on_delete=models.PROTECT, related_name="payments"
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="+")
    periods = models.PositiveSmallIntegerField(default=1)
    amount_ugx = models.PositiveIntegerField()
    phone = models.CharField(max_length=20, blank=True)
    provider = models.CharField(max_length=30)
    # The provider's id for this charge; unique, so a replayed webhook can't apply twice.
    provider_reference = models.CharField(max_length=100, unique=True, null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status, default=Status.PENDING)
    failure_reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"UGX {self.amount_ugx:,} ({self.status})"
