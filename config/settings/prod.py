from .base import *  # noqa: F403
from .base import BILLING, FLUTTERWAVE, env

SECRET_KEY = env("DJANGO_SECRET_KEY")

if BILLING["PROVIDER"] == "fake":
    raise RuntimeError("Set BILLING_PROVIDER: the fake provider takes no real money.")
if BILLING["PROVIDER"] == "flutterwave" and not (
    FLUTTERWAVE["SECRET_KEY"] and FLUTTERWAVE["SECRET_HASH"]
):
    raise RuntimeError("Set FLW_SECRET_KEY and FLW_SECRET_HASH for Flutterwave.")

CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

# Database cache until Redis arrives with Celery. Run `manage.py createcachetable` once.
CACHES = {"default": env.cache("CACHE_URL", default="dbcache://fotohub_cache")}

# Only correct if the proxy overwrites (not appends) X-Forwarded-Proto.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
