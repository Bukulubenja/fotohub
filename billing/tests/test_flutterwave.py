"""Flutterwave adapter against a recorded fake of the v3 API (no network)."""

import json

import pytest
from django.test import Client, RequestFactory
from django.urls import reverse

from assets.tests.factories import ready_photo
from billing.providers import flutterwave
from billing.providers.base import Charge, ProviderError, WebhookRejected
from billing.providers.flutterwave import FlutterwaveProvider, to_major, to_minor
from galleries.models import Gallery
from galleries.tests.factories import EventFactory, GalleryFactory
from shop.models import Order

pytestmark = pytest.mark.django_db
HASH = "my-secret-hash"


class FakeFlutterwave:
    """Records requests; answers like the v3 API for /payments and verify_by_reference."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict, dict | None]] = []
        self.transactions: dict[str, dict] = {}
        self.refuse = False

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if url.endswith("/payments"):
            if self.refuse:
                return 400, {"status": "error", "message": "Invalid currency"}
            return 200, {
                "status": "success",
                "data": {"link": "https://checkout.flutterwave.com/pay/abc"},
            }
        if "/transactions/verify_by_reference?tx_ref=" in url:
            reference = url.split("tx_ref=")[1]
            tx = self.transactions.get(reference)
            if tx is None:
                return 404, {"status": "error", "message": "No transaction was found"}
            return 200, {"status": "success", "data": {"tx_ref": reference, **tx}}
        return 404, {}


@pytest.fixture
def fw(settings, monkeypatch):
    settings.FLUTTERWAVE = {
        "SECRET_KEY": "FLWSECK_TEST-123",
        "SECRET_HASH": HASH,
        "BASE_URL": "https://api.flutterwave.com/v3",
    }
    settings.BILLING = {**settings.BILLING, "PROVIDER": "flutterwave"}
    fake = FakeFlutterwave()
    monkeypatch.setattr(flutterwave, "urllib_http", fake)
    monkeypatch.setattr(FlutterwaveProvider.__init__, "__defaults__", (fake,))
    return fake


def charge(currency="UGX", amount=2700) -> Charge:
    return Charge(
        amount_minor=amount,
        currency=currency,
        phone="256772123456",
        email="guest@example.com",
        description="3 photos",
        return_url="https://fotohub.ug/orders/tok/",
    )


class TestAmounts:
    def test_conversions(self):
        assert to_major(2700, "UGX") == 2700
        assert to_major(72, "USD") == 0.72
        assert to_minor(0.72, "USD") == 72
        assert to_minor("2700.00", "UGX") == 2700


class TestStart:
    def test_ugx_goes_to_mobile_money_on_the_hosted_page(self, fw):
        checkout = FlutterwaveProvider().start(charge())

        method, url, headers, body = fw.calls[0]
        assert (method, url) == ("POST", "https://api.flutterwave.com/v3/payments")
        assert headers["Authorization"] == "Bearer FLWSECK_TEST-123"
        assert body["payment_options"] == "mobilemoneyuganda"
        assert (body["amount"], body["currency"]) == (2700, "UGX")
        assert body["redirect_url"] == "https://fotohub.ug/orders/tok/"
        assert body["customer"] == {"email": "guest@example.com", "phonenumber": "256772123456"}
        assert checkout.action == "redirect"
        assert checkout.redirect_url == "https://checkout.flutterwave.com/pay/abc"
        assert checkout.reference == body["tx_ref"]

    def test_usd_goes_to_card_in_dollars(self, fw):
        FlutterwaveProvider().start(charge("USD", 72))

        body = fw.calls[0][3]
        assert (body["payment_options"], body["amount"], body["currency"]) == ("card", 0.72, "USD")

    def test_refusal_is_a_provider_error(self, fw):
        fw.refuse = True

        with pytest.raises(ProviderError, match="unavailable"):
            FlutterwaveProvider().start(charge())


class TestCheck:
    @pytest.mark.parametrize(
        ("flw_status", "verdict"),
        [
            ("successful", "succeeded"),
            ("failed", "failed"),
            ("cancelled", "failed"),
            ("pending", "pending"),
        ],
    )
    def test_status_mapping(self, fw, flw_status, verdict):
        fw.transactions["fh-1"] = {"status": flw_status, "amount": 0.72, "currency": "USD"}

        outcome = FlutterwaveProvider().check("fh-1")

        assert outcome.status == verdict
        assert (outcome.amount_minor, outcome.currency) == (72, "USD")

    def test_unknown_transaction_is_still_pending(self, fw):
        assert FlutterwaveProvider().check("fh-missing").status == "pending"


class TestWebhook:
    def request(self, body: dict, hash_: str = HASH):
        return RequestFactory().post(
            "/hook", json.dumps(body), content_type="application/json", HTTP_VERIF_HASH=hash_
        )

    def test_wrong_hash_is_rejected(self, fw):
        with pytest.raises(WebhookRejected):
            FlutterwaveProvider().parse_webhook(self.request({"data": {}}, "guess"))

    def test_body_is_never_trusted(self, fw):
        """The webhook claims success; the API says it failed. The API wins."""
        fw.transactions["fh-1"] = {"status": "failed", "amount": 2700, "currency": "UGX"}
        body = {"event": "charge.completed", "data": {"tx_ref": "fh-1", "status": "successful"}}

        outcome = FlutterwaveProvider().parse_webhook(self.request(body))

        assert outcome.status == "failed"


class TestEndToEnd:
    @pytest.fixture
    def published(self, photographer):
        return GalleryFactory(
            event=EventFactory(owner=photographer, title="Amina & Daniel"),
            status=Gallery.Status.PUBLISHED,
        )

    def test_photo_order_through_flutterwave(self, fw, client: Client, published):
        photos = [ready_photo(published), ready_photo(published)]
        client.post(
            reverse("shop-cart-add", args=[published.public_token]),
            {"photo": [str(p.pk) for p in photos]},
        )

        response = client.post(
            reverse("shop-checkout", args=[published.public_token]),
            {"currency": "UGX", "email": "guest@example.com", "phone": "0772123456"},
        )

        order = Order.objects.get()
        assert response["Location"] == "https://checkout.flutterwave.com/pay/abc"
        # Flutterwave confirms by webhook; we re-verify and credit.
        fw.transactions[order.provider_reference] = {
            "status": "successful",
            "amount": 1800,
            "currency": "UGX",
        }
        hook = client.post(
            reverse("billing-webhook", args=["flutterwave"]),
            json.dumps({"event": "charge.completed", "data": {"tx_ref": order.provider_reference}}),
            content_type="application/json",
            HTTP_VERIF_HASH=HASH,
        )
        order.refresh_from_db()
        assert hook.status_code == 200
        assert order.status == Order.Status.PAID

    def test_cancelling_on_flutterwave_marks_the_order(self, fw, client: Client, published):
        client.post(
            reverse("shop-cart-add", args=[published.public_token]),
            {"photo": str(ready_photo(published).pk)},
        )
        client.post(
            reverse("shop-checkout", args=[published.public_token]),
            {"currency": "USD", "email": "guest@example.com"},
        )
        order = Order.objects.get()

        html = client.get(
            reverse("shop-order", args=[order.token]), {"status": "cancelled"}
        ).content.decode()

        order.refresh_from_db()
        assert order.status == Order.Status.FAILED
        assert "You cancelled the payment" in html

    def test_a_paid_order_is_not_cancelled_by_the_return_url(self, fw, client: Client, published):
        client.post(
            reverse("shop-cart-add", args=[published.public_token]),
            {"photo": str(ready_photo(published).pk)},
        )
        client.post(
            reverse("shop-checkout", args=[published.public_token]),
            {"currency": "USD", "email": "guest@example.com"},
        )
        order = Order.objects.get()
        fw.transactions[order.provider_reference] = {
            "status": "successful",
            "amount": 0.24,
            "currency": "USD",
        }

        client.get(reverse("shop-order", args=[order.token]), {"status": "cancelled"})

        order.refresh_from_db()
        assert order.status == Order.Status.PAID
