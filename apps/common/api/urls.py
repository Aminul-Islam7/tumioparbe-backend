from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.common.api.views import SMSViewSet, AutomationViewSet

# Create a router and register our viewsets
router = DefaultRouter()
router.register(r'sms', SMSViewSet)
router.register(r'automation', AutomationViewSet, basename='automation')

urlpatterns = [
    path('', include(router.urls)),
]
