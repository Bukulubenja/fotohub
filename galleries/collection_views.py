"""The guest home page at / (enter a passkey) and a collection's page at /c/<id>/.

Server-rendered like the gallery, with the same private headers. The passkey form carries a
CSRF token, so these pages are never gzipped (BREACH).
"""

import uuid
from dataclasses import dataclass

from django.db.models import Count, F, Q, Window
from django.db.models.functions import Lower, RowNumber
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods
from rest_framework.throttling import SimpleRateThrottle

from assets.gallery_media import Image, thumb
from assets.models import Photo
from billing import services as billing
from galleries import collections
from galleries.models import Collection, Gallery
from galleries.public_views import TOO_MANY_TRIES, private_page, unavailable

WRONG_PASSKEY = "That passkey isn't right. Check the message from your photographer or school."
SUSPENDED = "This collection is temporarily unavailable. Contact your photographer."
STACK_SIZE = 4  # the cover plus three smaller photos fanned over it on each card
EAGER_CARDS = 4  # cards whose photos load straight away; the rest load on scroll
UPDATE_NOTICES = {"sent", "invalid", "busy"}  # ?updates= after asking for emails (update_views)


@dataclass(frozen=True)
class GalleryCard:
    url: str
    event_title: str
    gallery_title: str  # shown only when it differs from the event's name
    event_date: object
    location: str
    photo_count: int
    thumbs: list[Image]  # cover first; empty until a photo is ready


class PasskeyMisses(SimpleRateThrottle):
    """Wrong passkeys per visitor (`collection_unlock`). Only misses count: a school's parents
    behind one Wi-Fi share an IP, and right entries mustn't use up each other's allowance.
    Uses DRF's rate parsing and cache history, so the rate lives with the other throttles."""

    scope = "collection_unlock"

    def get_cache_key(self, request, view) -> str:
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}

    def _recent(self, request: HttpRequest) -> list[float]:
        self.key = self.get_cache_key(request, None)
        self.now = self.timer()
        return [t for t in self.cache.get(self.key, []) if t > self.now - self.duration]

    def blocked(self, request: HttpRequest) -> bool:
        return len(self._recent(request)) >= self.num_requests

    def record(self, request: HttpRequest) -> None:
        recent = self._recent(request)
        self.cache.set(self.key, [self.now, *recent], self.duration)


def home_form(request: HttpRequest, passkey: str = "", error: str = "", status: int = 200):
    context = {
        "passkey": passkey,
        "error": error,
        "remembered": collections.remembered_collections(request),
    }
    return render(request, "galleries/public/home.html", context, status=status)


@require_http_methods(["GET", "POST"])
@private_page
def home(request: HttpRequest) -> HttpResponse:
    """Guests enter the passkey their photographer or school shared. Photographers go to /app/."""
    if request.method == "GET":
        # A photographer opening a QR link (?k=) wants to see what guests see.
        if request.user.is_authenticated and "k" not in request.GET:
            return redirect("dashboard")
        return home_form(request, passkey=request.GET.get("k", "")[:40])

    typed = request.POST.get("passkey", "")[:40]
    # The animated form posts with fetch and wants JSON; without JavaScript it's a plain form.
    wants_json = "application/json" in request.headers.get("Accept", "")

    def refuse(error: str, status: int) -> HttpResponse:
        if wants_json:
            return JsonResponse({"error": error}, status=status)
        return home_form(request, typed, error, status=status)

    misses = PasskeyMisses()
    if misses.blocked(request):
        return refuse(TOO_MANY_TRIES, 429)
    collection = collections.find_by_passkey(typed)
    if collection is None:
        misses.record(request)
        return refuse(WRONG_PASSKEY, 403)
    if not billing.standing_for(collection.owner).galleries_live:
        return refuse(SUSPENDED, 403)
    url = reverse("public-collection", args=[collection.pk])
    response = JsonResponse({"url": url}) if wants_json else HttpResponseRedirect(url, status=303)
    collections.remember(request, response, collection)
    return response


def visible_galleries(collection: Collection) -> list[Gallery]:
    """Published, unexpired galleries of the events the passkey opens, newest event first."""
    return list(
        collections.open_galleries(collection)
        .select_related("event", "cover_photo")
        .annotate(photo_count=Count("photos", filter=Q(photos__status=Photo.Status.READY)))
        .order_by("-event__event_date", "-event__created_at", "created_at")
    )


def first_photos(galleries: list[Gallery]) -> dict:
    """Each gallery's first few ready photos (customer order), in one query for the whole page."""
    if not galleries:
        return {}
    ranked = (
        Photo.objects.filter(gallery__in=galleries, status=Photo.Status.READY)
        .annotate(
            rank=Window(
                RowNumber(),
                partition_by=[F("gallery_id")],
                order_by=[Lower("original_filename").asc(), F("id").asc()],
            )
        )
        .filter(rank__lte=STACK_SIZE)
        .order_by("gallery_id", "rank")
    )
    by_gallery: dict = {}
    for photo in ranked:
        by_gallery.setdefault(photo.gallery_id, []).append(photo)
    return by_gallery


def stack(gallery: Gallery, first: list[Photo]) -> list[Image]:
    """The cover (as `assets.gallery_media.cover_photo` picks it), then the next few photos."""
    chosen = gallery.cover_photo
    cover = chosen if chosen is not None and chosen.status == Photo.Status.READY else None
    cover = cover or (first[0] if first else None)
    if cover is None:
        return []
    others = [photo for photo in first if photo.pk != cover.pk][: STACK_SIZE - 1]
    return [image for image in map(thumb, [cover, *others]) if image is not None]


def card(gallery: Gallery, first: list[Photo]) -> GalleryCard:
    event = gallery.event
    return GalleryCard(
        url=reverse("public-gallery-page", args=[gallery.public_token]),
        event_title=event.title,
        gallery_title=gallery.title if gallery.title != event.title else "",
        event_date=event.event_date,
        location=event.location_label,
        photo_count=gallery.photo_count,
        thumbs=stack(gallery, first),
    )


@require_GET
@private_page
def collection_page(request: HttpRequest, collection_id: uuid.UUID) -> HttpResponse:
    collection = (
        Collection.objects.select_related("owner__profile").filter(pk=collection_id).first()
    )
    is_preview = (
        collection is not None
        and request.user.is_authenticated
        and collection.owner_id == request.user.pk
    )
    if collection is None or not (is_preview or collections.is_open(request, collection)):
        return redirect("home")
    if not is_preview and not billing.standing_for(collection.owner).galleries_live:
        return unavailable(request, 404, suspended=True)
    galleries = visible_galleries(collection)
    years = sorted({g.event.event_date.year for g in galleries}, reverse=True)
    year = request.GET.get("year", "")
    if year.isascii() and year.isdigit() and int(year) in years:  # "²".isdigit() is True
        galleries = [g for g in galleries if g.event.event_date.year == int(year)]
    else:
        year = ""
    first = first_photos(galleries)
    context = {
        "collection": collection,
        "profile": collection.owner.profile,
        "cards": [card(g, first.get(g.pk, [])) for g in galleries],
        "years": years if len(years) > 1 else [],
        "year": year,
        "eager_cards": EAGER_CARDS,
        "collection_preview": is_preview,
        "updates_notice": request.GET.get("updates")
        if request.GET.get("updates") in UPDATE_NOTICES
        else "",
    }
    response = render(request, "galleries/public/collection.html", context)
    if not is_preview:
        collections.remember(request, response, collection)  # 180 days from the last visit
    return response
