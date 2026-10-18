import datetime

import factory
import pyvips
from django.utils import timezone

from assets.models import Photo, UploadSession
from galleries.tests.factories import GalleryFactory


class PhotoFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Photo

    gallery = factory.SubFactory(GalleryFactory)
    original_filename = factory.Sequence(lambda n: f"IMG_{n:04}.jpg")
    content_type = "image/jpeg"
    size_bytes = 1024
    original_key = factory.LazyAttribute(lambda o: f"originals/test/{o.gallery.pk}/{o.id}")
    id = factory.Faker("uuid4", cast_to=None)


class UploadSessionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = UploadSession

    photo = factory.SubFactory(PhotoFactory)
    expires_at = factory.LazyFunction(lambda: timezone.now() + datetime.timedelta(hours=1))


def make_jpeg(width: int = 600, height: int = 400, *, exif_secret: str = "", orientation=1):
    """A real JPEG, optionally carrying EXIF that must not survive into derivatives."""
    image = (pyvips.Image.black(width, height) + [200, 120, 40]).cast("uchar")
    image = image.copy(interpretation="srgb")
    if orientation != 1:
        image.set_type(pyvips.GValue.gint_type, "orientation", orientation)
    if exif_secret:
        image.set_type(pyvips.GValue.gstr_type, "exif-ifd0-ImageDescription", exif_secret)
    return image.write_to_buffer(".jpg")
