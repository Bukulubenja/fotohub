from celery import shared_task

from billing import reminders


@shared_task
def send_reminders() -> int:
    return reminders.send_reminders()
