import factory

from accounts.models import User

DEFAULT_PASSWORD = "correct-horse-battery-42"


class UserFactory(factory.django.DjangoModelFactory):
    """The profile is created by the post_save signal; this only names it."""

    class Meta:
        model = User
        skip_postgeneration_save = True

    email = factory.Sequence(lambda n: f"photographer{n}@example.com")
    password = factory.PostGenerationMethodCall("set_password", DEFAULT_PASSWORD)

    @factory.post_generation
    def display_name(obj, create, extracted, **kwargs):  # noqa: N805
        if not create:
            return
        obj.save()
        obj.profile.display_name = extracted or f"Studio {obj.pk}"
        obj.profile.save()
