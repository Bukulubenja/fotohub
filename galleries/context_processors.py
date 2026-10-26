"""Values every customer page can show."""

from django.conf import settings
from django.http import HttpRequest


def support(request: HttpRequest) -> dict:
    return {"support_email": settings.SUPPORT_EMAIL}
