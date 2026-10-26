"""Photographer event insights: what's counted, what isn't, and who may see it."""

import pytest
from django.test import Client
from django.urls import reverse

from assets.tests.factories import ready_photo
from galleries.models import Gallery, GalleryDayStats
from galleries.tests.factories import EventFactory, GalleryFactory
from shop.models import Order
from shop.tests.factories import make_order

pytestmark = pytest.mark.django_db


@pytest.fixture
def published(photographer):
    return GalleryFactory(
        event=EventFactory(owner=photographer, title="Amina & Daniel"),
        status=Gallery.Status.PUBLISHED,
    )


def page(client: Client, gallery: Gallery, **params):
    return client.get(reverse("public-gallery-page", args=[gallery.public_token]), params)


def stats_url(gallery: Gallery) -> str:
    return reverse("gallery-stats", args=[gallery.pk])


def totals(gallery: Gallery) -> tuple[int, int, int]:
    rows = GalleryDayStats.objects.filter(gallery=gallery)
    return (
        sum(r.views for r in rows),
        sum(r.visitors for r in rows),
        sum(r.favourites for r in rows),
    )


class TestCounting:
    def test_a_guest_counts_one_visitor_however_often_they_reload(self, published):
        guest = Client()

        page(guest, published)
        page(guest, published)
        page(Client(), published)  # someone else

        assert totals(published)[:2] == (3, 2)

    def test_load_more_is_not_a_view(self, published):
        page(Client(), published, page=2, partial=1)

        assert totals(published) == (0, 0, 0)

    def test_owner_previews_are_not_counted(self, client: Client, published, photographer):
        client.force_login(photographer)

        page(client, published)

        assert totals(published) == (0, 0, 0)

    def test_favourites_count_hearts_net_of_removals(self, published):
        a, b = ready_photo(published), ready_photo(published)
        toggle = reverse("public-gallery-favourite-toggle", args=[published.public_token])
        guest = Client()

        guest.post(toggle, {"photo": str(a.pk)})
        guest.post(toggle, {"photo": str(b.pk)})
        guest.post(toggle, {"photo": str(a.pk)})  # un-heart

        assert totals(published)[2] == 1

    def test_visit_cookie_stays_on_this_gallerys_path(self, published):
        response = page(Client(), published)

        cookie = next(c for name, c in response.cookies.items() if name.startswith("fhv_"))
        assert cookie["path"] == f"/g/{published.public_token}/"
        assert cookie["httponly"]


class TestSummaryApi:
    def test_reports_visits_sales_and_conversion(self, auth_client, published):
        photo = ready_photo(published)
        for _ in range(4):
            page(Client(), published)
        make_order(published, [photo, ready_photo(published)])  # UGX 1,800
        make_order(published, [photo], currency="USD", unit=24)
        make_order(published, [photo], status=Order.Status.PENDING)  # not a sale

        data = auth_client.get(stats_url(published)).json()

        assert data["views"] == 4 and data["visitors"] == 4
        assert data["orders"] == 2 and data["photos_sold"] == 3
        assert data["gross"] == [
            {"currency": "UGX", "amount_minor": 1800, "formatted": "UGX 1,800"},
            {"currency": "USD", "amount_minor": 24, "formatted": "$0.24"},
        ]
        assert data["conversion_percent"] == 50.0

    def test_empty_gallery_has_no_conversion_yet(self, auth_client, published):
        data = auth_client.get(stats_url(published)).json()

        assert data["visitors"] == 0 and data["conversion_percent"] is None and data["gross"] == []

    def test_another_photographer_cannot_read_them(self, other_client, published):
        assert other_client.get(stats_url(published)).status_code == 404
