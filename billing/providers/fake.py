"""Development and test provider: no money moves.

The webhook needs the shared secret from BILLING["FAKE_WEBHOOK_SECRET"], so it can't be used
to mark payments paid unless the secret is configured. Never enable it in production.
"""

import hmac
import json
import uuid

from django.conf import settings
from django.http import HttpRequest

from billing.providers.base import Charge, Checkout, Outcome, WebhookRejected

SECRET_HEADER = "HTTP_X_FAKE_PROVIDER_SECRET"
# What each test charge asked for, so check() can report the right amount (process-local).
_charges: dict[str, Charge] = {}


class FakeProvider:
    name = "fake"

    def start(self, charge: Charge) -> Checkout:
        reference = f"fake-{uuid.uuid4().hex}"
        _charges[reference] = charge
        if charge.currency == "USD":
            # A real card provider returns its hosted page; the fake one goes straight back.
            return Checkout(reference, "redirect", redirect_url=charge.return_url)
        return Checkout(
            reference,
            "prompt",
            message=f"Approve {charge.currency} {charge.amount_minor:,} on {charge.phone} (test).",
        )

    def check(self, reference: str) -> Outcome:
        charge = _charges.get(reference)
        if settings.BILLING.get("FAKE_AUTO_CONFIRM") and charge is not None:
            return Outcome(reference, "succeeded", charge.amount_minor, charge.currency)
        return Outcome(reference, "pending")

    def parse_webhook(self, request: HttpRequest) -> Outcome:
        secret = settings.BILLING.get("FAKE_WEBHOOK_SECRET") or ""
        given = request.META.get(SECRET_HEADER, "")
        if not secret or not hmac.compare_digest(given, secret):
            raise WebhookRejected("bad secret")
        try:
            body = json.loads(request.body)
            return Outcome(
                reference=str(body["reference"]),
                status=str(body["status"]),
                amount_minor=int(body["amount_minor"]),
                currency=str(body["currency"]),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise WebhookRejected("malformed body") from exc
