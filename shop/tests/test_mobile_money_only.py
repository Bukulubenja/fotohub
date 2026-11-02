"""The school takes payment by mobile money only (owner, 2026-11-02): no card option anywhere,
and a forged card checkout is refused."""

import pytest
from django.test import Client
from django.urls import reverse

from assets.tests.factories import ready_photo
from galleries.models import Gallery
from galleries.tests.factories import EventFactory, GalleryFactory
from shop.models import Order
from shop.tests.test_shop import add, pay, url

pytestmark = pytest.mark.django_db


@pytest.fixture
def published(photographer):
    return GalleryFactory(event=EventFactory(owner=photographer), status=Gallery.Status.PUBLISHED)


@pytest.fixture(autouse=True)
def cards_off(settings):
    settings.SHOP = {**settings.SHOP, "CARD_PAYMENTS": False}


def test_cart_offers_only_mobile_money(client: Client, published):
    add(client, published, ready_photo(published))

    html = client.get(url("shop-cart", published)).content.decode()

    assert "Mobile money" in html and 'name="phone"' in html
    assert "Card" not in html and "$" not in html
    assert '<input type="hidden" name="currency" value="UGX">' in html


def test_gallery_and_terms_mention_no_card_price(client: Client, published):
    ready_photo(published)

    gallery = client.get(reverse("public-gallery-page", args=[published.public_token]))
    terms = client.get(reverse("purchase-terms"))

    for html in (gallery.content.decode(), terms.content.decode()):
        assert "by card" not in html and "$0." not in html
    assert "mobile money" in terms.content.decode()


def test_a_forged_card_checkout_is_refused(client: Client, published):
    add(client, published, ready_photo(published))

    response = pay(client, published, currency="USD", phone="")

    assert response.status_code == 400
    assert not Order.objects.exists()


def test_mobile_money_checkout_still_works(client: Client, published):
    add(client, published, ready_photo(published))

    assert pay(client, published).status_code == 303
    assert Order.objects.get().currency == "UGX"
