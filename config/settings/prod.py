from .base import *  # noqa: F403
from .base import BILLING, FLUTTERWAVE, MIDDLEWARE, env
from .mail import SMTP_BACKEND, mailer

SECRET_KEY = env("DJANGO_SECRET_KEY")

if BILLING["PROVIDER"] == "fake":
    raise RuntimeError("Set BILLING_PROVIDER: the fake provider takes no real money.")
if BILLING["PROVIDER"] == "flutterwave" and not (
    FLUTTERWAVE["SECRET_KEY"] and FLUTTERWAVE["SECRET_HASH"]
):
    raise RuntimeError("Set FLW_SECRET_KEY and FLW_SECRET_HASH for Flutterwave.")

CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

# Redis is shared by Celery and the cache, so throttles count across all web workers.
CACHES = {"default": env.cache("CACHE_URL", default="redis://redis:6379/1")}

# Static files (admin, dashboard build) straight from Django, hashed and compressed.
MIDDLEWARE = [MIDDLEWARE[0], "whitenoise.middleware.WhiteNoiseMiddleware", *MIDDLEWARE[1:]]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# Caddy terminates HTTPS and overwrites X-Forwarded-Proto/-For (see deploy/Caddyfile).
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_REDIRECT_EXEMPT = [r"^healthz/$"]  # Docker's health check talks plain HTTP inside
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=60 * 60 * 24 * 365)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Email (receipts, reminders). Django 6.1 MAILERS; SMTP settings from the environment.
MAILERS = {
    "default": mailer(
        env("MAILER_BACKEND", default=SMTP_BACKEND),
        {
            "host": env("SMTP_HOST", default="localhost"),
            "port": env.int("SMTP_PORT", default=587),
            "username": env("SMTP_USERNAME", default=""),
            "password": env("SMTP_PASSWORD", default=""),
            "use_tls": env.bool("SMTP_USE_TLS", default=True),
            "timeout": 20,
        },
    )
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
}

# Error tracking: on when SENTRY_DSN is set. No personal data is sent.
if env("SENTRY_DSN", default=""):
    import sentry_sdk

    sentry_sdk.init(
        dsn=env("SENTRY_DSN"),
        environment=env("SENTRY_ENVIRONMENT", default="production"),
        traces_sample_rate=env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.05),
        send_default_pii=False,
    )
