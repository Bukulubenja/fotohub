import logging

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from rest_framework import generics, mixins, status, viewsets
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from billing import lifecycle, services
from billing.models import Payment, Plan
from billing.providers import get_provider
from billing.providers.base import WebhookRejected
from billing.serializers import (
    PaymentSerializer,
    PlanSerializer,
    StartPaymentSerializer,
    SubscriptionSerializer,
)
from shop import services as shop_services
from shop.models import Order

logger = logging.getLogger(__name__)


class PlanListView(generics.ListAPIView):
    serializer_class = PlanSerializer
    pagination_class = None
    queryset = Plan.objects.filter(is_public=True)


class SubscriptionView(APIView):
    def get(self, request: Request) -> Response:
        subscription = services.subscription_for(request.user)
        context = {"standing": lifecycle.standing(subscription.paid_until, timezone.now())}
        return Response(SubscriptionSerializer(subscription, context=context).data)


class PaymentViewSet(mixins.RetrieveModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = PaymentSerializer
    throttle_scope = "payments"

    def get_queryset(self):
        return Payment.objects.filter(subscription__user=self.request.user).select_related("plan")

    def create(self, request: Request) -> Response:
        serializer = StartPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            payment, checkout = services.start_payment(
                request.user, data["plan"], data["periods"], data["phone"]
            )
        except services.PaymentRejected as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "payment": PaymentSerializer(payment).data,
                "next": {
                    "action": checkout.action,
                    "message": checkout.message,
                    "redirect_url": checkout.redirect_url,
                },
            },
            status=status.HTTP_201_CREATED,
        )

    def retrieve(self, request: Request, pk: str | None = None) -> Response:
        """The dashboard polls this while the photographer approves on their phone."""
        payment = services.refresh_payment(self.get_object())
        return Response(PaymentSerializer(payment).data)


@csrf_exempt  # called by the provider's servers; authenticity comes from its signature
@require_POST
def provider_webhook(request: HttpRequest, provider: str) -> HttpResponse:
    # Charges only go through the configured provider, so only it may settle them. This keeps
    # the test provider's webhook closed in production even if its secret is ever set there.
    if provider != settings.BILLING["PROVIDER"]:
        return HttpResponse(status=404)
    try:
        outcome = get_provider(provider).parse_webhook(request)
    except LookupError:
        return HttpResponse(status=404)
    except WebhookRejected as exc:
        logger.warning("rejected %s webhook: %s", provider, exc)
        return HttpResponse(status=400)
    # One provider account collects both subscription renewals and photo orders.
    try:
        services.apply_outcome(outcome)
    except Payment.DoesNotExist:
        try:
            shop_services.apply_outcome(outcome)
        except Order.DoesNotExist:
            logger.warning("%s webhook for unknown reference %s", provider, outcome.reference)
            return HttpResponse(status=404)
    return HttpResponse(status=200)
