"""Asking for collection updates (on the collection page), and the link in every email that
confirms or stops them. The rules live in `galleries.updates`."""

from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from galleries import collections, updates
from galleries.models import Collection, CollectionSubscription
from galleries.public_views import private_page, unavailable, within_rate


def _back(collection: Collection, outcome: str) -> HttpResponse:
    url = reverse("public-collection", args=[collection.pk]) + f"?updates={outcome}"
    return HttpResponseRedirect(url, status=303)  # after a POST: GET the page


@require_POST
@private_page
def request_updates(request: HttpRequest, collection_id) -> HttpResponse:
    collection = Collection.objects.filter(pk=collection_id).first()
    if collection is None or not collections.is_open(request, collection):
        return redirect("home")
    if not within_rate(request, "collection_subscribe", str(collection.pk)):
        return _back(collection, "busy")
    try:
        updates.request_updates(collection, request.POST.get("email", ""))
    except ValueError:
        return _back(collection, "invalid")
    # The same answer whether or not the address was already subscribed.
    return _back(collection, "sent")


def _subscription(token: str) -> CollectionSubscription | None:
    return CollectionSubscription.objects.select_related("collection").filter(token=token).first()


@require_http_methods(["GET", "POST"])
@private_page
def manage(request: HttpRequest, token: str) -> HttpResponse:
    """GET only shows a button: mail scanners open links, and mustn't confirm or stop anything."""
    subscription = _subscription(token)
    if subscription is None:
        return unavailable(request, 404)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "confirm":
            updates.confirm(subscription)
        elif action == "stop":
            updates.stop(subscription)
        return HttpResponseRedirect(request.path, status=303)
    context = {"subscription": subscription, "collection": subscription.collection}
    return render(request, "galleries/public/updates.html", context)


@csrf_exempt  # RFC 8058: the mail app posts here itself; the token in the URL is the key
@require_POST
@private_page
def one_click_stop(request: HttpRequest, token: str) -> HttpResponse:
    subscription = _subscription(token)
    if subscription is None:
        return unavailable(request, 404)
    updates.stop(subscription)
    return HttpResponse("You won't get any more updates.", content_type="text/plain")
