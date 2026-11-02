from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from feedback import views as feedback_views
from galleries import collection_views, public_views, update_views
from ops import pwa
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
    path("feedback/", feedback_views.feedback_page, name="feedback"),
    # The installable app. The worker lives at the root so its scope covers the whole site.
    path("manifest.webmanifest", pwa.manifest, name="pwa-manifest"),
    path("app/manifest.webmanifest", pwa.dashboard_manifest, name="pwa-dashboard-manifest"),
    path("sw.js", pwa.service_worker, name="pwa-service-worker"),
    path("offline/", pwa.offline, name="pwa-offline"),
    path("g/", include("galleries.public_urls")),
    path("g/", include("shop.gallery_urls")),
    path("orders/", include("shop.order_urls")),
]
