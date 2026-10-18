from .base import *  # noqa: F403
from .base import REST_FRAMEWORK

SECRET_KEY = "test-only-key"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Throttling is tested explicitly; keep it out of the way elsewhere.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": {
        "auth": "1000/min",
        "gallery_unlock": "1000/min",
        "gallery_unlock_global": "1000/min",
    },
}
