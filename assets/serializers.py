import ntpath

from django.conf import settings
from rest_framework import serializers

from assets.models import Photo
from assets.storage import get_storage
from galleries.models import Gallery


class StartUploadSerializer(serializers.Serializer):
    gallery = serializers.PrimaryKeyRelatedField(queryset=Gallery.objects.none())
    filename = serializers.CharField(max_length=255)
    content_type = serializers.ChoiceField(choices=settings.UPLOADS["ALLOWED_CONTENT_TYPES"])
    size = serializers.IntegerField(min_value=1, max_value=settings.UPLOADS["MAX_FILE_BYTES"])

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request is not None and request.user.is_authenticated:
            # Only the photographer's own galleries are valid targets (tenant isolation).
            self.fields["gallery"].queryset = Gallery.objects.owned_by(request.user).select_related(
                "event"
            )

    def validate_filename(self, value: str) -> str:
        # Browsers may send "C:\\fakepath\\IMG_1.jpg"; keep the base name for display only.
        name = ntpath.basename(value).strip()
        if not name:
            raise serializers.ValidationError("Give the file a name.")
        return name


class SignPartsSerializer(serializers.Serializer):
    part_numbers = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=False,
        max_length=settings.UPLOADS["MAX_PARTS_PER_SIGN_REQUEST"],
    )


class PhotoSerializer(serializers.ModelSerializer):
    """Owner view of a photo. Storage keys stay server-side; only signed URLs go out."""

    thumbnail_url = serializers.SerializerMethodField()

    class Meta:
        model = Photo
        fields = [
            "id",
            "gallery",
            "original_filename",
            "content_type",
            "size_bytes",
            "status",
            "width",
            "height",
            "failure_reason",
            "thumbnail_url",
            "created_at",
        ]
        read_only_fields = fields

    def get_thumbnail_url(self, photo: Photo) -> str | None:
        thumb = photo.variants.get("thumb")
        return get_storage().presign_get(thumb["key"]) if thumb else None


class StorageUsageSerializer(serializers.Serializer):
    used_bytes = serializers.IntegerField()
    quota_bytes = serializers.IntegerField()
    percent = serializers.IntegerField()
    warning_level = serializers.IntegerField(allow_null=True)
