from django.contrib import admin

from shop.models import Order, OrderItem


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    readonly_fields = ["photo", "filename"]


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    """Read-only: orders change only through the payment provider."""

    list_display = ["created_at", "gallery", "quantity", "currency", "amount_minor", "status"]
    list_filter = ["status", "currency"]
    search_fields = ["email", "phone", "provider_reference", "gallery__title"]
    readonly_fields = [field.name for field in Order._meta.fields]
    inlines = [OrderItemInline]

    def has_add_permission(self, request) -> bool:
        return False
