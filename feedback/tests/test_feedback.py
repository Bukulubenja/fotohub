"""Guest feedback (the school's review, 2026-11-02): saved for the admin, emailed to support."""

import pytest
from django.contrib.admin.sites import site
from django.core import mail
from django.test import Client
from django.urls import reverse

from feedback import services
from feedback.models import Feedback
from galleries.models import Gallery
from galleries.tests.factories import EventFactory, GalleryFactory

pytestmark = pytest.mark.django_db
URL = reverse("feedback")
MESSAGE = "The photos are lovely, but I couldn't find the Sports Day album."


def send(client: Client, **fields):
    return client.post(URL, {"message": MESSAGE, **fields})


@pytest.fixture
def support(settings):
    settings.SUPPORT_EMAIL = "support@school.test"


class TestForm:
    def test_page_offers_the_form(self, client: Client):
        response = client.get(URL)

        html = response.content.decode()
        assert response.status_code == 200
        assert 'name="message"' in html and "csrfmiddlewaretoken" in html
        assert response["X-Robots-Tag"].startswith("noindex")

    def test_every_guest_page_links_to_it(self, client: Client, photographer):
        gallery = GalleryFactory(
            event=EventFactory(owner=photographer), status=Gallery.Status.PUBLISHED
        )
        page = reverse("public-gallery-page", args=[gallery.public_token])

        html = client.get(page).content.decode()

        assert "Send feedback" in html
        assert f"{URL}?from={page.replace('/', '%2F')}" in html

    def test_thanks_and_a_way_back_after_sending(self, client: Client, support):
        response = send(client, page="/g/abc/")

        assert response.status_code == 303
        html = client.get(response["Location"]).content.decode()
        assert "Thank you" in html
        assert 'href="/g/abc/"' in html

    def test_back_link_never_leaves_the_site(self, client: Client):
        for outside in ["https://evil.test/", "//evil.test/", "javascript:alert(1)"]:
            html = client.get(URL, {"from": outside}).content.decode()
            assert "evil.test" not in html and "javascript:" not in html


class TestSaving:
    def test_saved_and_emailed_to_support(self, client: Client, support):
        send(client, name="Amina", contact="amina@example.test", page="/g/abc/")

        feedback = Feedback.objects.get()
        assert (feedback.message, feedback.name, feedback.page) == (MESSAGE, "Amina", "/g/abc/")
        assert not feedback.handled
        [email] = mail.outbox
        assert email.to == ["support@school.test"]
        assert email.reply_to == ["amina@example.test"]
        assert MESSAGE in email.body and "/g/abc/" in email.body

    def test_saved_even_without_a_support_address(self, client: Client, settings):
        settings.SUPPORT_EMAIL = ""

        send(client)

        assert Feedback.objects.count() == 1
        assert not mail.outbox

    def test_a_failed_email_does_not_lose_the_message(self, client: Client, support, monkeypatch):
        def broken(*args, **kwargs):
            raise OSError("mail server down")

        monkeypatch.setattr(services.EmailMessage, "send", broken)

        assert send(client).status_code == 303
        assert Feedback.objects.count() == 1

    def test_phone_numbers_are_not_used_as_a_reply_address(self, client: Client, support):
        send(client, contact="0772 123456")

        assert mail.outbox[0].reply_to == []
        assert "0772 123456" in mail.outbox[0].body

    @pytest.mark.parametrize("message", ["", "   ", "x" * 2001])
    def test_empty_or_huge_messages_are_refused(self, client: Client, message):
        response = client.post(URL, {"message": message})

        assert response.status_code == 400
        assert not Feedback.objects.exists()

    def test_what_was_typed_is_kept_after_an_error(self, client: Client):
        response = client.post(URL, {"message": "", "name": "Amina"})

        assert 'value="Amina"' in response.content.decode()

    def test_spam_bots_are_quietly_ignored(self, client: Client, support):
        response = send(client, website="http://spam.test")

        assert response.status_code == 303
        assert not Feedback.objects.exists()
        assert not mail.outbox

    def test_sending_is_rate_limited(self, client: Client, strict_throttle):
        statuses = [send(client).status_code for _ in range(3)]

        assert statuses == [303, 303, 429]
        assert Feedback.objects.count() == 2

    def test_requires_a_csrf_token(self):
        strict = Client(enforce_csrf_checks=True)

        assert strict.post(URL, {"message": MESSAGE}).status_code == 403
        assert not Feedback.objects.exists()


def test_admin_can_read_and_mark_messages():
    assert Feedback in site._registry
