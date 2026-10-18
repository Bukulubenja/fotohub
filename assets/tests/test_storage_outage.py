"""R2 has been seen returning 5xx during long uploads (plan §3.2); failures must be loud."""

import pytest
from botocore.exceptions import EndpointConnectionError
from celery.exceptions import Retry
from django.urls import reverse

from assets import processing
from assets.models import Photo
from assets.storage import ObjectStorage
from assets.tasks import MAX_STORAGE_RETRIES, delete_objects, process_photo
from assets.tests.factories import PhotoFactory

pytestmark = pytest.mark.django_db

OUTAGE = EndpointConnectionError(endpoint_url="https://r2.example")


def fail(*args, **kwargs):
    raise OUTAGE


def start(client, gallery, size):
    return client.post(
        reverse("upload-list"),
        {
            "gallery": str(gallery.pk),
            "filename": "a.jpg",
            "content_type": "image/jpeg",
            "size": size,
        },
        format="json",
    )


@pytest.mark.usefixtures("small_uploads")
def test_multipart_start_failure_releases_the_reservation(
    auth_client, gallery, storage, monkeypatch
):
    monkeypatch.setattr(ObjectStorage, "create_multipart", fail)

    response = start(auth_client, gallery, size=4096)

    assert response.status_code == 503
    assert not Photo.objects.exists()


def test_complete_during_outage_keeps_the_upload_resumable(
    auth_client, gallery, storage, monkeypatch
):
    photo_id = start(auth_client, gallery, size=10).data["photo"]["id"]
    monkeypatch.setattr(ObjectStorage, "object_size", fail)

    response = auth_client.post(reverse("upload-complete", args=[photo_id]))

    assert response.status_code == 503
    assert Photo.objects.get(pk=photo_id).status == Photo.Status.UPLOADING


@pytest.mark.usefixtures("small_uploads")
def test_abort_during_outage_keeps_the_upload(auth_client, gallery, storage, monkeypatch):
    photo_id = start(auth_client, gallery, size=4096).data["photo"]["id"]
    monkeypatch.setattr(ObjectStorage, "abort_multipart", fail)

    response = auth_client.delete(reverse("upload-detail", args=[photo_id]))

    assert response.status_code == 503
    assert Photo.objects.filter(pk=photo_id).exists()


def test_file_deletion_retries_on_storage_errors(storage, monkeypatch):
    monkeypatch.setattr(ObjectStorage, "delete", fail)

    with pytest.raises(Retry):
        delete_objects.apply(args=[["originals/x"]])


def test_processing_retries_on_storage_errors(storage, monkeypatch):
    photo = PhotoFactory(status=Photo.Status.PROCESSING)
    monkeypatch.setattr(processing, "process_photo", fail)

    with pytest.raises(Retry):
        process_photo.apply(args=[str(photo.pk)], retries=0)

    photo.refresh_from_db()
    assert photo.status == Photo.Status.PROCESSING


def test_processing_marks_the_photo_failed_after_the_last_retry(storage, monkeypatch):
    photo = PhotoFactory(status=Photo.Status.PROCESSING)
    monkeypatch.setattr(processing, "process_photo", fail)

    with pytest.raises(EndpointConnectionError):
        process_photo.apply(args=[str(photo.pk)], retries=MAX_STORAGE_RETRIES, throw=True)

    photo.refresh_from_db()
    assert photo.status == Photo.Status.FAILED
    assert "Storage was unavailable" in photo.failure_reason
