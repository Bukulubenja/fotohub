from django.conf import settings
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("api/v1/", include("accounts.urls")),
    path("api/v1/", include("galleries.urls")),
    path("api/v1/", include("assets.urls")),
    path("g/", include("galleries.public_urls")),
    path("g/", include("delivery.urls")),
]
