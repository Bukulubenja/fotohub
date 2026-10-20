"""Private S3-compatible object storage (Cloudflare R2 in production).

The only module that talks to the bucket. Browsers never receive credentials, just
short-lived presigned URLs for one object or one multipart part.
"""

import datetime
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from urllib.parse import quote

import boto3
import botocore.auth
from botocore.config import Config
from botocore.exceptions import ClientError
from django.conf import settings

MISSING_CODES = {"404", "NoSuchKey", "NotFound"}
NO_CONFIG_CODES = {"NoSuchCORSConfiguration", "NoSuchLifecycleConfiguration"}


class StorageError(Exception):
    pass


# --- Cache-friendly presigned URLs -------------------------------------------------------
# A presigned URL embeds its signing time, so signing on every page view gives every visit
# new URLs and the browser downloads the same thumbnails again. Signing gallery images at the
# start of a fixed window makes the URL identical for everyone within that window, so the
# browser (and later a CDN) caches it. botocore reads the time from one module function; we
# swap in a version that honours a per-thread pinned time and is the real clock otherwise.
_pinned = threading.local()
_real_now = botocore.auth.get_current_datetime


def _signing_now() -> datetime.datetime:
    return getattr(_pinned, "at", None) or _real_now()


botocore.auth.get_current_datetime = _signing_now


@contextmanager
def _signing_time(at: datetime.datetime) -> Iterator[None]:
    _pinned.at = at
    try:
        yield
    finally:
        _pinned.at = None


def window_start(now: datetime.datetime, window_seconds: int) -> datetime.datetime:
    epoch = int(now.timestamp())
    return datetime.datetime.fromtimestamp(epoch - epoch % window_seconds, tz=datetime.UTC)


def attachment_disposition(filename: str) -> str:
    """RFC 6266 header that survives non-ASCII names (e.g. "Amina & Dàniel.jpg")."""
    ascii_name = filename.encode("ascii", "ignore").decode().replace('"', "") or "photo.jpg"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


class ObjectStorage:
    def __init__(self, conf: dict) -> None:
        self.bucket = conf["BUCKET"]
        self.client = boto3.client(
            "s3",
            endpoint_url=conf["ENDPOINT_URL"],
            region_name=conf["REGION"],
            aws_access_key_id=conf["ACCESS_KEY_ID"],
            aws_secret_access_key=conf["SECRET_ACCESS_KEY"],
            config=Config(
                signature_version="s3v4",
                retries={"max_attempts": 5, "mode": "adaptive"},  # R2 sheds load with 5xx
            ),
        )

    @property
    def url_ttl(self) -> int:
        return settings.UPLOADS["URL_TTL_SECONDS"]

    def presign_put(self, key: str, content_type: str, size_bytes: int) -> str:
        return self.client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ContentType": content_type,
                "ContentLength": size_bytes,
            },
            ExpiresIn=self.url_ttl,
        )

    def presign_get(self, key: str) -> str:
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=self.url_ttl
        )

    def presign_get_cacheable(self, key: str) -> str:
        """Same URL for everyone during a window; valid for at least one more window."""
        window = settings.GALLERY_MEDIA["URL_WINDOW_SECONDS"]
        start = window_start(_real_now(), window)
        with _signing_time(start):
            return self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=2 * window,
            )

    def presign_download(self, key: str, filename: str) -> str:
        """A short-lived link that makes the browser save the file instead of showing it."""
        return self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentDisposition": attachment_disposition(filename),
            },
            ExpiresIn=settings.GALLERY_MEDIA["DOWNLOAD_URL_TTL_SECONDS"],
        )

    def create_multipart(self, key: str, content_type: str) -> str:
        response = self.client.create_multipart_upload(
            Bucket=self.bucket, Key=key, ContentType=content_type
        )
        return response["UploadId"]

    def presign_part(self, key: str, upload_id: str, part_number: int) -> str:
        return self.client.generate_presigned_url(
            "upload_part",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "UploadId": upload_id,
                "PartNumber": part_number,
            },
            ExpiresIn=self.url_ttl,
        )

    def list_parts(self, key: str, upload_id: str) -> list[dict]:
        paginator = self.client.get_paginator("list_parts")
        parts = []
        for page in paginator.paginate(Bucket=self.bucket, Key=key, UploadId=upload_id):
            parts += [
                {"PartNumber": p["PartNumber"], "ETag": p["ETag"], "Size": p["Size"]}
                for p in page.get("Parts", [])
            ]
        return parts

    def complete_multipart(self, key: str, upload_id: str) -> None:
        """Assemble from the parts the bucket holds, not from what the client claims."""
        parts = self.list_parts(key, upload_id)
        if not parts:
            raise StorageError("No parts have been uploaded yet.")
        self.client.complete_multipart_upload(
            Bucket=self.bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={
                "Parts": [{"PartNumber": p["PartNumber"], "ETag": p["ETag"]} for p in parts]
            },
        )

    def abort_multipart(self, key: str, upload_id: str) -> None:
        try:
            self.client.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id)
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "NoSuchUpload":
                raise

    def object_size(self, key: str) -> int | None:
        try:
            return self.client.head_object(Bucket=self.bucket, Key=key)["ContentLength"]
        except ClientError as exc:
            if exc.response["Error"]["Code"] in MISSING_CODES:
                return None
            raise

    def iter_object(self, key: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        """Stream an object without holding it in memory (ZIP archives of whole galleries)."""
        body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"]
        try:
            yield from body.iter_chunks(chunk_size)
        finally:
            body.close()

    def upload_part(self, key: str, upload_id: str, part_number: int, data: bytes) -> None:
        self.client.upload_part(
            Bucket=self.bucket, Key=key, UploadId=upload_id, PartNumber=part_number, Body=data
        )

    def download_to(self, key: str, path: str) -> None:
        self.client.download_file(self.bucket, key, path)

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            CacheControl="private, max-age=31536000, immutable",  # keys never change content
        )

    # Bucket setup, used by `manage.py storage_check`. R2 tokens scoped to "Object Read &
    # Write" can't call these; they raise ClientError(AccessDenied) and the check says so.

    def head_bucket(self) -> None:
        self.client.head_bucket(Bucket=self.bucket)

    def cors_rules(self) -> list[dict]:
        try:
            return self.client.get_bucket_cors(Bucket=self.bucket)["CORSRules"]
        except ClientError as exc:
            if exc.response["Error"]["Code"] in NO_CONFIG_CODES:
                return []
            raise

    def put_cors_rules(self, rules: list[dict]) -> None:
        self.client.put_bucket_cors(Bucket=self.bucket, CORSConfiguration={"CORSRules": rules})

    def lifecycle_rules(self) -> list[dict]:
        try:
            return self.client.get_bucket_lifecycle_configuration(Bucket=self.bucket)["Rules"]
        except ClientError as exc:
            if exc.response["Error"]["Code"] in NO_CONFIG_CODES:
                return []
            raise

    def put_lifecycle_rules(self, rules: list[dict]) -> None:
        self.client.put_bucket_lifecycle_configuration(
            Bucket=self.bucket, LifecycleConfiguration={"Rules": rules}
        )

    def delete(self, keys: list[str]) -> None:
        for start in range(0, len(keys), 1000):  # DeleteObjects limit
            batch = keys[start : start + 1000]
            self.client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
            )


@lru_cache(maxsize=1)
def get_storage() -> ObjectStorage:
    return ObjectStorage(settings.OBJECT_STORAGE)
