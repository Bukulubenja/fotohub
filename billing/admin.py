from django.contrib import admin

from billing.models import Payment, Plan, Subscription


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "price_ugx", "period_days", "storage_quota_bytes", "is_public"]
    list_editable = ["price_ugx", "is_public"]


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ["user", "plan", "paid_until", "reminded_state"]
    list_filter = ["plan"]
    search_fields = ["user__email"]
    autocomplete_fields = ["user"]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    """Read-only: payments change only through the provider (webhook or status check)."""

    list_display = ["created_at", "subscription", "amount_ugx", "provider", "status"]
    list_filter = ["status", "provider"]
    search_fields = ["subscription__user__email", "provider_reference", "phone"]
    readonly_fields = [field.name for field in Payment._meta.fields]

    def has_add_permission(self, request) -> bool:
        return False
