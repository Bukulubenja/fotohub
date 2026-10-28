"""Customer-facing photo data for gallery pages (spec FR-21, FR-22, FR-23).

Pages get URLs and dimensions only; storage keys stay in this module and `storage.py`.
"""

from dataclasses import dataclass
from pathlib import PurePath

from django.db.models import QuerySet
from django.db.models.functions import Lower

from assets.models import Photo
from assets.storage import get_storage
from galleries.models import Gallery

WEB_DOWNLOAD_SUFFIX = ".jpg"  # the `web` variant is always JPEG


@dataclass(frozen=True)
class Image:
    url: str
    width: int
    height: int


@dataclass(frozen=True)
class GalleryPhoto:
    id: str
    thumb: Image
    viewer: Image


def ready_photos(gallery: Gallery) -> QuerySet[Photo]:
    """Customer order: by filename, which follows the camera's shooting sequence.

    Case-insensitive (cameras and editing exports disagree on case); the id keeps pages stable
    when two files share a name. Grid, pages, default cover and ZIP all use this order.
    """
    return Photo.objects.filter(gallery=gallery, status=Photo.Status.READY).order_by(
        Lower("original_filename"), "id"
    )


def _image(photo: Photo, variant: str) -> Image | None:
    data = photo.variants.get(variant)
    if not data:
        return None
    url = get_storage().presign_get_cacheable(data["key"])
    return Image(url, data["width"], data["height"])


def gallery_photo(photo: Photo) -> GalleryPhoto | None:
    thumb, viewer = _image(photo, "thumb"), _image(photo, "viewer")
    if thumb is None or viewer is None:
        return None  # processed before a variant existed; skip rather than break the page
    return GalleryPhoto(str(photo.pk), thumb, viewer)


def gallery_photos(photos: list[Photo]) -> list[GalleryPhoto]:
    return [item for item in map(gallery_photo, photos) if item is not None]


def cover_photo(gallery: Gallery) -> Photo | None:
    chosen = gallery.cover_photo
    if chosen is not None and chosen.status == Photo.Status.READY:
        return chosen
    return ready_photos(gallery).first()


def cover_image(gallery: Gallery) -> Image | None:
    photo = cover_photo(gallery)
    return _image(photo, "cover") if photo else None


def thumb(photo: Photo) -> Image | None:
    """A photo at grid size, for lists of galleries (the collection page)."""
    return _image(photo, "thumb")


@dataclass(frozen=True)
class DownloadSource:
    key: str
    filename: str
    size: int


def download_source(photo: Photo, policy: str) -> DownloadSource | None:
    """The file a permitted download serves (single photo or ZIP entry); None if none."""
    if policy == Gallery.DownloadPolicy.ORIGINAL:
        return DownloadSource(photo.original_key, photo.original_filename, photo.size_bytes)
    web = photo.variants.get("web")
    if policy == Gallery.DownloadPolicy.WEB and web:
        filename = PurePath(photo.original_filename).stem + WEB_DOWNLOAD_SUFFIX
        return DownloadSource(web["key"], filename, web.get("bytes", 0))
    return None


def download_url(photo: Photo, policy: str) -> str | None:
    source = download_source(photo, policy)
    if source is None:
        return None
    return get_storage().presign_download(source.key, source.filename)
