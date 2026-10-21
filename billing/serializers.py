from django.utils import timezone
from rest_framework import serializers

from billing.lifecycle import Standing
from billing.models import Payment, Plan, Subscription


class PlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = Plan
        fields = ["code", "name", "price_ugx", "period_days", "storage_quota_bytes"]


class SubscriptionSerializer(serializers.ModelSerializer):
    """The dashboard's billing banner: where the photographer stands and what still works."""

    plan = PlanSerializer()
    state = serializers.SerializerMethodField()
    can_upload = serializers.SerializerMethodField()
    galleries_live = serializers.SerializerMethodField()
    days_left = serializers.SerializerMethodField()

    class Meta:
        model = Subscription
        fields = ["plan", "paid_until", "state", "can_upload", "galleries_live", "days_left"]

    def _standing(self) -> Standing:
        return self.context["standing"]

    def get_state(self, subscription: Subscription) -> str:
        return self._standing().state

    def get_can_upload(self, subscription: Subscription) -> bool:
        return self._standing().can_upload

    def get_galleries_live(self, subscription: Subscription) -> bool:
        return self._standing().galleries_live

    def get_days_left(self, subscription: Subscription) -> int:
        return max(0, (subscription.paid_until - timezone.now()).days)


class StartPaymentSerializer(serializers.Serializer):
    plan = serializers.SlugRelatedField(
        slug_field="code", queryset=Plan.objects.filter(is_public=True)
    )
    periods = serializers.IntegerField(min_value=1, max_value=12, default=1)
    phone = serializers.CharField(max_length=20)


class PaymentSerializer(serializers.ModelSerializer):
    plan = serializers.SlugRelatedField(slug_field="code", read_only=True)

    class Meta:
        model = Payment
        fields = [
            "id",
            "plan",
            "periods",
            "amount_ugx",
            "status",
            "failure_reason",
            "created_at",
            "confirmed_at",
        ]
        read_only_fields = fields
