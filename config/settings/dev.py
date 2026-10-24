from .base import *  # noqa: F403
from .base import env

DEBUG = True
SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-only-insecure-key")
# Add your LAN IP (DJANGO_ALLOWED_HOSTS=192.168.1.136) to open the dev server from a phone.
ALLOWED_HOSTS = ["localhost", "127.0.0.1", *env.list("DJANGO_ALLOWED_HOSTS", default=[])]

# No Redis locally: run image processing inline unless a broker is configured.
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=True)

# Test payments confirm on the first status check, so the renew flow can be clicked through.
BILLING = {**BILLING, "FAKE_AUTO_CONFIRM": True}  # noqa: F405
