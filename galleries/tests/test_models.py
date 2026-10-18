import datetime

import pytest
from django.utils import timezone

from galleries.models import Gallery
from galleries.services import (
    GalleryStateError,
    publish_gallery,
    rotate_gallery_token,
    unpublish_gallery,
)
from galleries.tests.factories import GalleryFactory

pytestmark = pytest.mark.django_db


class TestPublicToken:
    def test_is_long_random_and_unique(self):
        tokens = {GalleryFactory().public_token for _ in range(20)}

        assert len(tokens) == 20
        assert all(len(token) >= 40 for token in tokens)

    def test_rotation_replaces_token(self):
        gallery = GalleryFactory()
        old_token = gallery.public_token

        rotate_gallery_token(gallery)

        gallery.refresh_from_db()
        assert gallery.public_token != old_token


class TestPassword:
    def test_password_is_hashed_not_stored_plain(self):
        gallery = GalleryFactory()

        gallery.set_access_password("wedding2026")
        gallery.save()

        assert "wedding2026" not in gallery.password_hash
        assert gallery.has_password
        assert gallery.check_access_password("wedding2026")
        assert not gallery.check_access_password("wrong")

    def test_clearing_password_removes_protection(self):
        gallery = GalleryFactory()
        gallery.set_access_password("secret")

        gallery.set_access_password(None)

        assert not gallery.has_password
        assert not gallery.check_access_password("")


class TestPublishing:
    def test_draft_can_be_published(self):
        gallery = GalleryFactory()

        publish_gallery(gallery)

        gallery.refresh_from_db()
        assert gallery.status == Gallery.Status.PUBLISHED
        assert gallery.published_at is not None

    def test_cannot_publish_with_past_expiry(self):
        gallery = GalleryFactory(expires_at=timezone.now() - datetime.timedelta(days=1))

        with pytest.raises(GalleryStateError):
            publish_gallery(gallery)

    def test_cannot_publish_archived_gallery(self):
        gallery = GalleryFactory(status=Gallery.Status.ARCHIVED)

        with pytest.raises(GalleryStateError):
            publish_gallery(gallery)

    def test_unpublish_returns_to_draft(self):
        gallery = GalleryFactory()
        publish_gallery(gallery)

        unpublish_gallery(gallery)

        gallery.refresh_from_db()
        assert gallery.status == Gallery.Status.DRAFT

    def test_cannot_unpublish_a_draft(self):
        with pytest.raises(GalleryStateError):
            unpublish_gallery(GalleryFactory())


class TestIsViewable:
    def test_only_published_galleries_are_viewable(self):
        assert not GalleryFactory().is_viewable()

    def test_expired_published_gallery_is_not_viewable(self):
        gallery = GalleryFactory(expires_at=timezone.now() + datetime.timedelta(hours=1))
        publish_gallery(gallery)

        later = timezone.now() + datetime.timedelta(hours=2)

        assert gallery.is_viewable()
        assert not gallery.is_viewable(now=later)
