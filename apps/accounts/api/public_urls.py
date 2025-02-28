from django.urls import path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.accounts.api.views import (
    request_otp,
    verify_otp,
    RegisterView
)

# These URLs don't require authentication
urlpatterns = [
    # Authentication endpoints
    path('token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),

    # OTP verification for registration
    path('request-otp/', request_otp, name='request-otp'),
    path('verify-otp/', verify_otp, name='verify-otp'),

    # Registration
    path('register/', RegisterView.as_view(), name='register'),
]
