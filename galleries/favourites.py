"""Visitor favourites, kept in the session like the cart (customers have no accounts).

Only photo ids live in the session; reads re-check them against the gallery's ready photos,
so a stale or tampered session can't surface photos from another gallery.
"""

import uuid

from django.db.models import QuerySet
from django.http import HttpRequest

from assets.gallery_media import ready_photos
from assets.models import Photo
from galleries.models import Gallery

FAVOURITES = "favourites"  # {gallery_id: [photo_id, ...]}


def ids(request: HttpRequest, gallery: Gallery) -> list[str]:
    return list(request.session.get(FAVOURITES, {}).get(str(gallery.pk), []))


def _store(request: HttpRequest, gallery: Gallery, photo_ids: list[str]) -> None:
    saved = {**request.session.get(FAVOURITES, {}), str(gallery.pk): photo_ids}
    request.session[FAVOURITES] = {key: value for key, value in saved.items() if value}


def photos(request: HttpRequest, gallery: Gallery) -> QuerySet[Photo]:
    """Favourite photos still in the gallery, in the gallery's own order."""
    return ready_photos(gallery).filter(pk__in=ids(request, gallery))


def count(request: HttpRequest, gallery: Gallery) -> int:
    wanted = ids(request, gallery)
    return photos(request, gallery).count() if wanted else 0


def toggle(request: HttpRequest, gallery: Gallery, photo_id: str) -> bool | None:
    """Flip a photo in or out of the favourites. None when it isn't a ready photo here."""
    current = ids(request, gallery)
    if photo_id in current:
        _store(request, gallery, [pk for pk in current if pk != photo_id])
        return False
    try:
        uuid.UUID(photo_id)
    except ValueError:
        return None
    if not ready_photos(gallery).filter(pk=photo_id).exists():
        return None
    _store(request, gallery, [*current, photo_id])
    return True
