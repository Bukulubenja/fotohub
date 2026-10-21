"""Download-all archives: ZIP streaming, sharing, expiry, and the customer flow."""

import datetime
import io
import os
import zipfile

import pytest
from django.utils import timezone

from assets.models import Photo
from assets.tests.factories import ready_photo
from delivery import services
from delivery.archive import Entry, unique_names, write_archive
from delivery.models import DownloadJob
from galleries.models import Gallery
from galleries.tests.factories import EventFactory, GalleryFactory
from shop.models import Order
from shop.tests.factories import make_order

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
    def test_reasking_reuses_the_live_job(
        self, storage, published, django_capture_on_commit_callbacks
    ):
        order = make_order(published, [stored_photo(published, storage)])

        with django_capture_on_commit_callbacks(execute=True):
            first = services.request_archive(order)
        second = services.request_archive(order)

        assert first.pk == second.pk
        assert DownloadJob.objects.count() == 1

    def test_builds_a_zip_of_the_ordered_originals_only(
        self, storage, published, django_capture_on_commit_callbacks
    ):
        bought = stored_photo(published, storage, b"first", original_filename="IMG_2.jpg")
        stored_photo(published, storage, b"not bought", original_filename="IMG_3.jpg")
        order = make_order(published, [bought])

        with django_capture_on_commit_callbacks(execute=True):
            job = services.request_archive(order)

        job.refresh_from_db()
        assert job.status == DownloadJob.Status.READY
        archive = read_zip(storage, job.archive_key)
        assert archive.namelist() == ["IMG_2.jpg"]
        assert archive.read("IMG_2.jpg") == b"first!"  # the original, not the web JPEG

    def test_unpaid_order_is_refused(self, storage, published):
        order = make_order(
            published, [stored_photo(published, storage)], status=Order.Status.PENDING
        )

        with pytest.raises(services.ArchiveUnavailable, match="hasn't been paid"):
            services.request_archive(order)

    def test_order_whose_photos_were_all_deleted_is_refused(self, storage, published):
        photo = stored_photo(published, storage)
        order = make_order(published, [photo])
        photo.delete()

        with pytest.raises(services.ArchiveUnavailable, match="available any more"):
            services.request_archive(order)

    def test_oversized_order_is_refused(self, storage, published, settings):
        settings.DELIVERY = {**settings.DELIVERY, "MAX_ARCHIVE_BYTES": 5}
        order = make_order(published, [stored_photo(published, storage, b"too big")])

        with pytest.raises(services.ArchiveUnavailable, match="too large"):
            services.request_archive(order)

    def test_a_failed_build_is_recorded_and_not_reused(
        self, storage, published, django_capture_on_commit_callbacks
    ):
        photo = stored_photo(published, storage)
        order = make_order(published, [photo])
        storage.delete([photo.original_key])  # the source vanished

        # robust on_commit: the buyer gets the status page, not a 500; the error is logged.
        with django_capture_on_commit_callbacks(execute=True):
            services.request_archive(order)

        job = DownloadJob.objects.get()
        assert job.status == DownloadJob.Status.FAILED
        assert "couldn't prepare" in job.failure_reason
        storage.put_bytes(photo.original_key, b"back", "image/jpeg")
        with django_capture_on_commit_callbacks(execute=True):
            retry = services.request_archive(order)
        assert retry.pk != job.pk


class TestExpiry:
    def build(self, storage, published, capture) -> DownloadJob:
        order = make_order(published, [stored_photo(published, storage)])
        with capture(execute=True):
            job = services.request_archive(order)
        job.refresh_from_db()
        return job

    def test_expired_archives_are_deleted(
        self, storage, published, django_capture_on_commit_callbacks
    ):
        job = self.build(storage, published, django_capture_on_commit_callbacks)

        expired = services.expire_archives(now=job.expires_at + datetime.timedelta(seconds=1))

        job.refresh_from_db()
        assert expired == 1
        assert job.status == DownloadJob.Status.EXPIRED
        assert storage.object_size(job.archive_key) is None

    def test_stuck_jobs_fail(self, published, settings):
        order = make_order(published, [])
        job = DownloadJob.objects.create(
            gallery=published,
            order=order,
            policy="original",
            fingerprint="x",
            photo_count=1,
            expected_bytes=1,
        )
        later = timezone.now() + datetime.timedelta(
            seconds=settings.DELIVERY["STUCK_AFTER_SECONDS"] + 1
        )

        services.expire_archives(now=later)

        job.refresh_from_db()
        assert job.status == DownloadJob.Status.FAILED

    def test_deleting_the_gallery_deletes_its_archives(
        self, storage, published, django_capture_on_commit_callbacks
    ):
        job = self.build(storage, published, django_capture_on_commit_callbacks)

        with django_capture_on_commit_callbacks(execute=True):
            published.delete()

        assert storage.object_size(job.archive_key) is None
