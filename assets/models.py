import uuid

from django.db import models
from django.utils import timezone

from galleries.models import Gallery


class PhotoQuerySet(models.QuerySet["Photo"]):
    def owned_by(self, user) -> "PhotoQuerySet":
        return self.filter(gallery__event__owner=user)

    def counting_toward_quota(self, now=None) -> "PhotoQuerySet":
        """Stored photos plus uploads still holding a live reservation (spec FR-27)."""
        live_upload = models.Q(
            status=Photo.Status.UPLOADING,
            upload_session__state=UploadSession.State.ACTIVE,
            upload_session__expires_at__gt=now or timezone.now(),
        )
        return self.filter(~models.Q(status=Photo.Status.UPLOADING) | live_upload)


class Photo(models.Model):
    class Status(models.TextChoices):
        UPLOADING = "uploading"
        PROCESSING = "processing"
        READY = "ready"
        FAILED = "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    gallery = models.ForeignKey(Gallery, on_delete=models.CASCADE, related_name="photos")
    # Shown to the photographer only; never used to build storage keys.
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=50)
    size_bytes = models.PositiveBigIntegerField()
    original_key = models.CharField(max_length=255, unique=True)
    status = models.CharField(max_length=12, choices=Status, default=Status.UPLOADING)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    # {"thumb": {"key": ..., "width": ..., "height": ..., "bytes": ...}, ...}
    variants = models.JSONField(default=dict, blank=True)
    failure_reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = PhotoQuerySet.as_manager()

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["gallery", "status"])]

    def __str__(self) -> str:
        return self.original_filename


class UploadSession(models.Model):
    """Server-side state for one file's upload, so retries survive a closed browser (FR-12)."""

    class State(models.TextChoices):
        ACTIVE = "active"
        COMPLETED = "completed"

    photo = models.OneToOneField(Photo, on_delete=models.CASCADE, related_name="upload_session")
    # S3 multipart UploadId; blank for small files sent with a single presigned PUT.
    upload_id = models.CharField(max_length=1024, blank=True)
    part_size = models.PositiveIntegerField(null=True, blank=True)
    state = models.CharField(max_length=10, choices=State, default=State.ACTIVE)
    expires_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["state", "expires_at"])]

    def __str__(self) -> str:
        return f"Upload of {self.photo_id} ({self.state})"

    @property
    def is_multipart(self) -> bool:
        return bool(self.upload_id)

    @property
    def part_count(self) -> int:
        if not self.part_size:
            return 1
        return max(1, -(-self.photo.size_bytes // self.part_size))

    def is_expired(self, now=None) -> bool:
        return self.expires_at <= (now or timezone.now())
