from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.http import require_http_methods

from feedback import services
from feedback.models import MAX_MESSAGE_LENGTH
from galleries.public_views import private_page, within_rate

HONEYPOT = "website"  # hidden from people; bots that fill every field give themselves away


@require_http_methods(["GET", "POST"])
@private_page
def feedback_page(request: HttpRequest) -> HttpResponse:
    source = request.POST if request.method == "POST" else request.GET
    page = services.safe_page(source.get("page") or source.get("from", ""))
    context = {"page": page, "max_length": MAX_MESSAGE_LENGTH, "sent": "sent" in request.GET}
    if request.method == "GET":
        return render(request, "feedback/feedback.html", context)

    thanks = reverse("feedback") + "?" + urlencode({"sent": 1, "from": page})
    if request.POST.get(HONEYPOT):
        return HttpResponseRedirect(thanks, status=303)
    if not within_rate(request, "feedback", "site"):
        context.update(error="That's a lot of messages. Try again in an hour.", form=request.POST)
        return render(request, "feedback/feedback.html", context, status=429)
    try:
        services.submit(
            request.POST.get("message", ""),
            name=request.POST.get("name", ""),
            contact=request.POST.get("contact", ""),
            page=page,
        )
    except ValueError as exc:
        context.update(error=str(exc), form=request.POST)
        return render(request, "feedback/feedback.html", context, status=400)
    return HttpResponseRedirect(thanks, status=303)  # after a POST: GET the thank-you
