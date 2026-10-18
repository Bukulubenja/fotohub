# FotoHub

Private photo-delivery galleries for event photographers. See
`FotoHub_Product_Requirements_and_UI_UX_Documentation.docx` (spec) and
`FotoHub_Research_and_Launch_Plan.md` (research and roadmap).

## Local setup (Windows / Git Bash)

```bash
py -3.14 -m venv .venv
.venv/Scripts/python -m pip install -r requirements/dev.txt
cp .env.example .env
.venv/Scripts/python manage.py migrate
.venv/Scripts/python manage.py createsuperuser
.venv/Scripts/python manage.py runserver
```

Local development uses SQLite. Production uses PostgreSQL through `DATABASE_URL`.

## Checks

```bash
.venv/Scripts/python -m pytest --cov --cov-report=term-missing
.venv/Scripts/ruff check . && .venv/Scripts/ruff format --check .
```

## Layout

| App | Responsibility |
|---|---|
| `config/` | Settings split into `base`, `dev`, `test` and `prod` |
| `accounts/` | Email-login `User`, `PhotographerProfile`, session auth API |
| `galleries/` | `Event`, `Gallery`, state transitions (`services.py`), customer access policy (`access.py`) |
| `assets/` | `Photo`, `UploadSession`, quota reservation and upload lifecycle (`services.py`), R2 client (`storage.py`), pyvips variants (`processing.py`, Celery `tasks.py`) |

Planned apps: `delivery` (downloads/ZIP), `billing`, `audit`.

## API (v1)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/auth/csrf/` | Sets CSRF cookie for the dashboard |
| POST | `/api/v1/auth/register/` · `/login/` · `/logout/` | Session auth, throttled |
| GET/PATCH | `/api/v1/me/` | Photographer profile |
| CRUD | `/api/v1/events/` | Scoped to the signed-in photographer |
| CRUD | `/api/v1/galleries/` | Scoped through `event.owner` |
| POST | `/api/v1/galleries/{id}/publish/` · `/unpublish/` · `/rotate-token/` | 409 on invalid transition |
| GET | `/api/v1/public/galleries/{token}/` | Customer metadata. Drafts and unknown tokens return 404, expired galleries 410 |
| POST | `/api/v1/public/galleries/{token}/unlock/` | Gallery password, throttled, unlock stored in session |
| POST | `/api/v1/uploads/` | `{gallery, filename, content_type, size}`. Reserves quota, returns a presigned PUT (`type: single`) or a multipart session. 409 `quota_exceeded` when full |
| GET | `/api/v1/uploads/{id}/` | Resume: current state plus a freshly signed PUT URL |
| POST | `/api/v1/uploads/{id}/sign-parts/` | `{part_numbers: [...]}` (max 100 per call) → presigned part URLs |
| GET | `/api/v1/uploads/{id}/parts/` | Parts already in the bucket, so a resumed upload skips them |
| POST | `/api/v1/uploads/{id}/complete/` | Idempotent. Verifies the stored size, then queues processing |
| DELETE | `/api/v1/uploads/{id}/` | Abort an unfinished upload and release its quota |
| GET/DELETE | `/api/v1/photos/` | `?gallery=<id>&status=failed` for the dashboard's retry list |
| GET | `/api/v1/storage/usage/` | `used_bytes`, `quota_bytes`, `percent`, `warning_level` (75/90/100) |

## Uploads and processing

Browsers upload straight to the bucket; Django only signs URLs and tracks state. After
`complete`, Celery renders `thumb` (400 px WebP), `viewer` (1600 px WebP), `web` (2048 px
JPEG) and `cover` (1200×630 JPEG) with libvips, dropping all metadata (EXIF/GPS). Originals are
never modified. Abandoned uploads stop counting toward quota after 24 h and are removed by the
`expire-stale-uploads` beat task. `requeue-stuck-photos` re-queues photos left in
processing for 30+ minutes (e.g. the broker was down at `complete`) and fails them after 24 h.

```bash
# Production workers (dev runs tasks inline unless CELERY_TASK_ALWAYS_EAGER=False)
celery -A config worker --concurrency=2
celery -A config beat
```

Bucket setup (once per environment):

- **CORS**: allow `PUT` from the dashboard origin and expose the `ETag` header (Uppy needs it
  for multipart).
- **Lifecycle rule**: abort incomplete multipart uploads after 1 day. This catches parts left
  behind when a gallery is deleted mid-upload.
- Keep the bucket private. Nothing is served without a signed URL.

## Security rules this code relies on

- Every owner query goes through `owned_by(user)`. Never use `Gallery.objects.get(pk=...)` in an owner view.
- Only `galleries/services.py` changes a gallery's `status`.
- Public responses send `X-Robots-Tag: noindex, nofollow` and expose only `PublicGallerySerializer` fields.
- Changing the password or rotating the token bumps `access_version`, which re-locks existing sessions.
- Storage keys are built from ids only, never from client filenames, and are never serialized.
- Only `assets/services.py` changes a photo's upload state; only `processing.py` decodes images.
- Deleting a `Photo` row (directly or by cascade) queues deletion of its files after commit.
