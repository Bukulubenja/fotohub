"""Flutterwave v3: Standard (hosted) checkout for UGX mobile money and USD cards.

Hosted checkout rather than direct charges: Flutterwave's page handles the MTN/Airtel prompt
and card 3-D Secure, and no direct-charge approval is needed.

- start:   POST /payments -> a link to Flutterwave's page (payment_options picks the method).
- check:   GET /transactions/verify_by_reference?tx_ref=... (our own reference).
- webhook: the `verif-hash` header must equal our secret hash; the body is then ignored and
           the transaction re-verified through the API before anything is credited.

Docs: developer.flutterwave.com (Flutterwave Standard, Transaction Verification, Webhooks).
"""

import hmac
import json
import logging
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from decimal import Decimal

from django.conf import settings
from django.http import HttpRequest

from billing.providers.base import Charge, Checkout, Outcome, ProviderError, WebhookRejected

logger = logging.getLogger(__name__)

HASH_HEADER = "HTTP_VERIF_HASH"
TIMEOUT_SECONDS = 20
PAYMENT_OPTIONS = {"UGX": "mobilemoneyuganda", "USD": "card"}
MINOR_UNITS = {"UGX": 1, "USD": 100}  # UGX has no minor unit
STATUSES = {"successful": "succeeded", "failed": "failed", "cancelled": "failed"}
UNAVAILABLE = "Payments are unavailable right now. Try again in a few minutes."

# (method, url, headers, body) -> (HTTP status, parsed JSON or {})
Http = Callable[[str, str, dict[str, str], dict | None], tuple[int, dict]]


def urllib_http(method: str, url: str, headers: dict[str, str], body: dict | None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except ValueError:
            return exc.code, {}
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise ProviderError(UNAVAILABLE) from exc


def to_major(amount_minor: int, currency: str) -> int | float:
    major = Decimal(amount_minor) / MINOR_UNITS[currency]
    return int(major) if major == major.to_integral() else float(major)


def to_minor(amount, currency: str) -> int:
    return int((Decimal(str(amount)) * MINOR_UNITS[currency]).to_integral_value())


class FlutterwaveProvider:
    name = "flutterwave"

    def __init__(self, http: Http = urllib_http) -> None:
        conf = settings.FLUTTERWAVE
        self.base = conf["BASE_URL"].rstrip("/")
        self.secret_key = conf["SECRET_KEY"]
        self.secret_hash = conf["SECRET_HASH"]
        self.http = http

    def _call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        headers = {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        return self.http(method, self.base + path, headers, body)

    def start(self, charge: Charge) -> Checkout:
        if charge.currency not in PAYMENT_OPTIONS:
            raise ProviderError(f"{charge.currency} payments aren't supported.")
        reference = f"fh-{uuid.uuid4().hex}"
        status, body = self._call(
            "POST",
            "/payments",
            {
                "tx_ref": reference,
                "amount": to_major(charge.amount_minor, charge.currency),
                "currency": charge.currency,
                "redirect_url": charge.return_url,
                "payment_options": PAYMENT_OPTIONS[charge.currency],
                "customer": {"email": charge.email, "phonenumber": charge.phone},
                "customizations": {
                    "title": settings.BRAND["NAME"],
                    "description": charge.description,
                },
            },
        )
        link = (body.get("data") or {}).get("link")
        if status != 200 or body.get("status") != "success" or not link:
            logger.error("flutterwave refused payment %s: %s %s", reference, status, body)
            raise ProviderError(UNAVAILABLE)
        return Checkout(reference, "redirect", redirect_url=link)

    def check(self, reference: str) -> Outcome:
        status, body = self._call(
            "GET", f"/transactions/verify_by_reference?tx_ref={urllib.request.quote(reference)}"
        )
        data = body.get("data") or {}
        if status != 200 or body.get("status") != "success" or data.get("tx_ref") != reference:
            # Not found yet (customer still on the page, or never paid): keep waiting.
            return Outcome(reference, "pending")
        currency = str(data.get("currency", ""))
        verdict = STATUSES.get(str(data.get("status", "")).lower(), "pending")
        amount = data.get("amount")
        return Outcome(
            reference=reference,
            status=verdict,
            amount_minor=to_minor(amount, currency) if currency in MINOR_UNITS else None,
            currency=currency,
            reason="" if verdict != "failed" else "The payment was declined or cancelled.",
        )

    def parse_webhook(self, request: HttpRequest) -> Outcome:
        given = request.META.get(HASH_HEADER, "")
        if not self.secret_hash or not hmac.compare_digest(given, self.secret_hash):
            raise WebhookRejected("bad verif-hash")
        try:
            reference = str(json.loads(request.body)["data"]["tx_ref"])
        except (ValueError, KeyError, TypeError) as exc:
            raise WebhookRejected("malformed body") from exc
        # Never credit from the webhook body: ask Flutterwave what really happened.
        return self.check(reference)
