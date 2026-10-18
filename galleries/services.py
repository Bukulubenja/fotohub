"""Gallery state transitions. Views call these; they never set `status` directly.

Each transition re-reads the row under a lock and saves only the fields it changes, so a
concurrent edit can't overwrite it with stale values (or be overwritten by it).
"""

from collections.abc import Callable

from django.db import transaction
from django.utils import timezone

from galleries.models import Gallery, generate_public_token


class GalleryStateError(Exception):
    pass


PUBLISHABLE_FROM = {Gallery.Status.DRAFT}


def _apply_locked(gallery: Gallery, change: Callable[[Gallery], list[str]]) -> Gallery:
    with transaction.atomic():
        locked = Gallery.objects.select_for_update().get(pk=gallery.pk)
        locked.save(update_fields=[*change(locked), "updated_at"])
    # Refresh the caller's instance so it reflects the committed row, not stale values.
    for field in Gallery._meta.concrete_fields:
        setattr(gallery, field.attname, getattr(locked, field.attname))
    return gallery


def _publish(gallery: Gallery) -> list[str]:
    if gallery.status not in PUBLISHABLE_FROM:
        raise GalleryStateError(
            f"A {gallery.get_status_display().lower()} gallery can't be published."
        )
    if gallery.is_expired():
        raise GalleryStateError("Set an expiry date in the future before publishing.")
    gallery.status = Gallery.Status.PUBLISHED
    gallery.published_at = timezone.now()
    return ["status", "published_at"]


def _unpublish(gallery: Gallery) -> list[str]:
    if gallery.status != Gallery.Status.PUBLISHED:
        raise GalleryStateError("Only a published gallery can be unpublished.")
    gallery.status = Gallery.Status.DRAFT
    return ["status"]


def _rotate_token(gallery: Gallery) -> list[str]:
    gallery.public_token = generate_public_token()
    gallery.access_version += 1
    return ["public_token", "access_version"]


def publish_gallery(gallery: Gallery) -> Gallery:
    return _apply_locked(gallery, _publish)


def unpublish_gallery(gallery: Gallery) -> Gallery:
    return _apply_locked(gallery, _unpublish)


def rotate_gallery_token(gallery: Gallery) -> Gallery:
    """Revoke the current link (spec FR-10); anyone holding the old URL loses access."""
    return _apply_locked(gallery, _rotate_token)


def update_gallery_settings(gallery: Gallery, changes: dict) -> Gallery:
    """Apply owner edits. `password` (None or "" clears it) is hashed, never stored raw."""

    def _update(locked: Gallery) -> list[str]:
        fields = dict(changes)
        updated: list[str] = []
        if "password" in fields:
            locked.set_access_password(fields.pop("password"))
            updated += ["password_hash", "access_version"]
        for name, value in fields.items():
            setattr(locked, name, value)
            updated.append(name)
        return updated

    return _apply_locked(gallery, _update)
