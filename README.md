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

Planned apps: `assets` (uploads, pyvips), `delivery` (downloads/ZIP), `billing`, `audit`.

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

## Security rules this code relies on

- Every owner query goes through `owned_by(user)`. Never use `Gallery.objects.get(pk=...)` in an owner view.
- Only `galleries/services.py` changes a gallery's `status`.
- Public responses send `X-Robots-Tag: noindex, nofollow` and expose only `PublicGallerySerializer` fields.
- Changing the password or rotating the token bumps `access_version`, which re-locks existing sessions.
