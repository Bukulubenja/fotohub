from .base import *  # noqa: F403
from .base import BILLING, REST_FRAMEWORK, env

SECRET_KEY = "test-only-key"
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
OBJECT_STORAGE = {
    "BUCKET": "fotohub-test",
    "ENDPOINT_URL": None,
    "REGION": "us-east-1",
    "ACCESS_KEY_ID": "testing",
    "SECRET_ACCESS_KEY": "testing",
}
# SQLite by default; CI also runs against Postgres to exercise row locks.
if env("TEST_DATABASE_URL", default=""):
    DATABASES = {"default": env.db("TEST_DATABASE_URL")}
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Throttling is tested explicitly; keep it out of the way elsewhere.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": {
        "auth": "1000/min",
        "gallery_unlock": "1000/min",
        "gallery_unlock_global": "1000/min",
        "collection_unlock": "1000/min",
        "collection_subscribe": "1000/min",
        "uploads": "1000/min",
        "archive": "1000/min",
        "payments": "1000/min",
        "checkout": "1000/min",
    },
}

# Never charge through a real provider from tests, whatever the developer's .env says.
BILLING = {**BILLING, "PROVIDER": "fake", "FAKE_WEBHOOK_SECRET": "", "FAKE_AUTO_CONFIRM": False}
FLUTTERWAVE = {"SECRET_KEY": "", "SECRET_HASH": "", "BASE_URL": "https://flutterwave.invalid/v3"}
