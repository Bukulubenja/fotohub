import pytest
from django.core.cache import cache
from rest_framework.test import APIClient
from rest_framework.throttling import SimpleRateThrottle

from accounts.tests.factories import UserFactory


@pytest.fixture
def strict_throttle(monkeypatch):
    """2 requests/min per client, 3/min per gallery. DRF caches rates on the class."""
    monkeypatch.setattr(
        SimpleRateThrottle,
        "THROTTLE_RATES",
        {
            "auth": "2/min",
            "gallery_unlock": "2/min",
            "gallery_unlock_global": "3/min",
            "uploads": "2/min",
        },
    )
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def photographer():
    return UserFactory()


@pytest.fixture
def other_photographer():
    return UserFactory()


@pytest.fixture
def auth_client(photographer) -> APIClient:
    client = APIClient()
    client.force_authenticate(photographer)
    return client


@pytest.fixture
def other_client(other_photographer) -> APIClient:
    client = APIClient()
    client.force_authenticate(other_photographer)
    return client
