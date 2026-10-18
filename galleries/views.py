from collections.abc import Callable

from django.shortcuts import get_object_or_404
from django.utils.cache import patch_vary_headers
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, NotFound
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle, SimpleRateThrottle
from rest_framework.views import APIView

from galleries import access
from galleries.models import Event, Gallery
from galleries.serializers import (
    EventSerializer,
    GallerySerializer,
    LockedGallerySerializer,
    PublicGallerySerializer,
    UnlockSerializer,
)
from galleries.services import (
    GalleryStateError,
    publish_gallery,
    rotate_gallery_token,
    unpublish_gallery,
)


class EventViewSet(viewsets.ModelViewSet):
    serializer_class = EventSerializer

    def get_queryset(self):
        return Event.objects.owned_by(self.request.user)

    def perform_create(self, serializer: EventSerializer) -> None:
        serializer.save(owner=self.request.user)


class GalleryViewSet(viewsets.ModelViewSet):
    serializer_class = GallerySerializer

    def get_queryset(self):
        return Gallery.objects.owned_by(self.request.user).select_related("event")

    def _transition(self, transition: Callable[[Gallery], Gallery]) -> Response:
        gallery = self.get_object()
        try:
            transition(gallery)
        except GalleryStateError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(self.get_serializer(gallery).data)

    @action(detail=True, methods=["post"])
    def publish(self, request: Request, pk: str | None = None) -> Response:
        return self._transition(publish_gallery)

    @action(detail=True, methods=["post"])
    def unpublish(self, request: Request, pk: str | None = None) -> Response:
        return self._transition(unpublish_gallery)

    @action(detail=True, methods=["post"], url_path="rotate-token")
    def rotate_token(self, request: Request, pk: str | None = None) -> Response:
        return self._transition(rotate_gallery_token)


class GalleryGone(APIException):
    status_code = status.HTTP_410_GONE
    default_detail = "This gallery has expired. Contact your photographer for access."


NOINDEX = "noindex, nofollow"


def get_public_gallery(token: str) -> Gallery:
    """Resolve a customer link. Drafts and unknown tokens look identical (404)."""
    gallery = get_object_or_404(
        Gallery.objects.select_related("event__owner__profile"), public_token=token
    )
    if gallery.status != Gallery.Status.PUBLISHED:
        raise NotFound()
    if gallery.is_expired():
        raise GalleryGone()
    return gallery


class GalleryUnlockThrottle(SimpleRateThrottle):
    """Caps password attempts per gallery across all clients (complements per-IP limits)."""

    scope = "gallery_unlock_global"

    def get_cache_key(self, request: Request, view: APIView) -> str:
        return self.cache_format % {"scope": self.scope, "ident": view.kwargs["token"]}


class PublicGalleryAPIView(APIView):
    """Base for customer endpoints: anonymous, session-aware, never indexed or cached."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def finalize_response(self, request: Request, response: Response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["X-Robots-Tag"] = NOINDEX
        response["Cache-Control"] = "private, no-store"
        patch_vary_headers(response, ["Cookie"])
        return response


class PublicGalleryView(PublicGalleryAPIView):
    def get(self, request: Request, token: str) -> Response:
        gallery = get_public_gallery(token)
        if access.is_unlocked(request, gallery):
            return Response(PublicGallerySerializer(gallery).data)
        return Response(LockedGallerySerializer(gallery).data)


@method_decorator(csrf_protect, name="post")
class PublicGalleryUnlockView(PublicGalleryAPIView):
    throttle_scope = "gallery_unlock"
    throttle_classes = [ScopedRateThrottle, GalleryUnlockThrottle]

    def post(self, request: Request, token: str) -> Response:
        gallery = get_public_gallery(token)
        serializer = UnlockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not gallery.check_access_password(serializer.validated_data["password"]):
            return Response(
                {"detail": "That password isn't right. Check the message from your photographer."},
                status=status.HTTP_403_FORBIDDEN,
            )
        access.mark_unlocked(request, gallery)
        return Response(status=status.HTTP_204_NO_CONTENT)
