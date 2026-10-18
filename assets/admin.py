from django.contrib import admin

from assets.models import Photo, UploadSession


class UploadSessionInline(admin.StackedInline):
    model = UploadSession
    can_delete = False
    readonly_fields = ["upload_id", "part_size", "state", "expires_at", "completed_at"]


@admin.register(Photo)
class PhotoAdmin(admin.ModelAdmin):
    list_display = ["original_filename", "gallery", "status", "size_bytes", "created_at"]
    list_filter = ["status"]
    search_fields = ["original_filename", "gallery__title", "gallery__event__owner__email"]
    readonly_fields = ["original_key", "variants", "width", "height", "failure_reason"]
    list_select_related = ["gallery"]
    inlines = [UploadSessionInline]
