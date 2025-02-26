from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.payments.api.views import PaymentViewSet, BkashCallbackView, BkashWebhookView

# Create a router and register our viewsets
router = DefaultRouter()
router.register(r'payments', PaymentViewSet)

urlpatterns = [
    path('', include(router.urls)),
    path('bkash/callback/', BkashCallbackView.as_view(), name='bkash-callback'),
    path('bkash/webhook/', BkashWebhookView.as_view(), name='bkash-webhook'),
]
