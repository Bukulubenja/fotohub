"""Renewal reminders (spec FR-29, plan §3.5): one message per stage, never repeated.

Stages: due (D-3), grace (uploads paused), lapsed, hidden (galleries no longer shown). Email
for now; WhatsApp/SMS can plug into `deliver` once the payment aggregator is chosen.
"""

import datetime
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse
from django.utils import timezone

from billing import lifecycle
from billing.models import Subscription

logger = logging.getLogger(__name__)

HIDDEN = "hidden"  # lapsed long enough that customers no longer see the galleries


def stage(standing: lifecycle.Standing) -> str:
    if standing.state == lifecycle.State.LAPSED and not standing.galleries_live:
        return HIDDEN
    return standing.state


def message(subscription: Subscription, current: str) -> tuple[str, str]:
    BRAND = settings.BRAND["NAME"]  # noqa: N806
    what = "free trial" if subscription.plan.code == settings.BILLING["TRIAL_PLAN"] else "plan"
    ends = timezone.localtime(subscription.paid_until).strftime("%d %B %Y").lstrip("0")
    renew = f"Renew from your dashboard: {settings.SITE_URL.rstrip('/')}{reverse('dashboard')}"
    texts = {
        lifecycle.State.DUE: (
            f"Your {BRAND} {what} ends on {ends}",
            f"Your {what} ends on {ends}. Renew before then to keep uploading without a break.",
        ),
        lifecycle.State.GRACE: (
            f"Uploads are paused on {BRAND}",
            f"Your {what} ended on {ends}, so new uploads are paused. Your galleries are still "
            "online and your clients can still see them.",
        ),
        lifecycle.State.LAPSED: (
            f"Your {BRAND} galleries will be hidden soon",
            f"Your {what} ended on {ends}. Your galleries are still online for now, but will be "
            "hidden from clients if you don't renew. Your photos are kept safe.",
        ),
        HIDDEN: (
            f"Your {BRAND} galleries are hidden from clients",
            "Your galleries are now hidden from clients. Nothing has been deleted: renew and "
            "they come back straight away.",
        ),
    }
    subject, body = texts[current]
    return subject, f"{body}\n\n{renew}\n\n— {BRAND}"


def deliver(subscription: Subscription, subject: str, body: str) -> None:
    send_mail(subject, body, from_email=None, recipient_list=[subscription.user.email])


def send_reminders(now: datetime.datetime | None = None) -> int:
    """Send each subscription the reminder for its current stage, once. Returns how many."""
    now = now or timezone.now()
    soon = now + datetime.timedelta(days=settings.BILLING["DUE_DAYS"])
    sent = 0
    candidates = Subscription.objects.filter(paid_until__lte=soon).select_related("user", "plan")
    for subscription in candidates.iterator():
        current = stage(lifecycle.standing(subscription.paid_until, now))
        if current == lifecycle.State.ACTIVE or subscription.reminded_state == current:
            continue
        # Claim first, so two overlapping runs can't both send it.
        claimed = (
            Subscription.objects.filter(pk=subscription.pk)
            .exclude(reminded_state=current)
            .update(reminded_state=current)
        )
        if not claimed:
            continue
        try:
            deliver(subscription, *message(subscription, current))
        except Exception:
            logger.exception("reminder for subscription %s failed", subscription.pk)
            Subscription.objects.filter(pk=subscription.pk, reminded_state=current).update(
                reminded_state=subscription.reminded_state
            )
            continue
        sent += 1
    return sent
