import secrets
import uuid

from django.db import models

from assets.models import Photo
from galleries.models import Gallery

ORDER_TOKEN_BYTES = 24


def generate_order_token() -> str:
    """The order link is the buyer's key to their photos (no customer accounts)."""
    return secrets.token_urlsafe(ORDER_TOKEN_BYTES)


class Order(models.Model):
    """A visitor's purchase of photos from one gallery. Prices are snapshots at checkout."""

    class Status(models.TextChoices):
        PENDING = "pending"
        PAID = "paid"
        FAILED = "failed"

    class Currency(models.TextChoices):
        UGX = "UGX", "Uganda shillings (mobile money)"
        USD = "USD", "US dollars (card)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    token = models.CharField(
        max_length=64, unique=True, default=generate_order_token, editable=False
    )
    # Kept when a gallery is deleted: the order is also a financial record.
    gallery = models.ForeignKey(
        Gallery, null=True, on_delete=models.SET_NULL, related_name="orders"
    )
    currency = models.CharField(max_length=3, choices=Currency)
    unit_price_minor = models.PositiveIntegerField()  # shillings, or US cents
    quantity = models.PositiveIntegerField()
    amount_minor = models.PositiveIntegerField()
    email = models.EmailField()
    phone = models.CharField(max_length=20, blank=True)
    provider = models.CharField(max_length=30)
    provider_reference = models.CharField(max_length=100, unique=True, null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status, default=Status.PENDING)
    failure_reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.quantity} photos, {self.currency} {self.amount_minor} ({self.status})"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    # Kept if the photographer deletes the photo; the download then says it's gone.
    photo = models.ForeignKey(Photo, null=True, on_delete=models.SET_NULL, related_name="+")
    filename = models.CharField(max_length=255)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["order", "photo"], name="one_photo_per_order")
        ]

    def __str__(self) -> str:
        return self.filename
