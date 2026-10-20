from django.contrib import admin

from delivery.models import DownloadJob


@admin.register(DownloadJob)
class DownloadJobAdmin(admin.ModelAdmin):
    list_display = ["gallery", "policy", "status", "photo_count", "size_bytes", "created_at"]
    list_filter = ["status", "policy"]
    search_fields = ["gallery__title", "gallery__event__owner__email"]
    readonly_fields = [field.name for field in DownloadJob._meta.fields]
