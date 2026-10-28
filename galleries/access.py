"""Customer access policy: which visitors may see a gallery's photos (spec FR-20, FR-25).

Unlocks live in the server-side session, keyed by gallery id and pinned to the gallery's
`access_version`, so changing the password or rotating the link re-locks every visitor.
A collection the visitor has opened with its passkey also unlocks its galleries
(`galleries.collections`), except events the photographer keeps separate.
"""

from django.http import HttpRequest

from galleries import collections
from galleries.models import Gallery

SESSION_KEY = "unlocked_galleries"


def is_unlocked(request: HttpRequest, gallery: Gallery) -> bool:
    if not gallery.has_password:
        return True
    unlocked = request.session.get(SESSION_KEY, {})
    if unlocked.get(str(gallery.pk)) == gallery.access_version:
        return True
    return collections.opens_gallery(request, gallery)


def mark_unlocked(request: HttpRequest, gallery: Gallery) -> None:
    request.session.cycle_key()  # new session id on privilege change
    unlocked = {**request.session.get(SESSION_KEY, {}), str(gallery.pk): gallery.access_version}
    request.session[SESSION_KEY] = unlocked
