import datetime
import logging

from botocore.exceptions import BotoCoreError, ClientError
from celery import shared_task
from celery.exceptions import Retry
from django.utils import timezone

from assets import processing, services
from assets.models import Photo
from assets.storage import get_storage

logger = logging.getLogger(__name__)

STORAGE_ERRORS = (BotoCoreError, ClientError)
MAX_STORAGE_RETRIES = 5
# A photo still PROCESSING after this was probably never queued (broker outage) or lost.
STUCK_AFTER = datetime.timedelta(minutes=30)
GIVE_UP_AFTER = datetime.timedelta(hours=24)


@shared_task(
    bind=True,
    max_retries=MAX_STORAGE_RETRIES,
    acks_late=True,
    soft_time_limit=120,  # a 120 MP photo renders in seconds; anything longer is stuck
    time_limit=180,
)
def process_photo(self, photo_id: str) -> None:
    try:
        processing.process_photo(photo_id)
    except STORAGE_ERRORS as exc:
        if self.request.retries >= MAX_STORAGE_RETRIES:
            processing.mark_failed(photo_id, "Storage was unavailable. Retry processing later.")
            raise
        raise self.retry(exc=exc, countdown=2 ** (self.request.retries + 2)) from exc
    except Retry:
        raise
    except Exception:
        logger.exception("processing photo %s failed", photo_id)
        processing.mark_failed(photo_id, "Processing failed unexpectedly. Upload it again.")
        raise


@shared_task(
    autoretry_for=STORAGE_ERRORS,
    retry_backoff=True,
    max_retries=MAX_STORAGE_RETRIES,
    acks_late=True,
)
def delete_objects(keys: list[str]) -> None:
    get_storage().delete(keys)


@shared_task
def expire_stale_uploads() -> int:
    return services.expire_stale_uploads()


@shared_task
def requeue_stuck_photos() -> int:
    """Re-queue photos whose processing was never queued or was lost; fail hopeless ones."""
    now = timezone.now()
    stuck = Photo.objects.filter(status=Photo.Status.PROCESSING)
    stuck.filter(updated_at__lte=now - GIVE_UP_AFTER).update(
        status=Photo.Status.FAILED,
        failure_reason="Processing didn't finish. Upload it again.",
        updated_at=now,
    )
    photo_ids = list(stuck.filter(updated_at__lte=now - STUCK_AFTER).values_list("pk", flat=True))
    for photo_id in photo_ids:
        process_photo.delay(str(photo_id))
    return len(photo_ids)
