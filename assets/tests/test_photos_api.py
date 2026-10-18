import datetime

import pytest
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from assets.models import Photo
from assets.processing import VARIANTS, variant_key
from assets.services import expire_stale_uploads, storage_usage
from assets.tasks import expire_stale_uploads as expire_task
from assets.tests.factories import PhotoFactory, UploadSessionFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def photos(gallery):
    return {
        "ready": PhotoFactory(gallery=gallery, status=Photo.Status.READY),
        "failed": PhotoFactory(gallery=gallery, status=Photo.Status.FAILED),
    }


class TestPhotoList:
    def test_lists_only_own_photos(self, auth_client, photos):
        PhotoFactory()  # someone else's

        response = auth_client.get(reverse("photo-list"))

        assert {p["id"] for p in response.data["results"]} == {str(p.pk) for p in photos.values()}

    def test_filters_failed_photos_for_retry(self, auth_client, gallery, photos):
        response = auth_client.get(
            reverse("photo-list"), {"gallery": str(gallery.pk), "status": "failed"}
        )

        assert [p["id"] for p in response.data["results"]] == [str(photos["failed"].pk)]

    @pytest.mark.parametrize("params", [{"gallery": "not-a-uuid"}, {"status": "bogus"}])
    def test_malformed_filters_return_nothing(self, auth_client, photos, params):
        response = auth_client.get(reverse("photo-list"), params)

        assert response.status_code == 200
        assert response.data["results"] == []

    def test_unprocessed_photo_has_no_thumbnail(self, auth_client, photos, storage):
        response = auth_client.get(reverse("photo-detail", args=[photos["failed"].pk]))

        assert response.data["thumbnail_url"] is None
        assert "variants" not in response.data


class TestPhotoDelete:
    def test_deletes_original_and_derivatives(
        self, auth_client, gallery, storage, django_capture_on_commit_callbacks
    ):
        photo = PhotoFactory(gallery=gallery, status=Photo.Status.READY)
        for key in [photo.original_key, *(variant_key(photo, spec) for spec in VARIANTS)]:
            storage.client.put_object(Bucket=storage.bucket, Key=key, Body=b"x")

        with django_capture_on_commit_callbacks(execute=True):
            response = auth_client.delete(reverse("photo-detail", args=[photo.pk]))

        assert response.status_code == 204
        assert not Photo.objects.filter(pk=photo.pk).exists()
        assert storage.client.list_objects_v2(Bucket=storage.bucket).get("KeyCount", 0) == 0

    def test_deleting_a_gallery_removes_its_stored_files(
        self, auth_client, gallery, storage, django_capture_on_commit_callbacks
    ):
        photos = PhotoFactory.create_batch(3, gallery=gallery, status=Photo.Status.READY)
        for photo in photos:
            storage.client.put_object(Bucket=storage.bucket, Key=photo.original_key, Body=b"x")

        with django_capture_on_commit_callbacks(execute=True):
            response = auth_client.delete(reverse("gallery-detail", args=[gallery.pk]))

        assert response.status_code == 204
        assert storage.client.list_objects_v2(Bucket=storage.bucket).get("KeyCount", 0) == 0

    def test_rolled_back_delete_keeps_the_files(self, gallery, storage):
        photo = PhotoFactory(gallery=gallery)
        storage.client.put_object(Bucket=storage.bucket, Key=photo.original_key, Body=b"x")

        with pytest.raises(RuntimeError), transaction.atomic():
            photo.delete()
            raise RuntimeError

        assert storage.object_size(photo.original_key) == 1

    def test_cannot_delete_another_photographers_photo(self, other_client, photos, storage):
        response = other_client.delete(reverse("photo-detail", args=[photos["ready"].pk]))

        assert response.status_code == 404
        assert Photo.objects.filter(pk=photos["ready"].pk).exists()

    def test_finished_photo_is_not_deleted_through_the_upload_endpoint(
        self, auth_client, photos, storage
    ):
        response = auth_client.delete(reverse("upload-detail", args=[photos["ready"].pk]))

        assert response.status_code == 409


class TestExpiry:
    def test_removes_only_abandoned_uploads(self, gallery, storage):
        past = timezone.now() - datetime.timedelta(minutes=1)
        stale = UploadSessionFactory(photo__gallery=gallery, expires_at=past).photo
        live = UploadSessionFactory(photo__gallery=gallery).photo
        finished = PhotoFactory(gallery=gallery, status=Photo.Status.READY)

        assert expire_stale_uploads() == 1

        remaining = set(Photo.objects.values_list("pk", flat=True))
        assert remaining == {live.pk, finished.pk}
        assert stale.pk not in remaining

    def test_task_runs_the_sweep(self, gallery, storage):
        past = timezone.now() - datetime.timedelta(minutes=1)
        UploadSessionFactory(photo__gallery=gallery, expires_at=past)

        assert expire_task.delay().get() == 1

    def test_usage_counts_stored_and_live_uploads_only(self, photographer, gallery):
        past = timezone.now() - datetime.timedelta(minutes=1)
        PhotoFactory(gallery=gallery, status=Photo.Status.READY, size_bytes=100)
        UploadSessionFactory(photo__gallery=gallery, photo__size_bytes=20)
        UploadSessionFactory(photo__gallery=gallery, photo__size_bytes=5, expires_at=past)

        assert storage_usage(photographer).used_bytes == 120
