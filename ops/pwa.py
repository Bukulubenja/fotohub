"""The installable app (PWA): two manifests (guests start at the passkey page, the school's
staff at the dashboard), the service worker and the page it shows when there's no network.

The worker keeps only public, versioned files (fonts, icons, the offline page). Gallery pages,
photos, orders and the API are private and marked no-store, so it never caches them."""

import hashlib
import json

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.template.loader import get_template
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.http import require_GET

THEME_COLOR = "#1e6b34"  # the school's green
BACKGROUND_COLOR = "#f9f9f9"  # the page background, shown while the app starts
ICON_DIR = "galleries/icons"
# Files the offline page needs, so it renders fully with no network.
PRECACHED_STATIC = [
    "galleries/fonts/inter-latin-wght-normal.woff2",
    "galleries/fonts/outfit-latin-wght-normal.woff2",
    f"{ICON_DIR}/logo-96.png",
    f"{ICON_DIR}/icon-192.png",
]


OFFLINE_TEMPLATES = [
    "ops/offline.html",
    "galleries/public/base.html",
    "galleries/public/_wordmark.html",
]


def _icons() -> list[dict]:
    return [
        {"src": static(f"{ICON_DIR}/icon-192.png"), "sizes": "192x192", "type": "image/png"},
        {"src": static(f"{ICON_DIR}/icon-512.png"), "sizes": "512x512", "type": "image/png"},
        {
            "src": static(f"{ICON_DIR}/icon-maskable-512.png"),
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "maskable",
        },
    ]


def _manifest(name: str, short_name: str, start: str) -> HttpResponse:
    data = {
        "name": name,
        "short_name": short_name,
        "description": settings.BRAND["TAGLINE"],
        "start_url": start,
        "scope": start,
        "display": "standalone",
        "theme_color": THEME_COLOR,
        "background_color": BACKGROUND_COLOR,
        "icons": _icons(),
    }
    return HttpResponse(json.dumps(data), content_type="application/manifest+json")


@require_GET
def manifest(request: HttpRequest) -> HttpResponse:
    return _manifest(settings.BRAND["NAME"], "SMAGOK", "/")


@require_GET
def dashboard_manifest(request: HttpRequest) -> HttpResponse:
    return _manifest(f"{settings.BRAND['NAME']} dashboard", "SMAGOK admin", "/app/")


@require_GET
def service_worker(request: HttpRequest) -> HttpResponse:
    offline = reverse("pwa-offline")
    precache = [offline, *(static(path) for path in PRECACHED_STATIC)]
    # A new cache whenever a precached file changes: static URLs carry content hashes in
    # production, and the offline page's templates are hashed here, so its look stays current.
    sources = [get_template(name).template.source for name in OFFLINE_TEMPLATES]
    version = hashlib.sha256("\n".join([*precache, *sources]).encode()).hexdigest()[:12]
    context = {"version": version, "offline": offline, "precache": json.dumps(precache)}
    response = render(request, "ops/sw.js", context, content_type="text/javascript")
    # Browsers must always ask for a fresh worker, or a deploy could keep an old one alive.
    response["Cache-Control"] = "no-cache"
    return response


@require_GET
def offline(request: HttpRequest) -> HttpResponse:
    return render(request, "ops/offline.html")
