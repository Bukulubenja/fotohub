import datetime

import factory

from accounts.tests.factories import UserFactory
from galleries.models import Event, Gallery


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
