from celery import shared_task

from ops import backups


@shared_task(acks_late=True, soft_time_limit=60 * 60)
def backup_database() -> str:
    backup = backups.take_backup()
    backups.prune()
    return backup.key
