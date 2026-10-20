"""Download-all archives: find or create the shared job, build it, expire it.

Views and tasks call these; only this module changes a job's status.
"""

import datetime
import hashlib
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from assets.gallery_media import download_source, ready_photos
from assets.models import Photo
from assets.storage import get_storage
from delivery.archive import Entry, unique_names, write_archive
from delivery.models import DownloadJob
from galleries.models import Gallery

logger = logging.getLogger(__name__)

LIVE = (DownloadJob.Status.QUEUED, DownloadJob.Status.BUILDING, DownloadJob.Status.READY)


class ArchiveUnavailable(Exception):
    """The visitor can't have a ZIP right now; the message is shown to them."""


def archive_entries(photos: list[Photo], policy: str) -> list[Entry]:
    sources = [s for s in (download_source(p, policy) for p in photos) if s is not None]
    names = unique_names([s.filename for s in sources])
    return [Entry(s.key, name, s.size) for s, name in zip(sources, names, strict=True)]


def fingerprint(photos: list[Photo], policy: str) -> str:
    digest = hashlib.sha256(policy.encode())
    for photo in photos:
        digest.update(f"{photo.pk}:{photo.updated_at.isoformat()};".encode())
    return digest.hexdigest()


def estimate_bytes(gallery: Gallery) -> int:
    """Size of "Download all", shown on the button before anyone asks for it."""
    photos = list(ready_photos(gallery))
    return sum(entry.size for entry in archive_entries(photos, gallery.download_policy))


def request_archive(gallery: Gallery, now: datetime.datetime | None = None) -> DownloadJob:
    """The live job for this gallery's current photos, creating (and queuing) one if needed."""
    from delivery.tasks import build_archive

    now = now or timezone.now()
    if gallery.download_policy == Gallery.DownloadPolicy.DISABLED:
        raise ArchiveUnavailable("Downloads are turned off for this gallery.")
    photos = list(ready_photos(gallery))
    entries = archive_entries(photos, gallery.download_policy)
    if not entries:
        raise ArchiveUnavailable("There are no photos to download yet.")
    expected = sum(entry.size for entry in entries)
    if expected > settings.DELIVERY["MAX_ARCHIVE_BYTES"]:
        raise ArchiveUnavailable(
            "This gallery is too large for one download. Download photos one at a time, "
            "or ask your photographer to split it into albums."
        )
    photo_set = fingerprint(photos, gallery.download_policy)
    with transaction.atomic():
        Gallery.objects.select_for_update().get(pk=gallery.pk)  # one creator at a time
        existing = (
            DownloadJob.objects.filter(
                gallery=gallery,
                policy=gallery.download_policy,
                fingerprint=photo_set,
                status__in=LIVE,
            )
            .exclude(expires_at__lte=now)
            .first()
        )
        if existing:
            return existing
        job = DownloadJob.objects.create(
            gallery=gallery,
            policy=gallery.download_policy,
            fingerprint=photo_set,
            photo_count=len(entries),
            expected_bytes=expected,
        )
        transaction.on_commit(lambda: build_archive.delay(str(job.pk)), robust=True)
    return job


def build(job_id: str) -> None:
    """Called by the worker. Re-reads the photos so the ZIP matches the gallery right now."""
    with transaction.atomic():
        job = DownloadJob.objects.select_for_update().select_related("gallery").get(pk=job_id)
        if job.status != DownloadJob.Status.QUEUED:
            return  # already built, building elsewhere, or failed: don't double-build
        job.status = DownloadJob.Status.BUILDING
        job.started_at = timezone.now()
        job.save(update_fields=["status", "started_at"])

    photos = list(ready_photos(job.gallery))
    entries = archive_entries(photos, job.policy)
    try:
        size = write_archive(
            get_storage(),
            job.archive_key,
            entries,
            settings.UPLOADS["PART_SIZE_BYTES"],
            timezone.localtime(),
        )
    except Exception:
        logger.exception("building archive %s failed", job_id)
        fail(job, "We couldn't prepare the download. Try again in a few minutes.")
        raise
    finished = timezone.now()
    job.status = DownloadJob.Status.READY
    job.size_bytes = size
    job.photo_count = len(entries)
    job.finished_at = finished
    job.expires_at = finished + datetime.timedelta(seconds=settings.DELIVERY["ARCHIVE_TTL_SECONDS"])
    job.save(update_fields=["status", "size_bytes", "photo_count", "finished_at", "expires_at"])


def fail(job: DownloadJob, reason: str) -> None:
    job.status = DownloadJob.Status.FAILED
    job.failure_reason = reason
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "failure_reason", "finished_at"])


def expire_archives(now: datetime.datetime | None = None) -> int:
    """Delete archives past their expiry; fail jobs a lost worker left building."""
    now = now or timezone.now()
    stuck_before = now - datetime.timedelta(seconds=settings.DELIVERY["STUCK_AFTER_SECONDS"])
    DownloadJob.objects.filter(
        status__in=(DownloadJob.Status.QUEUED, DownloadJob.Status.BUILDING),
        created_at__lte=stuck_before,
    ).update(
        status=DownloadJob.Status.FAILED,
        failure_reason="The download took too long to prepare. Try again.",
        finished_at=now,
    )
    expired = list(DownloadJob.objects.filter(status=DownloadJob.Status.READY, expires_at__lte=now))
    if expired:
        get_storage().delete([job.archive_key for job in expired])
        DownloadJob.objects.filter(pk__in=[job.pk for job in expired]).update(
            status=DownloadJob.Status.EXPIRED
        )
    return len(expired)
