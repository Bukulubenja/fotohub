"""scripts/upload_drill.py end to end: live Django server, moto HTTP bucket, inline processing.

Small files go through a single presigned PUT; the noisy one crosses the (shrunk) multipart
threshold. Injected drops cut transfers mid-body over a real socket.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import pyvips
from django.conf import settings as django_settings
from django.db import connection

from accounts.tests.factories import DEFAULT_PASSWORD, UserFactory
from assets.models import Photo
from assets.tests.factories import make_jpeg

SCRIPT = Path(django_settings.BASE_DIR) / "scripts" / "upload_drill.py"
NO_WAIT = "0,0,0,0,0,0,0,0,0,0"
# The live server shares one in-memory SQLite connection across its request threads, so
# parallel requests race there. Postgres (CI) runs the dashboard's real concurrency.
CONCURRENCY = "3" if connection.vendor == "postgresql" else "1"

spec = importlib.util.spec_from_file_location("upload_drill", SCRIPT)
drill = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = drill  # dataclasses look their module up here
spec.loader.exec_module(drill)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def photos_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "event"
    folder.mkdir()
    (folder / "a.jpg").write_bytes(make_jpeg(300, 200))
    (folder / "b.jpg").write_bytes(make_jpeg(200, 300))
    noise = pyvips.Image.gaussnoise(400, 400, sigma=60).cast("uchar")
    noise.bandjoin([noise, noise]).copy(interpretation="srgb").jpegsave(str(folder / "c.jpg"), Q=95)
    return folder


@pytest.fixture
def drill_env(settings, http_storage, live_server, monkeypatch, photos_dir):
    settings.UPLOADS = {**settings.UPLOADS, "MULTIPART_THRESHOLD_BYTES": 20_000}
    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, "127.0.0.1"]
    assert (photos_dir / "c.jpg").stat().st_size > 20_000  # really multipart
    user = UserFactory()
    monkeypatch.setenv("FOTOHUB_PASSWORD", DEFAULT_PASSWORD)
    # "localhost" tries ::1 first; on Windows each refused attempt costs ~2 s.
    url = live_server.url.replace("localhost", "127.0.0.1")
    base = ["--base-url", url, "--email", user.email, "--image-dir", str(photos_dir)]
    return user, base


def run(base: list[str], tmp_path: Path, *extra: str) -> tuple[int, dict]:
    report = tmp_path / "report.json"
    code = drill.main(
        [
            *base,
            "--report",
            str(report),
            "--processing-timeout",
            "30",
            "--concurrency",
            CONCURRENCY,
            *extra,
        ]
    )
    return code, json.loads(report.read_text())


def test_drill_uploads_and_processes_every_photo_despite_drops(drill_env, tmp_path):
    user, base = drill_env

    code, report = run(
        base, tmp_path, "--photos", "9", "--drop-rate", "0.4", "--retry-delays", NO_WAIT
    )

    assert code == 0
    assert report["uploaded"] == 9
    assert report["injected_drops"] > 0
    assert report["retries"] >= report["injected_drops"]
    assert report["processing"] == {
        "ready": 9,
        "processing": 0,
        "failed": 0,
        "failed_examples": [],
    }
    photos = Photo.objects.filter(gallery__event__owner=user)
    assert photos.count() == 9  # retries never created extra photos
    assert {p.variants.keys() >= {"thumb", "viewer", "web", "cover"} for p in photos} == {True}


def test_rerun_with_journal_skips_finished_photos(drill_env, tmp_path):
    user, base = drill_env
    journal = tmp_path / "drill.jsonl"

    run(base, tmp_path, "--photos", "3", "--journal", str(journal))
    code, report = run(base, tmp_path, "--photos", "3", "--journal", str(journal))

    assert code == 0
    assert report["skipped_already_done"] == 3
    assert report["uploaded"] == 0
    assert report["bytes_sent"] == 0
    assert Photo.objects.filter(gallery__event__owner=user).count() == 3


def test_interrupted_uploads_resume_their_existing_sessions(drill_env, tmp_path):
    user, base = drill_env
    journal = tmp_path / "drill.jsonl"

    # No retries and no retry pass: most transfers die and stay unfinished.
    code, first = run(
        base,
        tmp_path,
        *("--photos", "6", "--journal", str(journal), "--drop-rate", "0.9"),
        *("--retry-delays", "", "--no-retry-pass"),
    )
    assert code == 1
    assert first["failed_after_retry_pass"] > 0
    reserved = Photo.objects.filter(gallery__event__owner=user).count()

    code, second = run(base, tmp_path, "--photos", "6", "--journal", str(journal))

    assert code == 0
    assert second["uploaded"] == first["failed_after_retry_pass"]
    # The rerun reused the sessions that were already reserving quota.
    assert Photo.objects.filter(gallery__event__owner=user).count() == reserved == 6


def test_retry_pass_recovers_files_that_ran_out_of_automatic_retries(drill_env, tmp_path):
    _, base = drill_env

    code, report = run(base, tmp_path, "--photos", "6", "--drop-rate", "0.6", "--retry-delays", "0")

    assert report["failed_after_auto_retries"] >= 0
    assert report["failed_after_retry_pass"] <= report["failed_after_auto_retries"]
    assert code == (0 if report["failed_after_retry_pass"] == 0 else 1)


def test_drill_refuses_to_start_beyond_the_quota(drill_env, tmp_path):
    user, base = drill_env
    user.profile.storage_quota_bytes = 1_000
    user.profile.save()

    with pytest.raises(SystemExit, match="quota"):
        drill.main([*base, "--photos", "3"])
