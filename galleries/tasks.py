from celery import shared_task

from galleries import updates


@shared_task
def send_collection_updates() -> int:
    return updates.send_updates()
