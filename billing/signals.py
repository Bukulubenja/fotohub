from django.db.models.signals import post_save
from django.dispatch import receiver

from accounts.models import User
from billing.services import start_trial


@receiver(post_save, sender=User)
def start_trial_for_new_user(sender: type[User], instance: User, created: bool, **kwargs) -> None:
    """Every new photographer starts a trial. Runs after accounts' profile signal."""
    if created:
        start_trial(instance)
