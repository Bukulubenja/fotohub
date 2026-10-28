import datetime

import factory

from accounts.tests.factories import UserFactory
from galleries import collections
from galleries.models import Collection, Event, Gallery


class CollectionFactory(factory.django.DjangoModelFactory):
    """Passkeys come from the real generator, so lookups behave as in production."""

    class Meta:
        model = Collection

    owner = factory.SubFactory(UserFactory)
    name = factory.Sequence(lambda n: f"School {n}")

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        return collections.create_collection(kwargs["owner"], kwargs["name"], prefix="SCH")


class EventFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Event

    owner = factory.SubFactory(UserFactory)
    title = factory.Sequence(lambda n: f"Event {n}")
    event_date = datetime.date(2026, 9, 12)


class GalleryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Gallery

    event = factory.SubFactory(EventFactory)
    title = factory.LazyAttribute(lambda o: o.event.title)
