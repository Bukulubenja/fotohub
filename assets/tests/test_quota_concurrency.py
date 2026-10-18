"""Quota reservation under real concurrency. Needs Postgres: SQLite ignores row locks."""

import threading

import pytest
from django.db import connection, connections

from assets import services
from assets.models import Photo

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql", reason="row locks need Postgres (runs in CI)"
    ),
]

UPLOADERS = 4


def test_parallel_uploads_cannot_jointly_exceed_quota(photographer, gallery, settings):
    settings.UPLOADS = {**settings.UPLOADS, "MULTIPART_THRESHOLD_BYTES": 10**9}
    photographer.profile.storage_quota_bytes = 1000
    photographer.profile.save()
    barrier = threading.Barrier(UPLOADERS)
    outcomes = []

    def upload() -> None:
        try:
            barrier.wait()
            services.start_upload(
                photographer, gallery, filename="a.jpg", content_type="image/jpeg", size_bytes=600
            )
            outcomes.append("ok")
        except services.QuotaExceeded:
            outcomes.append("quota")
        finally:
            connections.close_all()

    threads = [threading.Thread(target=upload) for _ in range(UPLOADERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["ok"] + ["quota"] * (UPLOADERS - 1)
    assert Photo.objects.count() == 1
