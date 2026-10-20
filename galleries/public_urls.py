from django.urls import path

from galleries import public_views

urlpatterns = [
    path("<str:token>/", public_views.gallery_page, name="public-gallery-page"),
    path("<str:token>/unlock/", public_views.unlock, name="public-gallery-page-unlock"),
    path(
        "<str:token>/photos/<uuid:photo_id>/download/",
        public_views.download_photo,
        name="public-photo-download",
    ),
]
