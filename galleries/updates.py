"""Collection updates by email: "tell me when new photos are added".

- **Double opt-in.** A guest with the passkey gives an address; only a confirmation goes out
  until its owner presses the button behind the link. Anyone could type someone else's
  address, so nothing else is sent before that. Confirmations are resent at most every
  `RESEND_AFTER`, so the form can't be used to flood an inbox.
- **One summary, sent when things are quiet.** The hourly `send-collection-updates` task waits
  until the collection has had no new photos for `QUIET` (the photographer has finished
  uploading), and mails each subscriber at most once per `MIN_GAP`. Each email lists what
  changed since the last one, so nothing is lost by waiting.
- **Only what the passkey opens** (`collections.open_galleries`): drafts, expired galleries and
  events with their own password are never mentioned. Emails never contain the passkey,
  because they get forwarded.
- **One-click stop** (RFC 8058): every summary carries `List-Unsubscribe` headers, so mail apps
  show an unsubscribe button that works without opening FotoHub.
"""

import datetime
import email.policy
import logging
import secrets
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage, send_mail
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q
from django.urls import reverse
from django.utils import timezone

from assets.models import Photo
from billing import services as billing
from galleries import collections
from galleries.models import Collection, CollectionSubscription, Gallery

logger = logging.getLogger(__name__)

Status = CollectionSubscription.Status
RESEND_AFTER = datetime.timedelta(minutes=10)
QUIET = datetime.timedelta(hours=1)  # no new photos for this long: the upload is finished
MIN_GAP = datetime.timedelta(hours=20)  # about once a day, without drifting later each day
TOKEN_BYTES = 32
MAX_EMAIL_LENGTH = 254
# Python folds headers at 78 characters, and a URL has no space to fold at, so it would be sent
# as an encoded word (=?utf-8?q?...?=) that mail apps don't read as a link. RFC 5322 allows 998.
LONG_HEADERS = email.policy.default.clone(max_line_length=998)


@dataclass(frozen=True)
class Change:
    title: str
    event_date: datetime.date
    photos: int
    is_new: bool  # a new event, rather than more photos in one already reported


def clean_email(raw: str) -> str:
    """The address, lower-cased, or ValueError."""
    email = raw.strip().lower()
    if len(email) > MAX_EMAIL_LENGTH:
        raise ValueError("too long")
    try:
        validate_email(email)
    except ValidationError as exc:
        raise ValueError("not an email address") from exc
    return email


def _link(name: str, *args) -> str:
    return settings.SITE_URL.rstrip("/") + reverse(name, args=args)


def manage_link(subscription: CollectionSubscription) -> str:
    return _link("collection-updates", subscription.token)


def _send_confirmation(subscription: CollectionSubscription) -> None:
    name = subscription.collection.name
    send_mail(
        subject=f"Confirm photo updates from {name}",
        message=(
            f"Someone asked for an email when new photos from {name} are added on FotoHub.\n\n"
            f"If it was you, confirm here:\n{manage_link(subscription)}\n\n"
            "If it wasn't, ignore this email. You won't get anything else."
        ),
        from_email=None,
        recipient_list=[subscription.email],
    )


def request_updates(collection: Collection, email: str, now=None) -> CollectionSubscription:
    """Start (or restart) a subscription and send its confirmation when due. Already active
    subscriptions get nothing, so the form doesn't reveal who's subscribed."""
    now = now or timezone.now()
    email = clean_email(email)
    try:
        with transaction.atomic():
            subscription, _ = CollectionSubscription.objects.get_or_create(
                collection=collection,
                email=email,
                defaults={"token": secrets.token_urlsafe(TOKEN_BYTES)},
            )
    except IntegrityError:  # the same address, asked twice at once
        subscription = CollectionSubscription.objects.get(collection=collection, email=email)
    if subscription.status == Status.ACTIVE:
        return subscription
    recently = subscription.confirm_sent_at and now - subscription.confirm_sent_at < RESEND_AFTER
    if subscription.status == Status.PENDING and recently:
        return subscription
    # Claim the send, so two requests at once can't both mail it.
    claimed = (
        CollectionSubscription.objects.filter(pk=subscription.pk)
        .filter(Q(confirm_sent_at=subscription.confirm_sent_at) | Q(status=Status.STOPPED))
        .update(status=Status.PENDING, confirm_sent_at=now)
    )
    if claimed:
        subscription.refresh_from_db()
        _send_confirmation(subscription)
    return subscription


def confirm(subscription: CollectionSubscription, now=None) -> None:
    """Pending or stopped → active. News starts from now, not from when they first asked."""
    now = now or timezone.now()
    CollectionSubscription.objects.filter(pk=subscription.pk).exclude(status=Status.ACTIVE).update(
        status=Status.ACTIVE, confirmed_at=now, notified_until=now
    )


def stop(subscription: CollectionSubscription) -> None:
    CollectionSubscription.objects.filter(pk=subscription.pk).update(status=Status.STOPPED)


# --- the summary ------------------------------------------------------------------------------


def _ready_photos(galleries):
    # A ready photo's updated_at is when it became ready (assets.processing sets it then).
    return Photo.objects.filter(gallery__in=galleries, status=Photo.Status.READY)


def last_activity(collection: Collection) -> datetime.datetime | None:
    """When something guests can see last appeared: a gallery published or a photo ready."""
    galleries = collections.open_galleries(collection)
    stamps = [
        galleries.aggregate(at=Max("published_at"))["at"],
        _ready_photos(galleries).aggregate(at=Max("updated_at"))["at"],
    ]
    return max((s for s in stamps if s), default=None)


def changes(
    collection: Collection, since: datetime.datetime, until: datetime.datetime
) -> list[Change]:
    """New events (with all their photos) and more photos in older ones, newest event first.
    A new event is only listed once it has photos; until then it waits."""
    galleries = list(collections.open_galleries(collection).select_related("event"))
    added = dict(
        _ready_photos(galleries)
        .filter(updated_at__gt=since, updated_at__lte=until)
        .values_list("gallery")
        .annotate(n=Count("id"))
    )
    totals = dict(_ready_photos(galleries).values_list("gallery").annotate(n=Count("id")))
    found = []
    for gallery in galleries:
        is_new = gallery.published_at is not None and since < gallery.published_at <= until
        photos = totals.get(gallery.pk, 0) if is_new else added.get(gallery.pk, 0)
        if photos:
            found.append(Change(_title(gallery), gallery.event.event_date, photos, is_new))
    return sorted(found, key=lambda c: (not c.is_new, -c.event_date.toordinal()))


def _title(gallery: Gallery) -> str:
    event = gallery.event
    return event.title if gallery.title == event.title else f"{event.title} · {gallery.title}"


def summary(collection: Collection, found: list[Change], subscription) -> tuple[str, str]:
    lines = []
    for change in found:
        date = f"{change.event_date.day} {change.event_date:%B %Y}"
        plural = "s" if change.photos != 1 else ""
        if change.is_new:
            lines.append(f"- {change.title} ({date}): {change.photos} photo{plural}")
        else:
            lines.append(f"- {change.title}: {change.photos} new photo{plural}")
    body = "\n".join(
        [
            f"New on FotoHub from {collection.name}:",
            "",
            *lines,
            "",
            f"See them: {_link('public-collection', collection.pk)}",
            "Open it on the phone where you entered the passkey. On another phone, enter the "
            "passkey from your photographer's or school's message.",
            "",
            f"You asked for these updates. Stop them: {manage_link(subscription)}",
        ]
    )
    return f"New photos from {collection.name}", body


class UpdateEmail(EmailMessage):
    """Keeps the List-Unsubscribe URL on one plain header line (see LONG_HEADERS)."""

    def message(self, *, policy=LONG_HEADERS):
        return super().message(policy=policy)


def deliver(subscription: CollectionSubscription, subject: str, body: str) -> None:
    stop_link = _link("collection-updates-stop", subscription.token)
    UpdateEmail(
        subject,
        body,
        to=[subscription.email],
        headers={
            "List-Unsubscribe": f"<{stop_link}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
    ).send()


def _due(subscription: CollectionSubscription, now) -> bool:
    return subscription.last_sent_at is None or now - subscription.last_sent_at >= MIN_GAP


def _send_one(subscription, collection, found, now) -> bool:
    previous = (subscription.notified_until, subscription.last_sent_at)
    # Claim first, so two overlapping runs can't both send it.
    claimed = CollectionSubscription.objects.filter(
        pk=subscription.pk, status=Status.ACTIVE, notified_until=previous[0]
    ).update(notified_until=now, last_sent_at=now)
    if not claimed:
        return False
    try:
        deliver(subscription, *summary(collection, found, subscription))
    except Exception:
        logger.exception("collection update for subscription %s failed", subscription.pk)
        CollectionSubscription.objects.filter(pk=subscription.pk, notified_until=now).update(
            notified_until=previous[0], last_sent_at=previous[1]
        )
        return False
    return True


def send_collection(collection: Collection, subscriptions, now) -> int:
    if not billing.standing_for(collection.owner, now).galleries_live:
        return 0
    latest = last_activity(collection)
    if latest is None or now - latest < QUIET:
        return 0
    sent = 0
    found_since: dict = {}  # subscribers mailed in the same run share a cursor
    for subscription in subscriptions:
        since = subscription.notified_until
        if since is None or since >= latest or not _due(subscription, now):
            continue
        if since not in found_since:
            found_since[since] = changes(collection, since, now)
        if found_since[since] and _send_one(subscription, collection, found_since[since], now):
            sent += 1
    return sent


def send_updates(now=None) -> int:
    """Mail every subscriber whose collection has news and who is due. Returns how many."""
    now = now or timezone.now()
    active = (
        CollectionSubscription.objects.filter(
            status=Status.ACTIVE, collection__status=Collection.Status.ACTIVE
        )
        .select_related("collection__owner")
        .order_by("collection_id", "notified_until")
    )
    sent = 0
    by_collection: dict = {}
    for subscription in active.iterator():
        by_collection.setdefault(subscription.collection_id, []).append(subscription)
    for subscriptions in by_collection.values():
        sent += send_collection(subscriptions[0].collection, subscriptions, now)
    return sent
