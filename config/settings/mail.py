"""Building the MAILERS entry (Django 6.1) from environment settings."""

SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"


def mailer(backend: str, smtp_options: dict) -> dict:
    """SMTP options only for the SMTP backend: others (console, file) reject unknown options,
    and every email would fail."""
    if backend == SMTP_BACKEND:
        return {"BACKEND": backend, "OPTIONS": smtp_options}
    return {"BACKEND": backend}
