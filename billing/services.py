"""Subscriptions and payments. Views, signals and tasks call these; nothing else changes a
subscription's `paid_until` or a payment's status.
"""

import logging
import re

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from accounts.models import PhotographerProfile
from billing import lifecycle
from billing.models import Payment, Plan, Subscription
from billing.providers import get_provider
from billing.providers.base import Charge, Outcome, ProviderError

logger = logging.getLogger(__name__)

UGANDA_MOBILE = re.compile(r"^(?:\+?256|0)?(7\d{8})$")
MAX_PERIODS = 12


class PaymentRejected(Exception):
    """The payment can't start; the message is shown to the photographer."""


def normalise_phone(raw: str) -> str:
    """ "0772 123 456", "+256772123456" and "256-772-123456" all become "256772123456"."""
    match = UGANDA_MOBILE.match(re.sub(r"[\s\-()]", "", raw or ""))
    if not match:
        raise PaymentRejected("Enter an MTN or Airtel number, for example 0772 123 456.")
    return "256" + match.group(1)


def _apply_plan_quota(user_id: int, plan: Plan) -> None:
    PhotographerProfile.objects.filter(user_id=user_id).update(
        storage_quota_bytes=plan.storage_quota_bytes
    )


def start_trial(user) -> Subscription:
    """New photographers start on the trial plan, free for BILLING["TRIAL_DAYS"]."""
    # Recreated if missing, so a deleted plan can never block sign-ups or uploads.
    plan, _ = Plan.objects.get_or_create(
        code=settings.BILLING["TRIAL_PLAN"],
        defaults={
            "name": "Free trial",
            "price_ugx": 0,
            "storage_quota_bytes": settings.DEFAULT_STORAGE_QUOTA_BYTES,
            "is_public": False,
        },
    )
    now = timezone.now()
    subscription, created = Subscription.objects.get_or_create(
        user=user,
        defaults={
            "plan": plan,
            "paid_until": lifecycle.extended(now, now, settings.BILLING["TRIAL_DAYS"]),
        },
    )
    if created:
        _apply_plan_quota(user.pk, plan)
    return subscription


def subscription_for(user) -> Subscription:
    try:
        return user.subscription
    except Subscription.DoesNotExist:
        return start_trial(user)


def standing_for(user, now=None) -> lifecycle.Standing:
    return lifecycle.standing(subscription_for(user).paid_until, now or timezone.now())


def can_upload(user) -> bool:
    return standing_for(user).can_upload


def start_payment(user, plan: Plan, periods: int, phone: str) -> tuple[Payment, object]:
    """Create the payment and ask the provider to collect it. Returns (payment, checkout)."""
    if not 1 <= periods <= MAX_PERIODS:
        raise PaymentRejected(f"Pay for between 1 and {MAX_PERIODS} periods at a time.")
    if not plan.is_public:
        raise PaymentRejected("That plan isn't available.")
    provider = get_provider()
    payment = Payment.objects.create(
        subscription=subscription_for(user),
        plan=plan,
        periods=periods,
        amount_ugx=plan.price_ugx * periods,
        phone=normalise_phone(phone),
        provider=provider.name,
    )
    charge = Charge(
        amount_minor=payment.amount_ugx,
        currency="UGX",
        phone=payment.phone,
        email=user.email,
        description=f"{settings.BRAND['NAME']} {plan.name}, {periods} x {plan.period_days} days",
        return_url=settings.BILLING["RETURN_URL"]
        or settings.SITE_URL.rstrip("/") + reverse("dashboard"),
    )
    try:
        checkout = provider.start(charge)
    except ProviderError as exc:
        _fail(payment, str(exc))
        raise PaymentRejected(str(exc)) from exc
    payment.provider_reference = checkout.reference
    payment.save(update_fields=["provider_reference"])
    return payment, checkout


def apply_outcome(outcome: Outcome) -> Payment:
    """Record the provider's verdict. Idempotent: a repeated webhook changes nothing."""
    with transaction.atomic():
        payment = (
            Payment.objects.select_for_update()
            .select_related("subscription", "plan")
            .get(provider_reference=outcome.reference)
        )
        if payment.status != Payment.Status.PENDING or outcome.status == Payment.Status.PENDING:
            return payment
        if outcome.status != Payment.Status.SUCCEEDED:
            return _fail(payment, outcome.reason or "The payment was declined or cancelled.")
        if outcome.amount_minor != payment.amount_ugx or outcome.currency != "UGX":
            logger.error(
                "payment %s: provider reported %s %s, expected UGX %s",
                payment.pk,
                outcome.currency,
                outcome.amount_minor,
                payment.amount_ugx,
            )
            return _fail(payment, "The amount paid didn't match. Contact support.")
        _credit(payment)
    return payment


def refresh_payment(payment: Payment) -> Payment:
    """Ask the provider about a pending payment (webhooks can be late or lost)."""
    if payment.status != Payment.Status.PENDING or not payment.provider_reference:
        return payment
    try:
        outcome = get_provider(payment.provider).check(payment.provider_reference)
    except ProviderError:
        logger.warning("status check failed for payment %s", payment.pk, exc_info=True)
        return payment
    return apply_outcome(outcome)


def _credit(payment: Payment) -> None:
    now = timezone.now()
    subscription = Subscription.objects.select_for_update().get(pk=payment.subscription_id)
    subscription.paid_until = lifecycle.extended(
        subscription.paid_until, now, payment.plan.period_days * payment.periods
    )
    subscription.plan = payment.plan
    subscription.reminded_state = ""
    subscription.save(update_fields=["paid_until", "plan", "reminded_state", "updated_at"])
    _apply_plan_quota(subscription.user_id, payment.plan)
    payment.status = Payment.Status.SUCCEEDED
    payment.confirmed_at = now
    payment.save(update_fields=["status", "confirmed_at"])


def _fail(payment: Payment, reason: str) -> Payment:
    payment.status = Payment.Status.FAILED
    payment.failure_reason = reason[:255]
    payment.save(update_fields=["status", "failure_reason"])
    return payment
