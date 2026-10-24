"""Nightly Postgres backups to object storage, and restore (launch plan, phase 4).

pg_dump's custom format, uploaded to `backups/db/`, kept for BACKUPS["RETENTION_DAYS"].
Restoring is a deliberate, manual step (`manage.py restore_db`), never automatic.
"""

import datetime
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.utils import timezone

from assets.storage import get_storage

logger = logging.getLogger(__name__)

PREFIX = "backups/db/"


class BackupError(Exception):
    pass


@dataclass(frozen=True)
class Backup:
    key: str
    size: int
    taken_at: datetime.datetime


def _pg_env() -> tuple[list[str], dict[str, str]]:
    """Connection flags and environment for pg_dump/pg_restore, from Django's settings."""
    if connection.vendor != "postgresql":
        raise BackupError("Backups need PostgreSQL; this database is " + connection.vendor)
    db = connection.settings_dict
    args = ["--host", db["HOST"] or "localhost", "--port", str(db["PORT"] or 5432)]
    args += ["--username", db["USER"], "--dbname", db["NAME"]]
    env = {**os.environ, "PGPASSWORD": db["PASSWORD"] or ""}
    return args, env


def _run(command: list[str], env: dict[str, str]) -> None:
    result = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise BackupError(f"{command[0]} failed: {result.stderr.strip()[-500:]}")


def backup_key(now: datetime.datetime) -> str:
    return f"{PREFIX}{now:%Y/%m/%d}/fotohub-{now:%Y%m%dT%H%M%SZ}.dump"


def take_backup(now: datetime.datetime | None = None) -> Backup:
    now = (now or timezone.now()).astimezone(datetime.UTC)
    args, env = _pg_env()
    store = get_storage()
    key = backup_key(now)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "fotohub.dump"
        _run(["pg_dump", "--format=custom", "--no-owner", "--file", str(path), *args], env)
        size = path.stat().st_size
        store.client.upload_file(str(path), store.bucket, key)
    logger.info("database backup %s (%s bytes)", key, size)
    return Backup(key, size, now)


def list_backups() -> list[Backup]:
    """Newest first."""
    store = get_storage()
    found = []
    for page in store.client.get_paginator("list_objects_v2").paginate(
        Bucket=store.bucket, Prefix=PREFIX
    ):
        found += [Backup(o["Key"], o["Size"], o["LastModified"]) for o in page.get("Contents", [])]
    return sorted(found, key=lambda b: b.key, reverse=True)


def prune(now: datetime.datetime | None = None) -> int:
    """Delete backups older than the retention window, but always keep the newest."""
    now = now or timezone.now()
    cutoff = now - datetime.timedelta(days=settings.BACKUPS["RETENTION_DAYS"])
    old = [b.key for b in list_backups()[1:] if b.taken_at < cutoff]
    if old:
        get_storage().delete(old)
    return len(old)


def restore(key: str) -> None:
    """Replace this database's contents with a backup. Destructive by design."""
    args, env = _pg_env()
    store = get_storage()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "restore.dump"
        store.download_to(key, str(path))
        _run(
            [
                "pg_restore",
                "--clean",
                "--if-exists",
                "--no-owner",
                "--single-transaction",
                *args,
                str(path),
            ],
            env,
        )
    logger.warning("database restored from %s", key)
