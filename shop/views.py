"""Selling photos to gallery visitors: cart, checkout, and the buyer's order page.

Cart changes are plain forms (JSON when fetched) and work without JavaScript. They skip CSRF
because the gzipped gallery page carries no token, and a forged request can only put photos
in someone's cart: paying still needs the buyer. Checkout and order actions use CSRF.
"""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from assets.gallery_media import download_url, gallery_photos
from assets.storage import get_storage
from billing.services import PaymentRejected
from delivery import services as delivery
from delivery.models import DownloadJob
from galleries.models import Gallery
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
from shop import cart, services
from shop.models import Order
from shop.pricing import format_money, price

TOO_MANY_CHECKOUTS = "Too many payment attempts. Wait a while, then try again."
REFRESH_SECONDS = 5


def open_gallery(request: HttpRequest, token: str) -> Visit | HttpResponse:
    try:
        visit = resolve(request, token)
    except Unavailable as exc:
        return unavailable(request, exc.status, exc.suspended)
    if not can_view(request, visit):
        return redirect("public-gallery-page", token=token)
    return visit


def totals(quantity: int) -> dict:
    return {
        currency: format_money(currency, price(currency).total(quantity))
        for currency in Order.Currency.values
    }


# --- Cart (under /g/<token>/) ------------------------------------------------------------


def cart_changed(request: HttpRequest, visit: Visit) -> HttpResponse:
    count = len(cart.photos(request, visit.gallery))
    if "application/json" in request.headers.get("Accept", ""):
        return JsonResponse({"count": count, "totals": totals(count)})
    return HttpResponseRedirect(reverse("shop-cart", args=[visit.gallery.public_token]), status=303)


@csrf_exempt
@require_POST
@private_page
def cart_add(request: HttpRequest, token: str) -> HttpResponse:
    visit = open_gallery(request, token)
    if isinstance(visit, HttpResponse):
        return visit
    cart.add(request, visit.gallery, request.POST.getlist("photo"))
    return cart_changed(request, visit)


@csrf_exempt
@require_POST
@private_page
def cart_add_all(request: HttpRequest, token: str) -> HttpResponse:
    """ "Add folder": the whole gallery, summed at the fixed price per photo."""
    visit = open_gallery(request, token)
    if isinstance(visit, HttpResponse):
        return visit
    cart.add_all(request, visit.gallery)
    return cart_changed(request, visit)


@csrf_exempt
@require_POST
@private_page
def cart_remove(request: HttpRequest, token: str) -> HttpResponse:
    visit = open_gallery(request, token)
    if isinstance(visit, HttpResponse):
        return visit
    cart.remove(request, visit.gallery, request.POST.get("photo", ""))
    return cart_changed(request, visit)


def cart_page_response(request: HttpRequest, visit: Visit, status: int = 200, **extra):
    items = cart.photos(request, visit.gallery)
    context = {
        **base_context(request, visit),
        "items": gallery_photos(items),
        "quantity": len(items),
        "totals": totals(len(items)),
        "unit": totals(1),
        "form": request.POST if request.method == "POST" else {"currency": "UGX"},
        **extra,
    }
    return render(request, "shop/cart.html", context, status=status)


@require_GET
@private_page
def cart_page(request: HttpRequest, token: str) -> HttpResponse:
    visit = open_gallery(request, token)
    if isinstance(visit, HttpResponse):
        return visit
    return cart_page_response(request, visit)


@require_POST
@private_page
def checkout(request: HttpRequest, token: str) -> HttpResponse:
    visit = open_gallery(request, token)
    if isinstance(visit, HttpResponse):
        return visit
    if not within_rate(request, "checkout", token):
        return cart_page_response(request, visit, status=429, error=TOO_MANY_CHECKOUTS)
    currency = request.POST.get("currency", "")
    email = request.POST.get("email", "").strip()
    try:
        if currency not in Order.Currency.values:
            raise PaymentRejected("Choose how you'd like to pay.")
        try:
            validate_email(email)
        except ValidationError as exc:
            raise PaymentRejected("Enter your email, so we can send your download link.") from exc
        order, started = services.checkout(
            visit.gallery,
            cart.photos(request, visit.gallery),
            currency=currency,
            email=email,
            phone=request.POST.get("phone", ""),
            return_url_for=lambda o: request.build_absolute_uri(
                reverse("shop-order", args=[o.token])
            ),
        )
    except PaymentRejected as exc:
        return cart_page_response(request, visit, status=400, error=str(exc))
    cart.remember_order(request, order.token)
    if started.action == "redirect" and started.redirect_url:
        return HttpResponseRedirect(started.redirect_url)
    return HttpResponseRedirect(reverse("shop-order", args=[order.token]), status=303)


# --- Order (under /orders/<token>/): the link is the buyer's key --------------------------


def find_order(request: HttpRequest, token: str) -> Order:
    order = get_object_or_404(
        Order.objects.select_related("gallery__event__owner__profile"), token=token
    )
    cart.remember_order(request, token)  # unlocks downloads on the gallery page too
    return order


def order_context(order: Order) -> dict:
    gallery = order.gallery
    return {
        "order": order,
        "gallery": gallery,
        "event": gallery.event if gallery else None,
        "profile": gallery.event.owner.profile if gallery else None,
        "total": format_money(order.currency, order.amount_minor),
        "refresh_seconds": REFRESH_SECONDS,
    }


@require_GET
@private_page
def order_page(request: HttpRequest, token: str) -> HttpResponse:
    order = services.refresh(find_order(request, token))
    context = order_context(order)
    if order.status == Order.Status.PAID:
        context["items"] = gallery_photos(delivery.order_photos(order))
        context["missing"] = order.quantity - len(context["items"])
    return render(request, "shop/order.html", context)


@require_GET
@private_page
def order_photo(request: HttpRequest, token: str, photo_id: uuid.UUID) -> HttpResponse:
    order = find_order(request, token)
    if order.status != Order.Status.PAID:
        return redirect("shop-order", token=token)
    photo = next((p for p in delivery.order_photos(order) if p.pk == photo_id), None)
    if photo is None:
        raise Http404("That photo isn't in this order, or the photographer removed it.")
    return HttpResponseRedirect(download_url(photo, Gallery.DownloadPolicy.ORIGINAL))


@require_POST
@private_page
def order_archive(request: HttpRequest, token: str) -> HttpResponse:
    order = find_order(request, token)
    try:
        job = delivery.request_archive(order)
    except delivery.ArchiveUnavailable as exc:
        context = {**order_context(order), "error": str(exc)}
        return render(request, "shop/archive.html", context, status=409)
    return HttpResponseRedirect(reverse("shop-order-archive", args=[token, job.pk]), status=303)


def archive_ready(job: DownloadJob) -> bool:
    return (
        job.status == DownloadJob.Status.READY
        and job.expires_at is not None
        and job.expires_at > timezone.now()
    )


@require_GET
@private_page
def order_archive_status(request: HttpRequest, token: str, job_id: uuid.UUID) -> HttpResponse:
    order = find_order(request, token)
    job = get_object_or_404(DownloadJob, pk=job_id, order=order)
    context = {
        **order_context(order),
        "job": job,
        "ready": archive_ready(job),
        "large_bytes": settings.DELIVERY["LARGE_DOWNLOAD_BYTES"],
    }
    return render(request, "shop/archive.html", context)


@require_GET
@private_page
def order_archive_file(request: HttpRequest, token: str, job_id: uuid.UUID) -> HttpResponse:
    order = find_order(request, token)
    job = get_object_or_404(DownloadJob, pk=job_id, order=order)
    if not archive_ready(job):
        return redirect("shop-order-archive", token=token, job_id=job.pk)
    title = order.gallery.event.title if order.gallery else "Photos"
    filename = f"{title} - {order.quantity} photos.zip"
    return HttpResponseRedirect(get_storage().presign_download(job.archive_key, filename))
