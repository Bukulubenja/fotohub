from django.contrib import admin

from galleries.models import CollectionSubscription, Event, Gallery


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ["title", "owner", "event_date"]
    search_fields = ["title", "owner__email"]
    list_select_related = ["owner"]


@admin.register(CollectionSubscription)
class CollectionSubscriptionAdmin(admin.ModelAdmin):
    list_display = ["email", "collection", "status", "confirmed_at", "last_sent_at"]
    list_filter = ["status"]
    search_fields = ["email", "collection__name"]
    readonly_fields = ["token", "confirm_sent_at", "confirmed_at", "notified_until", "last_sent_at"]
    list_select_related = ["collection"]


@admin.register(Gallery)
class GalleryAdmin(admin.ModelAdmin):
    list_display = ["title", "event", "status", "download_policy", "expires_at"]
    list_filter = ["status", "download_policy"]
    search_fields = ["title", "event__title", "event__owner__email"]
    readonly_fields = ["public_token", "password_hash", "access_version", "published_at"]
    list_select_related = ["event"]
