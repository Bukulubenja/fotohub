"""`manage.py storage_check` against a real HTTP S3 endpoint (moto server).

moto doesn't verify signatures, so it accepts a PUT whose size differs from the signed
Content-Length. The command must flag that; R2 and S3 refuse such a PUT with 403.
"""

from io import StringIO

import pytest
from botocore.exceptions import ClientError
from django.core.management import CommandError, call_command

from assets.management.commands import storage_check
from assets.management.commands.storage_check import (
    CORS_RULE_ID,
    apply_cors,
    apply_lifecycle,
    check_cors,
    check_signed_length,
    run_check,
)

ORIGIN = "https://app.fotohub.test"


def run(*args: str) -> tuple[str, str]:
    out = StringIO()
    try:
        call_command("storage_check", *args, stdout=out)
    except CommandError as exc:
        return out.getvalue(), str(exc)
    return out.getvalue(), ""


def test_fresh_bucket_reports_missing_lifecycle(http_storage):
    out, error = run("--origin", ORIGIN)

    assert "ok    bucket reachable" in out
    assert "ok    presigned PUT" in out
    assert "ok    multipart" in out
    assert "FAIL  lifecycle" in out
    assert "lifecycle" in error


def test_preflight_without_allow_origin_fails(http_storage, monkeypatch):
    # moto allows every preflight while a bucket has no CORS rules; R2 refuses them.
    monkeypatch.setattr(storage_check, "http_request", lambda *a, **kw: (403, {}, b""))

    result = check_cors(http_storage, ORIGIN)

    assert not result.ok
    assert "--apply" in result.detail


def test_apply_fixes_cors_and_lifecycle(http_storage):
    out, error = run("--origin", ORIGIN, "--apply")

    assert "ok    apply CORS" in out
    assert "ok    apply lifecycle" in out
    assert "ok    lifecycle" in out
    assert f"ok    CORS for {ORIGIN}" in out
    # Only moto's missing signature check is left.
    assert error == "1 check(s) failed: signed Content-Length enforced"
    assert "Uploads could bypass the storage quota" in out


def test_other_origins_are_refused_by_the_applied_rule(http_storage):
    run("--origin", ORIGIN, "--apply")

    out, _ = run("--origin", "https://evil.example")

    assert "FAIL  CORS for https://evil.example" in out


def test_passes_when_the_bucket_enforces_the_signed_length(http_storage, monkeypatch):
    real_request = storage_check.http_request

    def strict_bucket(method, url, body=None, headers=None):
        if method == "PUT" and body is not None and body.endswith(b"extra"):
            return 403, {}, b"SignatureDoesNotMatch"
        return real_request(method, url, body, headers)

    monkeypatch.setattr(storage_check, "http_request", strict_bucket)

    out, error = run("--origin", ORIGIN, "--apply")

    assert error == ""
    assert "Storage is ready for browser uploads." in out


def test_signed_length_leaves_no_object_behind(http_storage):
    check_signed_length(http_storage)

    listed = http_storage.client.list_objects_v2(Bucket=http_storage.bucket)
    assert listed.get("KeyCount", 0) == 0


def test_apply_cors_keeps_unrelated_rules_and_replaces_its_own(http_storage):
    foreign = {"AllowedOrigins": ["https://cdn.example"], "AllowedMethods": ["GET"]}
    http_storage.put_cors_rules([foreign])

    apply_cors(http_storage, ["https://old.example"])
    apply_cors(http_storage, [ORIGIN])

    rules = http_storage.cors_rules()
    ours = [r for r in rules if r.get("ID") == CORS_RULE_ID]
    assert len(rules) == 2
    assert ours[0]["AllowedOrigins"] == [ORIGIN]
    assert ours[0]["AllowedMethods"] == ["PUT"]


def test_apply_lifecycle_is_idempotent_and_keeps_existing_rules(http_storage):
    expire_tmp = {
        "ID": "expire-tmp",
        "Status": "Enabled",
        "Filter": {"Prefix": "tmp/"},
        "Expiration": {"Days": 7},
    }
    http_storage.put_lifecycle_rules([expire_tmp])

    apply_lifecycle(http_storage)
    second = apply_lifecycle(http_storage)

    ids = [r["ID"] for r in http_storage.lifecycle_rules()]
    assert ids == ["expire-tmp", "fotohub-abort-incomplete-multipart"]
    assert "left unchanged" in second.detail


def test_access_denied_becomes_a_failed_check_with_a_hint():
    def denied():
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "nope"}}, "PutBucketCors")

    result = run_check("apply CORS", denied)

    assert not result.ok
    assert "token lacks permission" in result.detail


def test_missing_credentials_stop_before_touching_the_bucket(settings):
    settings.OBJECT_STORAGE = {**settings.OBJECT_STORAGE, "ACCESS_KEY_ID": None}

    out, error = run()

    assert "STORAGE_ACCESS_KEY_ID" in out
    assert error == "Storage is not configured."


def test_apply_without_origin_is_refused():
    with pytest.raises(CommandError, match="--origin"):
        call_command("storage_check", "--apply", stdout=StringIO())
