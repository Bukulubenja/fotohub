import datetime

import pytest
import requests
from django.urls import reverse
from django.utils import timezone

from assets.models import Photo, UploadSession
from assets.tests.factories import make_jpeg
from galleries.models import Gallery

pytestmark = pytest.mark.django_db


def start(client, gallery, *, size, filename="IMG_0001.jpg", content_type="image/jpeg"):
    return client.post(
        reverse("upload-list"),
        {
            "gallery": str(gallery.pk),
            "filename": filename,
            "content_type": content_type,
            "size": size,
        },
        format="json",
    )


def put_object(storage, photo_id, data):
    photo = Photo.objects.get(pk=photo_id)
    storage.client.put_object(Bucket=storage.bucket, Key=photo.original_key, Body=data)


def complete(client, photo_id, capture):
    with capture(execute=True):
        return client.post(reverse("upload-complete", args=[photo_id]))


class TestStartUpload:
    def test_small_file_gets_a_presigned_put(self, auth_client, gallery, storage):
        response = start(auth_client, gallery, size=2048)

        assert response.status_code == 201
        upload = response.data["upload"]
        assert upload["type"] == "single"
        assert upload["method"] == "PUT"
        assert upload["headers"] == {"Content-Type": "image/jpeg"}
        assert response.data["photo"]["status"] == "uploading"
        assert "original_key" not in response.data["photo"]

    def test_large_file_starts_a_multipart_upload(self, auth_client, gallery, storage, settings):
        size = settings.UPLOADS["MULTIPART_THRESHOLD_BYTES"] + 1

        response = start(auth_client, gallery, size=size)

        assert response.status_code == 201
        assert response.data["upload"]["type"] == "multipart"
        assert response.data["upload"]["part_count"] == 5  # 32 MiB + 1 byte in 8 MiB parts
        session = UploadSession.objects.get(photo_id=response.data["photo"]["id"])
        assert session.upload_id

    def test_storage_key_ignores_the_client_filename(self, auth_client, gallery, storage):
        response = start(auth_client, gallery, size=10, filename="C:\\fakepath\\../../evil.jpg")

        photo = Photo.objects.get(pk=response.data["photo"]["id"])
        assert photo.original_filename == "evil.jpg"
        assert photo.original_key == f"originals/{gallery.event.owner_id}/{gallery.pk}/{photo.pk}"

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("content_type", "image/svg+xml"),
            ("content_type", "application/pdf"),
            ("size", 0),
            ("size", 100 * 1024 * 1024 + 1),
        ],
    )
    def test_rejects_invalid_files(self, auth_client, gallery, storage, field, value):
        payload = {"size": 10, "content_type": "image/jpeg", field: value}

        response = start(auth_client, gallery, **payload)

        assert response.status_code == 400
        assert field in response.data

    def test_cannot_upload_into_another_photographers_gallery(self, other_client, gallery, storage):
        response = start(other_client, gallery, size=10)

        assert response.status_code == 400
        assert "gallery" in response.data
        assert not Photo.objects.exists()

    def test_archived_gallery_is_closed_for_uploads(self, auth_client, gallery, storage):
        Gallery.objects.filter(pk=gallery.pk).update(status=Gallery.Status.ARCHIVED)

        response = start(auth_client, gallery, size=10)

        assert response.status_code == 409

    def test_requires_authentication(self, api_client, gallery, storage):
        assert start(api_client, gallery, size=10).status_code == 403


class TestQuota:
    def test_rejects_upload_that_would_exceed_quota(
        self, auth_client, photographer, gallery, storage
    ):
        photographer.profile.storage_quota_bytes = 1000
        photographer.profile.save()

        assert start(auth_client, gallery, size=600).status_code == 201
        response = start(auth_client, gallery, size=600)

        assert response.status_code == 409
        assert response.data["detail"].code == "quota_exceeded"

    def test_aborting_releases_the_reservation(self, auth_client, photographer, gallery, storage):
        photographer.profile.storage_quota_bytes = 1000
        photographer.profile.save()
        first = start(auth_client, gallery, size=600).data["photo"]["id"]

        assert auth_client.delete(reverse("upload-detail", args=[first])).status_code == 204
        assert start(auth_client, gallery, size=600).status_code == 201

    def test_expired_reservations_stop_counting(self, auth_client, photographer, gallery, storage):
        photographer.profile.storage_quota_bytes = 1000
        photographer.profile.save()
        start(auth_client, gallery, size=600)
        UploadSession.objects.update(expires_at=timezone.now() - datetime.timedelta(seconds=1))

        assert start(auth_client, gallery, size=600).status_code == 201

    def test_usage_endpoint_reports_warning_level(
        self, auth_client, photographer, gallery, storage
    ):
        photographer.profile.storage_quota_bytes = 1000
        photographer.profile.save()
        start(auth_client, gallery, size=900)

        response = auth_client.get(reverse("storage-usage"))

        assert response.data == {
            "used_bytes": 900,
            "quota_bytes": 1000,
            "percent": 90,
            "warning_level": 90,
        }


class TestSingleUpload:
    def test_presigned_put_url_accepts_the_bytes(self, auth_client, gallery, storage):
        data = make_jpeg()
        upload = start(auth_client, gallery, size=len(data)).data["upload"]

        response = requests.put(upload["url"], data=data, headers=upload["headers"], timeout=5)

        assert response.status_code == 200

    def test_complete_processes_the_photo(
        self, auth_client, gallery, storage, django_capture_on_commit_callbacks
    ):
        data = make_jpeg(800, 600)
        photo_id = start(auth_client, gallery, size=len(data)).data["photo"]["id"]
        put_object(storage, photo_id, data)

        response = complete(auth_client, photo_id, django_capture_on_commit_callbacks)

        assert response.status_code == 200
        photo = Photo.objects.get(pk=photo_id)
        assert photo.status == Photo.Status.READY
        assert set(photo.variants) == {"thumb", "viewer", "web", "cover"}
        assert auth_client.get(reverse("photo-detail", args=[photo_id])).data["thumbnail_url"]

    def test_complete_is_idempotent(
        self, auth_client, gallery, storage, django_capture_on_commit_callbacks
    ):
        data = make_jpeg()
        photo_id = start(auth_client, gallery, size=len(data)).data["photo"]["id"]
        put_object(storage, photo_id, data)
        complete(auth_client, photo_id, django_capture_on_commit_callbacks)

        with django_capture_on_commit_callbacks() as callbacks:
            response = auth_client.post(reverse("upload-complete", args=[photo_id]))

        assert response.status_code == 200
        assert response.data["status"] == "ready"
        assert callbacks == []  # processing isn't queued twice

    def test_complete_before_bytes_arrive_is_a_conflict(self, auth_client, gallery, storage):
        photo_id = start(auth_client, gallery, size=10).data["photo"]["id"]

        response = auth_client.post(reverse("upload-complete", args=[photo_id]))

        assert response.status_code == 409
        assert Photo.objects.get(pk=photo_id).status == Photo.Status.UPLOADING

    def test_size_mismatch_discards_the_upload(
        self, auth_client, gallery, storage, django_capture_on_commit_callbacks
    ):
        photo_id = start(auth_client, gallery, size=10).data["photo"]["id"]
        key = Photo.objects.get(pk=photo_id).original_key
        put_object(storage, photo_id, b"x" * 5000)  # more than was reserved

        response = complete(auth_client, photo_id, django_capture_on_commit_callbacks)

        assert response.status_code == 409
        assert not Photo.objects.filter(pk=photo_id).exists()
        assert storage.object_size(key) is None

    def test_expired_upload_cannot_complete(self, auth_client, gallery, storage):
        photo_id = start(auth_client, gallery, size=10).data["photo"]["id"]
        UploadSession.objects.update(expires_at=timezone.now() - datetime.timedelta(seconds=1))

        assert auth_client.post(reverse("upload-complete", args=[photo_id])).status_code == 409

    def test_retrieve_resigns_the_put_url(self, auth_client, gallery, storage):
        photo_id = start(auth_client, gallery, size=10).data["photo"]["id"]

        response = auth_client.get(reverse("upload-detail", args=[photo_id]))

        assert response.status_code == 200
        assert response.data["upload"]["url"].startswith("https://")

    def test_other_photographer_cannot_touch_the_upload(
        self, auth_client, other_client, gallery, storage
    ):
        photo_id = start(auth_client, gallery, size=10).data["photo"]["id"]

        for method, name in [
            ("get", "upload-detail"),
            ("post", "upload-complete"),
            ("delete", "upload-detail"),
            ("get", "upload-parts"),
        ]:
            response = getattr(other_client, method)(reverse(name, args=[photo_id]))
            assert response.status_code == 404, name
        assert Photo.objects.filter(pk=photo_id).exists()


@pytest.mark.usefixtures("small_uploads")
class TestMultipartUpload:
    def start_multipart(self, client, gallery, data):
        response = start(client, gallery, size=len(data))
        assert response.data["upload"]["type"] == "multipart"
        return response.data["photo"]["id"]

    def test_full_flow(self, auth_client, gallery, storage, django_capture_on_commit_callbacks):
        data = make_jpeg(1200, 900)
        photo_id = self.start_multipart(auth_client, gallery, data)

        signed = auth_client.post(
            reverse("upload-sign-parts", args=[photo_id]), {"part_numbers": [1]}, format="json"
        )
        put = requests.put(signed.data["urls"]["1"], data=data, timeout=5)
        parts = auth_client.get(reverse("upload-parts", args=[photo_id]))
        response = complete(auth_client, photo_id, django_capture_on_commit_callbacks)

        assert put.status_code == 200
        assert [p["PartNumber"] for p in parts.data["parts"]] == [1]
        assert response.status_code == 200
        assert Photo.objects.get(pk=photo_id).status == Photo.Status.READY

    def test_rejects_part_numbers_outside_the_file(self, auth_client, gallery, storage):
        photo_id = self.start_multipart(auth_client, gallery, make_jpeg())

        response = auth_client.post(
            reverse("upload-sign-parts", args=[photo_id]), {"part_numbers": [1, 2]}, format="json"
        )

        assert response.status_code == 409

    def test_caps_parts_per_request(self, auth_client, gallery, storage, settings):
        photo_id = self.start_multipart(auth_client, gallery, make_jpeg())
        too_many = list(range(1, settings.UPLOADS["MAX_PARTS_PER_SIGN_REQUEST"] + 2))

        response = auth_client.post(
            reverse("upload-sign-parts", args=[photo_id]),
            {"part_numbers": too_many},
            format="json",
        )

        assert response.status_code == 400

    def test_complete_without_parts_is_a_conflict(self, auth_client, gallery, storage):
        photo_id = self.start_multipart(auth_client, gallery, make_jpeg())

        assert auth_client.post(reverse("upload-complete", args=[photo_id])).status_code == 409

    def test_abort_cancels_the_multipart_upload(self, auth_client, gallery, storage):
        photo_id = self.start_multipart(auth_client, gallery, make_jpeg())
        session = UploadSession.objects.select_related("photo").get(photo_id=photo_id)

        response = auth_client.delete(reverse("upload-detail", args=[photo_id]))

        assert response.status_code == 204
        uploads = storage.client.list_multipart_uploads(Bucket=storage.bucket)
        assert session.upload_id not in [u["UploadId"] for u in uploads.get("Uploads", [])]

    def test_cannot_sign_parts_for_a_single_upload(self, auth_client, gallery, storage, settings):
        settings.UPLOADS = {**settings.UPLOADS, "MULTIPART_THRESHOLD_BYTES": 10**9}
        photo_id = start(auth_client, gallery, size=10).data["photo"]["id"]

        response = auth_client.post(
            reverse("upload-sign-parts", args=[photo_id]), {"part_numbers": [1]}, format="json"
        )

        assert response.status_code == 409
        assert auth_client.get(reverse("upload-parts", args=[photo_id])).data == {"parts": []}
