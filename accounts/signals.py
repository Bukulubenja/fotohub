from django.db.models.signals import post_save
from django.dispatch import receiver

from accounts.models import PhotographerProfile, User


@receiver(post_save, sender=User)
def ensure_profile(sender: type[User], instance: User, created: bool, **kwargs) -> None:
    """Every user has a profile, including ones made by createsuperuser or the admin."""
    if created:
        PhotographerProfile.objects.get_or_create(
            user=instance, defaults={"display_name": instance.email.split("@")[0]}
        )
