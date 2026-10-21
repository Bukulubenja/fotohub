import pytest
from django.core.cache import cache
from moto import mock_aws
from rest_framework.test import APIClient
from rest_framework.throttling import SimpleRateThrottle

from accounts.tests.factories import UserFactory
from assets.storage import get_storage


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
            "archive": "2/min",
            "payments": "2/min",
            "checkout": "2/min",
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


@pytest.fixture
def storage(settings):
    """An in-memory S3 bucket. The client is cached, so rebuild it inside the mock."""
    with mock_aws():
        get_storage.cache_clear()
        store = get_storage()
        store.client.create_bucket(Bucket=settings.OBJECT_STORAGE["BUCKET"])
        yield store
    get_storage.cache_clear()
