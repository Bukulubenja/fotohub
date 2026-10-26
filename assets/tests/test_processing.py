import pytest
import pyvips

from assets import processing
from assets.models import Photo
from assets.tests.factories import PhotoFactory, make_jpeg

pytestmark = pytest.mark.django_db


def stored_photo(storage, data: bytes) -> Photo:
    photo = PhotoFactory(status=Photo.Status.PROCESSING, size_bytes=len(data))
    storage.client.put_object(Bucket=storage.bucket, Key=photo.original_key, Body=data)
    return photo


def fetch(storage, key: str) -> bytes:
    return storage.client.get_object(Bucket=storage.bucket, Key=key)["Body"].read()


def test_renders_every_variant_within_its_box(storage):
    photo = stored_photo(storage, make_jpeg(3000, 2000))

    processing.process_photo(str(photo.pk))

    photo.refresh_from_db()
    assert photo.status == Photo.Status.READY
    assert (photo.width, photo.height) == (3000, 2000)
    sizes = {name: (v["width"], v["height"]) for name, v in photo.variants.items()}
    assert sizes == {
        "thumb": (400, 267),
        "viewer": (1600, 1067),
        "web": (2048, 1365),
        "cover": (1200, 630),
    }
    thumb = pyvips.Image.new_from_buffer(fetch(storage, photo.variants["thumb"]["key"]), "")
    assert thumb.get("vips-loader") == "webpload_buffer"


def test_never_upscales_small_originals(storage):
    photo = stored_photo(storage, make_jpeg(300, 200))

    processing.process_photo(str(photo.pk))

    photo.refresh_from_db()
    assert (photo.variants["viewer"]["width"], photo.variants["viewer"]["height"]) == (300, 200)


def test_strips_metadata_from_every_variant(storage):
    photo = stored_photo(storage, make_jpeg(exif_secret="GPS 0.3476N 32.5825E"))

    processing.process_photo(str(photo.pk))

    photo.refresh_from_db()
    for variant in photo.variants.values():
        data = fetch(storage, variant["key"])
        assert b"GPS 0.3476N" not in data
        assert "exif-data" not in pyvips.Image.new_from_buffer(data, "").get_fields()
    assert b"GPS 0.3476N" in fetch(storage, photo.original_key)  # original untouched


def test_applies_exif_orientation(storage):
    photo = stored_photo(storage, make_jpeg(600, 400, orientation=6))

    processing.process_photo(str(photo.pk))

    photo.refresh_from_db()
    assert (photo.width, photo.height) == (400, 600)
    assert photo.variants["thumb"]["height"] > photo.variants["thumb"]["width"]


@pytest.mark.parametrize(
    "data",
    [
        b"not an image at all",
        b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>',
        make_jpeg()[:200],  # truncated
    ],
    ids=["garbage", "svg", "truncated"],
)
def test_unreadable_files_fail_with_a_reason(storage, data):
    photo = stored_photo(storage, data)

    processing.process_photo(str(photo.pk))

    photo.refresh_from_db()
    assert photo.status == Photo.Status.FAILED
    assert photo.failure_reason
    assert photo.variants == {}


def test_rejects_decompression_bombs(storage, settings):
    settings.UPLOADS = {**settings.UPLOADS, "MAX_PIXELS": 100 * 100}
    photo = stored_photo(storage, make_jpeg(200, 200))

    processing.process_photo(str(photo.pk))

    photo.refresh_from_db()
    assert photo.status == Photo.Status.FAILED
    assert "too many pixels" in photo.failure_reason


def test_skips_photos_not_awaiting_processing(storage):
    photo = stored_photo(storage, make_jpeg())
    Photo.objects.filter(pk=photo.pk).update(status=Photo.Status.READY)

    processing.process_photo(str(photo.pk))

    photo.refresh_from_db()
    assert photo.variants == {}


def test_cleans_up_derivatives_if_photo_deleted_mid_processing(storage, monkeypatch):
    photo = stored_photo(storage, make_jpeg())
    real_render_all = processing.render_all

    def render_then_delete(path, **kwargs):
        result = real_render_all(path, **kwargs)
        Photo.objects.filter(pk=photo.pk).delete()
        return result

    monkeypatch.setattr(processing, "render_all", render_then_delete)

    processing.process_photo(str(photo.pk))

    listing = storage.client.list_objects_v2(Bucket=storage.bucket, Prefix="derivatives/")
    assert listing.get("KeyCount", 0) == 0


def test_previews_are_watermarked_but_thumbs_and_paid_sizes_are_not(tmp_path):
    path = tmp_path / "photo.jpg"
    path.write_bytes(make_jpeg(3000, 2000))

    for spec in processing.VARIANTS:
        plain = processing.render(str(path), spec, label="")
        marked = processing.render(str(path), spec, label="\u00a9 Lens & Light")

        assert (plain.data != marked.data) is (spec.name in {"viewer", "cover"}), spec.name


def test_watermark_label_names_the_photographer():
    photo = PhotoFactory()
    photo.gallery.event.owner.profile.display_name = "Lens & Light"

    assert processing.watermark_label(photo) == "\u00a9 Lens & Light"
