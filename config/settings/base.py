from pathlib import Path

import environ

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

DEFAULT_STORAGE_QUOTA_BYTES = env.int("DEFAULT_STORAGE_QUOTA_BYTES", default=5 * GIB)
STORAGE_WARNING_THRESHOLDS = [75, 90, 100]  # percent, spec FR-28

CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1  # image jobs are heavy; take one at a time
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=False)
CELERY_BEAT_SCHEDULE = {
    "expire-stale-uploads": {
        "task": "assets.tasks.expire_stale_uploads",
        "schedule": 15 * 60,
    },
    "requeue-stuck-photos": {
        "task": "assets.tasks.requeue_stuck_photos",
        "schedule": 10 * 60,
    },
}
