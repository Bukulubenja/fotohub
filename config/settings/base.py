from pathlib import Path

import environ
from celery.schedules import crontab

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

# SECRET_KEY is defined per environment; prod requires it from the environment.
DEBUG = False
ALLOWED_HOSTS: list[str] = env.list("DJANGO_ALLOWED_HOSTS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "accounts",
    "galleries",
    "assets",
    "delivery",
    "billing",
    "shop",
    "ops",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "galleries.context_processors.support",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {"default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en"
TIME_ZONE = "Africa/Kampala"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# The photographer dashboard's Vite build (`npm run build`), served at the site root.
DASHBOARD_BUILD = BASE_DIR / "frontend" / "dist"
STATICFILES_DIRS = [("dashboard", DASHBOARD_BUILD)] if DASHBOARD_BUILD.is_dir() else []

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.ScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "auth": "10/min",
        "gallery_unlock": "10/min",
        # Across all clients, so rotating IPs doesn't multiply password guesses.
        "gallery_unlock_global": "60/hour",
        # create + sign + complete per file; a 1,000-photo event stays well under this.
        "uploads": "1200/min",
        # "Download all" requests per visitor; the archives themselves are shared.
        "archive": "20/hour",
        # Starting a payment sends a prompt to a phone; don't let a loop spam someone.
        "payments": "10/hour",
        "checkout": "20/hour",  # photo orders per visitor
    },
    # Trusted reverse proxies in front of the app. With 0, X-Forwarded-For is ignored
    # and throttles key on REMOTE_ADDR, so clients can't spoof their identity.
    "NUM_PROXIES": env.int("DJANGO_NUM_PROXIES", default=0),
}

# Throttle counters must be shared across workers; prod overrides with a shared cache.
CACHES = {"default": env.cache("CACHE_URL", default="locmemcache://")}

ADMIN_URL = env("DJANGO_ADMIN_URL", default="admin/")

# Public gallery tokens: 32 random bytes, URL-safe (~43 chars).
GALLERY_TOKEN_BYTES = 32

MIB = 1024 * 1024
GIB = 1024 * MIB

# Private S3-compatible object storage (Cloudflare R2 in production).
OBJECT_STORAGE = {
    "BUCKET": env("STORAGE_BUCKET", default="fotohub-dev"),
    "ENDPOINT_URL": env("STORAGE_ENDPOINT_URL", default=None),
    "REGION": env("STORAGE_REGION", default="auto"),
    "ACCESS_KEY_ID": env("STORAGE_ACCESS_KEY_ID", default=None),
    "SECRET_ACCESS_KEY": env("STORAGE_SECRET_ACCESS_KEY", default=None),
}

UPLOADS = {
    "ALLOWED_CONTENT_TYPES": ["image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"],
    "MAX_FILE_BYTES": 100 * MIB,
    "MULTIPART_THRESHOLD_BYTES": 32 * MIB,
    "PART_SIZE_BYTES": 8 * MIB,  # S3 minimum is 5 MiB
    "MAX_PARTS_PER_SIGN_REQUEST": 100,
    "URL_TTL_SECONDS": 60 * 60,
    "SESSION_TTL_SECONDS": 24 * 60 * 60,  # unfinished uploads stop reserving quota after this
    "MAX_PIXELS": 120_000_000,  # decompression-bomb guard (~120 MP)
    "MAX_ACTIVE_UPLOADS": 500,  # per photographer
}

# Customer gallery media. Image URLs are signed per window so browsers can cache them; an
# unpublished gallery's already-issued URLs keep working until at most 2 windows later.
GALLERY_MEDIA = {
    "URL_WINDOW_SECONDS": 3 * 60 * 60,
    "DOWNLOAD_URL_TTL_SECONDS": 10 * 60,
    "PAGE_SIZE": 60,  # photos per page; the first screen loads only the first few
}

# Download-all ZIP archives (spec FR-21).
DELIVERY = {
    "ARCHIVE_TTL_SECONDS": 24 * 60 * 60,
    "MAX_ARCHIVE_BYTES": 20 * GIB,
    "STUCK_AFTER_SECONDS": 3 * 60 * 60,  # longer than the build task's time limit
    "LARGE_DOWNLOAD_BYTES": 200 * MIB,  # above this, suggest Wi-Fi
}

# Subscriptions (spec FR-26..29). Plan prices live in the admin.
BILLING = {
    "PROVIDER": env("BILLING_PROVIDER", default="fake"),
    "TRIAL_PLAN": "trial",
    "TRIAL_DAYS": 14,
    "DUE_DAYS": 3,  # reminder this long before paid_until
    "GRACE_DAYS": 7,  # after paid_until: uploads stop, galleries stay live
    "LAPSED_VISIBLE_DAYS": 30,  # after grace: galleries still viewable, then hidden
    "FAKE_WEBHOOK_SECRET": env("BILLING_FAKE_WEBHOOK_SECRET", default=""),
    "FAKE_AUTO_CONFIRM": False,
    # Where a hosted checkout sends the photographer back after a renewal (the dashboard).
    "RETURN_URL": env("BILLING_RETURN_URL", default=""),
}

# Flutterwave v3 (BILLING_PROVIDER=flutterwave). Keys from the Flutterwave dashboard
# (Settings > API keys); SECRET_HASH is the webhook "secret hash" you set there.
FLUTTERWAVE = {
    "SECRET_KEY": env("FLW_SECRET_KEY", default=""),
    "SECRET_HASH": env("FLW_SECRET_HASH", default=""),
    "BASE_URL": env("FLW_BASE_URL", default="https://api.flutterwave.com/v3"),
}

# Photo sales to gallery visitors: one fixed price per photo (decided 2026-10-21).
# FotoHub keeps the revenue. Update PRICE_UGX by hand when the exchange rate moves.
SHOP = {
    "PRICE_USD_CENTS": env.int("SHOP_PRICE_USD_CENTS", default=24),
    "PRICE_UGX": env.int("SHOP_PRICE_UGX", default=900),
}
# Public base URL, for links in emails (receipts, reminders).
SITE_URL = env("SITE_URL", default="http://127.0.0.1:8000")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="FotoHub <photos@fotohub.ug>")
# Where guests reach FotoHub about payments and takedown requests. Empty: pages point them
# to the photographer instead.
SUPPORT_EMAIL = env("SUPPORT_EMAIL", default="")
# Django 6.1 MAILERS (EMAIL_BACKEND is deprecated). Console in development; production sets
# MAILER_BACKEND to SMTP or a provider backend, with its OPTIONS, at deployment (phase 4).
MAILERS = {
    "default": {
        "BACKEND": env("MAILER_BACKEND", default="django.core.mail.backends.console.EmailBackend"),
    }
}

# Nightly database backups to object storage (ops app).
BACKUPS = {"RETENTION_DAYS": env.int("BACKUP_RETENTION_DAYS", default=30)}

DEFAULT_STORAGE_QUOTA_BYTES = env.int("DEFAULT_STORAGE_QUOTA_BYTES", default=5 * GIB)
STORAGE_WARNING_THRESHOLDS = [75, 90, 100]  # percent, spec FR-28

CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1  # image jobs are heavy; take one at a time
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=False)
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {
    "backup-database": {
        "task": "ops.tasks.backup_database",
        "schedule": crontab(hour=2, minute=30),  # nightly, Kampala time
    },
    "expire-stale-uploads": {
        "task": "assets.tasks.expire_stale_uploads",
        "schedule": 15 * 60,
    },
    "send-billing-reminders": {
        "task": "billing.tasks.send_reminders",
        "schedule": 60 * 60,  # idempotent: each stage's reminder is sent once
    },
    "expire-archives": {
        "task": "delivery.tasks.expire_archives",
        "schedule": 30 * 60,
    },
    "requeue-stuck-photos": {
        "task": "assets.tasks.requeue_stuck_photos",
        "schedule": 10 * 60,
    },
}
