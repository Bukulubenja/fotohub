"""Collections: one passkey that opens every event a photographer adds for a group (a school).

Covers passkey rules, the owner API, the guest home page at /, the collection page, and how a
remembered collection opens its galleries without their own passwords.
"""

import datetime
import re

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from assets.tests.factories import ready_photo
from billing import services as billing
from galleries import collections
from galleries.models import Collection, Gallery
from galleries.tests.factories import CollectionFactory, EventFactory, GalleryFactory

pytestmark = pytest.mark.django_db
PASSWORD = "sports-day-2026"
RANDOM_PART = "[23456789ABCDEFGHJKMNPQRSTUVWXYZ]{5}"


@pytest.fixture
def school(photographer):
    return collections.create_collection(photographer, "St. Maria Goretti SS", prefix="MGSS")


def published_in(collection, title="Sports Day", password="", **event_fields) -> Gallery:
    event = EventFactory(owner=collection.owner, collection=collection, title=title, **event_fields)
    gallery = GalleryFactory(event=event, status=Gallery.Status.PUBLISHED)
    if password:
        gallery.set_access_password(password)
        gallery.save()
    return gallery


def enter(client: Client, passkey: str):
    return client.post(reverse("home"), {"passkey": passkey})


def hub_url(collection: Collection) -> str:
    return reverse("public-collection", args=[collection.pk])


def gallery_url(gallery: Gallery) -> str:
    return reverse("public-gallery-page", args=[gallery.public_token])


class TestPasskeys:
    def test_passkey_is_the_prefix_and_two_random_groups(self, school):
        assert re.fullmatch(f"MGSS-{RANDOM_PART}-{RANDOM_PART}", school.passkey)
        assert school.passkey_prefix == "MGSS"

    def test_default_prefix_is_the_initials_of_the_name(self):
        assert collections.default_prefix("St. Maria Goretti Secondary School") == "SMGSS"
        assert collections.default_prefix("Kampala International University Alumni") == "KIUA"
        assert collections.default_prefix("!!!") == "FH"

    def test_guests_may_type_it_in_any_case_with_or_without_dashes(self, school):
        typed = " " + school.passkey.lower().replace("-", " ") + " "

        assert collections.find_by_passkey(typed) == school

    @pytest.mark.parametrize("prefix", ["A", "TOOLONGPREFIX", "M-G", "MGSS!", ""])
    def test_rejects_prefixes_that_are_not_2_to_8_letters_or_digits(self, photographer, prefix):
        with pytest.raises(ValueError):
            collections.validate_prefix(prefix)

    def test_every_collection_gets_its_own_passkey(self, photographer):
        made = {
            collections.create_collection(photographer, f"School {n}", prefix="MGSS").passkey
            for n in range(20)
        }

        assert len(made) == 20

    def test_changing_the_passkey_keeps_the_prefix_and_signs_everyone_out(self, school):
        old_passkey, old_version = school.passkey, school.access_version

        collections.rotate_passkey(school)

        assert school.passkey != old_passkey
        assert school.passkey.startswith("MGSS-")
        assert school.access_version == old_version + 1
        assert collections.find_by_passkey(old_passkey) is None

    def test_archived_collections_are_not_found(self, school):
        school.status = Collection.Status.ARCHIVED
        school.save()

        assert collections.find_by_passkey(school.passkey) is None


class TestOwnerApi:
    def test_creates_a_collection_with_a_ready_to_send_message(self, auth_client):
        response = auth_client.post(
            "/api/v1/collections/", {"name": "St. Maria Goretti SS", "prefix": "mgss"}
        )

        assert response.status_code == 201
        body = response.json()
        assert body["passkey"].startswith("MGSS-")
        assert body["share_url"] == "http://testserver/"
        assert body["passkey"] in body["share_message"]
        assert "http://testserver/" in body["share_message"]

    def test_prefix_defaults_to_the_initials(self, auth_client):
        response = auth_client.post("/api/v1/collections/", {"name": "St. Maria Goretti SS"})

        assert response.json()["passkey"].startswith("SMGS-")

    def test_bad_prefix_is_a_field_error(self, auth_client):
        response = auth_client.post("/api/v1/collections/", {"name": "X", "prefix": "M!"})

        assert response.status_code == 400
        assert "prefix" in response.json()

    def test_passkey_cannot_be_set_directly(self, auth_client, school):
        response = auth_client.patch(
            f"/api/v1/collections/{school.pk}/", {"passkey": "EASY-1111-1111"}, format="json"
        )

        assert response.status_code == 200
        school.refresh_from_db()
        assert school.passkey != "EASY-1111-1111"

    def test_lists_only_your_own_collections(self, auth_client, other_client, school):
        assert auth_client.get("/api/v1/collections/").json()["count"] == 1
        assert other_client.get("/api/v1/collections/").json()["count"] == 0
        assert other_client.get(f"/api/v1/collections/{school.pk}/").status_code == 404

    def test_counts_its_events(self, auth_client, school):
        published_in(school)
        published_in(school, title="Prize Giving")

        body = auth_client.get(f"/api/v1/collections/{school.pk}/").json()

        assert body["event_count"] == 2

    def test_rotate_passkey(self, auth_client, school):
        old = school.passkey

        response = auth_client.post(f"/api/v1/collections/{school.pk}/rotate-passkey/")

        assert response.status_code == 200
        assert response.json()["passkey"] not in (old, "")

    def test_qr_code_opens_the_home_page_with_the_passkey_filled_in(self, auth_client, school):
        response = auth_client.get(f"/api/v1/collections/{school.pk}/qr/")

        assert response.status_code == 200
        assert response["Content-Type"] == "image/svg+xml"
        assert response.content.lstrip().startswith(b"<?xml") or b"<svg" in response.content

    def test_api_needs_a_signed_in_photographer(self, api_client, school):
        assert api_client.get("/api/v1/collections/").status_code in (401, 403)

    def test_events_can_leave_a_collection(self, auth_client, school):
        event = published_in(school).event

        response = auth_client.patch(
            f"/api/v1/events/{event.pk}/", {"collection": None}, format="json"
        )

        assert response.status_code == 200
        event.refresh_from_db()
        assert event.collection is None

    def test_qr_code_is_owner_only(self, other_client, school):
        assert other_client.get(f"/api/v1/collections/{school.pk}/qr/").status_code == 404

    def test_events_can_join_a_collection(self, auth_client, school):
        response = auth_client.post(
            "/api/v1/events/",
            {
                "title": "Staff party",
                "event_date": "2026-11-20",
                "collection": str(school.pk),
                "separate_access": True,
            },
            format="json",
        )

        assert response.status_code == 201
        body = response.json()
        assert (body["collection"], body["separate_access"]) == (str(school.pk), True)

    def test_events_cannot_join_another_photographers_collection(self, other_client, school):
        response = other_client.post(
            "/api/v1/events/",
            {"title": "Mine", "event_date": "2026-11-20", "collection": str(school.pk)},
            format="json",
        )

        assert response.status_code == 400
        assert "collection" in response.json()


class TestHomePage:
    def test_asks_for_a_passkey_with_private_headers(self, client: Client):
        response = client.get(reverse("home"))

        assert response.status_code == 200
        assert 'name="passkey"' in response.content.decode()
        assert response["X-Robots-Tag"] == "noindex, nofollow"
        assert response["Cache-Control"] == "private, no-store"
        assert response["Referrer-Policy"] == "same-origin"

    def test_signed_in_photographers_go_to_their_dashboard(self, client: Client, photographer):
        client.force_login(photographer)

        response = client.get(reverse("home"))

        assert response.status_code == 302
        assert response["Location"] == reverse("dashboard")

    def test_photographers_opening_a_qr_link_see_the_guest_page(self, client, photographer, school):
        client.force_login(photographer)

        response = client.get(reverse("home"), {"k": school.passkey})

        assert response.status_code == 200

    def test_qr_link_fills_in_the_passkey(self, client: Client, school):
        response = client.get(reverse("home"), {"k": school.passkey})

        assert f'value="{school.passkey}"' in response.content.decode()

    def test_right_passkey_opens_the_collection(self, client: Client, school):
        response = enter(client, school.passkey.lower())

        assert response.status_code == 303
        assert response["Location"] == hub_url(school)
        cookie = client.cookies[collections.COOKIE]
        assert cookie["httponly"]
        assert int(cookie["max-age"]) == collections.REMEMBER_SECONDS

    def test_wrong_passkey_is_refused(self, client: Client, school):
        response = enter(client, "MGSS-2222-2222")

        assert response.status_code == 403
        assert "That passkey isn&#x27;t right" in response.content.decode()
        assert collections.COOKIE not in client.cookies

    def test_lapsed_photographer_collection_is_unavailable(self, client: Client, school):
        lapse(school.owner)

        response = enter(client, school.passkey)

        assert response.status_code == 403
        assert "temporarily unavailable" in response.content.decode()

    def test_guessing_is_rate_limited(self, client: Client, school, strict_throttle):
        enter(client, "MGSS-2222-2222")
        enter(client, "MGSS-3333-3333")

        response = enter(client, school.passkey)

        assert response.status_code == 429

    def test_rate_limit_is_json_for_the_animated_form(
        self, client: Client, school, strict_throttle
    ):
        enter(client, "MGSS-2222-2222")
        enter(client, "MGSS-3333-3333")

        response = client.post(
            reverse("home"), {"passkey": school.passkey}, headers={"accept": "application/json"}
        )

        assert response.status_code == 429
        assert "Too many attempts" in response.json()["error"]

    def test_right_passkeys_do_not_use_up_the_limit(self, client: Client, school, strict_throttle):
        """Parents on one school Wi-Fi share an IP: only wrong guesses count."""
        for _ in range(5):
            assert enter(client, school.passkey).status_code == 303

    def test_form_needs_its_csrf_token(self, school):
        guarded = Client(enforce_csrf_checks=True)

        assert guarded.post(reverse("home"), {"passkey": school.passkey}).status_code == 403

        page = guarded.get(reverse("home")).content.decode()
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page).group(1)
        response = guarded.post(
            reverse("home"), {"passkey": school.passkey, "csrfmiddlewaretoken": token}
        )
        assert response.status_code == 303

    def test_animated_form_gets_json_and_the_cookie(self, client: Client, school):
        response = client.post(
            reverse("home"), {"passkey": school.passkey}, headers={"accept": "application/json"}
        )

        assert response.status_code == 200
        assert response.json() == {"url": hub_url(school)}
        assert collections.COOKIE in client.cookies

    def test_animated_form_gets_json_errors(self, client: Client, school):
        response = client.post(
            reverse("home"), {"passkey": "MGSS-2222-2222"}, headers={"accept": "application/json"}
        )

        assert response.status_code == 403
        assert "That passkey isn't right" in response.json()["error"]

    def test_remembers_collections_this_phone_has_opened(self, client: Client, school):
        enter(client, school.passkey)

        html = client.get(reverse("home")).content.decode()

        assert "St. Maria Goretti SS" in html
        assert hub_url(school) in html


class TestCollectionPage:
    def test_lists_its_published_events_newest_first(self, client: Client, school):
        older = published_in(school, title="Sports Day", event_date=datetime.date(2026, 3, 1))
        newer = published_in(school, title="Prize Giving", event_date=datetime.date(2026, 9, 1))
        ready_photo(newer)
        enter(client, school.passkey)

        response = client.get(hub_url(school))

        html = response.content.decode()
        assert response.status_code == 200
        assert html.index("Prize Giving") < html.index("Sports Day")
        assert gallery_url(older) in html and gallery_url(newer) in html
        assert response["X-Robots-Tag"] == "noindex, nofollow"
        assert response["Cache-Control"] == "private, no-store"

    def test_hides_drafts_expired_separate_and_other_collections(
        self, client: Client, school, photographer
    ):
        draft = published_in(school, title="Draft event")
        draft.status = Gallery.Status.DRAFT
        draft.save()
        expired = published_in(school, title="Old event")
        expired.expires_at = timezone.now() - datetime.timedelta(days=1)
        expired.save()
        separate = published_in(school, title="Staff party", separate_access=True)
        other = collections.create_collection(photographer, "Other school", prefix="OS")
        elsewhere = published_in(other, title="Elsewhere")
        enter(client, school.passkey)

        html = client.get(hub_url(school)).content.decode()

        for hidden in (draft, expired, separate, elsewhere):
            assert gallery_url(hidden) not in html

    def test_each_card_fans_out_up_to_four_photos(self, client: Client, school):
        gallery = published_in(school)
        photos = [ready_photo(gallery) for _ in range(6)]
        enter(client, school.passkey)

        html = client.get(hub_url(school)).content.decode()

        assert sum(f"{photo.pk}/thumb.webp" in html for photo in photos) == 4

    def test_year_chips_filter_events_when_there_are_several_years(self, client, school):
        this_year = published_in(school, title="Prize Giving", event_date=datetime.date(2026, 9, 1))
        last_year = published_in(school, title="Sports Day", event_date=datetime.date(2025, 3, 1))
        enter(client, school.passkey)

        everything = client.get(hub_url(school)).content.decode()
        filtered = client.get(hub_url(school), {"year": "2025"}).content.decode()

        assert "?year=2026" in everything and "?year=2025" in everything
        assert gallery_url(last_year) in filtered
        assert gallery_url(this_year) not in filtered

    def test_no_year_chips_for_a_single_year(self, client: Client, school):
        published_in(school)
        enter(client, school.passkey)

        assert "?year=" not in client.get(hub_url(school)).content.decode()

    def test_odd_year_values_are_ignored(self, client: Client, school):
        published_in(school)
        enter(client, school.passkey)

        for year in ("²", "abc", "1999"):
            assert client.get(hub_url(school), {"year": year}).status_code == 200

    def test_query_count_does_not_grow_with_events(
        self, client: Client, school, django_assert_max_num_queries
    ):
        for n in range(8):
            gallery = published_in(school, title=f"Event {n}")
            for _ in range(3):
                ready_photo(gallery)
        enter(client, school.passkey)

        with django_assert_max_num_queries(12):
            client.get(hub_url(school))

    def test_visiting_renews_the_remembered_passkey(self, client: Client, school):
        enter(client, school.passkey)

        response = client.get(hub_url(school))

        renewed = response.cookies[collections.COOKIE]
        assert int(renewed["max-age"]) == collections.REMEMBER_SECONDS

    def test_needs_the_passkey_first(self, client: Client, school):
        response = client.get(hub_url(school))

        assert response.status_code == 302
        assert response["Location"] == reverse("home")

    def test_tampered_cookie_is_ignored(self, client: Client, school):
        client.cookies[collections.COOKIE] = f'{{"{school.pk}": 1}}'

        assert client.get(hub_url(school)).status_code == 302

    def test_owner_can_preview_without_the_passkey(self, client: Client, school, photographer):
        client.force_login(photographer)

        assert client.get(hub_url(school)).status_code == 200

    def test_unarchiving_does_not_reopen_old_phones(self, client, auth_client, school):
        enter(client, school.passkey)
        url = f"/api/v1/collections/{school.pk}/"
        auth_client.patch(url, {"status": "archived"}, format="json")

        auth_client.patch(url, {"status": "active"}, format="json")

        assert client.get(hub_url(school)).status_code == 302

    def test_archived_collection_is_closed_even_to_remembered_phones(self, client, school):
        enter(client, school.passkey)
        school.status = Collection.Status.ARCHIVED
        school.save()

        assert client.get(hub_url(school)).status_code == 302


class TestGalleriesOpenedByTheCollection:
    def test_passkey_opens_password_protected_galleries_in_it(self, client: Client, school):
        gallery = published_in(school, password=PASSWORD)
        ready_photo(gallery)
        enter(client, school.passkey)

        html = client.get(gallery_url(gallery)).content.decode()

        assert 'name="password"' not in html
        assert "thumb.webp" in html

    def test_separate_events_still_ask_for_their_own_password(self, client: Client, school):
        gallery = published_in(school, password=PASSWORD, separate_access=True)
        enter(client, school.passkey)

        assert 'name="password"' in client.get(gallery_url(gallery)).content.decode()

    def test_does_not_open_galleries_outside_the_collection(self, client: Client, school):
        outside = GalleryFactory(
            event=EventFactory(owner=school.owner), status=Gallery.Status.PUBLISHED
        )
        outside.set_access_password(PASSWORD)
        outside.save()
        enter(client, school.passkey)

        assert 'name="password"' in client.get(gallery_url(outside)).content.decode()

    def test_changing_the_passkey_locks_remembered_phones_out(self, client: Client, school):
        gallery = published_in(school, password=PASSWORD)
        enter(client, school.passkey)

        collections.rotate_passkey(school)

        assert 'name="password"' in client.get(gallery_url(gallery)).content.decode()
        assert client.get(hub_url(school)).status_code == 302


def lapse(user) -> None:
    """Past the grace period and the 30 days galleries stay up afterwards."""
    billing.subscription_for(user)
    user.subscription.paid_until = timezone.now() - datetime.timedelta(days=60)
    user.subscription.save()


def test_collection_factory_builds_a_usable_collection():
    collection = CollectionFactory()

    assert collections.find_by_passkey(collection.passkey) == collection
