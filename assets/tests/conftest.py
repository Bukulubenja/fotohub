import pytest
from moto import mock_aws

from assets.storage import get_storage
from galleries.tests.factories import EventFactory, GalleryFactory


@pytest.fixture
def storage(settings):
    """An in-memory S3 bucket. The client is cached, so rebuild it inside the mock."""
    with mock_aws():
        get_storage.cache_clear()
        store = get_storage()
        store.client.create_bucket(Bucket=settings.OBJECT_STORAGE["BUCKET"])
        yield store
    get_storage.cache_clear()


@pytest.fixture
def small_uploads(settings):
    """Shrink the multipart threshold so tests can exercise parts with tiny files."""
    settings.UPLOADS = {
        **settings.UPLOADS,
        "MULTIPART_THRESHOLD_BYTES": 1024,
        "PART_SIZE_BYTES": 5 * 1024 * 1024,
    }


@pytest.fixture
def gallery(photographer):
    return GalleryFactory(event=EventFactory(owner=photographer))
