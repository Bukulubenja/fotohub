"""Derivative rendering with libvips (plan §3.3).

Every public variant is re-encoded with all metadata dropped, so EXIF GPS never leaves the
original. Originals stay untouched and private. Previews (viewer, cover) are watermarked
with the photographer's name; thumbnails are too small to be worth stealing, and the web
size is a paid download.
"""

import logging
import os
import tempfile
from dataclasses import dataclass

import pyvips
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone

from assets import watermark
from assets.models import Photo
from assets.storage import get_storage

logger = logging.getLogger(__name__)

# Uploads are untrusted: refuse loaders libvips marks unsafe (svg, matlab, ...).
pyvips.block_untrusted_set(True)

ALLOWED_LOADERS = {"jpegload", "pngload", "webpload", "heifload"}
ROTATED_ORIENTATIONS = {5, 6, 7, 8}  # EXIF values that swap width and height


class ProcessingError(Exception):
    """The file can't become a photo. The message is shown to the photographer."""


@dataclass(frozen=True)
class VariantSpec:
    name: str
    width: int
    height: int
    suffix: str
    content_type: str
    quality: int
    crop: str = "none"
    watermark: bool = False


VARIANTS = (
    VariantSpec("thumb", 400, 400, ".webp", "image/webp", 72),
    VariantSpec("viewer", 1600, 1600, ".webp", "image/webp", 78, watermark=True),
    VariantSpec("web", 2048, 2048, ".jpg", "image/jpeg", 84),
    VariantSpec("cover", 1200, 630, ".jpg", "image/jpeg", 80, crop="attention", watermark=True),
)


@dataclass(frozen=True)
class Rendered:
    spec: VariantSpec
    data: bytes
    width: int
    height: int


def _oriented_size(image: pyvips.Image) -> tuple[int, int]:
    orientation = image.get("orientation") if image.get_typeof("orientation") else 1
    if orientation in ROTATED_ORIENTATIONS:
        return image.height, image.width
    return image.width, image.height


def inspect(path: str) -> tuple[int, int]:
    """Read only the header: reject the wrong format or a decompression bomb before decoding."""
    try:
        header = pyvips.Image.new_from_file(path)
    except pyvips.Error as exc:
        raise ProcessingError("This file isn't a photo we can read.") from exc
    loader = header.get("vips-loader").removesuffix("_source").removesuffix("_buffer")
    if loader not in ALLOWED_LOADERS:
        raise ProcessingError("Upload JPEG, PNG, WebP or HEIC photos only.")
    if header.width * header.height > settings.UPLOADS["MAX_PIXELS"]:
        raise ProcessingError("This photo has too many pixels to process.")
    return _oriented_size(header)


def render(path: str, spec: VariantSpec, label: str = "") -> Rendered:
    image = pyvips.Image.thumbnail(
        f"{path}[fail_on=error]",  # loader options; rejects truncated files instead of padding
        spec.width,
        height=spec.height,
        size="down",  # never upscale small originals
        crop=spec.crop,
    )
    if spec.watermark:
        image = watermark.apply(image, label)
    # thumbnail() already converted to sRGB, so dropping the ICC profile is safe.
    options = {"Q": spec.quality, "keep": "none"}
    if spec.content_type == "image/jpeg":
        options.update(interlace=True, optimize_coding=True)
    data = image.write_to_buffer(spec.suffix, **options)
    return Rendered(spec, data, image.width, image.height)


def render_all(path: str, label: str = "") -> tuple[tuple[int, int], list[Rendered]]:
    size = inspect(path)
    try:
        return size, [render(path, spec, label) for spec in VARIANTS]
    except pyvips.Error as exc:
        raise ProcessingError("We couldn't read this photo. The file may be damaged.") from exc


def variant_key(photo: Photo, spec: VariantSpec) -> str:
    """Deterministic, so a deleted photo's derivatives can be found without reading the row."""
    return f"derivatives/{photo.gallery_id}/{photo.pk}/{spec.name}{spec.suffix}"


def watermark_label(photo: Photo) -> str:
    try:
        return f"© {photo.gallery.event.owner.profile.display_name}"
    except ObjectDoesNotExist:
        return "FotoHub preview"


def mark_failed(photo_id: str, reason: str) -> None:
    Photo.objects.filter(pk=photo_id, status=Photo.Status.PROCESSING).update(
        status=Photo.Status.FAILED, failure_reason=reason, updated_at=timezone.now()
    )


def process_photo(photo_id: str) -> None:
    photo = (
        Photo.objects.select_related("gallery__event__owner__profile")
        .filter(pk=photo_id, status=Photo.Status.PROCESSING)
        .first()
    )
    if photo is None:
        return  # deleted, or already processed by an earlier delivery of this task
    storage = get_storage()

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        path = os.path.join(tmp, "original")
        storage.download_to(photo.original_key, path)
        if os.path.getsize(path) != photo.size_bytes:
            mark_failed(photo_id, "The stored file changed after upload. Upload it again.")
            return
        try:
            (width, height), rendered = render_all(path, label=watermark_label(photo))
        except ProcessingError as exc:
            logger.info("photo %s rejected: %s", photo_id, exc)
            mark_failed(photo_id, str(exc))
            return

    variants = {}
    for item in rendered:
        key = variant_key(photo, item.spec)
        storage.put_bytes(key, item.data, item.spec.content_type)
        variants[item.spec.name] = {
            "key": key,
            "width": item.width,
            "height": item.height,
            "bytes": len(item.data),
        }

    updated = Photo.objects.filter(pk=photo_id, status=Photo.Status.PROCESSING).update(
        status=Photo.Status.READY,
        width=width,
        height=height,
        variants=variants,
        failure_reason="",
        updated_at=timezone.now(),
    )
    if not updated:  # deleted while we worked; don't leave orphaned derivatives behind
        storage.delete([v["key"] for v in variants.values()])
