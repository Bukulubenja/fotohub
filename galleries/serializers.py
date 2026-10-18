from django.utils import timezone
from rest_framework import serializers

from galleries.models import Event, Gallery
from galleries.services import update_gallery_settings


class EventSerializer(serializers.ModelSerializer):
    class Meta:
        model = Event
        fields = [
            "id",
            "title",
            "event_date",
            "description",
            "location_label",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class GallerySerializer(serializers.ModelSerializer):
    event = serializers.PrimaryKeyRelatedField(queryset=Event.objects.none())
    password = serializers.CharField(
        write_only=True, required=False, allow_null=True, allow_blank=True, max_length=128
    )
    has_password = serializers.BooleanField(read_only=True)

    class Meta:
        model = Gallery
        fields = [
            "id",
            "event",
            "title",
            "status",
            "public_token",
            "download_policy",
            "password",
            "has_password",
            "expires_at",
            "published_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "public_token",
            "published_at",
            "created_at",
            "updated_at",
        ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request is not None and request.user.is_authenticated:
            # Only the photographer's own events are valid targets (tenant isolation).
            self.fields["event"].queryset = Event.objects.owned_by(request.user)

    def validate_expires_at(self, value):
        unchanged = self.instance is not None and value == self.instance.expires_at
        if value is not None and not unchanged and value <= timezone.now():
            raise serializers.ValidationError("Expiry must be in the future.")
        return value

    def create(self, validated_data: dict) -> Gallery:
        password = validated_data.pop("password", None)
        gallery = Gallery(**validated_data)
        if password:
            gallery.set_access_password(password)
        gallery.save()
        return gallery

    def update(self, instance: Gallery, validated_data: dict) -> Gallery:
        return update_gallery_settings(instance, validated_data)


class LockedGallerySerializer(serializers.ModelSerializer):
    """Lock screen: just enough to recognise the gallery before entering the password."""

    photographer = serializers.CharField(source="event.owner.profile.display_name")
    requires_password = serializers.BooleanField(source="has_password")
    is_unlocked = serializers.SerializerMethodField()

    class Meta:
        model = Gallery
        fields = ["title", "photographer", "requires_password", "is_unlocked"]

    def get_is_unlocked(self, gallery: Gallery) -> bool:
        return False


class PublicGallerySerializer(serializers.ModelSerializer):
    """What an unlocked customer may see. Never add storage keys, ids or owner details here."""

    event_title = serializers.CharField(source="event.title")
    event_date = serializers.DateField(source="event.event_date")
    description = serializers.CharField(source="event.description")
    photographer = serializers.CharField(source="event.owner.profile.display_name")
    requires_password = serializers.BooleanField(source="has_password")
    is_unlocked = serializers.SerializerMethodField()

    class Meta:
        model = Gallery
        fields = [
            "title",
            "event_title",
            "event_date",
            "description",
            "photographer",
            "download_policy",
            "requires_password",
            "is_unlocked",
        ]

    def get_is_unlocked(self, gallery: Gallery) -> bool:
        return True


class UnlockSerializer(serializers.Serializer):
    password = serializers.CharField(trim_whitespace=False, max_length=128)
