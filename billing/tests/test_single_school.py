"""One school runs the whole system: with subscriptions switched off, nothing pauses or expires,
nobody is asked to pay for a plan and no renewal reminders go out."""

import datetime

import pytest
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from billing import reminders, services
from galleries.models import Gallery
from galleries.tests.factories import EventFactory, GalleryFactory

pytestmark = pytest.mark.django_db
LONG_AGO = datetime.timedelta(days=365)


@pytest.fixture(autouse=True)
def subscriptions_off(settings):
    settings.BILLING = {**settings.BILLING, "SUBSCRIPTIONS": False}


@pytest.fixture
def lapsed(photographer):
    """A trial that ended a year ago: with subscriptions on, uploads and galleries would stop."""
    subscription = services.subscription_for(photographer)
    subscription.paid_until = timezone.now() - LONG_AGO
    subscription.save()
    return photographer


def test_uploads_never_pause(auth_client, lapsed):
    gallery = GalleryFactory(event=EventFactory(owner=lapsed))

    response = auth_client.post(
        reverse("upload-list"),
        {"gallery": str(gallery.pk), "filename": "a.jpg", "content_type": "image/jpeg", "size": 10},
        format="json",
    )

    assert response.status_code == 201


def test_galleries_never_expire_with_the_trial(client: Client, lapsed):
    gallery = GalleryFactory(event=EventFactory(owner=lapsed), status=Gallery.Status.PUBLISHED)

    response = client.get(reverse("public-gallery-page", args=[gallery.public_token]))

    assert response.status_code == 200


def test_dashboard_is_told_there_is_no_plan_to_manage(auth_client, lapsed):
    data = auth_client.get(reverse("billing-subscription")).data

    assert data["enabled"] is False
    assert data["can_upload"] is True
    assert data["galleries_live"] is True


def test_plans_cannot_be_bought(auth_client, lapsed):
    response = auth_client.post(
        reverse("payment-list"), {"plan": "pro", "phone": "0772123456"}, format="json"
    )

    assert response.status_code == 400
    assert not mail.outbox


def test_no_renewal_reminders_are_sent(lapsed):
    assert reminders.send_reminders() == 0
    assert not mail.outbox
