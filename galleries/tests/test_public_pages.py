"""Customer gallery pages at /g/<token>/: access, privacy headers, pagination, downloads.

Presigned URLs are signed locally, so these tests need no bucket.
"""

import datetime
from urllib.parse import parse_qs, urlparse

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from assets import storage
from assets.models import Photo
from assets.tests.factories import PhotoFactory, ready_photo
from galleries.models import Gallery
from galleries.tests.factories import EventFactory, GalleryFactory

pytestmark = pytest.mark.django_db
PASSWORD = "amina-and-daniel"


@pytest.fixture
def published(photographer):
    return GalleryFactory(
        event=EventFactory(owner=photographer, title="Amina & Daniel"),
        status=Gallery.Status.PUBLISHED,
    )


@pytest.fixture
def locked_gallery(published):
    published.set_access_password(PASSWORD)
    published.save()
    return published


def page_url(gallery: Gallery) -> str:
    return reverse("public-gallery-page", args=[gallery.public_token])


def unlock_url(gallery: Gallery) -> str:
    return reverse("public-gallery-page-unlock", args=[gallery.public_token])


def download_url(gallery: Gallery, photo: Photo) -> str:
    return reverse("public-photo-download", args=[gallery.public_token, photo.pk])


class TestGalleryPage:
    def test_renders_photos_with_private_headers(self, client: Client, published):
        photo = ready_photo(published)

        response = client.get(page_url(published))

        assert response.status_code == 200
        html = response.content.decode()
        assert "Amina &amp; Daniel" in html
        assert f"{photo.pk}/thumb.webp" in html
        assert response["X-Robots-Tag"] == "noindex, nofollow"
        assert response["Cache-Control"] == "private, no-store"
        assert response["Referrer-Policy"] == "no-referrer"
        assert "Cookie" in response["Vary"]

    def test_page_is_compressed_for_clients_that_accept_it(self, client: Client, published):
        ready_photo(published)

        response = client.get(page_url(published), headers={"accept-encoding": "gzip"})

        assert response["Content-Encoding"] == "gzip"
        assert "Accept-Encoding" in response["Vary"]

    def test_locked_page_carrying_a_csrf_token_is_not_compressed(
        self, client: Client, locked_gallery
    ):
        response = client.get(page_url(locked_gallery), headers={"accept-encoding": "gzip"})

        assert "csrfmiddlewaretoken" in response.content.decode()
        assert not response.has_header("Content-Encoding")

    def test_never_exposes_originals_or_unprocessed_photos(self, client: Client, published):
        shown = ready_photo(published)
        pending = PhotoFactory(gallery=published, status=Photo.Status.PROCESSING)

        html = client.get(page_url(published)).content.decode()

        assert shown.original_key not in html
        assert str(pending.pk) not in html
        assert 'Photos <span class="muted">(1)</span>' in html

    def test_share_preview_uses_the_chosen_cover(self, client: Client, published):
        ready_photo(published)
        chosen = ready_photo(published)
        published.cover_photo = chosen
        published.save()

        html = client.get(page_url(published)).content.decode()

        assert 'property="og:image" content="' in html
        assert f"{chosen.pk}/cover.jpg" in html

    def test_cover_falls_back_to_first_ready_photo(self, client: Client, published):
        first = ready_photo(published)
        ready_photo(published)

        html = client.get(page_url(published)).content.decode()

        assert f"{first.pk}/cover.jpg" in html

    def test_titles_are_escaped(self, client: Client, published):
        published.event.title = "<script>alert(1)</script>"
        published.event.save()

        html = client.get(page_url(published)).content.decode()

        assert "<script>alert(1)" not in html
        assert "&lt;script&gt;alert(1)" in html

    def test_viewing_only_gallery_offers_no_downloads(self, client: Client, published):
        published.download_policy = Gallery.DownloadPolicy.DISABLED
        published.save()
        ready_photo(published)

        html = client.get(page_url(published)).content.decode()

        assert "data-download=" not in html
        assert "Viewing only" in html


class TestUnavailable:
    def test_unknown_token_is_404(self, client: Client):
        response = client.get(reverse("public-gallery-page", args=["nope"]))

        assert response.status_code == 404
        assert "isn't available" in response.content.decode()
        assert response["X-Robots-Tag"] == "noindex, nofollow"

    def test_draft_looks_like_an_unknown_link(self, client: Client, photographer):
        draft = GalleryFactory(event=EventFactory(owner=photographer, title="Secret Draft"))

        response = client.get(page_url(draft))

        assert response.status_code == 404
        assert "Secret Draft" not in response.content.decode()

    def test_expired_is_410(self, client: Client, published):
        published.expires_at = timezone.now() - datetime.timedelta(minutes=1)
        published.save()

        response = client.get(page_url(published))

        assert response.status_code == 410
        assert "expired" in response.content.decode()


class TestPassword:
    def test_locked_page_reveals_no_photos_or_cover(self, client: Client, locked_gallery):
        photo = ready_photo(locked_gallery)

        response = client.get(page_url(locked_gallery))

        html = response.content.decode()
        assert response.status_code == 200
        assert "Gallery password" in html
        assert str(photo.pk) not in html
        assert "og:image" not in html

    def test_wrong_password_is_refused(self, client: Client, locked_gallery):
        response = client.post(unlock_url(locked_gallery), {"password": "guess"})

        assert response.status_code == 403
        assert "That password isn&#x27;t right" in response.content.decode()

    def test_right_password_unlocks_this_session(self, client: Client, locked_gallery):
        photo = ready_photo(locked_gallery)

        response = client.post(unlock_url(locked_gallery), {"password": PASSWORD})

        assert response.status_code == 303
        assert response["Location"] == page_url(locked_gallery)
        assert str(photo.pk) in client.get(page_url(locked_gallery)).content.decode()
        assert "Gallery password" in Client().get(page_url(locked_gallery)).content.decode()

    def test_unlocked_gallery_still_hides_the_cover_in_share_preview(
        self, client: Client, locked_gallery
    ):
        ready_photo(locked_gallery)
        client.post(unlock_url(locked_gallery), {"password": PASSWORD})

        html = client.get(page_url(locked_gallery)).content.decode()

        assert "og:image" not in html

    def test_unlock_requires_csrf_token(self, locked_gallery):
        strict = Client(enforce_csrf_checks=True)

        response = strict.post(unlock_url(locked_gallery), {"password": PASSWORD})

        assert response.status_code == 403
        assert "Gallery password" in strict.get(page_url(locked_gallery)).content.decode()

    def test_guesses_are_throttled(self, client: Client, locked_gallery, strict_throttle):
        for _ in range(2):
            client.post(unlock_url(locked_gallery), {"password": "guess"})

        response = client.post(unlock_url(locked_gallery), {"password": PASSWORD})

        assert response.status_code == 429
        assert "Too many attempts" in response.content.decode()

    def test_page_and_api_share_one_guess_budget(
        self, client: Client, locked_gallery, strict_throttle
    ):
        api = reverse("public-gallery-unlock", args=[locked_gallery.public_token])
        for _ in range(2):
            client.post(api, {"password": "guess"}, content_type="application/json")

        response = client.post(unlock_url(locked_gallery), {"password": PASSWORD})

        assert response.status_code == 429


class TestOwnerPreview:
    def test_owner_previews_a_draft_without_password(self, client: Client, photographer):
        draft = GalleryFactory(event=EventFactory(owner=photographer))
        draft.set_access_password(PASSWORD)
        draft.save()
        photo = ready_photo(draft)
        client.force_login(photographer)

        html = client.get(page_url(draft)).content.decode()

        assert "Preview: this gallery is draft" in html
        assert str(photo.pk) in html

    def test_another_photographer_gets_404(self, client: Client, other_photographer):
        draft = GalleryFactory()
        client.force_login(other_photographer)

        assert client.get(page_url(draft)).status_code == 404


class TestPagination:
    @pytest.fixture(autouse=True)
    def two_per_page(self, settings):
        settings.GALLERY_MEDIA = {**settings.GALLERY_MEDIA, "PAGE_SIZE": 2}

    def test_first_page_links_to_more(self, client: Client, published):
        photos = [ready_photo(published) for _ in range(3)]

        html = client.get(page_url(published)).content.decode()

        assert 'href="?page=2" data-more' in html
        assert str(photos[2].pk) not in html
        assert 'data-total="3"' in html

    def test_partial_returns_only_tiles_and_the_end_marker(self, client: Client, published):
        photos = [ready_photo(published) for _ in range(3)]

        html = client.get(page_url(published), {"page": 2, "partial": 1}).content.decode()

        assert "<html" not in html
        assert str(photos[2].pk) in html
        assert 'data-next=""' in html
        assert 'alt="Photo 3 of 3"' in html


class TestDownload:
    def test_web_policy_serves_the_web_jpeg_as_attachment(self, client: Client, published):
        photo = ready_photo(published, original_filename="IMG_0042.CR3.jpeg")

        response = client.get(download_url(published, photo))

        assert response.status_code == 302
        target = urlparse(response["Location"])
        assert target.path.endswith(f"{photo.pk}/web.jpg")
        disposition = parse_qs(target.query)["response-content-disposition"][0]
        assert disposition.startswith('attachment; filename="IMG_0042.CR3.jpg"')
        assert response["X-Robots-Tag"] == "noindex, nofollow"

    def test_original_policy_serves_the_original(self, client: Client, published):
        published.download_policy = Gallery.DownloadPolicy.ORIGINAL
        published.save()
        photo = ready_photo(published, original_filename="Dàniel.jpg")

        response = client.get(download_url(published, photo))

        target = urlparse(response["Location"])
        assert target.path.endswith(photo.original_key)
        disposition = parse_qs(target.query)["response-content-disposition"][0]
        assert "filename*=UTF-8''D%C3%A0niel.jpg" in disposition

    def test_disabled_policy_refuses(self, client: Client, published):
        published.download_policy = Gallery.DownloadPolicy.DISABLED
        published.save()
        photo = ready_photo(published)

        assert client.get(download_url(published, photo)).status_code == 404

    def test_photo_from_another_gallery_is_404(self, client: Client, published):
        foreign = ready_photo(GalleryFactory(status=Gallery.Status.PUBLISHED))

        assert client.get(download_url(published, foreign)).status_code == 404

    def test_unprocessed_photo_is_404(self, client: Client, published):
        pending = PhotoFactory(gallery=published, status=Photo.Status.PROCESSING)

        assert client.get(download_url(published, pending)).status_code == 404

    def test_locked_gallery_sends_visitor_to_the_password_page(
        self, client: Client, locked_gallery
    ):
        photo = ready_photo(locked_gallery)

        response = client.get(download_url(locked_gallery, photo))

        assert response.status_code == 302
        assert response["Location"] == page_url(locked_gallery)

    def test_unpublished_gallery_blocks_downloads(self, client: Client, published):
        photo = ready_photo(published)
        published.status = Gallery.Status.DRAFT
        published.save()

        assert client.get(download_url(published, photo)).status_code == 404


class TestCacheableUrls:
    def at(self, monkeypatch, when: datetime.datetime) -> None:
        monkeypatch.setattr(storage, "_real_now", lambda: when)

    def test_same_url_within_a_window(self, monkeypatch, settings):
        window = settings.GALLERY_MEDIA["URL_WINDOW_SECONDS"]
        start = storage.window_start(
            datetime.datetime(2026, 10, 20, 9, tzinfo=datetime.UTC), window
        )
        store = storage.get_storage()

        self.at(monkeypatch, start + datetime.timedelta(seconds=5))
        first = store.presign_get_cacheable("derivatives/x/thumb.webp")
        self.at(monkeypatch, start + datetime.timedelta(seconds=window - 5))
        second = store.presign_get_cacheable("derivatives/x/thumb.webp")
        self.at(monkeypatch, start + datetime.timedelta(seconds=window + 5))
        third = store.presign_get_cacheable("derivatives/x/thumb.webp")

        assert first == second
        assert third != first

    def test_url_stays_valid_for_at_least_one_more_window(self, settings):
        window = settings.GALLERY_MEDIA["URL_WINDOW_SECONDS"]

        url = storage.get_storage().presign_get_cacheable("derivatives/x/thumb.webp")

        query = parse_qs(urlparse(url).query)
        assert int(query["X-Amz-Expires"][0]) == 2 * window

    def test_other_signing_uses_the_real_clock(self, monkeypatch):
        long_ago = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
        self.at(monkeypatch, long_ago)
        store = storage.get_storage()
        store.presign_get_cacheable("a")
        monkeypatch.undo()

        url = store.presign_get("a")

        assert parse_qs(urlparse(url).query)["X-Amz-Date"][0] != "20200101T000000Z"


class TestCoverSetting:
    def test_owner_can_pick_a_ready_photo_from_the_gallery(self, auth_client, published):
        photo = ready_photo(published)

        response = auth_client.patch(
            reverse("gallery-detail", args=[published.pk]),
            {"cover_photo": str(photo.pk)},
            format="json",
        )

        assert response.status_code == 200
        assert response.data["cover_photo"] == photo.pk

    def test_photo_from_another_of_my_galleries_is_rejected(
        self, auth_client, published, photographer
    ):
        mine_elsewhere = ready_photo(GalleryFactory(event=EventFactory(owner=photographer)))

        response = auth_client.patch(
            reverse("gallery-detail", args=[published.pk]),
            {"cover_photo": str(mine_elsewhere.pk)},
            format="json",
        )

        assert response.status_code == 400

    def test_another_photographers_photo_is_rejected(self, auth_client, published):
        foreign = ready_photo(GalleryFactory())

        response = auth_client.patch(
            reverse("gallery-detail", args=[published.pk]),
            {"cover_photo": str(foreign.pk)},
            format="json",
        )

        assert response.status_code == 400

    def test_processing_photo_is_rejected(self, auth_client, published):
        pending = PhotoFactory(gallery=published, status=Photo.Status.PROCESSING)

        response = auth_client.patch(
            reverse("gallery-detail", args=[published.pk]),
            {"cover_photo": str(pending.pk)},
            format="json",
        )

        assert response.status_code == 400
