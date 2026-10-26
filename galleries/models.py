import datetime
import secrets
import uuid

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone


def generate_public_token() -> str:
    """Unpredictable bearer token used in customer links (spec FR-19)."""
    return secrets.token_urlsafe(settings.GALLERY_TOKEN_BYTES)


class EventQuerySet(models.QuerySet["Event"]):
    def owned_by(self, user) -> "EventQuerySet":
        return self.filter(owner=user)


class Event(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="events"
    )
    title = models.CharField(max_length=200)
    event_date = models.DateField()
    description = models.TextField(blank=True)
    location_label = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = EventQuerySet.as_manager()

    class Meta:
        ordering = ["-event_date", "-created_at"]

    def __str__(self) -> str:
        return self.title


class GalleryQuerySet(models.QuerySet["Gallery"]):
    def owned_by(self, user) -> "GalleryQuerySet":
        return self.filter(event__owner=user)


class Gallery(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft"
        PROCESSING = "processing"
        PUBLISHED = "published"
        ARCHIVED = "archived"
        DELETION_PENDING = "deletion_pending"

    class DownloadPolicy(models.TextChoices):
        DISABLED = "disabled"
        WEB = "web"
        ORIGINAL = "original"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="galleries")
    title = models.CharField(max_length=200)
    public_token = models.CharField(
        max_length=64, unique=True, default=generate_public_token, editable=False
    )
    status = models.CharField(max_length=20, choices=Status, default=Status.DRAFT)
    download_policy = models.CharField(
        max_length=10, choices=DownloadPolicy, default=DownloadPolicy.WEB
    )
    password_hash = models.CharField(max_length=128, blank=True)
    # Bumped whenever the password or link changes, invalidating unlocked sessions.
    access_version = models.PositiveIntegerField(default=1)
    expires_at = models.DateTimeField(null=True, blank=True)
    # Hero image and share preview. Unset (or deleted) means the first ready photo.
    cover_photo = models.ForeignKey(
        "assets.Photo", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = GalleryQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "galleries"

    def __str__(self) -> str:
        return self.title

    @property
    def has_password(self) -> bool:
        return bool(self.password_hash)

    def set_access_password(self, raw_password: str | None) -> None:
        self.password_hash = make_password(raw_password) if raw_password else ""
        self.access_version += 1

    def check_access_password(self, raw_password: str) -> bool:
        return self.has_password and check_password(raw_password, self.password_hash)

    def is_expired(self, now: datetime.datetime | None = None) -> bool:
        return self.expires_at is not None and self.expires_at <= (now or timezone.now())

    def is_viewable(self, now: datetime.datetime | None = None) -> bool:
        return self.status == self.Status.PUBLISHED and not self.is_expired(now)


class GalleryDayStats(models.Model):
    """Per-gallery daily counters for the photographer's stats (blueprint: event insights).

    Sales come from orders; only what orders can't tell is counted here.
    """

    gallery = models.ForeignKey(Gallery, on_delete=models.CASCADE, related_name="day_stats")
    day = models.DateField()
    views = models.PositiveIntegerField(default=0)  # full gallery page loads by guests
    visitors = models.PositiveIntegerField(default=0)  # first view per browser per day
    favourites = models.IntegerField(default=0)  # hearts added minus removed that day

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["gallery", "day"], name="one_stats_row_per_gallery_day")
        ]
        verbose_name_plural = "gallery day stats"

    def __str__(self) -> str:
        return f"{self.gallery} {self.day}"
