# Deploying FotoHub (one server, Johannesburg)

Everything runs on one virtual server with Docker Compose:

| Service | What it does |
|---|---|
| `caddy` | HTTPS (automatic Let's Encrypt certificates), forwards to `web` |
| `web` | Django + gunicorn. Runs migrations on start; `/healthz/` checks the database and cache |
| `worker` | Celery: photo processing, ZIPs, emails |
| `beat` | Celery's scheduler: renewal reminders, archive expiry, stuck-photo requeue, **nightly database backup** (02:30 Kampala) |
| `db` | Postgres 17 (data in the `pgdata` volume) |
| `redis` | Celery broker and shared cache (throttles) |

Photos never touch the server: browsers upload straight to R2 and download from it.

## 1. Server

- **Region:** Johannesburg. Any provider with a Johannesburg location works (Vultr, Azure "South Africa North", or a local host).
- **Size:** 2 vCPU and 4 GB RAM is plenty for the pilot. Photo processing is the heaviest job.
- **OS:** Ubuntu 24.04. Install Docker by following Docker's official "Install Docker Engine on Ubuntu" guide.
- **Firewall:** allow ports 22, 80 and 443 only.

## 2. Address

- **With a domain:** point an `A` record at the server's IP.
- **Without one yet:** use `<server-ip>.sslip.io` (e.g. `102.130.1.2.sslip.io`). It resolves to the server's IP, so Caddy can still get a real HTTPS certificate.

## 3. Configure

```bash
git clone https://github.com/Bukulubenja/fotohub.git && cd fotohub
cp deploy/.env.example deploy/.env
nano deploy/.env          # fill every value marked CHANGE, plus storage, Flutterwave, SMTP
```

## 4. Start

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
docker compose -f deploy/docker-compose.yml --env-file deploy/.env ps        # all "healthy"/"running"
docker compose -f deploy/docker-compose.yml --env-file deploy/.env exec web python manage.py createsuperuser
```

Open `https://<SITE_ADDRESS>/app/` and you should see the dashboard sign-in (the site root `/` is the guest passkey page). The admin is at `https://<SITE_ADDRESS>/<DJANGO_ADMIN_URL>`.

## 5. Connect the outside services

1. **R2 CORS** for the new address, so browsers can upload:
   ```bash
   docker compose -f deploy/docker-compose.yml --env-file deploy/.env exec web \
     python manage.py storage_check --origin https://<SITE_ADDRESS> --apply
   ```
2. **Flutterwave webhook:** in the dashboard, set the URL to
   `https://<SITE_ADDRESS>/api/v1/billing/webhooks/flutterwave/` with the same secret hash as `FLW_SECRET_HASH`.
3. **Email:** fill the `SMTP_*` values from your email service (e.g. Brevo, Mailgun, Amazon SES), then send yourself a test:
   ```bash
   docker compose ... exec web python manage.py sendtestemail you@example.com
   ```

## 6. Backups and restore

- **Automatic:** a backup goes to `backups/db/` in the R2 bucket every night, and backups are kept for 30 days (`BACKUP_RETENTION_DAYS`).
- **By hand:**
  ```bash
  docker compose ... exec web python manage.py backup_db            # back up now
  docker compose ... exec web python manage.py backup_db --list      # what's there
  docker compose ... exec web python manage.py restore_db --yes-replace-everything   # newest
  ```
- **Restore is tested in CI** on every push: a real back up, delete, restore, check round trip on Postgres 17.
- **Rehearse once on the server too:** after the first nightly backup, restore it into a scratch copy, or into this server before launch while there's no real data.

## Updating

```bash
git pull
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
```

Migrations run automatically when `web` starts.

## Logs

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env logs -f web worker beat
```

Set `SENTRY_DSN` to get errors by email with the full context.
