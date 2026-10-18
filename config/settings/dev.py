from .base import *  # noqa: F403
from .base import env

DEBUG = True
SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-only-insecure-key")
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]
