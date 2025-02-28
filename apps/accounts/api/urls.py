from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.accounts.api.views import (
    StudentViewSet,
    ProfileView
)

# Create a router for ViewSets
router = DefaultRouter()
router.register(r'students', StudentViewSet, basename='student')

# These URLs require authentication
urlpatterns = [
    # Import the public, non-authenticated routes
    path('', include('apps.accounts.api.public_urls')),

    # Profile - requires authentication
    path('profile/', ProfileView.as_view(), name='profile'),

    # Include router URLs - requires authentication
    path('', include(router.urls)),
]
