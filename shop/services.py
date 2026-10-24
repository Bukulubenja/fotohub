"""Photo orders: checkout, the provider's verdict, receipts. Only this module changes an
order's status.
"""

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from assets.models import Photo
from billing.providers import get_provider
from billing.providers.base import Charge, Checkout, Outcome, ProviderError
from billing.services import PaymentRejected, normalise_phone
from galleries.models import Gallery
from shop.models import Order, OrderItem
from shop.pricing import format_money, price

logger = logging.getLogger(__name__)


def checkout(
    gallery: Gallery,
    photos: list[Photo],
    *,
    currency: str,
    email: str,
    phone: str,
    return_url_for,
) -> tuple[Order, Checkout]:
    """Create the order from the cart's photos and ask the provider to collect it.

    `return_url_for(order)` builds the absolute order-page URL a card checkout returns to.
    """
    if not photos:
        raise PaymentRejected("Your cart is empty.")
    unit = price(currency)
    phone = normalise_phone(phone) if currency == Order.Currency.UGX else ""
    provider = get_provider()
    with transaction.atomic():
        order = Order.objects.create(
            gallery=gallery,
            currency=currency,
            unit_price_minor=unit.unit_minor,
            quantity=len(photos),
            amount_minor=unit.total(len(photos)),
            email=email,
            phone=phone,
            provider=provider.name,
        )
        OrderItem.objects.bulk_create(
            OrderItem(order=order, photo=photo, filename=photo.original_filename)
            for photo in photos
        )
    charge = Charge(
        amount_minor=order.amount_minor,
        currency=currency,
        phone=phone,
        email=email,
        description=f"{order.quantity} photo{'s' if order.quantity != 1 else ''} "
        f"from {gallery.event.title}",
        return_url=return_url_for(order),
    )
    try:
        started = provider.start(charge)
    except ProviderError as exc:
        _fail(order, str(exc))
        raise PaymentRejected(str(exc)) from exc
    order.provider_reference = started.reference
    order.save(update_fields=["provider_reference"])
    return order, started


def apply_outcome(outcome: Outcome) -> Order:
    """Record the provider's verdict. Idempotent; raises Order.DoesNotExist if not ours."""
    with transaction.atomic():
        order = Order.objects.select_for_update().get(provider_reference=outcome.reference)
        if order.status != Order.Status.PENDING or outcome.status == "pending":
            return order
        if outcome.status != "succeeded":
            return _fail(order, outcome.reason or "The payment was declined or cancelled.")
        if outcome.amount_minor != order.amount_minor or outcome.currency != order.currency:
            logger.error(
                "order %s: provider reported %s %s, expected %s %s",
                order.pk,
                outcome.currency,
                outcome.amount_minor,
                order.currency,
                order.amount_minor,
            )
            return _fail(order, "The amount paid didn't match. Contact support.")
        order.status = Order.Status.PAID
        order.paid_at = timezone.now()
        order.save(update_fields=["status", "paid_at"])
        transaction.on_commit(lambda: send_receipt(order), robust=True)
    return order


def refresh(order: Order) -> Order:
    """Ask the provider about a pending order (webhooks can be late or lost)."""
    if order.status != Order.Status.PENDING or not order.provider_reference:
        return order
    try:
        outcome = get_provider(order.provider).check(order.provider_reference)
    except ProviderError:
        logger.warning("status check failed for order %s", order.pk, exc_info=True)
        return order
    return apply_outcome(outcome)


def cancel_if_unpaid(order: Order) -> Order:
    """The buyer came back from the provider's page having cancelled. Checks first, so a
    payment that did go through is never thrown away."""
    order = refresh(order)
    with transaction.atomic():
        locked = Order.objects.select_for_update().get(pk=order.pk)
        if locked.status == Order.Status.PENDING:
            return _fail(locked, "You cancelled the payment. Nothing was charged.")
    return order


def send_receipt(order: Order) -> None:
    link = settings.SITE_URL.rstrip("/") + f"/orders/{order.token}/"
    title = order.gallery.event.title if order.gallery else "your gallery"
    send_mail(
        subject=f"Your photos from {title}",
        message=(
            f"Thank you! You bought {order.quantity} photo{'s' if order.quantity != 1 else ''} "
            f"for {format_money(order.currency, order.amount_minor)}.\n\n"
            f"Download them here (keep this link private):\n{link}\n\n"
            "The link works on any device. Downloads are the original, full-quality files."
        ),
        from_email=None,
        recipient_list=[order.email],
    )


def _fail(order: Order, reason: str) -> Order:
    order.status = Order.Status.FAILED
    order.failure_reason = reason[:255]
    order.save(update_fields=["status", "failure_reason"])
    return order
