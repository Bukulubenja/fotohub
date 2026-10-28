"""Gallery links and ready-to-send messages (spec FR-24). WhatsApp is the main channel.

Messages never contain the gallery password: the photographer sends it separately. A
collection's passkey is the exception: sharing it once with the group is the whole point.
"""

from urllib.parse import quote, urlencode

from django.conf import settings
from django.http import HttpRequest
from django.urls import reverse

from galleries.models import Collection, Gallery

WHATSAPP_SHARE = "https://wa.me/?text="


def share_url(request: HttpRequest, gallery: Gallery) -> str:
    return request.build_absolute_uri(reverse("public-gallery-page", args=[gallery.public_token]))


def photographer_message(gallery: Gallery, url: str) -> str:
    """What the photographer sends their client."""
    event = gallery.event
    date = f"{event.event_date.day} {event.event_date:%B %Y}"  # no %-d: not portable
    lines = [
        f"Hi! Your photos from {event.title} ({date}) are ready to view and download:",
        url,
    ]
    if gallery.has_password:
        lines.append("You'll need the gallery password I'll send you separately.")
    lines.append(f"— {event.owner.profile.display_name}")
    return "\n".join(lines)


def guest_message(gallery: Gallery, url: str) -> str:
    """What a guest sends a friend from the gallery page."""
    event = gallery.event
    return f"Photos from {event.title} by {event.owner.profile.display_name}: {url}"


def whatsapp_link(message: str) -> str:
    return WHATSAPP_SHARE + quote(message, safe="")


def home_url(request: HttpRequest) -> str:
    return request.build_absolute_uri(reverse("home"))


def collection_qr_url(request: HttpRequest, collection: Collection) -> str:
    """Where the printed QR code points: the home page with the passkey filled in."""
    return f"{home_url(request)}?{urlencode({'k': collection.passkey})}"


def collection_message(collection: Collection, url: str) -> str:
    """What the photographer sends the group (a school's parents) once."""
    return "\n".join(
        [
            f"Photos from {collection.name} are on {settings.BRAND['NAME']}.",
            f"Open {url} and enter the passkey {collection.passkey}",
            "Every new event appears there, and your phone remembers the passkey.",
            f"\u2014 {collection.owner.profile.display_name}",
        ]
    )
