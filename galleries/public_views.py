"""Server-rendered customer gallery at /g/<token>/ (spec FR-19..FR-25, plan §3.1).

HTML rather than the JSON API: the first screen must be light on a cheap phone, and
WhatsApp link previews need Open Graph tags in the server's response. Every response is
private, uncached and noindex. The owner, signed in to the dashboard, can preview a gallery
in any state (FR-09) without its password.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from types import SimpleNamespace

from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import QuerySet
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.middleware.gzip import GZipMiddleware
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.cache import patch_vary_headers
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from rest_framework.request import Request
from rest_framework.throttling import BaseThrottle, ScopedRateThrottle

from assets.gallery_media import cover_image, download_url, gallery_photos, ready_photos
from assets.models import Photo
from billing import services as billing
from galleries import access, favourites, sharing, stats
from galleries.models import Gallery
from galleries.throttling import GalleryPasswordMisses
from galleries.views import NOINDEX
from shop import cart
from shop.pricing import format_money, price

# The drifting wall of photos on the gallery's first screen: a few thumbnails, repeated, so a
# cheap phone downloads little more than the grid's first row.
INTRO_PHOTOS = 12
INTRO_COLUMNS = 5
INTRO_PER_COLUMN = 6
WRONG_PASSWORD = "That password isn't right. Check the message from your photographer."
TOO_MANY_TRIES = "Too many attempts. Wait a minute, then try again."


def private_page(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    @wraps(view)
    def wrapper(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        response = view(request, *args, **kwargs)
        response["X-Robots-Tag"] = NOINDEX
        response["Cache-Control"] = "private, no-store"
        # The token is in the URL, so no referrer leaves the site. Not "no-referrer": with it,
        # browsers send "Origin: null" on form posts and CSRF rejects checkout and unlock
        # (found on the 2026-10-24 phone test; the test client sends no Origin).
        response["Referrer-Policy"] = "same-origin"
        patch_vary_headers(response, ["Cookie"])
        return response

    return wrapper


@dataclass(frozen=True)
class Visit:
    gallery: Gallery
    is_preview: bool  # the signed-in owner, seeing it the way customers will


class Unavailable(Exception):
    def __init__(self, status: int, suspended: bool = False) -> None:
        self.status = status
        self.suspended = suspended  # the photographer's account has lapsed (FR-29)


def resolve(request: HttpRequest, token: str) -> Visit:
    gallery = (
        Gallery.objects.select_related("event__owner__profile", "event__collection", "cover_photo")
        .filter(public_token=token)
        .first()
    )
    if gallery is None:
        raise Unavailable(404)
    if request.user.is_authenticated and gallery.event.owner_id == request.user.pk:
        return Visit(gallery, is_preview=True)
    if gallery.status != Gallery.Status.PUBLISHED:
        raise Unavailable(404)  # drafts look exactly like unknown links
    if gallery.is_expired():
        raise Unavailable(410)
    if not billing.standing_for(gallery.event.owner).galleries_live:
        raise Unavailable(404, suspended=True)
    return Visit(gallery, is_preview=False)


def unavailable(request: HttpRequest, status: int, suspended: bool = False) -> HttpResponse:
    context = {"expired": status == 410, "suspended": suspended}
    return render(request, "galleries/public/unavailable.html", context, status=status)


def can_view(request: HttpRequest, visit: Visit) -> bool:
    return visit.is_preview or access.is_unlocked(request, visit.gallery)


def within_rate(
    request: HttpRequest, scope: str, token: str, extra: tuple[BaseThrottle, ...] = ()
) -> bool:
    """Apply DRF throttles (and their cache counters) to a plain Django view."""
    drf_request = Request(request)
    view = SimpleNamespace(throttle_scope=scope, kwargs={"token": token})
    return all(t.allow_request(drf_request, view) for t in (ScopedRateThrottle(), *extra))


def unlock_allowed(request: HttpRequest, misses: GalleryPasswordMisses) -> bool:
    """Same counters as the JSON unlock API, so switching endpoints buys no extra guesses."""
    return within_rate(request, "gallery_unlock", misses.ident) and not misses.blocked(request)


def base_context(request: HttpRequest, visit: Visit) -> dict:
    gallery = visit.gallery
    return {
        "gallery": gallery,
        "event": gallery.event,
        "profile": gallery.event.owner.profile,
        "is_preview": visit.is_preview,
        "is_expired": gallery.is_expired(),
        "share_url": sharing.share_url(request, gallery),
    }


def locked(request: HttpRequest, visit: Visit, error: str = "", status: int = 200):
    context = {**base_context(request, visit), "error": error}
    return render(request, "galleries/public/locked.html", context, status=status)


def open_gallery(request: HttpRequest, token: str) -> Visit | HttpResponse:
    """The visit, or the page to show instead (unavailable, or the password screen)."""
    try:
        visit = resolve(request, token)
    except Unavailable as exc:
        return unavailable(request, exc.status, exc.suspended)
    if not can_view(request, visit):
        return redirect("public-gallery-page", token=token)
    return visit


def nav_context(request: HttpRequest, gallery: Gallery) -> dict:
    """Badge counts for the tab bar on every gallery page."""
    return {
        "cart_count": cart.count(request, gallery),
        "favourite_count": favourites.count(request, gallery),
    }


def intro_columns(photos: list) -> list[list]:
    """The first photos, repeated to fill every column of the intro's drifting wall."""
    if not photos:
        return []
    photos = photos[:INTRO_PHOTOS]
    slots = [photos[i % len(photos)] for i in range(INTRO_COLUMNS * INTRO_PER_COLUMN)]
    return [slots[column::INTRO_COLUMNS] for column in range(INTRO_COLUMNS)]


# Signed image URLs make the photo page ~70 KB raw but ~10 KB gzipped. Only photo grids are
# compressed: they reflect no visitor input and carry no CSRF token (BREACH-safe).
_gzip = GZipMiddleware(get_response=lambda request: None)


def grid_context(request: HttpRequest, visit: Visit, photos: QuerySet[Photo]) -> dict:
    gallery = visit.gallery
    paginator = Paginator(photos, settings.GALLERY_MEDIA["PAGE_SIZE"])
    page = paginator.get_page(request.GET.get("page"))
    return {
        **base_context(request, visit),
        "photos": gallery_photos(list(page.object_list)),
        "page": page,
        "photo_count": paginator.count,
        # Every photo is for sale; buyers (and the previewing owner) can download.
        "owned": cart.owned_photo_ids(request, gallery),
        "unit_price": format_money("UGX", price("UGX").unit_minor),
    }


def render_grid(request: HttpRequest, visit: Visit, context: dict, template: str) -> HttpResponse:
    """A full page with the photo viewer, or just the next tiles for "Load more"."""
    if request.GET.get("partial"):
        response = render(request, "galleries/public/_photos.html", context)
    else:
        gallery = visit.gallery
        context = {
            **context,
            **nav_context(request, gallery),
            "cart_ids": cart.ids(request, gallery),
            "favourite_ids": favourites.ids(request, gallery),
        }
        response = render(request, template, context)
    return _gzip.process_response(request, response)


@require_GET
@private_page
def gallery_page(request: HttpRequest, token: str) -> HttpResponse:
    try:
        visit = resolve(request, token)
    except Unavailable as exc:
        return unavailable(request, exc.status, exc.suspended)
    if not can_view(request, visit):
        return locked(request, visit)

    gallery = visit.gallery
    context = grid_context(request, visit, ready_photos(gallery))
    if not request.GET.get("partial"):
        # The share preview shows a photo only when anyone with the link may see it.
        cover = cover_image(gallery)
        context["share_cover"] = cover if not gallery.has_password else None
        context["intro_columns"] = intro_columns(context["photos"])
        message = sharing.guest_message(gallery, context["share_url"])
        context["share_data"] = {
            "title": gallery.event.title,
            "text": message,
            "url": context["share_url"],
        }
        context["whatsapp_url"] = sharing.whatsapp_link(message)
        unsold = context["photo_count"] - len(context["owned"])
        context["unsold_count"] = unsold
        context["unsold_total"] = format_money("UGX", price("UGX").total(unsold))
        context["unsold_total_usd"] = format_money("USD", price("USD").total(unsold))
        if context["owned"]:
            token = cart.latest_paid_order_token(request, gallery)
            context["order_url"] = reverse("shop-order", args=[token]) if token else ""
    response = render_grid(request, visit, context, "galleries/public/gallery.html")
    if not request.GET.get("partial") and not visit.is_preview:
        stats.record_view(request, response, gallery)
    return response


@require_GET
@private_page
def favourites_page(request: HttpRequest, token: str) -> HttpResponse:
    visit = open_gallery(request, token)
    if isinstance(visit, HttpResponse):
        return visit
    context = grid_context(request, visit, favourites.photos(request, visit.gallery))
    return render_grid(request, visit, context, "galleries/public/favourites.html")


@csrf_exempt  # like the cart: a forged request can only flip a visitor's favourite
@require_POST
@private_page
def favourite_toggle(request: HttpRequest, token: str) -> HttpResponse:
    visit = open_gallery(request, token)
    if isinstance(visit, HttpResponse):
        return visit
    is_favourite = favourites.toggle(request, visit.gallery, request.POST.get("photo", ""))
    wants_json = "application/json" in request.headers.get("Accept", "")
    if is_favourite is None:
        if wants_json:
            return JsonResponse({"error": "That photo isn't in this gallery."}, status=400)
        raise Http404("That photo isn't in this gallery.")
    if not visit.is_preview:
        stats.record_favourite(visit.gallery, added=is_favourite)
    if wants_json:
        count = favourites.count(request, visit.gallery)
        return JsonResponse({"favourite": is_favourite, "count": count})
    return HttpResponseRedirect(reverse("public-gallery-favourites", args=[token]), status=303)


@require_GET
@private_page
def profile_page(request: HttpRequest, token: str) -> HttpResponse:
    """The visitor's corner: purchases from this browser and how to reach the photographer."""
    visit = open_gallery(request, token)
    if isinstance(visit, HttpResponse):
        return visit
    orders = [
        {
            "order": order,
            "total": format_money(order.currency, order.amount_minor),
            "url": reverse("shop-order", args=[order.token]),
        }
        for order in cart.orders(request, visit.gallery)
    ]
    context = {
        **base_context(request, visit),
        **nav_context(request, visit.gallery),
        "orders": orders,
    }
    return render(request, "galleries/public/profile.html", context)


@require_GET
def terms_page(request: HttpRequest) -> HttpResponse:
    """Purchase terms and refunds, linked from every cart (blueprint: trust at checkout)."""
    context = {
        "price_ugx": format_money("UGX", price("UGX").unit_minor),
        "price_usd": format_money("USD", price("USD").unit_minor),
    }
    return render(request, "galleries/public/terms.html", context)


@require_POST
@private_page
def unlock(request: HttpRequest, token: str) -> HttpResponse:
    try:
        visit = resolve(request, token)
    except Unavailable as exc:
        return unavailable(request, exc.status, exc.suspended)
    page = reverse("public-gallery-page", args=[token])
    if can_view(request, visit):
        return redirect(page)
    misses = GalleryPasswordMisses(token)
    if not unlock_allowed(request, misses):
        return locked(request, visit, TOO_MANY_TRIES, status=429)
    if not visit.gallery.check_access_password(request.POST.get("password", "")):
        misses.record(request)
        return locked(request, visit, WRONG_PASSWORD, status=403)
    access.mark_unlocked(request, visit.gallery)
    return HttpResponseRedirect(page, status=303)


@require_GET
@private_page
def download_photo(request: HttpRequest, token: str, photo_id: uuid.UUID) -> HttpResponse:
    """Originals for buyers (and the previewing owner), re-checked on every download (FR-25)."""
    try:
        visit = resolve(request, token)
    except Unavailable as exc:
        return unavailable(request, exc.status, exc.suspended)
    if not can_view(request, visit):
        return redirect("public-gallery-page", token=token)
    photo = get_object_or_404(Photo, pk=photo_id, gallery=visit.gallery, status=Photo.Status.READY)
    if not visit.is_preview and str(photo.pk) not in cart.owned_photo_ids(request, visit.gallery):
        raise Http404("Buy this photo to download it.")
    return HttpResponseRedirect(download_url(photo, Gallery.DownloadPolicy.ORIGINAL))
