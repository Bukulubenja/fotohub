"""Values every customer page can show."""

from django.conf import settings
from django.http import HttpRequest


def support(request: HttpRequest) -> dict:
    return {"support_email": settings.SUPPORT_EMAIL}


def brand(request: HttpRequest) -> dict:
    return {"brand_name": settings.BRAND["NAME"], "brand_tagline": settings.BRAND["TAGLINE"]}
