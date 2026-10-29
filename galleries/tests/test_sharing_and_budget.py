"""Share links and messages (FR-24), and the customer page's data budget (plan §3.3)."""

import gzip
import json
import re
from pathlib import Path
from urllib.parse import unquote

import pytest
from django.contrib.staticfiles import finders
from django.http import HttpResponse
from django.templatetags.static import static
from django.test import Client
from django.urls import reverse

from assets.tests.factories import ready_photo
from galleries.models import Gallery
from galleries.tests.factories import EventFactory, GalleryFactory

pytestmark = pytest.mark.django_db

# First screen must stay under 1 MB (plan §3.3). The HTML's share of that is kept small so
# the cover (~150 KB) and the first thumbnails (20-40 KB each) have room.
MAX_GZIPPED_HTML_BYTES = 20 * 1024
MAX_EAGER_IMAGES = 6
MAX_FONT_BYTES = 64 * 1024  # Inter, latin subset, all weights in one variable file


@pytest.fixture
def published(photographer):
    photographer.profile.display_name = "Lens & Light"
    photographer.profile.save()
    return GalleryFactory(
        event=EventFactory(owner=photographer, title="Amina & Daniel"),
        status=Gallery.Status.PUBLISHED,
    )


def page(client: Client, gallery: Gallery, **headers) -> HttpResponse:
    response = client.get(reverse("public-gallery-page", args=[gallery.public_token]), **headers)
    return response


class TestPhotographerShare:
    def test_api_returns_link_and_whatsapp_ready_message(self, auth_client, published):
        response = auth_client.get(reverse("gallery-detail", args=[published.pk]))

        url = response.data["share_url"]
        assert url == f"http://testserver/g/{published.public_token}/"
        message = response.data["share_message"]
        assert "Amina & Daniel (12 September 2026)" in message
        assert url in message
        assert message.endswith("— Lens & Light")
        assert "password" not in message

    def test_message_mentions_the_password_but_never_contains_it(self, auth_client, published):
        published.set_access_password("s3cret-words")
        published.save()

        message = auth_client.get(reverse("gallery-detail", args=[published.pk])).data[
            "share_message"
        ]

        assert "password I'll send you separately" in message
        assert "s3cret-words" not in message

    def test_list_stays_at_a_fixed_number_of_queries(
        self, auth_client, photographer, django_assert_max_num_queries
    ):
        for _ in range(5):
            GalleryFactory(event=EventFactory(owner=photographer))

        with django_assert_max_num_queries(4):  # session, user, count, galleries
            auth_client.get(reverse("gallery-list"))


class TestGuestShare:
    def test_page_offers_whatsapp_with_the_link(self, client: Client, published):
        html = page(client, published).content.decode()

        link = re.search(r'href="(https://wa\.me/\?text=[^"]+)"', html).group(1)
        text = unquote(link.split("=", 1)[1].replace("&amp;", "&"))
        assert text == (
            f"Photos from Amina & Daniel by Lens & Light: "
            f"http://testserver/g/{published.public_token}/"
        )

    def test_share_data_is_safely_embedded(self, client: Client, published):
        published.event.title = "</script><script>alert(1)</script>"
        published.event.save()

        html = page(client, published).content.decode()

        assert "</script><script>alert(1)" not in html
        blob = re.search(r'<script id="share-data" type="application/json">(.*?)</script>', html)
        assert json.loads(blob.group(1))["title"] == "</script><script>alert(1)</script>"


class TestDataBudget:
    @pytest.fixture
    def full_page(self, published, settings):
        for _ in range(settings.GALLERY_MEDIA["PAGE_SIZE"] + 5):
            ready_photo(published)
        return published

    def test_first_page_html_is_small_once_compressed(self, client: Client, full_page):
        response = page(client, full_page, headers={"accept-encoding": "gzip"})

        assert response["Content-Encoding"] == "gzip"
        assert len(response.content) <= MAX_GZIPPED_HTML_BYTES
        assert b"Load more photos" in gzip.decompress(response.content)

    def test_only_the_first_rows_load_eagerly(self, client: Client, full_page):
        html = page(client, full_page).content.decode()

        assert html.count('loading="eager"') == MAX_EAGER_IMAGES
        assert html.count('loading="lazy"') > 0

    def test_page_needs_no_other_downloads(self, client: Client, full_page):
        html = page(client, full_page).content.decode()

        assert '<link rel="stylesheet"' not in html
        assert "<script src=" not in html
        assert "fonts.googleapis" not in html and "fonts.gstatic" not in html

    def test_web_fonts_are_ours_and_small(self, client: Client, full_page):
        """Inter (UI) and Alumni Sans Inline One (wordmark only) are the only extra downloads:
        self-hosted, cached after the first visit, and small enough to keep the first screen
        far under 1 MB (plan §3.3)."""
        html = page(client, full_page).content.decode()

        fonts = [
            "galleries/fonts/inter-latin-wght-normal.woff2",
            "galleries/fonts/alumni-sans-inline-one-latin-400-normal.woff2",
        ]
        sources = re.findall(r'url\("([^"]+)"\)', html)
        assert sources == [static(font) for font in fonts]
        for font in fonts:
            assert Path(finders.find(font)).stat().st_size <= MAX_FONT_BYTES

    def test_images_reserve_their_space(self, client: Client, full_page):
        html = page(client, full_page).content.decode()

        images = re.findall(r"<img [^>]*>", html)
        assert images
        assert all('width="' in tag and 'height="' in tag for tag in images if "src=" in tag)


def test_hidden_buttons_stay_hidden(client: Client, published):
    """Regression (phone test 2026-10-24): .lb-btn's display rule un-hid "Download" on
    photos the visitor hadn't bought. [hidden] must beat every author display rule."""
    ready_photo(published)

    html = page(client, published).content.decode()

    assert "[hidden] { display: none !important; }" in html
    assert re.search(r'<a class="lb-btn" data-download href="#" hidden>', html)
