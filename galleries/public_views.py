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
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.middleware.gzip import GZipMiddleware
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.cache import patch_vary_headers
from django.views.decorators.http import require_GET, require_POST
from rest_framework.request import Request
from rest_framework.throttling import BaseThrottle, ScopedRateThrottle

from assets.gallery_media import cover_image, download_url, gallery_photos, ready_photos
from assets.models import Photo
from delivery.services import estimate_bytes
from galleries import access
from galleries.models import Gallery
from galleries.views import NOINDEX, GalleryUnlockThrottle

WRONG_PASSWORD = "That password isn't right. Check the message from your photographer."
TOO_MANY_TRIES = "Too many attempts. Wait a minute, then try again."


def private_page(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    @wraps(view)
    def wrapper(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        response = view(request, *args, **kwargs)
        response["X-Robots-Tag"] = NOINDEX
        response["Cache-Control"] = "private, no-store"
        response["Referrer-Policy"] = "no-referrer"  # the token is in the URL
        patch_vary_headers(response, ["Cookie"])
        return response

    return wrapper


@dataclass(frozen=True)
class Visit:
    gallery: Gallery
    is_preview: bool  # the signed-in owner, seeing it the way customers will


class Unavailable(Exception):
    def __init__(self, status: int) -> None:
        self.status = status


def resolve(request: HttpRequest, token: str) -> Visit:
    gallery = (
        Gallery.objects.select_related("event__owner__profile", "cover_photo")
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
    return Visit(gallery, is_preview=False)


def unavailable(request: HttpRequest, status: int) -> HttpResponse:
    return render(
        request, "galleries/public/unavailable.html", {"expired": status == 410}, status=status
    )


def can_view(request: HttpRequest, visit: Visit) -> bool:
    return visit.is_preview or access.is_unlocked(request, visit.gallery)


def within_rate(
    request: HttpRequest, scope: str, token: str, extra: tuple[BaseThrottle, ...] = ()
) -> bool:
    """Apply DRF throttles (and their cache counters) to a plain Django view."""
    drf_request = Request(request)
    view = SimpleNamespace(throttle_scope=scope, kwargs={"token": token})
    return all(t.allow_request(drf_request, view) for t in (ScopedRateThrottle(), *extra))


def unlock_allowed(request: HttpRequest, token: str) -> bool:
    """Same counters as the JSON unlock API, so switching endpoints buys no extra guesses."""
    return within_rate(request, "gallery_unlock", token, (GalleryUnlockThrottle(),))


def base_context(request: HttpRequest, visit: Visit) -> dict:
    gallery = visit.gallery
    return {
        "gallery": gallery,
        "event": gallery.event,
        "profile": gallery.event.owner.profile,
        "is_preview": visit.is_preview,
        "is_expired": gallery.is_expired(),
        "share_url": request.build_absolute_uri(
            reverse("public-gallery-page", args=[gallery.public_token])
        ),
    }


def locked(request: HttpRequest, visit: Visit, error: str = "", status: int = 200):
    context = {**base_context(request, visit), "error": error}
    return render(request, "galleries/public/locked.html", context, status=status)


# Signed image URLs make the photo page ~70 KB raw but ~10 KB gzipped. Only that response
# is compressed: it reflects no visitor input and carries no CSRF token (BREACH-safe).
_gzip = GZipMiddleware(get_response=lambda request: None)


@require_GET
@private_page
def gallery_page(request: HttpRequest, token: str) -> HttpResponse:
    try:
        visit = resolve(request, token)
    except Unavailable as exc:
        return unavailable(request, exc.status)
    if not can_view(request, visit):
        return locked(request, visit)

    gallery = visit.gallery
    paginator = Paginator(ready_photos(gallery), settings.GALLERY_MEDIA["PAGE_SIZE"])
    page = paginator.get_page(request.GET.get("page"))
    context = {
        **base_context(request, visit),
        "photos": gallery_photos(list(page.object_list)),
        "page": page,
        "photo_count": paginator.count,
        "can_download": gallery.download_policy != Gallery.DownloadPolicy.DISABLED,
    }
    if request.GET.get("partial"):  # "Load more" fetches just the next tiles
        response = render(request, "galleries/public/_photos.html", context)
    else:
        # The share preview shows a photo only when anyone with the link may see it.
        context["cover"] = cover_image(gallery)
        context["share_cover"] = context["cover"] if not gallery.has_password else None
        if context["can_download"] and paginator.count:
            context["archive_bytes"] = estimate_bytes(gallery)
            context["large_bytes"] = settings.DELIVERY["LARGE_DOWNLOAD_BYTES"]
        response = render(request, "galleries/public/gallery.html", context)
    return _gzip.process_response(request, response)


@require_POST
@private_page
def unlock(request: HttpRequest, token: str) -> HttpResponse:
    try:
        visit = resolve(request, token)
    except Unavailable as exc:
        return unavailable(request, exc.status)
    page = reverse("public-gallery-page", args=[token])
    if can_view(request, visit):
        return redirect(page)
    if not unlock_allowed(request, token):
        return locked(request, visit, TOO_MANY_TRIES, status=429)
    if not visit.gallery.check_access_password(request.POST.get("password", "")):
        return locked(request, visit, WRONG_PASSWORD, status=403)
    access.mark_unlocked(request, visit.gallery)
    return HttpResponseRedirect(page, status=303)


@require_GET
@private_page
def download_photo(request: HttpRequest, token: str, photo_id: uuid.UUID) -> HttpResponse:
    """Check access and policy on every download (FR-25), then hand off to storage."""
    try:
        visit = resolve(request, token)
    except Unavailable as exc:
        return unavailable(request, exc.status)
    if not can_view(request, visit):
        return redirect("public-gallery-page", token=token)
    photo = get_object_or_404(Photo, pk=photo_id, gallery=visit.gallery, status=Photo.Status.READY)
    url = download_url(photo, visit.gallery.download_policy)
    if url is None:
        raise Http404("Downloads are turned off for this gallery.")
    return HttpResponseRedirect(url)
