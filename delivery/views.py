"""Customer "Download all" flow: ask, watch it build, download (spec FR-21, design §D).

Works without JavaScript: the request is a plain form, the status page refreshes itself.
"""

import re
import uuid

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from assets.storage import get_storage
from delivery import services
from delivery.models import DownloadJob
from galleries.public_views import (
    Unavailable,
    Visit,
    base_context,
    can_view,
    private_page,
    resolve,
    unavailable,
    within_rate,
)

TOO_MANY_REQUESTS = "You've asked for this download many times. Wait a while, then try again."
UNSAFE_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
REFRESH_SECONDS = 5


def archive_filename(visit: Visit) -> str:
    event = visit.gallery.event
    name = f"{event.title} - {event.owner.profile.display_name}"
    return UNSAFE_FILENAME.sub(" ", name).strip()[:150] + ".zip"


def is_downloadable(job: DownloadJob, visit: Visit) -> bool:
    """Ready, unexpired, and built under the gallery's current download setting."""
    return (
        job.status == DownloadJob.Status.READY
        and job.expires_at is not None
        and job.expires_at > timezone.now()
        and job.policy == visit.gallery.download_policy
    )


def page(request: HttpRequest, visit: Visit, status: int = 200, **context) -> HttpResponse:
    context = {
        **base_context(request, visit),
        "large_bytes": settings.DELIVERY["LARGE_DOWNLOAD_BYTES"],
        "refresh_seconds": REFRESH_SECONDS,
        **context,
    }
    return render(request, "delivery/archive.html", context, status=status)


def open_gallery(request: HttpRequest, token: str) -> Visit | HttpResponse:
    try:
        visit = resolve(request, token)
    except Unavailable as exc:
        return unavailable(request, exc.status)
    if not can_view(request, visit):
        return redirect("public-gallery-page", token=token)
    return visit


# Exempt: the gallery page carries no CSRF token (it is gzipped; see galleries.public_views),
# and a forged request could only start a shared, rate-limited archive the visitor may get anyway.
@csrf_exempt
@require_POST
@private_page
def request_archive(request: HttpRequest, token: str) -> HttpResponse:
    visit = open_gallery(request, token)
    if isinstance(visit, HttpResponse):
        return visit
    if not within_rate(request, "archive", token):
        return page(request, visit, status=429, error=TOO_MANY_REQUESTS)
    try:
        job = services.request_archive(visit.gallery)
    except services.ArchiveUnavailable as exc:
        return page(request, visit, status=409, error=str(exc))
    return HttpResponseRedirect(reverse("public-archive-status", args=[token, job.pk]), status=303)


@require_GET
@private_page
def archive_status(request: HttpRequest, token: str, job_id: uuid.UUID) -> HttpResponse:
    visit = open_gallery(request, token)
    if isinstance(visit, HttpResponse):
        return visit
    job = get_object_or_404(DownloadJob, pk=job_id, gallery=visit.gallery)
    return page(
        request,
        visit,
        job=job,
        downloadable=is_downloadable(job, visit),
        settings_changed=job.policy != visit.gallery.download_policy,
    )


@require_GET
@private_page
def archive_file(request: HttpRequest, token: str, job_id: uuid.UUID) -> HttpResponse:
    visit = open_gallery(request, token)
    if isinstance(visit, HttpResponse):
        return visit
    job = get_object_or_404(DownloadJob, pk=job_id, gallery=visit.gallery)
    if not is_downloadable(job, visit):
        return redirect("public-archive-status", token=token, job_id=job.pk)
    url = get_storage().presign_download(job.archive_key, archive_filename(visit))
    return HttpResponseRedirect(url)
