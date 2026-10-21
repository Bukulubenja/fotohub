from django.urls import path

from shop import views

urlpatterns = [
    path("<str:token>/", views.order_page, name="shop-order"),
    path("<str:token>/photos/<uuid:photo_id>/", views.order_photo, name="shop-order-photo"),
    path("<str:token>/archive/", views.order_archive, name="shop-order-archive-request"),
    path(
        "<str:token>/archive/<uuid:job_id>/",
        views.order_archive_status,
        name="shop-order-archive",
    ),
    path(
        "<str:token>/archive/<uuid:job_id>/file/",
        views.order_archive_file,
        name="shop-order-archive-file",
    ),
]
