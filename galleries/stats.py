"""Event insights for photographers: views, visitors, favourites, orders and gross sales.

Guests are counted with a small per-gallery cookie rather than a session, so browsing a
gallery doesn't create a session row for every visitor. Owner previews aren't counted.
"""

from django.db import IntegrityError, transaction
from django.db.models import Count, F, Sum
from django.http import HttpRequest, HttpResponse
from django.utils import timezone

from galleries.models import Gallery, GalleryDayStats

VISIT_COOKIE_DAYS = 2


def _bump(gallery: Gallery, **deltas: int) -> None:
    day = timezone.localdate()
    rows = GalleryDayStats.objects.filter(gallery=gallery, day=day)
    if rows.update(**{name: F(name) + delta for name, delta in deltas.items()}):
        return
    try:
        with transaction.atomic():
            GalleryDayStats.objects.create(gallery=gallery, day=day, **deltas)
    except IntegrityError:  # another request created today's row first
        rows.update(**{name: F(name) + delta for name, delta in deltas.items()})


def _visit_cookie(gallery: Gallery) -> str:
    return f"fhv_{gallery.pk.hex[:12]}"


def record_view(request: HttpRequest, response: HttpResponse, gallery: Gallery) -> None:
    today = timezone.localdate().isoformat()
    name = _visit_cookie(gallery)
    first_today = request.COOKIES.get(name) != today
    _bump(gallery, views=1, visitors=int(first_today))
    if first_today:
        response.set_cookie(
            name,
            today,
            max_age=VISIT_COOKIE_DAYS * 24 * 3600,
            path=f"/g/{gallery.public_token}/",
            httponly=True,
            samesite="Lax",
        )


def record_favourite(gallery: Gallery, added: bool) -> None:
    _bump(gallery, favourites=1 if added else -1)


def summary(gallery: Gallery) -> dict:
    from shop.models import Order
    from shop.pricing import format_money

    counted = GalleryDayStats.objects.filter(gallery=gallery).aggregate(
        views=Sum("views"), visitors=Sum("visitors"), favourites=Sum("favourites")
    )
    paid = Order.objects.filter(gallery=gallery, status=Order.Status.PAID)
    sold = paid.aggregate(orders=Count("id"), photos=Sum("quantity"))
    gross = [
        {
            "currency": row["currency"],
            "amount_minor": row["total"],
            "formatted": format_money(row["currency"], row["total"]),
        }
        for row in paid.values("currency").annotate(total=Sum("amount_minor")).order_by("currency")
    ]
    visitors = counted["visitors"] or 0
    orders = sold["orders"] or 0
    return {
        "views": counted["views"] or 0,
        "visitors": visitors,
        "favourites": max(counted["favourites"] or 0, 0),
        "orders": orders,
        "photos_sold": sold["photos"] or 0,
        "gross": gross,
        # Share of visitors who bought: orders per visitor, as a percentage.
        "conversion_percent": round(orders / visitors * 100, 1) if visitors else None,
    }
