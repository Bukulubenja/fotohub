"""Prove the configured bucket behaves the way the upload pipeline assumes.

Run once per environment, and again after rotating keys, before real photographers upload:

    manage.py storage_check --origin https://app.fotohub.ug
    manage.py storage_check --origin https://app.fotohub.ug --apply   # also set CORS + lifecycle

Every check goes over real HTTP with presigned URLs, exactly as a browser would.
"""

import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from email.message import Message

from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from assets.storage import ObjectStorage, get_storage

PROBE_PREFIX = "_storage_check/"
PROBE_BODY = b"fotohub storage check\n"
PROBE_CONTENT_TYPE = "text/plain"
# Parts aren't signed with a type. Without one, urllib sends the body as a form post.
PART_HEADERS = {"Content-Type": "application/octet-stream"}
HTTP_TIMEOUT_SECONDS = 30
CORS_RULE_ID = "fotohub-browser-uploads"
CORS_MAX_AGE_SECONDS = 3600
LIFECYCLE_RULE_ID = "fotohub-abort-incomplete-multipart"
LIFECYCLE_ABORT_DAYS = 1
REQUIRED_SETTINGS = ("BUCKET", "ACCESS_KEY_ID", "SECRET_ACCESS_KEY")


@dataclass(frozen=True)
class Result:
    name: str
    ok: bool
    detail: str = ""


def http_request(
    method: str, url: str, body: bytes | None = None, headers: dict[str, str] | None = None
) -> tuple[int, Message, bytes]:
    """(status, headers, body). Status 0 means the request never got an HTTP answer."""
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        return 0, Message(), str(exc).encode()


def probe_key() -> str:
    return f"{PROBE_PREFIX}{uuid.uuid4().hex}"


def check_config(conf: dict) -> Result:
    missing = [name for name in REQUIRED_SETTINGS if not conf.get(name)]
    if missing:
        names = ", ".join(f"STORAGE_{name}" for name in missing)
        return Result("config", False, f"Set {names} in .env")
    endpoint = conf.get("ENDPOINT_URL") or "AWS default endpoint"
    return Result("config", True, f"bucket {conf['BUCKET']} at {endpoint}")


def check_bucket(store: ObjectStorage) -> Result:
    store.head_bucket()
    return Result("bucket reachable", True)


def check_presigned_put(store: ObjectStorage) -> Result:
    """The single-file path: browser PUT with a presigned URL, then read back."""
    key = probe_key()
    try:
        url = store.presign_put(key, PROBE_CONTENT_TYPE, len(PROBE_BODY))
        status, _, body = http_request("PUT", url, PROBE_BODY, {"Content-Type": PROBE_CONTENT_TYPE})
        if status != 200:
            return Result("presigned PUT", False, f"HTTP {status}: {body[:200]!r}")
        if store.object_size(key) != len(PROBE_BODY):
            return Result("presigned PUT", False, "object missing or wrong size after PUT")
        status, _, body = http_request("GET", store.presign_get(key))
        if status != 200 or body != PROBE_BODY:
            return Result("presigned PUT", False, f"read-back failed (HTTP {status})")
        return Result("presigned PUT", True, "upload and signed read-back work")
    finally:
        store.delete([key])


def check_signed_length(store: ObjectStorage) -> Result:
    """Quota is reserved for the declared size; the bucket must refuse any other size."""
    key = probe_key()
    try:
        url = store.presign_put(key, PROBE_CONTENT_TYPE, len(PROBE_BODY))
        status, _, _ = http_request(
            "PUT", url, PROBE_BODY + b"extra", {"Content-Type": PROBE_CONTENT_TYPE}
        )
        if 400 <= status < 500 and store.object_size(key) is None:
            return Result("signed Content-Length enforced", True, f"oversized PUT got {status}")
        return Result(
            "signed Content-Length enforced",
            False,
            f"oversized PUT got HTTP {status}. Uploads could bypass the storage quota.",
        )
    finally:
        store.delete([key])


def check_multipart(store: ObjectStorage) -> Result:
    """The large-file path: create, PUT one part via a presigned URL, list it, abort."""
    key = probe_key()
    upload_id = store.create_multipart(key, PROBE_CONTENT_TYPE)
    try:
        status, _, body = http_request(
            "PUT", store.presign_part(key, upload_id, 1), PROBE_BODY, PART_HEADERS
        )
        if status != 200:
            return Result("multipart", False, f"part PUT got HTTP {status}: {body[:200]!r}")
        parts = store.list_parts(key, upload_id)
        if [p["PartNumber"] for p in parts] != [1]:
            return Result("multipart", False, f"bucket lists parts {parts}, expected part 1")
        return Result("multipart", True, "part upload and listing work")
    finally:
        store.abort_multipart(key, upload_id)


def check_cors(store: ObjectStorage, origin: str) -> Result:
    """Send the browser's preflight for a presigned PUT and read what the bucket answers."""
    name = f"CORS for {origin}"
    url = store.presign_put(probe_key(), PROBE_CONTENT_TYPE, len(PROBE_BODY))
    status, headers, _ = http_request(
        "OPTIONS",
        url,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    allowed_origin = headers.get("Access-Control-Allow-Origin")
    if 200 <= status < 300 and allowed_origin in (origin, "*"):
        return Result(name, True, "browser PUT preflight allowed")
    return Result(
        name,
        False,
        f"preflight got HTTP {status}, Allow-Origin={allowed_origin!r}. "
        "Run with --apply, or add the rule in the R2 dashboard (changes take ~30 s).",
    )


def is_abort_rule(rule: dict) -> bool:
    abort = rule.get("AbortIncompleteMultipartUpload") or {}
    return rule.get("Status") == "Enabled" and bool(abort.get("DaysAfterInitiation"))


def check_lifecycle(store: ObjectStorage) -> Result:
    if any(is_abort_rule(rule) for rule in store.lifecycle_rules()):
        return Result("lifecycle", True, "incomplete multipart uploads are aborted")
    return Result(
        "lifecycle",
        False,
        f"no rule aborts incomplete multipart uploads. Run with --apply, or add one "
        f"({LIFECYCLE_ABORT_DAYS} day) in the R2 dashboard.",
    )


def apply_cors(store: ObjectStorage, origins: list[str]) -> Result:
    rule = {
        "ID": CORS_RULE_ID,
        "AllowedOrigins": origins,
        "AllowedMethods": ["PUT"],
        "AllowedHeaders": ["content-type"],
        "MaxAgeSeconds": CORS_MAX_AGE_SECONDS,
    }
    others = [r for r in store.cors_rules() if r.get("ID") != CORS_RULE_ID]
    store.put_cors_rules([*others, rule])
    return Result("apply CORS", True, f"PUT allowed from {', '.join(origins)}")


def apply_lifecycle(store: ObjectStorage) -> Result:
    rules = store.lifecycle_rules()
    if any(is_abort_rule(rule) for rule in rules):
        return Result("apply lifecycle", True, "already present, left unchanged")
    rule = {
        "ID": LIFECYCLE_RULE_ID,
        "Status": "Enabled",
        "Filter": {"Prefix": ""},
        "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": LIFECYCLE_ABORT_DAYS},
    }
    store.put_lifecycle_rules([*rules, rule])
    return Result("apply lifecycle", True, f"abort after {LIFECYCLE_ABORT_DAYS} day")


def run_check(name: str, check: Callable[[], Result]) -> Result:
    """Turn storage errors into a failed result so one problem doesn't hide the rest."""
    try:
        return check()
    except ClientError as exc:
        error = exc.response["Error"]
        hint = " (token lacks permission)" if error.get("Code") == "AccessDenied" else ""
        return Result(name, False, f"{error.get('Code')}: {error.get('Message')}{hint}")
    except BotoCoreError as exc:
        return Result(name, False, str(exc))


class Command(BaseCommand):
    help = "Check that object storage supports browser uploads; optionally set CORS + lifecycle."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--origin",
            action="append",
            default=[],
            help="Dashboard origin that uploads from the browser, e.g. https://app.fotohub.ug. "
            "Repeat for several.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write the CORS rule for --origin and the multipart lifecycle rule first.",
        )

    def handle(self, *args, origin: list[str], apply: bool, **options) -> None:
        if apply and not origin:
            raise CommandError("--apply needs at least one --origin.")

        config = check_config(settings.OBJECT_STORAGE)
        self.report(config)
        if not config.ok:
            raise CommandError("Storage is not configured.")

        store = get_storage()
        steps: list[tuple[str, Callable[[], Result]]] = []
        if apply:
            steps += [
                ("apply CORS", lambda: apply_cors(store, origin)),
                ("apply lifecycle", lambda: apply_lifecycle(store)),
            ]
        steps += [
            ("bucket reachable", lambda: check_bucket(store)),
            ("presigned PUT", lambda: check_presigned_put(store)),
            ("signed Content-Length enforced", lambda: check_signed_length(store)),
            ("multipart", lambda: check_multipart(store)),
            ("lifecycle", lambda: check_lifecycle(store)),
        ]
        steps += [(f"CORS for {o}", lambda o=o: check_cors(store, o)) for o in origin]

        results = [self.report(run_check(name, check)) for name, check in steps]
        if not origin:
            self.stdout.write(self.style.WARNING("skip  CORS: pass --origin to check it"))

        failed = [r.name for r in results if not r.ok]
        if failed:
            raise CommandError(f"{len(failed)} check(s) failed: {', '.join(failed)}")
        self.stdout.write(self.style.SUCCESS("Storage is ready for browser uploads."))

    def report(self, result: Result) -> Result:
        style = self.style.SUCCESS if result.ok else self.style.ERROR
        label = "ok  " if result.ok else "FAIL"
        self.stdout.write(
            style(f"{label}  {result.name}") + (f": {result.detail}" if result.detail else "")
        )
        return result
