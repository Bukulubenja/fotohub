"""Customer-facing access policy (spec FR-19, FR-20, FR-25)."""

import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from galleries.services import publish_gallery, rotate_gallery_token
from galleries.tests.factories import GalleryFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def published_gallery():
    gallery = GalleryFactory()
    publish_gallery(gallery)
    return gallery


def metadata_url(gallery) -> str:
    return reverse("public-gallery", args=[gallery.public_token])


def unlock_url(gallery) -> str:
    return reverse("public-gallery-unlock", args=[gallery.public_token])


def test_published_gallery_returns_only_public_fields(api_client, published_gallery):
    response = api_client.get(metadata_url(published_gallery))

    assert response.status_code == 200
    assert set(response.data) == {
        "title",
        "event_title",
        "event_date",
        "description",
        "photographer",
        "download_policy",
        "requires_password",
        "is_unlocked",
    }
    assert response["X-Robots-Tag"] == "noindex, nofollow"
    assert response["Cache-Control"] == "private, no-store"
    assert "Cookie" in response["Vary"]


def test_draft_gallery_is_not_found(api_client):
    assert api_client.get(metadata_url(GalleryFactory())).status_code == 404


def test_unknown_token_is_not_found(api_client):
    response = api_client.get(reverse("public-gallery", args=["does-not-exist"]))

    assert response.status_code == 404


def test_expired_gallery_is_gone(api_client):
    gallery = GalleryFactory(expires_at=timezone.now() + datetime.timedelta(seconds=1))
    publish_gallery(gallery)
    gallery.expires_at = timezone.now() - datetime.timedelta(seconds=1)
    gallery.save()

    assert api_client.get(metadata_url(gallery)).status_code == 410


def test_rotated_token_stops_old_link(api_client, published_gallery):
    old_url = metadata_url(published_gallery)

    rotate_gallery_token(published_gallery)

    assert api_client.get(old_url).status_code == 404
    assert api_client.get(metadata_url(published_gallery)).status_code == 200


class TestPasswordProtection:
    @pytest.fixture
    def locked_gallery(self, published_gallery):
        published_gallery.set_access_password("family")
        published_gallery.save()
        return published_gallery

    def test_locked_gallery_reveals_only_lock_screen_fields(self, api_client, locked_gallery):
        response = api_client.get(metadata_url(locked_gallery))

        assert response.data == {
            "title": locked_gallery.title,
            "photographer": locked_gallery.event.owner.profile.display_name,
            "requires_password": True,
            "is_unlocked": False,
        }

    def test_unlocking_cycles_the_session_key(self, api_client, locked_gallery):
        api_client.get(metadata_url(locked_gallery))
        api_client.session.save()
        before = api_client.session.session_key

        api_client.post(unlock_url(locked_gallery), {"password": "family"}, format="json")

        assert api_client.session.session_key != before

    def test_per_gallery_limit_holds_across_ip_addresses(
        self, api_client, locked_gallery, strict_throttle
    ):
        statuses = [
            api_client.post(
                unlock_url(locked_gallery),
                {"password": "guess"},
                format="json",
                REMOTE_ADDR=f"10.0.0.{i}",
            ).status_code
            for i in range(4)
        ]

        assert statuses == [403, 403, 403, 429]

    def test_right_passwords_do_not_use_up_the_gallery_limit(
        self, api_client, locked_gallery, strict_throttle
    ):
        """Only wrong guesses count against the shared per-gallery limit, so a stranger can't
        lock real guests out, and guests opening the gallery don't block each other."""
        for i in range(5):
            response = api_client.post(
                unlock_url(locked_gallery),
                {"password": "family"},
                format="json",
                REMOTE_ADDR=f"10.0.1.{i}",
            )
            assert response.status_code == 204

    def test_wrong_password_is_rejected(self, api_client, locked_gallery):
        response = api_client.post(unlock_url(locked_gallery), {"password": "nope"}, format="json")

        assert response.status_code == 403

    def test_correct_password_unlocks_for_this_session(self, api_client, locked_gallery):
        response = api_client.post(
            unlock_url(locked_gallery), {"password": "family"}, format="json"
        )

        assert response.status_code == 204
        assert api_client.get(metadata_url(locked_gallery)).data["is_unlocked"] is True

    def test_changing_password_relocks_existing_sessions(self, api_client, locked_gallery):
        api_client.post(unlock_url(locked_gallery), {"password": "family"}, format="json")

        locked_gallery.set_access_password("new-secret")
        locked_gallery.save()

        assert api_client.get(metadata_url(locked_gallery)).data["is_unlocked"] is False

    def test_password_guessing_is_throttled(self, api_client, locked_gallery, strict_throttle):
        statuses = [
            api_client.post(
                unlock_url(locked_gallery), {"password": "guess"}, format="json"
            ).status_code
            for _ in range(3)
        ]

        assert statuses == [403, 403, 429]

    def test_open_gallery_is_always_unlocked(self, api_client, published_gallery):
        response = api_client.get(metadata_url(published_gallery))

        assert response.data["requires_password"] is False
        assert response.data["is_unlocked"] is True
