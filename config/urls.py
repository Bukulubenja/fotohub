from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from galleries import collection_views, public_views, update_views
from ops import views as ops_views

urlpatterns = [
    path("", collection_views.home, name="home"),
    path("app/", ops_views.dashboard, name="dashboard"),
    path("c/<uuid:collection_id>/", collection_views.collection_page, name="public-collection"),
    path(
        "c/<uuid:collection_id>/updates/",
        update_views.request_updates,
        name="collection-updates-request",
    ),
    path("updates/<str:token>/", update_views.manage, name="collection-updates"),
    path("updates/<str:token>/stop/", update_views.one_click_stop, name="collection-updates-stop"),
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
