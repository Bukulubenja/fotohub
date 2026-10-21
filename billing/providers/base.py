"""The seam between billing and a payment aggregator (plan §3.5).

pawaPay, Flutterwave, Pesapal and direct MTN/Airtel each get one adapter implementing this.
Billing never imports an aggregator SDK, so switching provider touches only that adapter.
"""

from dataclasses import dataclass
from typing import Protocol

from django.http import HttpRequest


class ProviderError(Exception):
    """The provider refused or couldn't be reached; the message is safe to show."""


class WebhookRejected(Exception):
    """A webhook failed verification (bad signature, unknown payment): answer 400."""


@dataclass(frozen=True)
class Checkout:
    """What the photographer does next. Mobile money is a USSD prompt on their phone."""

    reference: str
    action: str  # "prompt" (approve on phone) or "redirect" (card page)
    message: str = ""
    redirect_url: str = ""


@dataclass(frozen=True)
class Outcome:
    """The provider's verdict on one charge, from a webhook or a status check."""

    reference: str
    status: str  # pending, succeeded or failed
    # Smallest currency unit: shillings for UGX (no minor unit), cents for USD.
    amount_minor: int | None = None
    currency: str = "UGX"
    reason: str = ""


@dataclass(frozen=True)
class Charge:
    """One collection request: a subscription renewal or a photo order."""

    amount_minor: int
    currency: str  # "UGX" (mobile money) or "USD" (card)
    phone: str = ""  # for mobile money
    email: str = ""  # receipt, and card checkout
    description: str = ""
    return_url: str = ""  # where a card checkout sends the buyer back


class PaymentProvider(Protocol):
    name: str

    def start(self, charge: Charge) -> Checkout:
        """Ask the provider to collect the charge."""

    def check(self, reference: str) -> Outcome:
        """Ask the provider for the charge's current status (used when a webhook is late)."""

    def parse_webhook(self, request: HttpRequest) -> Outcome:
        """Verify the webhook's signature and read it. Raise WebhookRejected if it fails."""
