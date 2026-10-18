"""Photographer-facing API, with emphasis on tenant isolation (spec FR-03, acceptance #1)."""

import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from galleries.models import Event, Gallery
from galleries.tests.factories import EventFactory, GalleryFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def own_event(photographer):
    return EventFactory(owner=photographer)


@pytest.fixture
def own_gallery(own_event):
    return GalleryFactory(event=own_event)


@pytest.fixture
def foreign_gallery(other_photographer):
    return GalleryFactory(event=EventFactory(owner=other_photographer))


class TestEvents:
    def test_create_assigns_owner_from_session(self, auth_client, photographer, other_photographer):
        response = auth_client.post(
            reverse("event-list"),
            {"title": "Graduation", "event_date": "2026-11-01", "owner": other_photographer.pk},
            format="json",
        )

        assert response.status_code == 201
        assert Event.objects.get(pk=response.data["id"]).owner == photographer

    def test_list_shows_only_own_events(self, auth_client, own_event, foreign_gallery):
        response = auth_client.get(reverse("event-list"))

        ids = [item["id"] for item in response.data["results"]]
        assert ids == [str(own_event.pk)]

    def test_unauthenticated_access_denied(self, api_client):
        assert api_client.get(reverse("event-list")).status_code == 403


class TestGalleryCrud:
    def test_create_gallery_for_own_event(self, auth_client, own_event):
        response = auth_client.post(
            reverse("gallery-list"),
            {"event": str(own_event.pk), "title": "Ceremony", "download_policy": "web"},
            format="json",
        )

        assert response.status_code == 201
        assert response.data["status"] == "draft"
        assert response.data["has_password"] is False
        assert len(response.data["public_token"]) >= 40

    def test_cannot_attach_gallery_to_foreign_event(self, auth_client, foreign_gallery):
        response = auth_client.post(
            reverse("gallery-list"),
            {"event": str(foreign_gallery.event.pk), "title": "Sneaky"},
            format="json",
        )

        assert response.status_code == 400
        assert "event" in response.data

    def test_set_and_clear_password(self, auth_client, own_gallery):
        url = reverse("gallery-detail", args=[own_gallery.pk])

        set_response = auth_client.patch(url, {"password": "family"}, format="json")
        own_gallery.refresh_from_db()
        assert set_response.data["has_password"] is True
        assert "password" not in set_response.data
        assert own_gallery.check_access_password("family")

        clear_response = auth_client.patch(url, {"password": None}, format="json")
        assert clear_response.data["has_password"] is False

    def test_status_and_token_are_read_only(self, auth_client, own_gallery):
        original_token = own_gallery.public_token

        auth_client.patch(
            reverse("gallery-detail", args=[own_gallery.pk]),
            {"status": "published", "public_token": "chosen-by-attacker"},
            format="json",
        )

        own_gallery.refresh_from_db()
        assert own_gallery.status == Gallery.Status.DRAFT
        assert own_gallery.public_token == original_token

    def test_rejects_expiry_in_the_past(self, auth_client, own_gallery):
        past = (timezone.now() - datetime.timedelta(days=1)).isoformat()

        response = auth_client.patch(
            reverse("gallery-detail", args=[own_gallery.pk]), {"expires_at": past}, format="json"
        )

        assert response.status_code == 400


class TestConcurrentEdits:
    def test_stale_edit_does_not_revive_rotated_token(self, photographer, own_gallery):
        from rest_framework.test import APIRequestFactory

        from galleries.serializers import GallerySerializer
        from galleries.services import rotate_gallery_token

        stale = Gallery.objects.get(pk=own_gallery.pk)
        rotated = rotate_gallery_token(Gallery.objects.get(pk=own_gallery.pk))
        request = APIRequestFactory().patch("/")
        request.user = photographer

        serializer = GallerySerializer(
            stale, data={"title": "Renamed"}, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        fresh = Gallery.objects.get(pk=own_gallery.pk)
        assert fresh.title == "Renamed"
        assert fresh.public_token == rotated.public_token
        assert fresh.access_version == rotated.access_version

    def test_expired_gallery_can_still_be_edited(self, auth_client, own_gallery):
        own_gallery.expires_at = timezone.now() - datetime.timedelta(days=1)
        own_gallery.save()
        url = reverse("gallery-detail", args=[own_gallery.pk])
        unchanged_expiry = auth_client.get(url).data["expires_at"]

        response = auth_client.patch(
            url, {"title": "Renamed", "expires_at": unchanged_expiry}, format="json"
        )

        assert response.status_code == 200


class TestGalleryActions:
    def test_publish_and_unpublish(self, auth_client, own_gallery):
        publish = auth_client.post(reverse("gallery-publish", args=[own_gallery.pk]))
        assert publish.status_code == 200
        assert publish.data["status"] == "published"

        unpublish = auth_client.post(reverse("gallery-unpublish", args=[own_gallery.pk]))
        assert unpublish.status_code == 200
        assert unpublish.data["status"] == "draft"

    def test_invalid_transition_returns_conflict(self, auth_client, own_gallery):
        response = auth_client.post(reverse("gallery-unpublish", args=[own_gallery.pk]))

        assert response.status_code == 409

    def test_rotate_token(self, auth_client, own_gallery):
        old = own_gallery.public_token

        response = auth_client.post(reverse("gallery-rotate-token", args=[own_gallery.pk]))

        assert response.status_code == 200
        assert response.data["public_token"] != old


@pytest.mark.parametrize(
    ("method", "route"),
    [
        ("get", "gallery-detail"),
        ("patch", "gallery-detail"),
        ("delete", "gallery-detail"),
        ("post", "gallery-publish"),
        ("post", "gallery-unpublish"),
        ("post", "gallery-rotate-token"),
    ],
)
def test_foreign_gallery_is_invisible(auth_client, foreign_gallery, method, route):
    url = reverse(route, args=[foreign_gallery.pk])

    response = getattr(auth_client, method)(url, {}, format="json")

    assert response.status_code == 404
    foreign_gallery.refresh_from_db()
    assert foreign_gallery.status == Gallery.Status.DRAFT


@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_foreign_event_is_invisible(auth_client, foreign_gallery, method):
    url = reverse("event-detail", args=[foreign_gallery.event.pk])

    response = getattr(auth_client, method)(url, {"title": "Hacked"}, format="json")

    assert response.status_code == 404
    assert Event.objects.filter(pk=foreign_gallery.event.pk, title="Hacked").count() == 0
