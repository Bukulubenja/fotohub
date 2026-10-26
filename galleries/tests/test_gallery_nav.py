"""The customer gallery's intro wall, tab bar, favourites and profile pages."""

import pytest
from django.test import Client
from django.urls import reverse

from assets.tests.factories import ready_photo
from galleries import public_views
from galleries.models import Gallery
from galleries.tests.factories import EventFactory, GalleryFactory
from shop.models import Order
from shop.tests.factories import make_order

pytestmark = pytest.mark.django_db
JSON = {"HTTP_ACCEPT": "application/json"}


@pytest.fixture
def published(photographer):
    return GalleryFactory(
        event=EventFactory(owner=photographer, title="Amina & Daniel"),
        status=Gallery.Status.PUBLISHED,
    )


def url(name: str, gallery: Gallery) -> str:
    return reverse(name, args=[gallery.public_token])


def toggle(client: Client, gallery: Gallery, photo, **headers):
    return client.post(
        url("public-gallery-favourite-toggle", gallery), {"photo": str(photo)}, **headers
    )


class TestIntro:
    def test_wall_repeats_the_first_photos_to_fill_every_column(self):
        columns = public_views.intro_columns(["a", "b", "c"])

        assert len(columns) == public_views.INTRO_COLUMNS
        assert all(len(column) == public_views.INTRO_PER_COLUMN for column in columns)
        assert {photo for column in columns for photo in column} == {"a", "b", "c"}

    def test_wall_uses_only_the_first_few_photos(self):
        photos = list(range(100))

        used = {photo for column in public_views.intro_columns(photos) for photo in column}

        assert used == set(range(public_views.INTRO_PHOTOS))

    def test_no_photos_no_wall(self):
        assert public_views.intro_columns([]) == []

    def test_gallery_opens_on_the_wall_with_a_way_into_the_photos(self, client: Client, published):
        photo = ready_photo(published)

        html = client.get(url("public-gallery-page", published)).content.decode()

        assert 'class="mosaic" aria-hidden="true"' in html
        assert html.index(f"{photo.pk}/thumb.webp") < html.index('id="photos"')
        assert 'href="#photos"' in html

    def test_empty_gallery_still_shows_the_title(self, client: Client, published):
        html = client.get(url("public-gallery-page", published)).content.decode()

        assert 'class="mosaic"' not in html
        assert "Amina &amp; Daniel" in html


class TestTabBar:
    @pytest.mark.parametrize(
        ("page", "active"),
        [
            ("public-gallery-page", "Home"),
            ("shop-cart", "Cart"),
            ("public-gallery-favourites", "Favourites"),
            ("public-gallery-profile", "Profile"),
        ],
    )
    def test_every_page_links_all_four_tabs_and_marks_its_own(
        self, client: Client, published, page, active
    ):
        html = client.get(url(page, published)).content.decode()

        for name in ("public-gallery-page", "shop-cart", "public-gallery-favourites"):
            assert f'href="{url(name, published)}"' in html
        assert f'href="{url("public-gallery-profile", published)}"' in html
        nav = html.split('<nav class="tabbar"')[1].split("</nav>")[0]
        tabs = nav.split("</a>")
        current = [tab for tab in tabs if 'aria-current="page"' in tab]
        assert len(current) == 1
        assert f"<span>{active}</span>" in current[0]

    def test_badges_count_the_cart_and_favourites(self, client: Client, published):
        a, b = ready_photo(published), ready_photo(published)
        client.post(url("shop-cart-add", published), {"photo": [str(a.pk)]})
        toggle(client, published, a.pk)
        toggle(client, published, b.pk)

        html = client.get(url("public-gallery-page", published)).content.decode()

        assert '<span class="badge" data-cart-count>1</span>' in html
        assert '<span class="badge" data-fav-count>2</span>' in html


class TestFavourites:
    def test_toggle_adds_then_removes(self, client: Client, published):
        photo = ready_photo(published)

        added = toggle(client, published, photo.pk, **JSON).json()
        removed = toggle(client, published, photo.pk, **JSON).json()

        assert added == {"favourite": True, "count": 1}
        assert removed == {"favourite": False, "count": 0}

    def test_page_lists_only_favourites(self, client: Client, published):
        liked, other = ready_photo(published), ready_photo(published)
        toggle(client, published, liked.pk)

        html = client.get(url("public-gallery-favourites", published)).content.decode()

        assert f"{liked.pk}/thumb.webp" in html
        assert f"{other.pk}/thumb.webp" not in html
        assert 'Saved <span class="muted">(1)</span>' in html

    def test_without_javascript_the_form_lands_on_favourites(self, client: Client, published):
        response = toggle(client, published, ready_photo(published).pk)

        assert response.status_code == 303
        assert response["Location"] == url("public-gallery-favourites", published)

    def test_photo_from_another_gallery_is_refused(self, client: Client, published, photographer):
        elsewhere = GalleryFactory(
            event=EventFactory(owner=photographer), status=Gallery.Status.PUBLISHED
        )
        foreign = ready_photo(elsewhere)

        response = toggle(client, published, foreign.pk, **JSON)

        assert response.status_code == 400
        assert client.session.get("favourites", {}) == {}

    def test_nonsense_id_is_refused(self, client: Client, published):
        assert toggle(client, published, "not-a-uuid", **JSON).status_code == 400
        assert toggle(client, published, "not-a-uuid").status_code == 404

    def test_removed_photo_drops_out(self, client: Client, published):
        photo = ready_photo(published)
        toggle(client, published, photo.pk)
        photo.delete()

        html = client.get(url("public-gallery-favourites", published)).content.decode()

        assert "No favourites yet" in html

    def test_locked_gallery_sends_visitor_to_the_password_page(self, client: Client, published):
        photo = ready_photo(published)
        published.set_access_password("secret-pass")
        published.save()

        assert client.get(url("public-gallery-favourites", published)).status_code == 302
        assert toggle(client, published, photo.pk, **JSON).status_code == 302

    def test_private_headers(self, client: Client, published):
        response = client.get(url("public-gallery-favourites", published))

        assert response["X-Robots-Tag"] == "noindex, nofollow"
        assert response["Cache-Control"] == "private, no-store"


class TestProfile:
    def test_lists_this_browsers_orders_for_this_gallery(self, client: Client, published):
        photo = ready_photo(published)
        paid = make_order(published, [photo], client=client)
        pending = make_order(published, [photo], status=Order.Status.PENDING, client=client)
        strangers = make_order(published, [photo])

        html = client.get(url("public-gallery-profile", published)).content.decode()

        assert reverse("shop-order", args=[paid.token]) in html
        assert reverse("shop-order", args=[pending.token]) in html
        assert reverse("shop-order", args=[strangers.token]) not in html
        assert "Paid · Download" in html
        assert "Waiting for payment" in html

    def test_no_orders_explains_where_they_are(self, client: Client, published):
        html = client.get(url("public-gallery-profile", published)).content.decode()

        assert "No purchases from this gallery on this device yet." in html

    def test_shows_how_to_reach_the_photographer(self, client: Client, published, photographer):
        photographer.profile.contact_phone = "+256772123456"
        photographer.profile.save()

        html = client.get(url("public-gallery-profile", published)).content.decode()

        assert 'href="tel:+256772123456"' in html
        assert photographer.profile.display_name in html

    def test_locked_gallery_sends_visitor_to_the_password_page(self, client: Client, published):
        published.set_access_password("secret-pass")
        published.save()

        response = client.get(url("public-gallery-profile", published))

        assert response.status_code == 302
        assert response["Location"] == url("public-gallery-page", published)


class TestTrustCopy:
    def test_gallery_states_privacy_and_how_to_ask_for_a_takedown(
        self, client: Client, published, settings
    ):
        settings.SUPPORT_EMAIL = "help@example.com"
        ready_photo(published)

        html = client.get(url("public-gallery-page", published)).content.decode()

        assert "Private gallery" in html
        assert "This is a private event gallery." in html
        assert "Need an image reviewed or removed?" in html
        assert 'href="mailto:help@example.com?subject=Takedown%20request' in html

    def test_without_a_support_address_guests_are_sent_to_the_photographer(
        self, client: Client, published, settings
    ):
        settings.SUPPORT_EMAIL = ""

        html = client.get(url("public-gallery-page", published)).content.decode()

        assert "mailto:" not in html
        assert f"{url('public-gallery-profile', published)}#photographer" in html

    def test_cart_links_the_terms(self, client: Client, published):
        photo = ready_photo(published)
        client.post(url("shop-cart-add", published), {"photo": [str(photo.pk)]})

        html = client.get(url("shop-cart", published)).content.decode()

        assert "1 photo · <strong>UGX 900</strong>" in html
        assert f'href="{reverse("purchase-terms")}"' in html
        assert "Pay securely" in html

    def test_terms_page_shows_current_prices(self, client: Client, settings):
        settings.SUPPORT_EMAIL = "help@example.com"

        html = client.get(reverse("purchase-terms")).content.decode()

        assert "UGX 900 by mobile money or $0.24 by card" in html
        assert "mailto:help@example.com" in html

    def test_pending_order_uses_the_blueprint_wording(self, client: Client, published):
        order = make_order(published, [ready_photo(published)], status=Order.Status.PENDING)

        html = client.get(reverse("shop-order", args=[order.token])).content.decode()

        assert "Keep this page open or check your purchases shortly." in html
