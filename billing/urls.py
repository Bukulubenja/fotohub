from django.urls import path
from rest_framework.routers import SimpleRouter

from billing import views

router = SimpleRouter()
router.register("billing/payments", views.PaymentViewSet, basename="payment")

urlpatterns = [
    *router.urls,
    path("billing/plans/", views.PlanListView.as_view(), name="billing-plans"),
    path("billing/subscription/", views.SubscriptionView.as_view(), name="billing-subscription"),
    path("billing/webhooks/<slug:provider>/", views.provider_webhook, name="billing-webhook"),
]
