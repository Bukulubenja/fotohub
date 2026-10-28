"""Collection updates by email: guests ask on the collection page, confirm from their inbox,
then get at most one summary a day of new events and photos, with a one-click stop.
"""

import datetime
import re

import pytest
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from assets.models import Photo
from assets.tests.factories import ready_photo
from billing import services as billing
from galleries import collections, updates
from galleries.models import Collection, CollectionSubscription, Gallery
from galleries.tests.factories import EventFactory, GalleryFactory

pytestmark = pytest.mark.django_db
EMAIL = "parent@example.com"
HOUR = datetime.timedelta(hours=1)


@pytest.fixture
def school(photographer):
    return collections.create_collection(photographer, "St. Maria Goretti SS", prefix="MGSS")


@pytest.fixture
def guest(school) -> Client:
    client = Client()
    client.post(reverse("home"), {"passkey": school.passkey})
    return client


def published_in(collection, title="Sports Day", at=None, photos=0, **event_fields) -> Gallery:
    event = EventFactory(owner=collection.owner, collection=collection, title=title, **event_fields)
    gallery = GalleryFactory(event=event, status=Gallery.Status.PUBLISHED)
    Gallery.objects.filter(pk=gallery.pk).update(published_at=at or timezone.now())
    add_photos(gallery, photos, at=at)
    gallery.refresh_from_db()
    return gallery


def add_photos(gallery, count, at=None) -> None:
    for _ in range(count):
        photo = ready_photo(gallery)
        # A photo's updated_at is when it became ready (assets.processing).
        Photo.objects.filter(pk=photo.pk).update(updated_at=at or timezone.now())


def subscribed(collection, email=EMAIL, since=None) -> CollectionSubscription:
    subscription = updates.request_updates(collection, email)
    updates.confirm(subscription, now=since or timezone.now())
    subscription.refresh_from_db()
    return subscription


def subscribe_url(collection: Collection) -> str:
    return reverse("collection-updates-request", args=[collection.pk])


def manage_url(subscription: CollectionSubscription) -> str:
    return reverse("collection-updates", args=[subscription.token])


class TestAsking:
    def test_guest_asks_and_gets_one_confirmation_email(self, guest, school):
        response = guest.post(subscribe_url(school), {"email": " Parent@Example.com "})

        assert response.status_code == 303
        assert response["Location"].endswith("?updates=sent")
        subscription = CollectionSubscription.objects.get()
        assert subscription.email == EMAIL
        assert subscription.status == CollectionSubscription.Status.PENDING
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [EMAIL]
        assert manage_url(subscription) in mail.outbox[0].body
        assert school.passkey not in mail.outbox[0].body  # emails get forwarded

    def test_nothing_is_sent_until_confirmed(self, guest, school):
        guest.post(subscribe_url(school), {"email": EMAIL})
        published_in(school, at=timezone.now() - 3 * HOUR, photos=2)
        mail.outbox.clear()

        assert updates.send_updates() == 0
        assert mail.outbox == []

    def test_without_the_passkey_you_cannot_ask(self, school):
        response = Client().post(subscribe_url(school), {"email": EMAIL})

        assert response.status_code == 302
        assert response["Location"] == reverse("home")
        assert not CollectionSubscription.objects.exists()

    def test_bad_email_is_refused_without_saving(self, guest, school):
        response = guest.post(subscribe_url(school), {"email": "not-an-email"})

        assert response["Location"].endswith("?updates=invalid")
        assert not CollectionSubscription.objects.exists()

    def test_asking_twice_quickly_sends_one_email(self, guest, school):
        guest.post(subscribe_url(school), {"email": EMAIL})
        guest.post(subscribe_url(school), {"email": EMAIL})

        assert CollectionSubscription.objects.count() == 1
        assert len(mail.outbox) == 1

    def test_asking_again_later_resends_the_confirmation(self, school):
        subscription = updates.request_updates(school, EMAIL)
        later = timezone.now() + updates.RESEND_AFTER + datetime.timedelta(seconds=1)

        updates.request_updates(school, EMAIL, now=later)

        assert len(mail.outbox) == 2
        assert CollectionSubscription.objects.get().token == subscription.token

    def test_already_getting_updates_sends_nothing_and_looks_the_same(self, guest, school):
        subscribed(school)
        mail.outbox.clear()

        response = guest.post(subscribe_url(school), {"email": EMAIL})

        assert response["Location"].endswith("?updates=sent")  # no hint who's subscribed
        assert mail.outbox == []

    def test_page_shows_the_form_and_the_sent_notice(self, guest, school):
        page = guest.get(reverse("public-collection", args=[school.pk]))
        sent = guest.get(reverse("public-collection", args=[school.pk]) + "?updates=sent")

        assert b'name="email"' in page.content
        assert b"csrfmiddlewaretoken" in page.content
        assert b"Check your email" in sent.content

    def test_owner_preview_has_no_form(self, school, photographer):
        client = Client()
        client.force_login(photographer)

        page = client.get(reverse("public-collection", args=[school.pk]))

        assert b'name="email"' not in page.content

    def test_requires_csrf(self, guest, school):
        client = Client(enforce_csrf_checks=True)
        client.cookies = guest.cookies  # remembers the collection, but has no CSRF token

        response = client.post(subscribe_url(school), {"email": EMAIL})

        assert response.status_code == 403

    def test_throttled_per_visitor(self, guest, school, strict_throttle):
        for n in range(2):
            guest.post(subscribe_url(school), {"email": f"p{n}@example.com"})

        response = guest.post(subscribe_url(school), {"email": "p9@example.com"})

        assert response["Location"].endswith("?updates=busy")
        assert CollectionSubscription.objects.count() == 2


class TestManaging:
    def test_link_shows_a_button_and_a_get_changes_nothing(self, school):
        subscription = updates.request_updates(school, EMAIL)

        page = Client().get(manage_url(subscription))

        assert page.status_code == 200
        assert b"Send me updates" in page.content
        subscription.refresh_from_db()
        assert subscription.status == CollectionSubscription.Status.PENDING  # link scanners

    def test_button_confirms(self, school):
        subscription = updates.request_updates(school, EMAIL)

        response = Client().post(manage_url(subscription), {"action": "confirm"})

        assert response.status_code == 303
        subscription.refresh_from_db()
        assert subscription.status == CollectionSubscription.Status.ACTIVE
        assert subscription.notified_until is not None  # only news from now on

    def test_stop_and_start_again(self, school):
        subscription = subscribed(school)
        client = Client()

        client.post(manage_url(subscription), {"action": "stop"})
        subscription.refresh_from_db()
        assert subscription.status == CollectionSubscription.Status.STOPPED
        assert b"Start again" in client.get(manage_url(subscription)).content

        client.post(manage_url(subscription), {"action": "confirm"})
        subscription.refresh_from_db()
        assert subscription.status == CollectionSubscription.Status.ACTIVE

    def test_one_click_unsubscribe_needs_no_csrf_but_only_post(self, school):
        subscription = subscribed(school)
        url = reverse("collection-updates-stop", args=[subscription.token])
        client = Client(enforce_csrf_checks=True)

        assert client.get(url).status_code == 405
        assert client.post(url, {"List-Unsubscribe": "One-Click"}).status_code == 200
        subscription.refresh_from_db()
        assert subscription.status == CollectionSubscription.Status.STOPPED

    def test_unknown_link_is_404(self):
        assert Client().get(reverse("collection-updates", args=["nope"])).status_code == 404

    def test_photographer_sees_how_many_get_updates_not_who(self, auth_client, school):
        subscribed(school)
        subscribed(school, email="other@example.com")
        updates.request_updates(school, "pending@example.com")

        body = auth_client.get(reverse("collection-list")).json()["results"][0]

        assert body["subscriber_count"] == 2
        assert EMAIL not in str(body)


class TestSummaries:
    def test_new_event_and_more_photos_in_one_email(self, school):
        start = timezone.now() - 30 * HOUR
        old = published_in(school, title="Graduation", at=start - HOUR, photos=1)
        subscription = subscribed(school, since=start)
        published_in(school, title="Sports Day", at=start + HOUR, photos=3)
        add_photos(old, 2, at=start + 2 * HOUR)
        mail.outbox.clear()

        assert updates.send_updates() == 1

        message = mail.outbox[0]
        assert message.to == [EMAIL]
        assert "St. Maria Goretti SS" in message.subject
        assert "Sports Day" in message.body and "3 photos" in message.body
        assert "Graduation" in message.body and "2 new photos" in message.body
        assert reverse("public-collection", args=[school.pk]) in message.body
        assert manage_url(subscription) in message.body
        stop = reverse("collection-updates-stop", args=[subscription.token])
        assert stop in message.extra_headers["List-Unsubscribe"]
        assert message.extra_headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
        sent = message.message().as_string()  # as the mail server gets it
        headers = sent.split("\n\n", 1)[0]
        assert re.search(rf"^List-Unsubscribe: <http\S+{re.escape(stop)}>$", headers, re.M)
        assert "=?utf-8?" not in headers  # plain, not an encoded word

    def test_each_change_is_reported_once(self, school):
        start = timezone.now() - 30 * HOUR
        subscribed(school, since=start)
        published_in(school, at=start + HOUR, photos=1)

        updates.send_updates()
        later = timezone.now() + 2 * updates.MIN_GAP

        assert updates.send_updates(now=later) == 0
        assert len(mail.outbox) == 2  # confirmation + one summary

    def test_waits_while_the_photographer_is_still_uploading(self, school):
        subscribed(school, since=timezone.now() - 30 * HOUR)
        gallery = published_in(school, at=timezone.now() - 2 * HOUR, photos=1)
        add_photos(gallery, 1, at=timezone.now() - datetime.timedelta(minutes=5))
        mail.outbox.clear()

        assert updates.send_updates() == 0
        assert updates.send_updates(now=timezone.now() + updates.QUIET) == 1
        assert "2 photos" in mail.outbox[0].body

    def test_at_most_one_email_a_day(self, school):
        now = timezone.now()
        subscription = subscribed(school, since=now - 30 * HOUR)
        CollectionSubscription.objects.filter(pk=subscription.pk).update(
            last_sent_at=now - 5 * HOUR
        )
        published_in(school, at=now - 2 * HOUR, photos=1)
        mail.outbox.clear()

        assert updates.send_updates(now=now) == 0
        assert updates.send_updates(now=now + updates.MIN_GAP) == 1

    def test_leaves_out_what_the_passkey_does_not_open(self, school):
        start = timezone.now() - 30 * HOUR
        subscribed(school, since=start)
        published_in(school, title="Staff Party", at=start + HOUR, photos=1, separate_access=True)
        draft_event = EventFactory(owner=school.owner, collection=school, title="Prom")
        draft = GalleryFactory(event=draft_event, status=Gallery.Status.DRAFT)
        add_photos(draft, 1, at=start + HOUR)
        expired = published_in(school, title="Old Trip", at=start + HOUR, photos=1)
        Gallery.objects.filter(pk=expired.pk).update(expires_at=timezone.now() - HOUR)
        mail.outbox.clear()

        assert updates.send_updates() == 0

    def test_new_event_without_photos_waits_for_its_photos(self, school):
        start = timezone.now() - 30 * HOUR
        subscribed(school, since=start)
        gallery = published_in(school, at=start + HOUR, photos=0)
        mail.outbox.clear()

        assert updates.send_updates() == 0
        add_photos(gallery, 4, at=start + 2 * HOUR)
        assert updates.send_updates() == 1
        assert "Sports Day (" in mail.outbox[0].body  # still a new event, with all its photos
        assert "4 photos" in mail.outbox[0].body

    def test_archived_collection_and_lapsed_photographer_send_nothing(self, school, photographer):
        start = timezone.now() - 30 * HOUR
        subscribed(school, since=start)
        published_in(school, at=start + HOUR, photos=1)
        collections.set_status(school, Collection.Status.ARCHIVED)
        mail.outbox.clear()
        assert updates.send_updates() == 0

        collections.set_status(school, Collection.Status.ACTIVE)
        billing.subscription_for(photographer)
        photographer.subscription.paid_until = timezone.now() - datetime.timedelta(days=60)
        photographer.subscription.save()
        assert updates.send_updates() == 0

    def test_stopped_and_pending_get_nothing(self, school):
        start = timezone.now() - 30 * HOUR
        stopped = subscribed(school, since=start)
        updates.stop(stopped)
        updates.request_updates(school, "pending@example.com")
        published_in(school, at=start + HOUR, photos=1)
        mail.outbox.clear()

        assert updates.send_updates() == 0

    def test_a_failed_send_is_retried_next_time(self, school, monkeypatch):
        start = timezone.now() - 30 * HOUR
        subscribed(school, since=start)
        published_in(school, at=start + HOUR, photos=1)
        mail.outbox.clear()

        def broken(*args, **kwargs):
            raise OSError("smtp down")

        monkeypatch.setattr(updates, "deliver", broken)
        assert updates.send_updates() == 0
        monkeypatch.undo()
        assert updates.send_updates() == 1

    def test_queries_stay_flat_with_many_subscribers(self, school, django_assert_max_num_queries):
        start = timezone.now() - 30 * HOUR
        for n in range(15):
            subscribed(school, email=f"p{n}@example.com", since=start)
        published_in(school, at=start + HOUR, photos=2)
        mail.outbox.clear()

        # Changes are worked out once per collection, then one claim per subscriber.
        with django_assert_max_num_queries(15 + 12):
            assert updates.send_updates() == 15
