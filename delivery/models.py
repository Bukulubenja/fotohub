import uuid

from django.db import models

from galleries.models import Gallery
from shop.models import Order


class DownloadJob(models.Model):
    """A ZIP of the photos in one paid order (spec FR-21). Re-requests reuse the live job."""

    class Status(models.TextChoices):
        QUEUED = "queued"
        BUILDING = "building"
        READY = "ready"
        FAILED = "failed"
        EXPIRED = "expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    gallery = models.ForeignKey(Gallery, on_delete=models.CASCADE, related_name="download_jobs")
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="download_jobs")
    policy = models.CharField(max_length=10, choices=Gallery.DownloadPolicy)
    # Which photos (and versions of them) the archive holds; a change means a new archive.
    fingerprint = models.CharField(max_length=64)
    status = models.CharField(max_length=10, choices=Status, default=Status.QUEUED)
    photo_count = models.PositiveIntegerField()
    expected_bytes = models.PositiveBigIntegerField()
    size_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    failure_reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["gallery", "policy", "fingerprint", "status"])]

    def __str__(self) -> str:
        return f"{self.gallery} ({self.status})"

    @property
    def archive_key(self) -> str:
        return f"archives/{self.gallery_id}/{self.pk}.zip"

    @property
    def is_pending(self) -> bool:
        return self.status in (self.Status.QUEUED, self.Status.BUILDING)
