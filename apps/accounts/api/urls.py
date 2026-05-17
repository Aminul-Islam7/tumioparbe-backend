from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.accounts.api.views import (
    StudentViewSet,
    ProfileView,
    ChangePasswordView,
    ParentViewSet,
    AdminViewSet,
    UserViewSet
)

# Create a router for ViewSets
router = DefaultRouter()
router.register(r'students', StudentViewSet, basename='student')
router.register(r'parents', ParentViewSet, basename='parent')
router.register(r'admins', AdminViewSet, basename='admin')
router.register(r'users', UserViewSet, basename='user')

# These URLs require authentication
urlpatterns = [
    # Import the public, non-authenticated routes
    path('', include('apps.accounts.api.public_urls')),

    # Profile - requires authentication
    path('profile/', ProfileView.as_view(), name='profile'),
    
    # Change password - requires authentication
    path('change-password/', ChangePasswordView.as_view(), name='change-password'),

    # Include router URLs - requires authentication
    path('', include(router.urls)),
]

