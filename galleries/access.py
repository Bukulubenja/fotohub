"""Customer access policy: which visitors may see a gallery's photos (spec FR-20, FR-25).

Unlocks live in the server-side session, keyed by gallery id and pinned to the gallery's
`access_version`, so changing the password or rotating the link re-locks every visitor.
"""

from django.http import HttpRequest

from galleries.models import Gallery

SESSION_KEY = "unlocked_galleries"


def is_unlocked(request: HttpRequest, gallery: Gallery) -> bool:
    if not gallery.has_password:
        return True
    unlocked = request.session.get(SESSION_KEY, {})
    return unlocked.get(str(gallery.pk)) == gallery.access_version


def mark_unlocked(request: HttpRequest, gallery: Gallery) -> None:
    request.session.cycle_key()  # new session id on privilege change
    unlocked = {**request.session.get(SESSION_KEY, {}), str(gallery.pk): gallery.access_version}
    request.session[SESSION_KEY] = unlocked
