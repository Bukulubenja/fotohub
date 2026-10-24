# FotoHub: one image for web (gunicorn), worker and beat (celery). See deploy/README.md.

# --- Photographer dashboard (Vite build) ---
FROM node:24-slim AS dashboard
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Django ---
# Debian trixie ships postgresql-client 17, matching the Postgres 17 server for backups.
FROM python:3.14-slim-trixie
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 fotohub

WORKDIR /app
COPY requirements/ requirements/
RUN pip install -r requirements/prod.txt

COPY . .
COPY --from=dashboard /build/dist frontend/dist
# Settings demand real secrets at runtime; collectstatic only needs them to import.
RUN DJANGO_SECRET_KEY=build-only BILLING_PROVIDER=flutterwave \
    FLW_SECRET_KEY=build-only FLW_SECRET_HASH=build-only \
    python manage.py collectstatic --noinput \
    && chown -R fotohub /app

USER fotohub
EXPOSE 8000
CMD ["gunicorn", "config.wsgi", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60", "--access-logfile", "-"]
