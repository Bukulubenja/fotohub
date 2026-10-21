"""Photo sales: cart, checkout in UGX and USD, payment outcomes, and the buyer's order page."""

import json
from urllib.parse import parse_qs, urlparse

import pytest
from django.core import mail
from django.test import Client
from django.urls import reverse

from assets.models import Photo
from assets.tests.factories import PhotoFactory, ready_photo
from billing.providers.base import Outcome
from delivery.models import DownloadJob
from galleries.models import Gallery
from galleries.tests.factories import EventFactory, GalleryFactory
from shop import services
from shop.models import Order
from shop.pricing import format_money, price
from shop.tests.factories import make_order

pytestmark = pytest.mark.django_db
JSON = {"HTTP_ACCEPT": "application/json"}


@pytest.fixture
def published(photographer):
    return GalleryFactory(
        event=EventFactory(owner=photographer, title="Amina & Daniel"),
        status=Gallery.Status.PUBLISHED,
    )


def url(name: str, gallery: Gallery) -> str:
    return reverse(name, args=[gallery.public_token])


def add(client: Client, gallery: Gallery, *photos: Photo):
    return client.post(
        url("shop-cart-add", gallery), {"photo": [str(p.pk) for p in photos]}, **JSON
    )


def pay(client: Client, gallery: Gallery, **form):
    data = {"currency": "UGX", "email": "guest@example.com", "phone": "0772123456", **form}
    return client.post(url("shop-checkout", gallery), data)


class TestPricing:
    def test_fixed_prices_in_both_currencies(self):
        assert price("USD").total(10) == 240
        assert format_money("USD", 240) == "$2.40"
        assert format_money("UGX", price("UGX").total(10)) == "UGX 9,000"


class TestCart:
    def test_add_returns_count_and_totals(self, client: Client, published):
        a, b = ready_photo(published), ready_photo(published)

        data = add(client, published, a, b).json()

        assert data == {"count": 2, "totals": {"UGX": "UGX 1,800", "USD": "$0.48"}}

    def test_folder_adds_and_sums_the_whole_gallery(self, client: Client, published):
        for _ in range(5):
            ready_photo(published)
        PhotoFactory(gallery=published, status=Photo.Status.PROCESSING)  # not for sale yet

        data = client.post(url("shop-cart-add-all", published), **JSON).json()

        assert data["count"] == 5
        assert data["totals"] == {"UGX": "UGX 4,500", "USD": "$1.20"}

    def test_photos_from_other_galleries_are_ignored(self, client: Client, published):
        foreign = ready_photo(GalleryFactory(status=Gallery.Status.PUBLISHED))

        assert add(client, published, foreign).json()["count"] == 0

    def test_bought_photos_are_not_sold_twice(self, client: Client, published):
        bought = ready_photo(published)
        ready_photo(published)  # still for sale
        make_order(published, [bought], client=client)

        data = client.post(url("shop-cart-add-all", published), **JSON).json()

        assert data["count"] == 1
        assert data["totals"]["UGX"] == "UGX 900"

    def test_remove(self, client: Client, published):
        a, b = ready_photo(published), ready_photo(published)
        add(client, published, a, b)

        data = client.post(url("shop-cart-remove", published), {"photo": str(a.pk)}, **JSON).json()

        assert data["count"] == 1

    def test_without_javascript_the_form_lands_on_the_cart(self, client: Client, published):
        photo = ready_photo(published)

        response = client.post(url("shop-cart-add", published), {"photo": str(photo.pk)})

        assert response.status_code == 303
        assert response["Location"] == url("shop-cart", published)
        html = client.get(response["Location"]).content.decode()
        assert "1 photo from Amina &amp; Daniel" in html
        assert "UGX 900" in html and "$0.24" in html

    def test_locked_gallery_cart_is_closed(self, client: Client, published):
        published.set_access_password("secret-pass")
        published.save()

        response = add(client, published, ready_photo(published))

        assert response.status_code == 302


class TestCheckout:
    def test_mobile_money_order_prompts_the_phone(self, client: Client, published):
        add(client, published, ready_photo(published), ready_photo(published))

        response = pay(client, published)

        order = Order.objects.get()
        assert response.status_code == 303
        assert response["Location"] == reverse("shop-order", args=[order.token])
        assert (order.currency, order.amount_minor, order.quantity) == ("UGX", 1800, 2)
        assert order.phone == "256772123456"
        assert order.items.count() == 2
        assert (
            "Approve UGX 1,800 on 256772123456" in client.get(response["Location"]).content.decode()
        )

    def test_card_order_charges_usd_and_goes_to_the_card_page(self, client: Client, published):
        add(client, published, *(ready_photo(published) for _ in range(3)))

        response = pay(client, published, currency="USD", phone="")

        order = Order.objects.get()
        assert (order.currency, order.unit_price_minor, order.amount_minor) == ("USD", 24, 72)
        assert response.status_code == 302  # the fake provider's "card page" is the order page
        assert response["Location"].endswith(reverse("shop-order", args=[order.token]))

    def test_mobile_money_needs_a_ugandan_number(self, client: Client, published):
        add(client, published, ready_photo(published))

        response = pay(client, published, phone="12345")

        assert response.status_code == 400
        assert "MTN or Airtel" in response.content.decode()
        assert not Order.objects.exists()

    def test_email_is_required(self, client: Client, published):
        add(client, published, ready_photo(published))

        assert pay(client, published, email="nope").status_code == 400

    def test_empty_cart_cannot_check_out(self, client: Client, published):
        assert pay(client, published).status_code == 400

    def test_checkout_requires_csrf(self, published):
        strict = Client(enforce_csrf_checks=True)
        add(strict, published, ready_photo(published))

        assert pay(strict, published).status_code == 403
        assert not Order.objects.exists()

    def test_checkouts_are_throttled(self, client: Client, published, strict_throttle):
        add(client, published, ready_photo(published))
        for _ in range(2):
            pay(client, published)

        assert pay(client, published).status_code == 429


class TestPaymentOutcome:
    @pytest.fixture
    def pending(self, client: Client, published) -> Order:
        add(client, published, ready_photo(published), ready_photo(published))
        pay(client, published)
        return Order.objects.get()

    def test_success_marks_paid_and_emails_the_link_once(
        self, pending, django_capture_on_commit_callbacks
    ):
        outcome = Outcome(pending.provider_reference, "succeeded", 1800, "UGX")

        with django_capture_on_commit_callbacks(execute=True):
            services.apply_outcome(outcome)
            services.apply_outcome(outcome)  # webhook retried

        pending.refresh_from_db()
        assert pending.status == Order.Status.PAID
        assert len(mail.outbox) == 1
        assert f"/orders/{pending.token}/" in mail.outbox[0].body
        assert mail.outbox[0].to == ["guest@example.com"]

    @pytest.mark.parametrize(("amount", "currency"), [(900, "UGX"), (1800, "USD")])
    def test_wrong_amount_or_currency_is_not_paid(self, pending, amount, currency):
        services.apply_outcome(Outcome(pending.provider_reference, "succeeded", amount, currency))

        pending.refresh_from_db()
        assert pending.status == Order.Status.FAILED

    def test_order_page_polls_the_provider(self, client: Client, pending, settings):
        settings.BILLING = {**settings.BILLING, "FAKE_AUTO_CONFIRM": True}

        html = client.get(reverse("shop-order", args=[pending.token])).content.decode()

        assert "Your photos" in html
        assert "Download original" in html

    def test_webhook_settles_photo_orders_too(self, client: Client, pending, settings):
        settings.BILLING = {**settings.BILLING, "FAKE_WEBHOOK_SECRET": "s3cret"}

        response = client.post(
            reverse("billing-webhook", args=["fake"]),
            json.dumps(
                {
                    "reference": pending.provider_reference,
                    "status": "succeeded",
                    "amount_minor": 1800,
                    "currency": "UGX",
                }
            ),
            content_type="application/json",
            HTTP_X_FAKE_PROVIDER_SECRET="s3cret",
        )

        pending.refresh_from_db()
        assert response.status_code == 200
        assert pending.status == Order.Status.PAID

    def test_failed_payment_explains_and_offers_the_cart(self, client: Client, pending):
        services.apply_outcome(Outcome(pending.provider_reference, "failed", reason="Declined"))

        html = client.get(reverse("shop-order", args=[pending.token])).content.decode()

        assert "didn't go through" in html  # template text, not an escaped variable
        assert "Declined" in html


class TestOrderPage:
    def test_link_works_in_any_browser_and_unlocks_the_gallery(self, published):
        photo = ready_photo(published)
        order = make_order(published, [photo])
        phone = Client()

        page = phone.get(reverse("shop-order", args=[order.token]))
        gallery_html = phone.get(url("public-gallery-page", published)).content.decode()

        assert page.status_code == 200
        assert (
            reverse("public-photo-download", args=[published.public_token, photo.pk])
            in gallery_html
        )

    def test_download_serves_the_original(self, client: Client, published):
        photo = ready_photo(published, original_filename="IMG_9.jpg")
        order = make_order(published, [photo])

        response = client.get(reverse("shop-order-photo", args=[order.token, photo.pk]))

        assert response.status_code == 302
        assert urlparse(response["Location"]).path.endswith(photo.original_key)

    def test_photo_not_in_the_order_is_404(self, client: Client, published):
        order = make_order(published, [ready_photo(published)])
        other = ready_photo(published)

        assert (
            client.get(reverse("shop-order-photo", args=[order.token, other.pk])).status_code == 404
        )

    def test_bought_photos_stay_downloadable_after_unpublishing(self, client: Client, published):
        photo = ready_photo(published)
        order = make_order(published, [photo])
        published.status = Gallery.Status.DRAFT
        published.save()

        response = client.get(reverse("shop-order-photo", args=[order.token, photo.pk]))

        assert response.status_code == 302

    def test_unknown_order_is_404(self, client: Client):
        assert client.get(reverse("shop-order", args=["nope"])).status_code == 404

    def test_order_zip_flow(
        self, client: Client, storage, published, django_capture_on_commit_callbacks
    ):
        photo = ready_photo(published)
        storage.put_bytes(photo.original_key, b"raw", "image/jpeg")
        order = make_order(published, [photo])

        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(reverse("shop-order-archive-request", args=[order.token]))

        assert response.status_code == 303
        assert "Your ZIP is ready" in client.get(response["Location"]).content.decode()
        job = DownloadJob.objects.get()
        file_response = client.get(reverse("shop-order-archive-file", args=[order.token, job.pk]))
        disposition = parse_qs(urlparse(file_response["Location"]).query)[
            "response-content-disposition"
        ][0]
        assert 'filename="Amina & Daniel - 1 photos.zip"' in disposition

    def test_job_of_another_order_is_404(self, client: Client, published):
        mine = make_order(published, [ready_photo(published)])
        theirs = make_order(published, [ready_photo(published)])
        job = DownloadJob.objects.create(
            gallery=published,
            order=theirs,
            policy="original",
            fingerprint="x",
            photo_count=1,
            expected_bytes=1,
        )

        response = client.get(reverse("shop-order-archive", args=[mine.token, job.pk]))

        assert response.status_code == 404
