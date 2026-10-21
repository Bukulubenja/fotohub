"""Subscriptions: lifecycle, trial, enforcement, payments and the provider webhook."""

import datetime
import json

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.tests.factories import UserFactory
from billing import lifecycle, services
from billing.lifecycle import State
from billing.models import Payment, Plan
from billing.providers.base import Outcome
from galleries.models import Gallery
from galleries.tests.factories import EventFactory, GalleryFactory

pytestmark = pytest.mark.django_db
NOW = datetime.datetime(2026, 10, 21, 12, tzinfo=datetime.UTC)
DAY = datetime.timedelta(days=1)


def set_paid_until(user, when: datetime.datetime) -> None:
    services.subscription_for(user)
    user.subscription.paid_until = when
    user.subscription.save()


class TestLifecycle:
    @pytest.mark.parametrize(
        ("offset_days", "state", "can_upload", "live"),
        [
            (10, State.ACTIVE, True, True),
            (2, State.DUE, True, True),
            (-1, State.GRACE, False, True),
            (-8, State.LAPSED, False, True),
            (-38, State.LAPSED, False, False),
        ],
    )
    def test_states_follow_paid_until(self, offset_days, state, can_upload, live):
        standing = lifecycle.standing(NOW + offset_days * DAY, NOW)

        assert (standing.state, standing.can_upload, standing.galleries_live) == (
            state,
            can_upload,
            live,
        )

    def test_paying_early_extends_from_the_old_end(self):
        assert lifecycle.extended(NOW + 5 * DAY, NOW, 30) == NOW + 35 * DAY

    def test_paying_after_lapse_starts_today(self):
        assert lifecycle.extended(NOW - 20 * DAY, NOW, 30) == NOW + 30 * DAY


class TestTrial:
    def test_new_photographer_gets_a_trial_with_its_quota(self, settings):
        user = UserFactory()

        subscription = user.subscription
        assert subscription.plan.code == "trial"
        days = (subscription.paid_until - timezone.now()).days
        assert days == settings.BILLING["TRIAL_DAYS"] - 1
        user.profile.refresh_from_db()
        assert user.profile.storage_quota_bytes == subscription.plan.storage_quota_bytes

    def test_seeded_plans_are_offered(self, auth_client):
        codes = [p["code"] for p in auth_client.get(reverse("billing-plans")).data]

        assert codes == ["starter", "pro", "studio"]  # the trial isn't for sale


class TestEnforcement:
    def test_uploads_stop_in_grace(self, auth_client, photographer):
        gallery = GalleryFactory(event=EventFactory(owner=photographer))
        set_paid_until(photographer, timezone.now() - DAY)

        response = auth_client.post(
            reverse("upload-list"),
            {
                "gallery": str(gallery.pk),
                "filename": "a.jpg",
                "content_type": "image/jpeg",
                "size": 10,
            },
            format="json",
        )

        assert response.status_code == 409
        assert response.data["detail"].code == "subscription_inactive"

    def test_galleries_stay_live_in_grace(self, client: Client, photographer):
        gallery = GalleryFactory(
            event=EventFactory(owner=photographer), status=Gallery.Status.PUBLISHED
        )
        set_paid_until(photographer, timezone.now() - 2 * DAY)

        assert (
            client.get(reverse("public-gallery-page", args=[gallery.public_token])).status_code
            == 200
        )

    def test_long_lapsed_galleries_are_hidden_but_kept(self, client: Client, photographer):
        gallery = GalleryFactory(
            event=EventFactory(owner=photographer), status=Gallery.Status.PUBLISHED
        )
        set_paid_until(photographer, timezone.now() - 60 * DAY)

        page = client.get(reverse("public-gallery-page", args=[gallery.public_token]))
        api = client.get(reverse("public-gallery", args=[gallery.public_token]))

        assert page.status_code == 404
        assert "temporarily unavailable" in page.content.decode()
        assert api.status_code == 404
        assert Gallery.objects.filter(pk=gallery.pk).exists()

    def test_owner_can_still_preview_when_lapsed(self, client: Client, photographer):
        gallery = GalleryFactory(event=EventFactory(owner=photographer))
        set_paid_until(photographer, timezone.now() - 60 * DAY)
        client.force_login(photographer)

        assert (
            client.get(reverse("public-gallery-page", args=[gallery.public_token])).status_code
            == 200
        )


class TestPhone:
    @pytest.mark.parametrize(
        "raw", ["0772123456", "+256772123456", "256 772 123 456", "(0772) 123-456", "772123456"]
    )
    def test_ugandan_numbers_normalise(self, raw):
        assert services.normalise_phone(raw) == "256772123456"

    @pytest.mark.parametrize("raw", ["", "0412345678", "07721234", "+254712345678"])
    def test_other_numbers_are_refused(self, raw):
        with pytest.raises(services.PaymentRejected):
            services.normalise_phone(raw)


class TestPayments:
    def start(self, auth_client, plan="starter", periods=2, phone="0772123456"):
        return auth_client.post(
            reverse("payment-list"),
            {"plan": plan, "periods": periods, "phone": phone},
            format="json",
        )

    def test_start_prices_the_periods_and_prompts_the_phone(self, auth_client):
        response = self.start(auth_client)

        assert response.status_code == 201
        starter = Plan.objects.get(code="starter")
        assert response.data["payment"]["amount_ugx"] == starter.price_ugx * 2
        assert response.data["next"]["action"] == "prompt"
        assert Payment.objects.get().phone == "256772123456"

    def test_hidden_plan_cannot_be_bought(self, auth_client):
        assert self.start(auth_client, plan="trial").status_code == 400

    def test_success_extends_and_switches_plan_once(self, auth_client, photographer):
        self.start(auth_client)
        payment = Payment.objects.get()
        before = photographer.subscription.paid_until
        outcome = Outcome(payment.provider_reference, "succeeded", payment.amount_ugx, "UGX")

        services.apply_outcome(outcome)
        services.apply_outcome(outcome)  # the provider retries its webhook

        photographer.subscription.refresh_from_db()
        assert photographer.subscription.paid_until == before + 60 * DAY
        assert photographer.subscription.plan.code == "starter"
        photographer.profile.refresh_from_db()
        assert photographer.profile.storage_quota_bytes == payment.plan.storage_quota_bytes

    def test_wrong_amount_is_not_credited(self, auth_client, photographer):
        self.start(auth_client)
        payment = Payment.objects.get()
        before = photographer.subscription.paid_until

        services.apply_outcome(Outcome(payment.provider_reference, "succeeded", 1, "UGX"))

        payment.refresh_from_db()
        photographer.subscription.refresh_from_db()
        assert payment.status == Payment.Status.FAILED
        assert photographer.subscription.paid_until == before

    def test_polling_confirms_through_the_provider(self, auth_client, settings):
        settings.BILLING = {**settings.BILLING, "FAKE_AUTO_CONFIRM": True}
        payment_id = self.start(auth_client).data["payment"]["id"]

        response = auth_client.get(reverse("payment-detail", args=[payment_id]))

        assert response.data["status"] == "succeeded"

    def test_other_photographers_payments_are_invisible(self, auth_client, other_client):
        payment_id = self.start(auth_client).data["payment"]["id"]

        assert other_client.get(reverse("payment-detail", args=[payment_id])).status_code == 404

    def test_subscription_endpoint_reports_standing(self, auth_client, photographer):
        set_paid_until(photographer, timezone.now() - DAY)

        data = auth_client.get(reverse("billing-subscription")).data

        assert data["state"] == "grace"
        assert data["can_upload"] is False
        assert data["galleries_live"] is True


class TestWebhook:
    URL = "billing-webhook"

    def post(self, client: Client, body: dict, secret: str = "s3cret"):
        return client.post(
            reverse(self.URL, args=["fake"]),
            json.dumps(body),
            content_type="application/json",
            HTTP_X_FAKE_PROVIDER_SECRET=secret,
        )

    @pytest.fixture(autouse=True)
    def secret(self, settings):
        settings.BILLING = {**settings.BILLING, "FAKE_WEBHOOK_SECRET": "s3cret"}

    def test_valid_webhook_credits_the_payment(self, client: Client, auth_client):
        auth_client.post(
            reverse("payment-list"), {"plan": "pro", "phone": "0772123456"}, format="json"
        )
        payment = Payment.objects.get()

        response = self.post(
            client,
            {
                "reference": payment.provider_reference,
                "status": "succeeded",
                "amount_minor": payment.amount_ugx,
                "currency": "UGX",
            },
        )

        payment.refresh_from_db()
        assert response.status_code == 200
        assert payment.status == Payment.Status.SUCCEEDED

    def test_wrong_secret_is_rejected(self, client: Client):
        assert self.post(client, {"reference": "x"}, secret="guess").status_code == 400

    def test_unknown_reference_is_404(self, client: Client):
        body = {"reference": "nope", "status": "succeeded", "amount_minor": 1, "currency": "UGX"}

        assert self.post(client, body).status_code == 404

    def test_unknown_provider_is_404(self, client: Client):
        response = client.post(
            reverse(self.URL, args=["paypal"]), "{}", content_type="application/json"
        )

        assert response.status_code == 404
