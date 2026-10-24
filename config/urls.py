from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from ops import views as ops_views

urlpatterns = [
    path("", ops_views.dashboard, name="dashboard"),
    path("healthz/", ops_views.healthz, name="healthz"),
    path(settings.ADMIN_URL, admin.site.urls),
    path("api/v1/", include("accounts.urls")),
    path("api/v1/", include("galleries.urls")),
    path("api/v1/", include("assets.urls")),
    path("api/v1/", include("billing.urls")),
    path("g/", include("galleries.public_urls")),
    path("g/", include("shop.gallery_urls")),
    path("orders/", include("shop.order_urls")),
]
