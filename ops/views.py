from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.cache import cache
from django.db import connection
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

DASHBOARD_INDEX = "dashboard/index.html"


@require_GET
@never_cache
def healthz(request: HttpRequest) -> JsonResponse:
    """For Caddy, Docker and uptime checks: is the app able to reach its database and cache?"""
    checks = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception:  # noqa: BLE001 - any failure means unhealthy
        checks["database"] = "error"
    try:
        cache.set("healthz", "1", 10)
        checks["cache"] = "ok" if cache.get("healthz") == "1" else "error"
    except Exception:  # noqa: BLE001
        checks["cache"] = "error"
    healthy = all(value == "ok" for value in checks.values())
    return JsonResponse(checks, status=200 if healthy else 503)


@require_GET
@never_cache
def dashboard(request: HttpRequest) -> HttpResponse:
    """The photographer dashboard (Vite build, collected as static files) at the site root."""
    try:
        with staticfiles_storage.open(DASHBOARD_INDEX) as index:
            return HttpResponse(index.read(), content_type="text/html; charset=utf-8")
    except (FileNotFoundError, OSError) as exc:
        raise Http404("Dashboard not built. Run `npm run build` in frontend/.") from exc
