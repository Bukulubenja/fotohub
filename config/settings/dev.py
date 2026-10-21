from .base import *  # noqa: F403
from .base import env

DEBUG = True
SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-only-insecure-key")
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

# No Redis locally: run image processing inline unless a broker is configured.
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=True)

# Test payments confirm on the first status check, so the renew flow can be clicked through.
BILLING = {**BILLING, "FAKE_AUTO_CONFIRM": True}  # noqa: F405
