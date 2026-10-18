from django.urls import path
from rest_framework.routers import SimpleRouter

from assets import views

router = SimpleRouter()
router.register("uploads", views.UploadViewSet, basename="upload")
router.register("photos", views.PhotoViewSet, basename="photo")

urlpatterns = [
    *router.urls,
    path("storage/usage/", views.StorageUsageView.as_view(), name="storage-usage"),
]
