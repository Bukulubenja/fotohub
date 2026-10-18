import pytest
from django.urls import reverse

from accounts.models import User
from accounts.tests.factories import DEFAULT_PASSWORD

pytestmark = pytest.mark.django_db

REGISTER_PAYLOAD = {
    "email": "Amina@Example.com",
    "password": "a-long-unique-passphrase-9",
    "display_name": "Amina Studios",
}


class TestRegister:
    def test_creates_user_with_profile_and_logs_in(self, api_client):
        response = api_client.post(reverse("auth-register"), REGISTER_PAYLOAD, format="json")

        assert response.status_code == 201
        user = User.objects.get(email="amina@example.com")
        assert user.profile.display_name == "Amina Studios"
        assert "password" not in response.data
        assert api_client.get(reverse("me")).status_code == 200

    def test_rejects_duplicate_email_case_insensitively(self, api_client, photographer):
        payload = {**REGISTER_PAYLOAD, "email": photographer.email.upper()}

        response = api_client.post(reverse("auth-register"), payload, format="json")

        assert response.status_code == 400
        assert "email" in response.data

    def test_rejects_weak_password(self, api_client):
        payload = {**REGISTER_PAYLOAD, "password": "12345678"}

        response = api_client.post(reverse("auth-register"), payload, format="json")

        assert response.status_code == 400
        assert "password" in response.data
        assert not User.objects.exists()


class TestLogin:
    def test_valid_credentials_start_a_session(self, api_client, photographer):
        response = api_client.post(
            reverse("auth-login"),
            {"email": photographer.email, "password": DEFAULT_PASSWORD},
            format="json",
        )

        assert response.status_code == 200
        assert response.data["email"] == photographer.email
        assert api_client.get(reverse("me")).status_code == 200

    def test_wrong_password_gives_generic_error(self, api_client, photographer):
        response = api_client.post(
            reverse("auth-login"),
            {"email": photographer.email, "password": "wrong"},
            format="json",
        )

        assert response.status_code == 400
        assert response.data["detail"] == "Invalid email or password."

    def test_unknown_email_gives_same_generic_error(self, api_client):
        response = api_client.post(
            reverse("auth-login"),
            {"email": "nobody@example.com", "password": "whatever"},
            format="json",
        )

        assert response.status_code == 400
        assert response.data["detail"] == "Invalid email or password."

    def test_logout_ends_session(self, api_client, photographer):
        api_client.force_login(photographer)

        response = api_client.post(reverse("auth-logout"))

        assert response.status_code == 204
        assert api_client.get(reverse("me")).status_code == 403


class TestMe:
    def test_requires_authentication(self, api_client):
        assert api_client.get(reverse("me")).status_code == 403

    def test_returns_profile(self, auth_client, photographer):
        response = auth_client.get(reverse("me"))

        assert response.data == {
            "email": photographer.email,
            "display_name": photographer.profile.display_name,
            "contact_phone": "",
            "brand_color": "#172033",
        }

    def test_updates_profile_fields(self, auth_client, photographer):
        response = auth_client.patch(
            reverse("me"), {"display_name": "New Name", "brand_color": "#E56B55"}, format="json"
        )

        assert response.status_code == 200
        photographer.profile.refresh_from_db()
        assert photographer.profile.display_name == "New Name"
        assert photographer.profile.brand_color == "#E56B55"

    def test_rejects_invalid_brand_color(self, auth_client):
        response = auth_client.patch(reverse("me"), {"brand_color": "red"}, format="json")

        assert response.status_code == 400


def test_csrf_endpoint_sets_cookie(api_client):
    response = api_client.get(reverse("auth-csrf"))

    assert response.status_code == 204
    assert "csrftoken" in response.cookies


def test_login_is_throttled(api_client, strict_throttle):
    payload = {"email": "x@example.com", "password": "nope"}
    statuses = [
        api_client.post(reverse("auth-login"), payload, format="json").status_code for _ in range(3)
    ]

    assert statuses == [400, 400, 429]


def test_throttle_ignores_spoofed_forwarded_for(api_client, strict_throttle):
    payload = {"email": "x@example.com", "password": "nope"}
    statuses = [
        api_client.post(
            reverse("auth-login"), payload, format="json", HTTP_X_FORWARDED_FOR=f"10.0.0.{i}"
        ).status_code
        for i in range(3)
    ]

    assert statuses == [400, 400, 429]


def test_every_user_gets_a_profile_even_without_registration():
    superuser = User.objects.create_superuser("admin@example.com", "a-long-admin-pass-77")

    assert superuser.profile.display_name == "admin"


def test_registration_race_on_unique_email_returns_400(api_client, monkeypatch):
    from accounts import serializers

    # Simulate a concurrent request winning between validation and insert.
    monkeypatch.setattr(serializers.RegisterSerializer, "validate_email", lambda self, v: v.lower())
    api_client.post(reverse("auth-register"), REGISTER_PAYLOAD, format="json")

    response = api_client.post(reverse("auth-register"), REGISTER_PAYLOAD, format="json")

    assert response.status_code == 400
    assert "email" in response.data
