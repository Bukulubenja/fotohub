from celery import shared_task

from delivery import services

ARCHIVE_SOFT_LIMIT_SECONDS = 2 * 60 * 60  # ~20 GB at a datacenter's speed, with margin


@shared_task(acks_late=True, soft_time_limit=ARCHIVE_SOFT_LIMIT_SECONDS)
def build_archive(job_id: str) -> None:
    services.build(job_id)


@shared_task
def expire_archives() -> int:
    return services.expire_archives()
