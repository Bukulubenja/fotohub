from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from galleries import public_views
from ops import views as ops_views

urlpatterns = [
    path("", ops_views.dashboard, name="dashboard"),
    path("healthz/", ops_views.healthz, name="healthz"),
    path(settings.ADMIN_URL, admin.site.urls),
    path("api/v1/", include("accounts.urls")),
    path("api/v1/", include("galleries.urls")),
    path("api/v1/", include("assets.urls")),
    path("api/v1/", include("billing.urls")),
    path("terms/", public_views.terms_page, name="purchase-terms"),
    path("g/", include("galleries.public_urls")),
    path("g/", include("shop.gallery_urls")),
    path("orders/", include("shop.order_urls")),
]
