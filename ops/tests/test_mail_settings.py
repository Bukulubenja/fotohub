"""Production mail settings: SMTP options only go to the SMTP backend.

The console backend (used on the server until an email service is set up) rejects options it
doesn't know, which made every email in production fail (found 2026-10-29).
"""

from django.core import mail
from django.test import override_settings

from config.settings.mail import SMTP_BACKEND, mailer

SMTP = {"host": "smtp.example.com", "port": 587, "username": "u", "password": "p", "use_tls": True}
CONSOLE = "django.core.mail.backends.console.EmailBackend"


def test_smtp_backend_gets_its_options():
    assert mailer(SMTP_BACKEND, SMTP) == {"BACKEND": SMTP_BACKEND, "OPTIONS": SMTP}


def test_other_backends_get_no_smtp_options():
    assert mailer(CONSOLE, SMTP) == {"BACKEND": CONSOLE}


def test_console_backend_from_production_settings_can_send(capsys):
    with override_settings(MAILERS={"default": mailer(CONSOLE, SMTP)}):
        sent = mail.send_mail("Hello", "Body", None, ["guest@example.com"])

    assert sent == 1
    assert "Subject: Hello" in capsys.readouterr().out
