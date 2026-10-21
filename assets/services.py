"""Upload lifecycle: reserve quota, sign, complete, discard, expire. Views call these.

Quota is reserved when an upload starts, under a row lock on the photographer's profile,
so parallel uploads can't jointly overshoot the plan (spec FR-27). A reservation is simply
an active, unexpired UploadSession; aborting or expiring it releases the space.
"""

import datetime
import logging
import uuid
from dataclasses import dataclass

from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from accounts.models import PhotographerProfile
from assets.models import Photo, UploadSession
from assets.storage import StorageError, get_storage
from billing import services as billing
from galleries.models import Gallery

logger = logging.getLogger(__name__)

UPLOADABLE_GALLERY_STATUSES = {
    Gallery.Status.DRAFT,
    Gallery.Status.PROCESSING,
    Gallery.Status.PUBLISHED,
}
EXPIRE_BATCH_SIZE = 200
# Multipart errors caused by what the client sent; retrying the same parts can't succeed.
CLIENT_PART_ERRORS = {"EntityTooSmall", "InvalidPart", "InvalidPartOrder"}


class UploadError(Exception):
    """The request can't be honoured in the upload's current state."""


class SubscriptionInactive(Exception):
    pass


class QuotaExceeded(UploadError):
    pass


class StorageUnavailable(Exception):
    """Object storage failed; the client should retry later."""


@dataclass(frozen=True)
class StorageUsage:
    used_bytes: int
    quota_bytes: int

    @property
    def percent(self) -> int:
        if not self.quota_bytes:
            return 100
        return min(100, self.used_bytes * 100 // self.quota_bytes)

    @property
    def warning_level(self) -> int | None:
        """Highest threshold crossed (75/90/100, spec FR-28), or None."""
        crossed = [t for t in settings.STORAGE_WARNING_THRESHOLDS if self.percent >= t]
        return max(crossed, default=None)

    def can_fit(self, size_bytes: int) -> bool:
        return self.used_bytes + size_bytes <= self.quota_bytes


def storage_usage(user, quota_bytes: int | None = None) -> StorageUsage:
    used = (
        Photo.objects.owned_by(user)
        .counting_toward_quota()
        .aggregate(total=Sum("size_bytes"))["total"]
    )
    if quota_bytes is None:
        quota_bytes = user.profile.storage_quota_bytes
    return StorageUsage(used_bytes=used or 0, quota_bytes=quota_bytes)


def _live_uploads(user):
    return Photo.objects.owned_by(user).filter(
        status=Photo.Status.UPLOADING,
        upload_session__state=UploadSession.State.ACTIVE,
        upload_session__expires_at__gt=timezone.now(),
    )


def _original_key(gallery: Gallery, photo_id: uuid.UUID) -> str:
    return f"originals/{gallery.event.owner_id}/{gallery.pk}/{photo_id}"


def start_upload(
    user, gallery: Gallery, *, filename: str, content_type: str, size_bytes: int
) -> Photo:
    if gallery.status not in UPLOADABLE_GALLERY_STATUSES:
        raise UploadError(
            f"You can't add photos to a {gallery.get_status_display().lower()} gallery."
        )
    multipart = size_bytes >= settings.UPLOADS["MULTIPART_THRESHOLD_BYTES"]
    photo_id = uuid.uuid4()

    with transaction.atomic():
        profile = PhotographerProfile.objects.select_for_update().get(user=user)
        if not billing.can_upload(user):
            raise SubscriptionInactive(
                "Your plan has ended, so uploads are paused. Renew to add photos; "
                "your galleries stay online meanwhile."
            )
        if not storage_usage(user, profile.storage_quota_bytes).can_fit(size_bytes):
            raise QuotaExceeded(
                "This upload would go over your storage limit. Free up space or upgrade your plan."
            )
        # Byte quota alone doesn't stop thousands of tiny sessions piling up.
        if _live_uploads(user).count() >= settings.UPLOADS["MAX_ACTIVE_UPLOADS"]:
            raise UploadError("Too many uploads in progress. Let some finish, then try again.")
        photo = Photo.objects.create(
            id=photo_id,
            gallery=gallery,
            original_filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
            original_key=_original_key(gallery, photo_id),
        )
        session = UploadSession.objects.create(
            photo=photo,
            part_size=settings.UPLOADS["PART_SIZE_BYTES"] if multipart else None,
            expires_at=timezone.now()
            + datetime.timedelta(seconds=settings.UPLOADS["SESSION_TTL_SECONDS"]),
        )

    if multipart:
        try:
            session.upload_id = get_storage().create_multipart(photo.original_key, content_type)
        except (BotoCoreError, ClientError) as exc:
            logger.exception("create_multipart failed for photo %s", photo.pk)
            photo.delete()  # release the reservation
            raise StorageUnavailable() from exc
        session.save(update_fields=["upload_id"])
    return photo


def _active_session(photo: Photo) -> UploadSession:
    session = photo.upload_session
    if session.state != UploadSession.State.ACTIVE or photo.status != Photo.Status.UPLOADING:
        raise UploadError("This upload has already finished.")
    if session.is_expired():
        raise UploadError("This upload expired. Add the photo again.")
    return session


def upload_target(photo: Photo) -> dict:
    """Where the browser sends bytes. Single PUT URLs are re-signed on every call."""
    session = _active_session(photo)
    if session.is_multipart:
        return {
            "type": "multipart",
            "part_size": session.part_size,
            "part_count": session.part_count,
            "expires_at": session.expires_at,
        }
    return {
        "type": "single",
        "method": "PUT",
        # Content-Length is signed, so the bucket refuses any size other than the reserved one.
        "url": get_storage().presign_put(photo.original_key, photo.content_type, photo.size_bytes),
        "headers": {"Content-Type": photo.content_type},
        "expires_at": session.expires_at,
    }


def sign_parts(photo: Photo, part_numbers: list[int]) -> dict[int, str]:
    session = _active_session(photo)
    if not session.is_multipart:
        raise UploadError("This upload doesn't use parts.")
    out_of_range = [n for n in part_numbers if not 1 <= n <= session.part_count]
    if out_of_range:
        raise UploadError(f"Part numbers must be between 1 and {session.part_count}.")
    storage = get_storage()
    return {
        n: storage.presign_part(photo.original_key, session.upload_id, n)
        for n in sorted(set(part_numbers))
    }


def uploaded_parts(photo: Photo) -> list[dict]:
    """Parts the bucket already holds, so a resumed upload skips them."""
    session = _active_session(photo)
    if not session.is_multipart:
        return []
    try:
        return get_storage().list_parts(photo.original_key, session.upload_id)
    except (BotoCoreError, ClientError) as exc:
        raise StorageUnavailable() from exc


def _assemble(storage, photo: Photo, session: UploadSession) -> None:
    try:
        storage.complete_multipart(photo.original_key, session.upload_id)
    except StorageError as exc:
        raise UploadError(str(exc)) from exc
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in CLIENT_PART_ERRORS:
            raise UploadError(
                "Some parts are missing or the wrong size. Upload the file again."
            ) from exc
        # An earlier complete may have succeeded before we recorded it; the object decides.
        if code != "NoSuchUpload":
            raise


def complete_upload(photo: Photo) -> Photo:
    """Idempotent: completing twice returns the same photo and queues processing once."""
    from assets.tasks import process_photo

    storage = get_storage()
    size_mismatch = False
    try:
        with transaction.atomic():
            session = UploadSession.objects.select_for_update().get(photo=photo)
            if session.state == UploadSession.State.COMPLETED:
                photo.refresh_from_db()
                return photo
            if session.is_expired():
                raise UploadError("This upload expired. Add the photo again.")
            if session.is_multipart:
                _assemble(storage, photo, session)

            stored_size = storage.object_size(photo.original_key)
            if stored_size is None:
                raise UploadError("The file hasn't finished uploading.")
            if stored_size != photo.size_bytes:
                # Quota was reserved for the declared size; anything else bypasses it.
                photo.delete()
                size_mismatch = True
            else:
                now = timezone.now()
                session.state = UploadSession.State.COMPLETED
                session.completed_at = now
                session.save(update_fields=["state", "completed_at"])
                photo.status = Photo.Status.PROCESSING
                photo.save(update_fields=["status", "updated_at"])
                # robust: a broker outage mustn't fail a committed upload; the stuck-photo
                # sweep re-queues it.
                transaction.on_commit(lambda: process_photo.delay(str(photo.pk)), robust=True)
    except (BotoCoreError, ClientError) as exc:
        logger.exception("complete_upload failed for photo %s", photo.pk)
        raise StorageUnavailable() from exc

    if size_mismatch:
        raise UploadError("The uploaded file doesn't match the size you declared. Upload it again.")
    return photo


def abort_upload(photo: Photo, *, expired_by: datetime.datetime | None = None) -> None:
    """Cancel an unfinished upload. Locks the session so it can't race a `complete`.

    With `expired_by`, only aborts if the session had expired by then (the sweep's check).
    """
    with transaction.atomic():
        session = UploadSession.objects.select_for_update().filter(photo=photo).first()
        if session is None or session.state != UploadSession.State.ACTIVE:
            raise UploadError("This upload has finished. Delete the photo instead.")
        if expired_by is not None and not session.is_expired(expired_by):
            raise UploadError("This upload is still active.")
        if session.is_multipart:
            try:
                get_storage().abort_multipart(photo.original_key, session.upload_id)
            except (BotoCoreError, ClientError) as exc:
                logger.exception("abort failed for photo %s", photo.pk)
                raise StorageUnavailable() from exc
        photo.delete()  # files go via the post_delete signal


def delete_photo(photo: Photo) -> None:
    if photo.status == Photo.Status.UPLOADING:
        abort_upload(photo)
    else:
        photo.delete()


def expire_stale_uploads(now: datetime.datetime | None = None) -> int:
    """Clean up uploads abandoned past their TTL. Returns how many were removed."""
    now = now or timezone.now()
    stale = Photo.objects.filter(
        status=Photo.Status.UPLOADING,
        upload_session__state=UploadSession.State.ACTIVE,
        upload_session__expires_at__lte=now,
    )[:EXPIRE_BATCH_SIZE]
    removed = 0
    for photo in stale:
        try:
            abort_upload(photo, expired_by=now)
        except (UploadError, StorageUnavailable):
            continue  # completed meanwhile, or storage is down and the next run retries
        removed += 1
    return removed
