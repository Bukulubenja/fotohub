from django.db import models

MAX_MESSAGE_LENGTH = 2000


class Feedback(models.Model):
    """A guest's message from the "Send feedback" page, read and ticked off in the admin."""

    message = models.TextField(max_length=MAX_MESSAGE_LENGTH)
    name = models.CharField(max_length=80, blank=True)
    contact = models.CharField(max_length=120, blank=True, help_text="Email or phone, if given.")
    page = models.CharField(max_length=200, blank=True, help_text="Where the guest came from.")
    handled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "feedback"

    def __str__(self) -> str:
        return self.message[:60]
