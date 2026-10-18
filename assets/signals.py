from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from assets.models import Photo


@receiver(post_delete, sender=Photo)
def delete_stored_objects(sender: type[Photo], instance: Photo, **kwargs) -> None:
    """Remove the original and derivatives however the row went (photo, gallery, account).

    Runs after commit, so a rolled-back delete never loses files. Unfinished multipart
    parts are left to the bucket's abort-incomplete-uploads lifecycle rule.
    """
    from assets.processing import VARIANTS, variant_key
    from assets.tasks import delete_objects

    # Every possible variant, not just the recorded ones: a worker may still be writing them.
    keys = [instance.original_key, *(variant_key(instance, spec) for spec in VARIANTS)]
    transaction.on_commit(lambda: delete_objects.delay(keys), robust=True)
