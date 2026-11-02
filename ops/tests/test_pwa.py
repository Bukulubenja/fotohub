"""The installable app (PWA): manifests, icons, an offline page and a service worker that keeps
only public files, never a gallery page, photo, order or API answer."""

import json
import re

import pytest
from django.contrib.staticfiles import finders
from django.templatetags.static import static
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db


def manifest(client: Client, name: str) -> dict:
    response = client.get(reverse(name))
    assert response["Content-Type"] == "application/manifest+json"
    return json.loads(response.content)


@pytest.mark.parametrize(
    ("name", "start"), [("pwa-manifest", "/"), ("pwa-dashboard-manifest", "/app/")]
)
def test_manifests_make_the_site_installable(client: Client, name, start):
    data = manifest(client, name)

    assert data["name"].startswith("SMAGOK GALLERY")
    assert (data["start_url"], data["scope"], data["display"]) == (start, start, "standalone")
    sizes = {icon["sizes"] for icon in data["icons"]}
    assert {"192x192", "512x512"} <= sizes
    assert any(icon.get("purpose") == "maskable" for icon in data["icons"])


def test_every_icon_in_the_manifest_exists(client: Client):
    for icon in manifest(client, "pwa-manifest")["icons"]:
        path = icon["src"].removeprefix(static(""))
        assert finders.find(path), icon["src"]


def test_service_worker_is_served_from_the_root_and_never_stale(client: Client):
    response = client.get("/sw.js")

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/javascript")
    assert "no-cache" in response["Cache-Control"]
    assert reverse("pwa-offline") in response.content.decode()


def test_service_worker_keeps_only_public_files():
    """The rules, as written in the worker: same-site GETs only, /static/ cached, page visits
    straight to the network with the offline page as the fallback, nothing else stored."""
    js = Client().get("/sw.js").content.decode()

    assert "location.origin" in js  # R2 photos and payment pages are left alone
    assert 'request.method !== "GET"' in js
    assert '"/static/"' in js
    assert js.count("cache.put(") == 1  # only static files are ever written to the cache


def test_offline_page_works_without_the_network(client: Client):
    response = client.get(reverse("pwa-offline"))

    assert response.status_code == 200
    assert "offline" in response.content.decode().lower()


def test_guest_pages_link_the_manifest_and_register_the_worker(client: Client):
    html = client.get(reverse("home")).content.decode()

    assert f'<link rel="manifest" href="{reverse("pwa-manifest")}">' in html
    assert 'rel="apple-touch-icon"' in html and 'name="theme-color"' in html
    assert 'serviceWorker.register("/sw.js")' in html


def test_changing_the_offline_page_renews_the_cache(client: Client, monkeypatch):
    """Otherwise phones would keep showing an old offline page after a redesign."""
    from ops import pwa

    before = client.get("/sw.js").content.decode()
    monkeypatch.setattr(
        pwa, "OFFLINE_TEMPLATES", [*pwa.OFFLINE_TEMPLATES, "feedback/feedback.html"]
    )
    after = client.get("/sw.js").content.decode()

    cache_name = re.compile(r'const CACHE = "(smagok-\w+)"')
    assert cache_name.search(before).group(1) != cache_name.search(after).group(1)
