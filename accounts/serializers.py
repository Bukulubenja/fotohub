from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework import serializers

from accounts.models import PhotographerProfile, User

DUPLICATE_EMAIL = "An account with this email already exists."


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    display_name = serializers.CharField(max_length=120)

    def validate_email(self, value: str) -> str:
        email = value.lower()
        if User.objects.filter(email=email).exists():
            raise serializers.ValidationError(DUPLICATE_EMAIL)
        return email

    def validate(self, attrs: dict) -> dict:
        candidate = User(email=attrs["email"])
        try:
            password_validation.validate_password(attrs["password"], user=candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)}) from exc
        return attrs

    def create(self, validated_data: dict) -> User:
        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    email=validated_data["email"], password=validated_data["password"]
                )
                PhotographerProfile.objects.filter(user=user).update(
                    display_name=validated_data["display_name"]
                )
        except IntegrityError as exc:
            # A concurrent registration claimed the email after validate_email ran.
            raise serializers.ValidationError({"email": [DUPLICATE_EMAIL]}) from exc
        user.profile.refresh_from_db()
        return user


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(trim_whitespace=False)


class MeSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source="user.email", read_only=True)

    class Meta:
        model = PhotographerProfile
        fields = ["email", "display_name", "contact_phone", "brand_color"]
