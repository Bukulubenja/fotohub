from django.urls import path

from delivery import views

urlpatterns = [
    path("<str:token>/archive/", views.request_archive, name="public-archive-request"),
    path("<str:token>/archive/<uuid:job_id>/", views.archive_status, name="public-archive-status"),
    path(
        "<str:token>/archive/<uuid:job_id>/file/",
        views.archive_file,
        name="public-archive-file",
    ),
]
