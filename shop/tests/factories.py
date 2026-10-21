from django.test import Client
from django.utils import timezone

from assets.models import Photo
from galleries.models import Gallery
from shop.cart import ORDERS
from shop.models import Order, OrderItem


def make_order(
    gallery: Gallery,
    photos: list[Photo],
    *,
    status: str = Order.Status.PAID,
    currency: str = "UGX",
    unit: int = 900,
    client: Client | None = None,
) -> Order:
    """An order for `photos`; with `client`, that browser session owns it."""
    order = Order.objects.create(
        gallery=gallery,
        currency=currency,
        unit_price_minor=unit,
        quantity=len(photos),
        amount_minor=unit * len(photos),
        email="guest@example.com",
        provider="fake",
        status=status,
        paid_at=timezone.now() if status == Order.Status.PAID else None,
    )
    OrderItem.objects.bulk_create(
        OrderItem(order=order, photo=p, filename=p.original_filename) for p in photos
    )
    if client is not None:
        session = client.session
        session[ORDERS] = [*session.get(ORDERS, []), order.token]
        session.save()
    return order
