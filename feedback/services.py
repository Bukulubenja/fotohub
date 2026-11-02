"""Guest feedback: saved first, so a mail outage never loses a message, then sent to support."""

import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.core.validators import validate_email

from feedback.models import MAX_MESSAGE_LENGTH, Feedback

logger = logging.getLogger(__name__)


def safe_page(path: str) -> str:
    """A path on this site to send the guest back to; anything else (other sites, javascript:)
    becomes "", so the feedback form can't be used to bounce people elsewhere."""
    path = path.strip()
    if not path.startswith("/") or path.startswith("//") or "\\" in path:
        return ""
    return path[:200]


def _is_email(value: str) -> bool:
    try:
        validate_email(value)
    except ValidationError:
        return False
    return True


def submit(message: str, name: str = "", contact: str = "", page: str = "") -> Feedback:
    """Save the guest's message and email it to support. Raises ValueError for an empty or
    over-long message."""
    message = message.strip()
    if not message:
        raise ValueError("Write a message first.")
    if len(message) > MAX_MESSAGE_LENGTH:
        raise ValueError(f"Keep it under {MAX_MESSAGE_LENGTH:,} characters.")
    feedback = Feedback.objects.create(
        message=message,
        name=name.strip()[:80],
        contact=contact.strip()[:120],
        page=safe_page(page),
    )
    _email_support(feedback)
    return feedback


def _email_support(feedback: Feedback) -> None:
    if not settings.SUPPORT_EMAIL:
        return
    lines = [
        feedback.message,
        "",
        f"From: {feedback.name or 'not given'}",
        f"Contact: {feedback.contact or 'not given'}",
        f"Page: {feedback.page or 'not given'}",
    ]
    email = EmailMessage(
        subject=f"Feedback for {settings.BRAND['NAME']}",
        body="\n".join(lines),
        to=[settings.SUPPORT_EMAIL],
        reply_to=[feedback.contact] if _is_email(feedback.contact) else [],
    )
    try:
        email.send()
    except Exception:  # the message is saved; the admin still shows it
        logger.exception("feedback %s: email to support failed", feedback.pk)
