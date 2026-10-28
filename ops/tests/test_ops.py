"""Health check, dashboard serving, and database backups (round trip on Postgres in CI)."""

import datetime
import shutil
from io import StringIO

import pytest
from django.core.files.storage import FileSystemStorage
from django.core.management import CommandError, call_command
from django.db import connection
from django.test import Client

from galleries.models import Event
from galleries.tests.factories import EventFactory
from ops import backups, views

pytestmark = pytest.mark.django_db


class TestHealth:
    def test_healthy(self, client: Client):
        response = client.get("/healthz/")

        assert response.status_code == 200
        assert response.json() == {"database": "ok", "cache": "ok"}
        assert "no-cache" in response["Cache-Control"]

    def test_cache_failure_is_unhealthy(self, client: Client, monkeypatch):
        def broken(*args, **kwargs):
            raise ConnectionError("redis down")

        monkeypatch.setattr(views.cache, "set", broken)

        response = client.get("/healthz/")

        assert response.status_code == 503
        assert response.json()["cache"] == "error"


class TestDashboard:
    def test_serves_the_built_dashboard_under_app(self, client: Client, tmp_path, monkeypatch):
        (tmp_path / "dashboard").mkdir()
        (tmp_path / "dashboard" / "index.html").write_text("<div id=root></div>")
        monkeypatch.setattr(views, "staticfiles_storage", FileSystemStorage(location=tmp_path))

        response = client.get("/app/")

        assert response.status_code == 200
        assert b"<div id=root></div>" in response.content
        assert "no-cache" in response["Cache-Control"]

    def test_explains_when_not_built(self, client: Client, tmp_path, monkeypatch):
        monkeypatch.setattr(views, "staticfiles_storage", FileSystemStorage(location=tmp_path))

        assert client.get("/app/").status_code == 404


class TestBackupBookkeeping:
    def test_keys_sort_by_time(self):
        early = backups.backup_key(datetime.datetime(2026, 10, 1, 0, 30, tzinfo=datetime.UTC))
        late = backups.backup_key(datetime.datetime(2026, 10, 2, 0, 30, tzinfo=datetime.UTC))

        assert early == "backups/db/2026/10/01/fotohub-20261001T003000Z.dump"
        assert sorted([late, early]) == [early, late]

    def test_prune_keeps_the_newest_even_when_old(self, storage, settings):
        settings.BACKUPS = {"RETENTION_DAYS": 30}
        for day in (1, 2, 3):
            key = backups.backup_key(datetime.datetime(2026, 1, day, tzinfo=datetime.UTC))
            storage.put_bytes(key, b"dump", "application/octet-stream")

        deleted = backups.prune(now=datetime.datetime(2027, 1, 1, tzinfo=datetime.UTC))

        assert deleted == 2
        assert [b.key for b in backups.list_backups()] == [
            "backups/db/2026/01/03/fotohub-20260103T000000Z.dump"
        ]

    def test_restore_refuses_without_confirmation(self):
        with pytest.raises(CommandError, match="--yes-replace-everything"):
            call_command("restore_db", stdout=StringIO())

    def test_sqlite_is_refused_clearly(self, storage):
        if connection.vendor == "postgresql":
            pytest.skip("covered by the round trip")
        with pytest.raises(backups.BackupError, match="PostgreSQL"):
            backups.take_backup()


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(shutil.which("pg_dump") is None, reason="needs pg_dump/pg_restore")
def test_backup_restore_round_trip(storage):
    """The restore is tested, not assumed (launch plan phase 4 gate: "restore tested")."""
    if connection.vendor != "postgresql":
        pytest.skip("backups are Postgres-only; CI runs this against Postgres 17")
    kept = EventFactory(title="Before the backup")

    out = StringIO()
    call_command("backup_db", stdout=out)
    Event.objects.filter(pk=kept.pk).delete()
    EventFactory(title="After the backup")
    connection.close()  # pg_restore needs the tables free
    call_command("restore_db", "--yes-replace-everything", stdout=StringIO())

    titles = set(Event.objects.values_list("title", flat=True))
    assert "Before the backup" in titles
    assert "After the backup" not in titles
    assert "Backed up to backups/db/" in out.getvalue()
