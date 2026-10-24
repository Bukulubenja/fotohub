from django.conf import settings

from billing.providers.base import PaymentProvider
from billing.providers.fake import FakeProvider
from billing.providers.flutterwave import FlutterwaveProvider

PROVIDERS: dict[str, type] = {
    FakeProvider.name: FakeProvider,
    FlutterwaveProvider.name: FlutterwaveProvider,
}


def get_provider(name: str | None = None) -> PaymentProvider:
    name = name or settings.BILLING["PROVIDER"]
    try:
        return PROVIDERS[name]()
    except KeyError as exc:
        raise LookupError(f"Unknown payment provider {name!r}") from exc
