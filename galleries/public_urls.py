from django.urls import path

from galleries import public_views

urlpatterns = [
    path("<str:token>/", public_views.gallery_page, name="public-gallery-page"),
    path("<str:token>/unlock/", public_views.unlock, name="public-gallery-page-unlock"),
    path(
        "<str:token>/favourites/",
        public_views.favourites_page,
        name="public-gallery-favourites",
    ),
    path(
        "<str:token>/favourites/toggle/",
        public_views.favourite_toggle,
        name="public-gallery-favourite-toggle",
    ),
    path("<str:token>/profile/", public_views.profile_page, name="public-gallery-profile"),
    path(
        "<str:token>/photos/<uuid:photo_id>/download/",
        public_views.download_photo,
        name="public-photo-download",
    ),
]
