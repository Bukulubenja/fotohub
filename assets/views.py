import uuid
from collections.abc import Callable

from django.conf import settings
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from assets import services
from assets.models import Photo
from assets.serializers import (
    PhotoSerializer,
    SignPartsSerializer,
    StartUploadSerializer,
    StorageUsageSerializer,
)


class UploadConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "upload_conflict"


class StorageUnavailableError(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Photo storage is busy. Your upload will retry shortly."
    default_code = "storage_unavailable"


def _call(operation: Callable, *args, **kwargs):
    """Map service errors onto HTTP: 409 for state/quota problems, 503 for storage outages."""
    try:
        return operation(*args, **kwargs)
    except services.QuotaExceeded as exc:
        raise UploadConflict(str(exc), code="quota_exceeded") from exc
    except services.SubscriptionInactive as exc:
        raise UploadConflict(str(exc), code="subscription_inactive") from exc
    except services.UploadError as exc:
        raise UploadConflict(str(exc)) from exc
    except services.StorageUnavailable as exc:
        raise StorageUnavailableError() from exc


class OwnedPhotoMixin:
    def get_queryset(self):
        return Photo.objects.owned_by(self.request.user).select_related(
            "upload_session", "gallery__event"
        )


class UploadViewSet(OwnedPhotoMixin, viewsets.GenericViewSet):
    """Signing endpoints for Uppy's AWS S3 plugin. Bytes go straight to storage."""

    serializer_class = PhotoSerializer
    throttle_scope = "uploads"

    def _upload_response(self, photo: Photo, status_code: int = status.HTTP_200_OK) -> Response:
        target = _call(services.upload_target, photo)
        return Response(
            {"photo": PhotoSerializer(photo).data, "upload": target}, status=status_code
        )

    @action(detail=False, methods=["get"])
    def config(self, request: Request) -> Response:
        """Limits the dashboard checks before uploading, so bad files fail instantly."""
        uploads = settings.UPLOADS
        return Response(
            {
                "allowed_content_types": uploads["ALLOWED_CONTENT_TYPES"],
                "max_file_bytes": uploads["MAX_FILE_BYTES"],
                "max_parts_per_sign_request": uploads["MAX_PARTS_PER_SIGN_REQUEST"],
            }
        )

    def create(self, request: Request) -> Response:
        serializer = StartUploadSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        photo = _call(
            services.start_upload,
            request.user,
            data["gallery"],
            filename=data["filename"],
            content_type=data["content_type"],
            size_bytes=data["size"],
        )
        return self._upload_response(photo, status.HTTP_201_CREATED)

    def retrieve(self, request: Request, pk: str | None = None) -> Response:
        """Resume after a reload: current state plus a freshly signed target."""
        return self._upload_response(self.get_object())

    def destroy(self, request: Request, pk: str | None = None) -> Response:
        _call(services.abort_upload, self.get_object())
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="sign-parts")
    def sign_parts(self, request: Request, pk: str | None = None) -> Response:
        serializer = SignPartsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        urls = _call(
            services.sign_parts, self.get_object(), serializer.validated_data["part_numbers"]
        )
        return Response({"urls": {str(n): url for n, url in urls.items()}})

    @action(detail=True, methods=["get"])
    def parts(self, request: Request, pk: str | None = None) -> Response:
        return Response({"parts": _call(services.uploaded_parts, self.get_object())})

    @action(detail=True, methods=["post"])
    def complete(self, request: Request, pk: str | None = None) -> Response:
        photo = _call(services.complete_upload, self.get_object())
        return Response(PhotoSerializer(photo).data)


class PhotoViewSet(
    OwnedPhotoMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Dashboard list, e.g. ?gallery=<id>&status=failed for "retry those 12" (FR-12)."""

    serializer_class = PhotoSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params
        if "gallery" in params:
            try:
                queryset = queryset.filter(gallery_id=uuid.UUID(params["gallery"]))
            except ValueError:
                return queryset.none()
        if "status" in params:
            if params["status"] not in Photo.Status.values:
                return queryset.none()
            queryset = queryset.filter(status=params["status"])
        return queryset

    def perform_destroy(self, instance: Photo) -> None:
        _call(services.delete_photo, instance)


class StorageUsageView(APIView):
    def get(self, request: Request) -> Response:
        usage = services.storage_usage(request.user)
        return Response(StorageUsageSerializer(usage).data)
