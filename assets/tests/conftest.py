import uuid

import pytest
from moto.server import ThreadedMotoServer

from assets.storage import get_storage
from galleries.tests.factories import EventFactory, GalleryFactory


@pytest.fixture(scope="session")
def moto_server():
    """A real HTTP S3 endpoint, for code that sends presigned requests over the wire."""
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    server.start()
    host, port = server.get_host_and_port()
    yield f"http://{host}:{port}"
    server.stop()


@pytest.fixture
def http_storage(settings, moto_server):
    """Settings and a fresh bucket on the moto server; each test gets its own bucket."""
    settings.OBJECT_STORAGE = {
        **settings.OBJECT_STORAGE,
        "BUCKET": f"fotohub-http-{uuid.uuid4().hex[:12]}",
        "ENDPOINT_URL": moto_server,
    }
    get_storage.cache_clear()
    store = get_storage()
    store.client.create_bucket(Bucket=store.bucket)
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
