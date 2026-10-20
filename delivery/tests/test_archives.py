"""Download-all archives: ZIP streaming, sharing, expiry, and the customer flow."""

import datetime
import io
import os
import zipfile
from urllib.parse import parse_qs, urlparse

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from assets.models import Photo
from assets.tests.factories import ready_photo
from delivery import services
from delivery.archive import Entry, unique_names, write_archive
from delivery.models import DownloadJob
from galleries.models import Gallery
from galleries.tests.factories import EventFactory, GalleryFactory

pytestmark = pytest.mark.django_db
MIB = 1024 * 1024


def stored_photo(gallery: Gallery, store, content: bytes = b"jpeg-bytes", **kwargs) -> Photo:
    """A ready photo whose web JPEG and original really exist in the bucket."""
    photo = ready_photo(gallery, **kwargs)
    photo.variants["web"]["bytes"] = len(content)
    photo.size_bytes = len(content) + 1
    photo.save()
    store.put_bytes(photo.variants["web"]["key"], content, "image/jpeg")
    store.put_bytes(photo.original_key, content + b"!", "image/jpeg")
    return photo


def read_zip(store, key: str) -> zipfile.ZipFile:
    data = store.client.get_object(Bucket=store.bucket, Key=key)["Body"].read()
    return zipfile.ZipFile(io.BytesIO(data))


@pytest.fixture
def published(photographer):
    return GalleryFactory(
        event=EventFactory(owner=photographer, title="Amina & Daniel"),
        status=Gallery.Status.PUBLISHED,
    )


class TestUniqueNames:
    def test_duplicates_get_numbered_case_insensitively(self):
        assert unique_names(["IMG_1.jpg", "img_1.JPG", "IMG_1.jpg"]) == [
            "IMG_1.jpg",
            "img_1 (2).JPG",
            "IMG_1 (3).jpg",
        ]

    def test_numbering_skips_names_already_taken(self):
        assert unique_names(["a.jpg", "a (2).jpg", "a.jpg"]) == ["a.jpg", "a (2).jpg", "a (3).jpg"]

    def test_paths_are_flattened(self):
        assert unique_names(["../../etc/passwd", "C:\\Users\\x\\b.jpg", ""]) == [
            "passwd",
            "b.jpg",
            "photo.jpg",
        ]


class TestWriteArchive:
    def test_streams_entries_across_several_parts(self, storage):
        blobs = {f"src/{i}": os.urandom(3 * MIB) for i in range(3)}  # 9 MiB > one 5 MiB part
        for key, data in blobs.items():
            storage.put_bytes(key, data, "image/jpeg")
        entries = [
            Entry(key, f"photo-{i}.jpg", len(data)) for i, (key, data) in enumerate(blobs.items())
        ]

        size = write_archive(storage, "archives/x.zip", entries, 5 * MIB, timezone.localtime())

        archive = read_zip(storage, "archives/x.zip")
        assert archive.namelist() == ["photo-0.jpg", "photo-1.jpg", "photo-2.jpg"]
        assert archive.read("photo-1.jpg") == blobs["src/1"]
        assert archive.testzip() is None
        assert all(i.compress_type == zipfile.ZIP_STORED for i in archive.infolist())
        assert size == storage.object_size("archives/x.zip")

    def test_failure_aborts_the_upload_and_leaves_nothing(self, storage):
        storage.put_bytes("src/ok", b"fine", "image/jpeg")
        entries = [Entry("src/ok", "ok.jpg", 4), Entry("src/missing", "gone.jpg", 4)]

        with pytest.raises(Exception):  # noqa: B017 - any storage error must abort
            write_archive(storage, "archives/y.zip", entries, 5 * MIB, timezone.localtime())

        assert storage.object_size("archives/y.zip") is None
        uploads = storage.client.list_multipart_uploads(Bucket=storage.bucket)
        assert not uploads.get("Uploads")


class TestRequestArchive:
    def test_visitors_share_one_job(self, storage, published, django_capture_on_commit_callbacks):
        stored_photo(published, storage)

        with django_capture_on_commit_callbacks(execute=True):
            first = services.request_archive(published)
        second = services.request_archive(published)

        assert first.pk == second.pk
        assert DownloadJob.objects.count() == 1

    def test_new_photo_means_a_new_archive(
        self, storage, published, django_capture_on_commit_callbacks
    ):
        stored_photo(published, storage)
        with django_capture_on_commit_callbacks(execute=True):
            first = services.request_archive(published)
        stored_photo(published, storage)

        with django_capture_on_commit_callbacks(execute=True):
            second = services.request_archive(published)

        assert second.pk != first.pk
        assert second.photo_count == 2

    def test_builds_a_zip_of_web_jpegs(
        self, storage, published, django_capture_on_commit_callbacks
    ):
        stored_photo(published, storage, b"first", original_filename="IMG_1.CR3")
        stored_photo(published, storage, b"second", original_filename="IMG_1.cr3")

        with django_capture_on_commit_callbacks(execute=True):
            job = services.request_archive(published)

        job.refresh_from_db()
        assert job.status == DownloadJob.Status.READY
        assert job.expires_at > timezone.now()
        archive = read_zip(storage, job.archive_key)
        assert archive.namelist() == ["IMG_1.jpg", "IMG_1 (2).jpg"]
        assert archive.read("IMG_1 (2).jpg") == b"second"

    def test_original_policy_packs_originals(
        self, storage, published, django_capture_on_commit_callbacks
    ):
        published.download_policy = Gallery.DownloadPolicy.ORIGINAL
        published.save()
        stored_photo(published, storage, b"raw", original_filename="DSC_9.jpg")

        with django_capture_on_commit_callbacks(execute=True):
            job = services.request_archive(published)

        job.refresh_from_db()
        assert read_zip(storage, job.archive_key).read("DSC_9.jpg") == b"raw!"

    def test_disabled_downloads_are_refused(self, published):
        published.download_policy = Gallery.DownloadPolicy.DISABLED
        published.save()

        with pytest.raises(services.ArchiveUnavailable, match="turned off"):
            services.request_archive(published)

    def test_empty_gallery_is_refused(self, published):
        with pytest.raises(services.ArchiveUnavailable, match="no photos"):
            services.request_archive(published)

    def test_oversized_gallery_is_refused(self, storage, published, settings):
        settings.DELIVERY = {**settings.DELIVERY, "MAX_ARCHIVE_BYTES": 5}
        stored_photo(published, storage, b"too big")

        with pytest.raises(services.ArchiveUnavailable, match="too large"):
            services.request_archive(published)

    def test_a_failed_build_is_recorded_and_not_reused(
        self, storage, published, django_capture_on_commit_callbacks
    ):
        photo = stored_photo(published, storage)
        storage.delete([photo.variants["web"]["key"]])  # the source vanished

        # robust on_commit: the visitor gets the status page, not a 500; the error is logged.
        with django_capture_on_commit_callbacks(execute=True):
            services.request_archive(published)

        job = DownloadJob.objects.get()
        assert job.status == DownloadJob.Status.FAILED
        assert "couldn't prepare" in job.failure_reason
        storage.put_bytes(photo.variants["web"]["key"], b"back", "image/jpeg")
        with django_capture_on_commit_callbacks(execute=True):
            retry = services.request_archive(published)
        assert retry.pk != job.pk


class TestExpiry:
    def test_expired_archives_are_deleted(
        self, storage, published, django_capture_on_commit_callbacks
    ):
        stored_photo(published, storage)
        with django_capture_on_commit_callbacks(execute=True):
            job = services.request_archive(published)
        job.refresh_from_db()

        expired = services.expire_archives(now=job.expires_at + datetime.timedelta(seconds=1))

        job.refresh_from_db()
        assert expired == 1
        assert job.status == DownloadJob.Status.EXPIRED
        assert storage.object_size(job.archive_key) is None

    def test_stuck_jobs_fail(self, published, settings):
        job = DownloadJob.objects.create(
            gallery=published, policy="web", fingerprint="x", photo_count=1, expected_bytes=1
        )
        later = timezone.now() + datetime.timedelta(
            seconds=settings.DELIVERY["STUCK_AFTER_SECONDS"] + 1
        )

        services.expire_archives(now=later)

        job.refresh_from_db()
        assert job.status == DownloadJob.Status.FAILED

    def test_deleting_the_gallery_deletes_its_archive(
        self, storage, published, django_capture_on_commit_callbacks
    ):
        stored_photo(published, storage)
        with django_capture_on_commit_callbacks(execute=True):
            job = services.request_archive(published)

        with django_capture_on_commit_callbacks(execute=True):
            published.delete()

        assert storage.object_size(job.archive_key) is None


class TestCustomerFlow:
    def request_url(self, gallery: Gallery) -> str:
        return reverse("public-archive-request", args=[gallery.public_token])

    def test_gallery_page_offers_download_all_with_its_size(
        self, client: Client, storage, published
    ):
        stored_photo(published, storage, b"x" * 2048)

        html = client.get(
            reverse("public-gallery-page", args=[published.public_token])
        ).content.decode()

        assert "Download all · 2.0\xa0KB" in html

    def test_viewing_only_gallery_has_no_download_all(self, client: Client, storage, published):
        published.download_policy = Gallery.DownloadPolicy.DISABLED
        published.save()
        stored_photo(published, storage)

        html = client.get(
            reverse("public-gallery-page", args=[published.public_token])
        ).content.decode()

        assert "Download all" not in html

    def test_request_then_status_then_file(
        self, client: Client, storage, published, django_capture_on_commit_callbacks
    ):
        stored_photo(published, storage)

        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(self.request_url(published))

        assert response.status_code == 303
        status_page = client.get(response["Location"])
        html = status_page.content.decode()
        assert "Your download is ready" in html
        assert status_page["X-Robots-Tag"] == "noindex, nofollow"
        job = DownloadJob.objects.get()
        file_response = client.get(
            reverse("public-archive-file", args=[published.public_token, job.pk])
        )
        assert file_response.status_code == 302
        disposition = parse_qs(urlparse(file_response["Location"]).query)[
            "response-content-disposition"
        ][0]
        assert 'filename="Amina & Daniel - ' in disposition

    def test_pending_page_refreshes_itself(self, client: Client, published):
        job = DownloadJob.objects.create(
            gallery=published, policy="web", fingerprint="x", photo_count=3, expected_bytes=10
        )

        html = client.get(
            reverse("public-archive-status", args=[published.public_token, job.pk])
        ).content.decode()

        assert 'http-equiv="refresh"' in html
        assert "Preparing your download" in html

    def test_archive_built_under_old_settings_is_not_served(
        self, client: Client, storage, published, django_capture_on_commit_callbacks
    ):
        published.download_policy = Gallery.DownloadPolicy.ORIGINAL
        published.save()
        stored_photo(published, storage)
        with django_capture_on_commit_callbacks(execute=True):
            client.post(self.request_url(published))
        job = DownloadJob.objects.get()
        published.download_policy = Gallery.DownloadPolicy.WEB
        published.save()

        file_response = client.get(
            reverse("public-archive-file", args=[published.public_token, job.pk])
        )
        status_html = client.get(file_response["Location"]).content.decode()

        assert file_response["Location"].endswith(f"/archive/{job.pk}/")
        assert "Download settings changed" in status_html

    def test_locked_gallery_sends_visitor_to_password_page(self, client: Client, published):
        published.set_access_password("secret-pass")
        published.save()

        response = client.post(self.request_url(published))

        assert response.status_code == 302
        assert response["Location"] == reverse("public-gallery-page", args=[published.public_token])

    def test_disabled_downloads_explain_why(self, client: Client, published):
        published.download_policy = Gallery.DownloadPolicy.DISABLED
        published.save()

        response = client.post(self.request_url(published))

        assert response.status_code == 409
        assert "turned off" in response.content.decode()

    def test_requests_are_throttled(self, client: Client, published, strict_throttle):
        for _ in range(2):
            client.post(self.request_url(published))

        response = client.post(self.request_url(published))

        assert response.status_code == 429

    def test_job_from_another_gallery_is_404(self, client: Client, published):
        other = GalleryFactory(status=Gallery.Status.PUBLISHED)
        job = DownloadJob.objects.create(
            gallery=other, policy="web", fingerprint="x", photo_count=1, expected_bytes=1
        )

        response = client.get(
            reverse("public-archive-status", args=[published.public_token, job.pk])
        )

        assert response.status_code == 404

    def test_unpublished_gallery_blocks_archive_files(
        self, client: Client, storage, published, django_capture_on_commit_callbacks
    ):
        stored_photo(published, storage)
        with django_capture_on_commit_callbacks(execute=True):
            client.post(self.request_url(published))
        job = DownloadJob.objects.get()
        published.status = Gallery.Status.DRAFT
        published.save()

        response = client.get(reverse("public-archive-file", args=[published.public_token, job.pk]))

        assert response.status_code == 404
