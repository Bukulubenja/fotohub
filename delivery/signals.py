from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from delivery.models import DownloadJob


@receiver(post_delete, sender=DownloadJob)
def delete_archive(sender: type[DownloadJob], instance: DownloadJob, **kwargs) -> None:
    """Remove the ZIP however the row went (job, gallery, account), after commit."""
    from assets.tasks import delete_objects

    key = instance.archive_key
    transaction.on_commit(lambda: delete_objects.delay([key]), robust=True)
