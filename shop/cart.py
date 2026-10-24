"""Visitor carts and purchases, kept in the session (customers have no accounts).

Only photo ids live in the session; every read re-checks them against the gallery's ready
photos, so a stale or tampered session can't add photos from another gallery.
"""

from django.http import HttpRequest

from assets.gallery_media import ready_photos
from assets.models import Photo
from galleries.models import Gallery

CARTS = "carts"  # {gallery_id: [photo_id, ...]}
ORDERS = "order_tokens"  # tokens of orders started in this browser


def _ids(request: HttpRequest, gallery: Gallery) -> list[str]:
    return list(request.session.get(CARTS, {}).get(str(gallery.pk), []))


def _store(request: HttpRequest, gallery: Gallery, ids: list[str]) -> None:
    carts = {**request.session.get(CARTS, {}), str(gallery.pk): ids}
    request.session[CARTS] = {key: value for key, value in carts.items() if value}


def owned_photo_ids(request: HttpRequest, gallery: Gallery) -> set[str]:
    """Photos of this gallery the visitor has paid for, in any order from this browser."""
    from shop.models import Order, OrderItem

    tokens = request.session.get(ORDERS, [])
    if not tokens:
        return set()
    return {
        str(photo_id)
        for photo_id in OrderItem.objects.filter(
            order__token__in=tokens,
            order__status=Order.Status.PAID,
            order__gallery=gallery,
            photo__isnull=False,
        ).values_list("photo_id", flat=True)
    }


def latest_paid_order_token(request: HttpRequest, gallery: Gallery) -> str | None:
    """The newest paid order from this browser for this gallery, for a "Your photos" link."""
    from shop.models import Order

    tokens = request.session.get(ORDERS, [])
    if not tokens:
        return None
    order = (
        Order.objects.filter(token__in=tokens, status=Order.Status.PAID, gallery=gallery)
        .order_by("-paid_at")
        .only("token")
        .first()
    )
    return order.token if order else None


def photos(request: HttpRequest, gallery: Gallery) -> list[Photo]:
    """The cart's photos still for sale: ready, in this gallery, not already bought."""
    wanted = _ids(request, gallery)
    if not wanted:
        return []
    owned = owned_photo_ids(request, gallery)
    return [p for p in ready_photos(gallery).filter(pk__in=wanted) if str(p.pk) not in owned]


def ids(request: HttpRequest, gallery: Gallery) -> list[str]:
    return _ids(request, gallery)


def count(request: HttpRequest, gallery: Gallery) -> int:
    return len(photos(request, gallery))


def add(request: HttpRequest, gallery: Gallery, photo_ids: list[str]) -> None:
    current = _ids(request, gallery)
    valid = {
        str(pk)
        for pk in ready_photos(gallery).filter(pk__in=photo_ids).values_list("pk", flat=True)
    }
    owned = owned_photo_ids(request, gallery)
    _store(request, gallery, current + [pk for pk in valid - owned if pk not in current])


def add_all(request: HttpRequest, gallery: Gallery) -> None:
    """ "Add folder": every photo in the gallery the visitor doesn't already own."""
    add(request, gallery, [str(pk) for pk in ready_photos(gallery).values_list("pk", flat=True)])


def remove(request: HttpRequest, gallery: Gallery, photo_id: str) -> None:
    _store(request, gallery, [pk for pk in _ids(request, gallery) if pk != photo_id])


def clear(request: HttpRequest, gallery: Gallery) -> None:
    _store(request, gallery, [])


def remember_order(request: HttpRequest, token: str) -> None:
    tokens = request.session.get(ORDERS, [])
    if token not in tokens:
        request.session[ORDERS] = [*tokens, token]
