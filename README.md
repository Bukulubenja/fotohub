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

## Photographer dashboard (`frontend/`)

React + TypeScript (Vite). In development it proxies `/api` to Django, so the session cookie
and CSRF work without CORS. Needs Node 20.19+ (CI uses Node 24 / npm 11; npm 10.8 has a
resolver bug with this dependency tree, so use `npx npm@11 install` there).

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173, with Django on :8000
npm test && npm run typecheck && npm run lint && npm run build
```

`src/upload/queue.ts` is the upload engine: 3 files at a time, per-file retries with backoff,
resume from the parts already in the bucket, and a retry that reuses the existing server
session so quota isn't reserved twice. Uppy isn't used: v6 drives S3 itself and wants the
server to sign arbitrary requests, which conflicts with the server-owned lifecycle above.

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
| `frontend/` | Photographer dashboard: sign-in, gallery picker, upload manager |
| `assets/` | `Photo`, `UploadSession`, quota reservation and upload lifecycle (`services.py`), R2 client (`storage.py`), pyvips variants (`processing.py`, Celery `tasks.py`) |

| `galleries/public_views.py` | Customer gallery at `/g/<token>/`: server-rendered page, password unlock, single downloads, owner preview |
| `delivery/` | "Download all": shared `DownloadJob`s, ZIP streamed into R2 (`archive.py`), status page, expiry |

Planned apps: `billing`, `audit`.

## API (v1)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/auth/csrf/` | Sets CSRF cookie for the dashboard |
| POST | `/api/v1/auth/register/` · `/login/` · `/logout/` | Session auth, throttled |
| GET/PATCH | `/api/v1/me/` | Photographer profile |
| CRUD | `/api/v1/events/` | Scoped to the signed-in photographer |
| CRUD | `/api/v1/galleries/` | Scoped through `event.owner`. Read-only `share_url` and `share_message` (WhatsApp-ready, never contains the password); writable `cover_photo` (a ready photo of that gallery) |
| POST | `/api/v1/galleries/{id}/publish/` · `/unpublish/` · `/rotate-token/` | 409 on invalid transition |
| GET | `/api/v1/public/galleries/{token}/` | Customer metadata. Drafts and unknown tokens return 404, expired galleries 410 |
| POST | `/api/v1/public/galleries/{token}/unlock/` | Gallery password, throttled, unlock stored in session |
| POST | `/api/v1/uploads/` | `{gallery, filename, content_type, size}`. Reserves quota, returns a presigned PUT (`type: single`) or a multipart session. 409 `quota_exceeded` when full |
| GET | `/api/v1/uploads/{id}/` | Resume: current state plus a freshly signed PUT URL |
| POST | `/api/v1/uploads/{id}/sign-parts/` | `{part_numbers: [...]}` (max 100 per call) → presigned part URLs |
| GET | `/api/v1/uploads/{id}/parts/` | Parts already in the bucket, so a resumed upload skips them |
| POST | `/api/v1/uploads/{id}/complete/` | Idempotent. Verifies the stored size, then queues processing |
| DELETE | `/api/v1/uploads/{id}/` | Abort an unfinished upload and release its quota |
| GET | `/api/v1/uploads/config/` | Allowed types and size limits, checked client-side before uploading |
| GET/DELETE | `/api/v1/photos/` | `?gallery=<id>&status=failed` for the dashboard's retry list |
| GET | `/api/v1/storage/usage/` | `used_bytes`, `quota_bytes`, `percent`, `warning_level` (75/90/100) |

## Customer gallery (`/g/<token>/`)

Server-rendered Django templates, not the React app. The first screen has to be light on a
cheap phone, and WhatsApp link previews need Open Graph tags in the HTML.

| Path | What it does |
|---|---|
| `GET /g/<token>/` | Gallery: cover, grid (60 per page, `?page=`), lightbox. Password page when locked |
| `POST /g/<token>/unlock/` | Gallery password (CSRF, same throttle counters as the JSON API) |
| `GET /g/<token>/photos/<id>/download/` | Re-checks access and download policy, then redirects to a 10-minute signed link |
| `POST /g/<token>/archive/` | "Download all": finds or creates the shared ZIP job, then shows its status page |
| `GET /g/<token>/archive/<job>/` and `…/file/` | Status page (refreshes itself), then the ZIP |

- Photos appear in filename order, ignoring case. That follows the camera's shooting sequence, and the ZIP uses the same order.
- Unknown links and drafts both return the same 404 page, so a draft can't be detected. Expired galleries get a 410 page.
- Every response sends `noindex`, `private, no-store`, and `Referrer-Policy: no-referrer`.
- The photographer, signed in to the dashboard, can preview a gallery in any state.
- **Data budget.** The first screen must stay under 1 MB:
  - no web fonts and no external CSS or JS
  - only 6 thumbnails load eagerly, and every image has fixed dimensions
  - the photo page is gzipped (about 70 KB down to 10 KB)
  - `galleries/tests/test_sharing_and_budget.py` enforces these rules.
- **Cacheable image links.** Gallery image URLs are signed for a 3-hour window
  (`GALLERY_MEDIA`), so everyone gets the same URLs and browsers can cache them. Unpublishing
  doesn't cancel links already issued; they can stay valid for up to 6 hours. (Accepted
  trade-off: phones reuse cached images instead of downloading them again.)
- **Share previews.** For a password-protected gallery, the preview shows no cover photo.
- **ZIP archives** are shared per gallery, download setting and set of photos. They're built
  into R2 by a worker without holding the archive in memory, and expire after 24 hours (the
  `expire-archives` beat task). An archive built under an older download setting is never served.

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

- **CORS**: allow `PUT` with the `Content-Type` header from the dashboard origin. No headers
  need exposing: the server assembles multipart uploads from the bucket's own part list.
- **Lifecycle rule**: abort incomplete multipart uploads after 1 day. This catches parts left
  behind when a gallery is deleted mid-upload.
- Keep the bucket private. Nothing is served without a signed URL.

`manage.py storage_check` proves all of this over real HTTP with presigned URLs, as a browser
would. It also proves that the bucket refuses a PUT whose size differs from the signed
`Content-Length`; without that, uploads could bypass the quota.

```bash
.venv/Scripts/python manage.py storage_check --origin https://app.fotohub.ug          # check
.venv/Scripts/python manage.py storage_check --origin https://app.fotohub.ug --apply  # + set CORS/lifecycle
```

`--apply` needs an R2 token with admin rights on the bucket. An "Object Read & Write" token
gets `AccessDenied`; set the two rules in the R2 dashboard instead and rerun the check.

## Phase 2 exit gate: the upload drill

The gate: a 1,000-photo event uploads over a Kampala connection, with retries working.
`scripts/upload_drill.py` drives the real API and bucket the way the dashboard does (3 files at
once, the same 1/3/10/30 s retries, resume from stored parts), then presses "retry failed" once
and waits for processing. It uses the standard library only; pyvips is needed just for
`--synthetic`.

1. **Bucket.** Create an R2 bucket and an API token, fill the `STORAGE_*` lines in `.env`, and
   run `storage_check` until every line says `ok`. Use `--origin http://localhost:5173` locally.
2. **Quota.** 1,000 photos at 3–8 MB need more than the default 5 GiB. Raise the drill
   account's `storage_quota_bytes` in the admin. The drill refuses to start if it won't fit.
3. **Worker** (optional for a first pass). Dev settings process photos inline. To test the real
   queue, run Redis (Memurai or WSL on Windows), set `CELERY_TASK_ALWAYS_EAGER=False`, and start
   `celery -A config worker --pool=solo` (the prefork pool doesn't run on Windows) plus `celery -A config beat`.
4. **Rehearse small**, e.g. 50 photos with simulated trouble:

   ```bash
   .venv/Scripts/python scripts/upload_drill.py --base-url http://127.0.0.1:8000 \
       --email drill@example.com --synthetic --photos 50 --drop-rate 0.05 --journal rehearsal.jsonl
   ```

5. **The real run**, from the Kampala connection, with your own event photos:

   ```bash
   .venv/Scripts/python scripts/upload_drill.py --base-url http://127.0.0.1:8000 \
       --email drill@example.com --image-dir "D:/events/some-wedding" --photos 1000 \
       --journal drill.jsonl --report drill-report.json
   ```

   Partway through, pull the network cable or switch off Wi-Fi for a minute, and kill the
   script once (Ctrl+C or close the window). Rerun the same command and it resumes from the
   journal: finished photos are skipped, and unfinished ones continue with their existing upload
   sessions and parts.

Use `127.0.0.1`, not `localhost`. On Windows, `localhost` tries IPv6 first and each request
loses about 2 s. `--bandwidth-kbps` caps the link and `--drop-rate` cuts a share of transfers
mid-body, for testing on a fast line. The run **passes** when it prints `PASS`: every file
uploaded (after at most one retry pass) and every photo `ready`. Keep `drill-report.json`. It
records throughput, p50/p95 seconds per file, how many files needed the manual retry pass, and
any processing failures.

The bytes really leave your connection: 1,000 photos at ~5 MB is about 5 GB of mobile data.

## Security rules this code relies on

- Every owner query goes through `owned_by(user)`. Never use `Gallery.objects.get(pk=...)` in an owner view.
- Only `galleries/services.py` changes a gallery's `status`.
- Public responses send `X-Robots-Tag: noindex, nofollow` and expose only `PublicGallerySerializer` fields.
- Changing the password or rotating the token bumps `access_version`, which re-locks existing sessions.
- Storage keys are built from ids only, never from client filenames, and are never serialized.
- Only `assets/services.py` changes a photo's upload state; only `processing.py` decodes images.
- Deleting a `Photo` row (directly or by cascade) queues deletion of its files after commit.
