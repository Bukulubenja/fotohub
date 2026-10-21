"""One fixed price per photo, in both currencies (shop pricing decision, 2026-10-21).

USD 0.24 by card; a fixed shilling price for mobile money, set in SHOP["PRICE_UGX"] and
updated by hand when the exchange rate moves.
"""

from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True)
class Price:
    currency: str
    unit_minor: int

    def total(self, quantity: int) -> int:
        return self.unit_minor * quantity


def price(currency: str) -> Price:
    if currency == "UGX":
        return Price("UGX", settings.SHOP["PRICE_UGX"])
    if currency == "USD":
        return Price("USD", settings.SHOP["PRICE_USD_CENTS"])
    raise ValueError(f"unsupported currency {currency!r}")


def format_money(currency: str, amount_minor: int) -> str:
    """UGX 9,000 / $2.40."""
    if currency == "USD":
        return f"${amount_minor / 100:,.2f}"
    return f"UGX {amount_minor:,}"
