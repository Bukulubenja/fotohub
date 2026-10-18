"""Regression tests for the races and abuse paths found in the Phase 2 review."""

import datetime
from urllib.parse import parse_qs, urlparse

import pytest
from botocore.exceptions import ClientError
from django.urls import reverse
from django.utils import timezone
from kombu.exceptions import OperationalError

from assets import processing, services, tasks
from assets.models import Photo, UploadSession
from assets.storage import ObjectStorage
from assets.tests.factories import PhotoFactory, UploadSessionFactory, make_jpeg

pytestmark = pytest.mark.django_db


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


def test_presigned_put_pins_the_reserved_size(auth_client, gallery, storage):
    url = start(auth_client, gallery, size=1234).data["upload"]["url"]

    signed = parse_qs(urlparse(url).query)["X-Amz-SignedHeaders"][0].split(";")

    assert "content-length" in signed


def test_caps_active_uploads_per_photographer(auth_client, gallery, storage, settings):
    settings.UPLOADS = {**settings.UPLOADS, "MAX_ACTIVE_UPLOADS": 2}
    start(auth_client, gallery, size=1)
    start(auth_client, gallery, size=1)

    response = start(auth_client, gallery, size=1)

    assert response.status_code == 409
    assert "Too many uploads" in response.data["detail"]


@pytest.mark.usefixtures("small_uploads")
def test_bad_parts_are_a_client_error_not_an_outage(auth_client, gallery, storage, monkeypatch):
    photo_id = start(auth_client, gallery, size=4096).data["photo"]["id"]

    def too_small(*args, **kwargs):
        raise ClientError({"Error": {"Code": "EntityTooSmall"}}, "CompleteMultipartUpload")

    monkeypatch.setattr(ObjectStorage, "complete_multipart", too_small)

    response = auth_client.post(reverse("upload-complete", args=[photo_id]))

    assert response.status_code == 409


def test_broker_outage_does_not_fail_a_committed_upload(
    auth_client, gallery, storage, monkeypatch, django_capture_on_commit_callbacks
):
    data = make_jpeg()
    photo_id = start(auth_client, gallery, size=len(data)).data["photo"]["id"]
    photo = Photo.objects.get(pk=photo_id)
    storage.client.put_object(Bucket=storage.bucket, Key=photo.original_key, Body=data)

    def broker_down(*args, **kwargs):
        raise OperationalError("redis unreachable")

    monkeypatch.setattr(tasks.process_photo, "delay", broker_down)

    with django_capture_on_commit_callbacks(execute=True):
        response = auth_client.post(reverse("upload-complete", args=[photo_id]))

    assert response.status_code == 200
    assert Photo.objects.get(pk=photo_id).status == Photo.Status.PROCESSING


def test_abort_loses_the_race_against_a_completed_upload(gallery, storage):
    session = UploadSessionFactory(photo__gallery=gallery)
    stale_photo = Photo.objects.get(pk=session.photo_id)  # still reads as UPLOADING
    UploadSession.objects.filter(pk=session.pk).update(state=UploadSession.State.COMPLETED)
    Photo.objects.filter(pk=stale_photo.pk).update(status=Photo.Status.PROCESSING)

    with pytest.raises(services.UploadError):
        services.abort_upload(stale_photo)

    assert Photo.objects.filter(pk=stale_photo.pk).exists()


def test_sweep_spares_a_session_extended_after_it_was_listed(gallery, storage):
    now = timezone.now()
    session = UploadSessionFactory(photo__gallery=gallery, expires_at=now)
    UploadSession.objects.filter(pk=session.pk).update(expires_at=now + datetime.timedelta(hours=1))

    with pytest.raises(services.UploadError):
        services.abort_upload(session.photo, expired_by=now - datetime.timedelta(seconds=1))

    assert Photo.objects.filter(pk=session.photo_id).exists()


def test_worker_rejects_a_file_swapped_after_complete(storage):
    data = make_jpeg()
    photo = PhotoFactory(status=Photo.Status.PROCESSING, size_bytes=len(data) - 1)
    storage.client.put_object(Bucket=storage.bucket, Key=photo.original_key, Body=data)

    processing.process_photo(str(photo.pk))

    photo.refresh_from_db()
    assert photo.status == Photo.Status.FAILED
    assert "changed after upload" in photo.failure_reason


def test_unexpected_processing_errors_mark_the_photo_failed(storage, monkeypatch):
    photo = PhotoFactory(status=Photo.Status.PROCESSING)

    def boom(photo_id):
        raise ValueError("unexpected")

    monkeypatch.setattr(processing, "process_photo", boom)

    with pytest.raises(ValueError):
        tasks.process_photo.apply(args=[str(photo.pk)])

    photo.refresh_from_db()
    assert photo.status == Photo.Status.FAILED


def test_requeues_stuck_photos_and_fails_hopeless_ones(storage, monkeypatch):
    now = timezone.now()
    fresh = PhotoFactory(status=Photo.Status.PROCESSING)
    stuck = PhotoFactory(status=Photo.Status.PROCESSING)
    hopeless = PhotoFactory(status=Photo.Status.PROCESSING)
    Photo.objects.filter(pk=stuck.pk).update(updated_at=now - datetime.timedelta(hours=1))
    Photo.objects.filter(pk=hopeless.pk).update(updated_at=now - datetime.timedelta(days=2))
    queued = []
    monkeypatch.setattr(tasks.process_photo, "delay", queued.append)

    assert tasks.requeue_stuck_photos() == 1

    assert queued == [str(stuck.pk)]
    hopeless.refresh_from_db()
    fresh.refresh_from_db()
    assert hopeless.status == Photo.Status.FAILED
    assert fresh.status == Photo.Status.PROCESSING
