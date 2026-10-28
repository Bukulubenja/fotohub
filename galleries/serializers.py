from django.utils import timezone
from rest_framework import serializers

from assets.models import Photo
from galleries import collections, sharing
from galleries.models import Collection, CollectionSubscription, Event, Gallery
from galleries.services import update_gallery_settings


class CollectionSerializer(serializers.ModelSerializer):
    # Only on create: the readable start of the passkey. Defaults to the name's initials.
    prefix = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=8)
    event_count = serializers.SerializerMethodField()
    # How many guests get email updates. Never who: the addresses stay with FotoHub.
    subscriber_count = serializers.SerializerMethodField()
    share_url = serializers.SerializerMethodField()
    share_message = serializers.SerializerMethodField()

    class Meta:
        model = Collection
        fields = [
            "id",
            "name",
            "prefix",
            "passkey",
            "status",
            "event_count",
            "subscriber_count",
            "share_url",
            "share_message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "passkey", "created_at", "updated_at"]

    def validate_prefix(self, value: str) -> str:
        if not value:
            return ""
        try:
            return collections.validate_prefix(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def get_event_count(self, collection: Collection) -> int:
        annotated = getattr(collection, "event_count", None)
        return annotated if annotated is not None else collection.events.count()

    def get_subscriber_count(self, collection: Collection) -> int:
        annotated = getattr(collection, "subscriber_count", None)
        if annotated is not None:
            return annotated
        return collection.subscriptions.filter(status=CollectionSubscription.Status.ACTIVE).count()

    def get_share_url(self, collection: Collection) -> str:
        return sharing.home_url(self.context["request"])

    def get_share_message(self, collection: Collection) -> str:
        return sharing.collection_message(collection, self.get_share_url(collection))

    def create(self, validated_data: dict) -> Collection:
        return collections.create_collection(
            self.context["request"].user,
            validated_data["name"],
            prefix=validated_data.get("prefix", ""),
        )

    def update(self, instance: Collection, validated_data: dict) -> Collection:
        validated_data.pop("prefix", None)  # a new passkey is its own action (rotate-passkey)
        status = validated_data.pop("status", None)
        instance = super().update(instance, validated_data)
        return collections.set_status(instance, status) if status else instance


class EventSerializer(serializers.ModelSerializer):
    collection = serializers.PrimaryKeyRelatedField(
        queryset=Collection.objects.none(), required=False, allow_null=True
    )

    class Meta:
        model = Event
        fields = [
            "id",
            "title",
            "event_date",
            "description",
            "location_label",
            "collection",
            "separate_access",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request is not None and request.user.is_authenticated:
            # Only the photographer's own collections are valid targets (tenant isolation).
            self.fields["collection"].queryset = Collection.objects.owned_by(request.user)


class GallerySerializer(serializers.ModelSerializer):
    event = serializers.PrimaryKeyRelatedField(queryset=Event.objects.none())
    password = serializers.CharField(
        write_only=True, required=False, allow_null=True, allow_blank=True, max_length=128
    )
    has_password = serializers.BooleanField(read_only=True)
    cover_photo = serializers.PrimaryKeyRelatedField(
        queryset=Photo.objects.none(), required=False, allow_null=True
    )
    share_url = serializers.SerializerMethodField()
    share_message = serializers.SerializerMethodField()

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
            "cover_photo",
            "share_url",
            "share_message",
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
            self.fields["cover_photo"].queryset = Photo.objects.owned_by(request.user)

    def validate_expires_at(self, value):
        unchanged = self.instance is not None and value == self.instance.expires_at
        if value is not None and not unchanged and value <= timezone.now():
            raise serializers.ValidationError("Expiry must be in the future.")
        return value

    def get_share_url(self, gallery: Gallery) -> str:
        return sharing.share_url(self.context["request"], gallery)

    def get_share_message(self, gallery: Gallery) -> str:
        return sharing.photographer_message(gallery, self.get_share_url(gallery))

    def validate(self, attrs: dict) -> dict:
        cover = attrs.get("cover_photo")
        if cover is not None and (self.instance is None or cover.gallery_id != self.instance.pk):
            raise serializers.ValidationError({"cover_photo": "Pick a photo from this gallery."})
        if cover is not None and cover.status != Photo.Status.READY:
            raise serializers.ValidationError({"cover_photo": "That photo is still processing."})
        return attrs

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
