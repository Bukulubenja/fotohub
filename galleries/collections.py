"""Collection passkeys, and the phones that remember them.

A passkey both finds and unlocks a collection, so it can't be guessable: a readable prefix the
photographer picks (MGSS) plus 10 random characters from an alphabet without look-alikes (about
50 bits). Wrong guesses are rate-limited per visitor on the home page. There's deliberately no
site-wide cap: anyone could trip it on purpose and lock every guest out. The 50 bits are what
keep many IPs guessing together hopeless (they protect photos of children).

Unlocks live in a signed cookie, not the session: sessions last two weeks and are shared with
the photographer's sign-in, while a parent should stay in for months. The cookie holds only
collection ids pinned to `access_version`, so a new passkey signs every phone out.
"""

import json
import re
import secrets

from django.conf import settings
from django.core.signing import BadSignature
from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet
from django.http import HttpRequest, HttpResponse
from django.utils import timezone

from galleries.models import Collection, Gallery

ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # no 0/O, 1/I/L
RANDOM_GROUPS = 2
GROUP_LENGTH = 5
PREFIX_PATTERN = re.compile(r"[A-Z0-9]{2,8}")
DEFAULT_PREFIX = "FH"
MAX_DEFAULT_PREFIX = 6
CREATE_ATTEMPTS = 5

COOKIE = "fh_collections"
COOKIE_SALT = "galleries.collections"
REMEMBER_SECONDS = 180 * 24 * 60 * 60
MAX_REMEMBERED = 20  # keeps the cookie small; the oldest drop off first


def normalize_passkey(raw: str) -> str:
    """What guests type, reduced to letters and digits: ' mgss 7k4qp-x9abm ' -> 'MGSS7K4QPX9ABM'."""
    return re.sub(r"[^A-Z0-9]", "", raw.upper())


def validate_prefix(prefix: str) -> str:
    cleaned = prefix.strip().upper()
    if not PREFIX_PATTERN.fullmatch(cleaned):
        raise ValueError("Use 2 to 8 letters or digits, like MGSS.")
    return cleaned


def default_prefix(name: str) -> str:
    """Initials of the name: 'St. Maria Goretti Secondary School' -> 'SMGSS'."""
    initials = "".join(word[0] for word in re.findall(r"[A-Za-z0-9]+", name)).upper()
    initials = initials[:MAX_DEFAULT_PREFIX]
    return initials if len(initials) >= 2 else DEFAULT_PREFIX


def generate_passkey(prefix: str) -> str:
    groups = (
        "".join(secrets.choice(ALPHABET) for _ in range(GROUP_LENGTH)) for _ in range(RANDOM_GROUPS)
    )
    return "-".join([prefix, *groups])


def _assign_passkey(collection: Collection, prefix: str) -> None:
    collection.passkey_prefix = prefix
    collection.passkey = generate_passkey(prefix)
    collection.passkey_key = normalize_passkey(collection.passkey)


def _save_with_fresh_passkey(collection: Collection, prefix: str, **save_kwargs) -> None:
    """Retry on the (astronomically rare) clash with another collection's passkey."""
    for attempt in range(CREATE_ATTEMPTS):
        _assign_passkey(collection, prefix)
        try:
            with transaction.atomic():
                collection.save(**save_kwargs)
            return
        except IntegrityError:
            if attempt == CREATE_ATTEMPTS - 1:
                raise


def create_collection(owner, name: str, prefix: str = "") -> Collection:
    prefix = validate_prefix(prefix) if prefix else default_prefix(name)
    collection = Collection(owner=owner, name=name)
    _save_with_fresh_passkey(collection, prefix)
    return collection


def _sign_everyone_out(collection: Collection, change=None) -> Collection:
    """Bump `access_version` under a row lock, so two changes at once can't share a version."""
    with transaction.atomic():
        locked = Collection.objects.select_for_update().get(pk=collection.pk)
        locked.access_version += 1
        fields = ["access_version", "updated_at"]
        if change is not None:
            fields += change(locked)
        else:
            locked.save(update_fields=fields)
    for field in Collection._meta.concrete_fields:
        setattr(collection, field.attname, getattr(locked, field.attname))
    return collection


def rotate_passkey(collection: Collection) -> Collection:
    """A new passkey with the same prefix. Phones that remembered the old one are signed out."""

    def new_passkey(locked: Collection) -> list[str]:
        fields = ["passkey", "passkey_key", "passkey_prefix", "access_version", "updated_at"]
        _save_with_fresh_passkey(locked, locked.passkey_prefix, update_fields=fields)
        return []

    return _sign_everyone_out(collection, new_passkey)


def set_status(collection: Collection, status: str) -> Collection:
    """Archive or reopen. Either way phones are signed out, so reopening needs the passkey again."""
    if status == collection.status:
        return collection

    def apply(locked: Collection) -> list[str]:
        locked.status = status
        locked.save(update_fields=["status", "access_version", "updated_at"])
        return []

    return _sign_everyone_out(collection, apply)


def open_galleries(collection: Collection) -> QuerySet[Gallery]:
    """The galleries its passkey shows: published, unexpired, not kept behind their own password.
    The collection page and the update emails both use this, so they can't disagree."""
    return Gallery.objects.filter(
        event__collection=collection,
        event__separate_access=False,
        status=Gallery.Status.PUBLISHED,
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))


def find_by_passkey(raw: str) -> Collection | None:
    key = normalize_passkey(raw)
    if not key:
        return None
    return (
        Collection.objects.select_related("owner__profile")
        .filter(passkey_key=key, status=Collection.Status.ACTIVE)
        .first()
    )


# --- Phones that remember a collection -----------------------------------------------------


def remembered(request: HttpRequest) -> dict[str, int]:
    """{collection id: access_version} from the signed cookie; anything tampered reads as {}."""
    try:
        raw = request.get_signed_cookie(COOKIE, salt=COOKIE_SALT, max_age=REMEMBER_SECONDS)
        data = json.loads(raw)
    except (KeyError, BadSignature, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, int)}


def remember(request: HttpRequest, response: HttpResponse, collection: Collection) -> None:
    kept = {k: v for k, v in remembered(request).items() if k != str(collection.pk)}
    kept[str(collection.pk)] = collection.access_version  # newest last
    kept = dict(list(kept.items())[-MAX_REMEMBERED:])
    response.set_signed_cookie(
        COOKIE,
        json.dumps(kept),
        salt=COOKIE_SALT,
        max_age=REMEMBER_SECONDS,
        httponly=True,
        secure=settings.SESSION_COOKIE_SECURE,
        samesite="Lax",
    )


def is_open(request: HttpRequest, collection: Collection) -> bool:
    return (
        collection.status == Collection.Status.ACTIVE
        and remembered(request).get(str(collection.pk)) == collection.access_version
    )


def remembered_collections(request: HttpRequest) -> list[Collection]:
    """Active collections this phone still holds the current passkey for, newest first."""
    versions = remembered(request)
    if not versions:
        return []
    found = Collection.objects.filter(pk__in=list(versions), status=Collection.Status.ACTIVE)
    by_id = {str(c.pk): c for c in found if versions[str(c.pk)] == c.access_version}
    return [by_id[k] for k in reversed(versions) if k in by_id]


def opens_gallery(request: HttpRequest, gallery: Gallery) -> bool:
    """Does a remembered collection open this gallery? Not for events kept separate."""
    event = gallery.event
    if event.collection_id is None or event.separate_access:
        return False
    return is_open(request, event.collection)
