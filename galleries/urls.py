from django.urls import path
from rest_framework.routers import SimpleRouter

from galleries import views

router = SimpleRouter()
router.register("events", views.EventViewSet, basename="event")
router.register("galleries", views.GalleryViewSet, basename="gallery")

urlpatterns = [
    *router.urls,
    path("public/galleries/<str:token>/", views.PublicGalleryView.as_view(), name="public-gallery"),
    path(
        "public/galleries/<str:token>/unlock/",
        views.PublicGalleryUnlockView.as_view(),
        name="public-gallery-unlock",
    ),
]
