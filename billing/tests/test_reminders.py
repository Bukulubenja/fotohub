"""Renewal reminders: one email per stage, never repeated, reset by paying."""

import datetime

import pytest
from django.core import mail
from django.utils import timezone

from accounts.tests.factories import UserFactory
from billing import reminders, services
from billing.models import Payment, Plan
from billing.providers.base import Outcome

pytestmark = pytest.mark.django_db
DAY = datetime.timedelta(days=1)


@pytest.fixture
def now():
    return timezone.now()


def photographer_ending(now, offset: datetime.timedelta, plan: str = "trial"):
    user = UserFactory()
    sub = user.subscription
    sub.paid_until = now + offset
    sub.plan = Plan.objects.get(code=plan)
    sub.save()
    return user


def test_active_photographers_get_nothing(now):
    photographer_ending(now, 10 * DAY)

    assert reminders.send_reminders(now) == 0
    assert mail.outbox == []


def test_due_reminder_goes_out_once(now):
    user = photographer_ending(now, 2 * DAY)

    first = reminders.send_reminders(now)
    second = reminders.send_reminders(now + datetime.timedelta(hours=1))

    assert (first, second) == (1, 0)
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [user.email]
    assert "free trial ends on" in mail.outbox[0].subject


def test_each_stage_sends_its_own_reminder(now):
    photographer_ending(now, 2 * DAY, plan="starter")
    stages = [now, now + 3 * DAY, now + 10 * DAY, now + 40 * DAY]

    for moment in stages:
        reminders.send_reminders(moment)

    ends = timezone.localtime(now + 2 * DAY)
    subjects = [m.subject for m in mail.outbox]
    assert subjects == [
        f"Your SMAGOK HIVE plan ends on {ends.day} {ends:%B %Y}",
        "Uploads are paused on SMAGOK HIVE",
        "Your SMAGOK HIVE galleries will be hidden soon",
        "Your SMAGOK HIVE galleries are hidden from clients",
    ]
    assert "Nothing has been deleted" in mail.outbox[-1].body


def test_paying_resets_the_reminders(now, client):
    user = photographer_ending(now, 2 * DAY)
    reminders.send_reminders(now)
    client.force_login(user)
    client.post(
        "/api/v1/billing/payments/",
        {"plan": "starter", "phone": "0772123456"},
        content_type="application/json",
    )
    payment = Payment.objects.get()
    services.apply_outcome(
        Outcome(payment.provider_reference, "succeeded", payment.amount_ugx, "UGX")
    )

    user.subscription.refresh_from_db()
    assert user.subscription.reminded_state == ""
    # Thirty days later the next "due" reminder goes out again.
    later = user.subscription.paid_until - 2 * DAY
    assert reminders.send_reminders(later) == 1


def test_a_failed_email_is_retried_next_run(now, monkeypatch):
    photographer_ending(now, 2 * DAY)

    def broken(*args, **kwargs):
        raise ConnectionError("smtp down")

    monkeypatch.setattr(reminders, "deliver", broken)
    assert reminders.send_reminders(now) == 0
    monkeypatch.undo()

    assert reminders.send_reminders(now) == 1
