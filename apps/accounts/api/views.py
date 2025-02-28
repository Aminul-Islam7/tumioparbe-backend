from rest_framework import status, viewsets
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.cache import cache
import random
import logging
import time
import re

from apps.accounts.models import Student
from apps.accounts.api.serializers import UserSerializer, StudentSerializer
from services.sms.client import send_otp

# Get logger
logger = logging.getLogger(__name__)

# Get User model
User = get_user_model()

# Cache keys


def otp_cache_key(phone):
    return f"otp:{phone}"


def verified_phone_cache_key(phone):
    return f"verified_phone:{phone}"


# OTP expiration time (in seconds)
OTP_EXPIRY = 300  # 5 minutes
VERIFICATION_EXPIRY = 600  # 10 minutes

# Bangladesh phone number regex pattern (01X XXXXXXXX)
BD_PHONE_REGEX = re.compile(r'^01[2-9]\d{8}$')


@api_view(['POST'])
@permission_classes([AllowAny])
@authentication_classes([])  # Empty list means no authentication required
def request_otp(request):
    """
    Request an OTP sent to the provided phone number
    """
    phone = request.data.get('phone')

    # Validate phone number with regex
    if not phone or not BD_PHONE_REGEX.match(phone):
        return Response({
            'success': False,
            'message': 'Invalid phone number format. Must be a valid Bangladesh number (e.g., 01841257770).'
        }, status=status.HTTP_400_BAD_REQUEST)

    # Check rate limiting (prevent OTP flooding)
    last_request_time = cache.get(f"last_otp_request:{phone}")
    current_time = int(time.time())

    if last_request_time and (current_time - last_request_time < 60):  # 1 minute limit
        return Response({
            'success': False,
            'message': 'Please wait before requesting another OTP.',
            'retry_after': 60 - (current_time - last_request_time)
        }, status=status.HTTP_429_TOO_MANY_REQUESTS)

    # Update last request time
    cache.set(f"last_otp_request:{phone}", current_time, 300)  # Store for 5 minutes

    # Generate a 6-digit OTP
    otp = str(random.randint(100000, 999999))

    # Store OTP in cache with expiration
    cache.set(otp_cache_key(phone), otp, OTP_EXPIRY)

    # Send the OTP via SMS using our enhanced client
    sms_result = send_otp(phone, otp)

    # Log the SMS sending result
    if sms_result.get('success'):
        logger.info(f"OTP sent successfully to {phone}")
    else:
        logger.error(f"Failed to send OTP to {phone}: {sms_result.get('message')}")

    # For development, return the OTP in the response
    if settings.DEBUG:
        return Response({
            'success': True,
            'phone': phone,
            'otp': otp,  # Only in DEBUG mode
            'expires_in': OTP_EXPIRY,
            'message': 'OTP generated successfully. In production, this would only be sent via SMS.'
        })
    else:
        return Response({
            'success': True,
            'phone': phone,
            'expires_in': OTP_EXPIRY,
            'message': 'OTP sent successfully to your phone.'
        })


@api_view(['POST'])
@permission_classes([AllowAny])
@authentication_classes([])  # Empty list means no authentication required
def verify_otp(request):
    """
    Verify the OTP for the provided phone number
    """
    phone = request.data.get('phone')
    otp = request.data.get('otp')

    # Validate inputs
    if not phone:
        return Response({'success': False, 'message': 'Phone number is required.'},
                        status=status.HTTP_400_BAD_REQUEST)

    if not otp:
        return Response({'success': False, 'message': 'OTP is required.'},
                        status=status.HTTP_400_BAD_REQUEST)

    # Check for failed attempts
    failed_attempts = cache.get(f"failed_otp_attempts:{phone}") or 0
    if failed_attempts >= 5:  # Max 5 attempts
        return Response({
            'success': False,
            'message': 'Too many failed attempts. Please request a new OTP.'
        }, status=status.HTTP_429_TOO_MANY_REQUESTS)

    # Get stored OTP from cache
    stored_otp = cache.get(otp_cache_key(phone))

    # Check if OTP exists and is valid
    if stored_otp and stored_otp == otp:
        # Clear the OTP and failed attempts after successful verification
        cache.delete(otp_cache_key(phone))
        cache.delete(f"failed_otp_attempts:{phone}")

        # Mark phone as verified with timestamp in cache
        cache.set(verified_phone_cache_key(phone), int(time.time()), VERIFICATION_EXPIRY)

        return Response({
            'success': True,
            'message': 'OTP verified successfully.',
            'valid_for': VERIFICATION_EXPIRY
        })
    else:
        # Increment failed attempts
        cache.set(f"failed_otp_attempts:{phone}", failed_attempts + 1, 300)  # Store for 5 minutes

        if stored_otp is None:
            return Response({
                'success': False,
                'message': 'OTP has expired or was never sent. Please request a new one.'
            }, status=status.HTTP_400_BAD_REQUEST)
        else:
            return Response({
                'success': False,
                'message': 'Invalid OTP. Please try again.',
                'attempts_left': 5 - (failed_attempts + 1)
            }, status=status.HTTP_400_BAD_REQUEST)


class RegisterView(APIView):
    """
    Register a new user after OTP verification
    """
    permission_classes = [AllowAny]
    authentication_classes = []  # No authentication required

    def post(self, request):
        # Check if the phone number has been verified first
        phone = request.data.get('phone')

        # Check if the phone verification exists in cache
        verification_time = cache.get(verified_phone_cache_key(phone))
        if not verification_time:
            return Response({
                'success': False,
                'message': 'Phone number must be verified with OTP first.'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Debug: Log the phone number and the admin phone numbers from settings
        admin_phones = settings.ADMIN_PHONE_NUMBERS
        logger.info(f"Registering phone: {phone}")
        logger.info(f"Admin phone numbers from settings: {admin_phones}")
        logger.info(f"Is admin phone? {phone in admin_phones}")

        # Check if the ADMIN_PHONE_NUMBERS is not empty and if the phone is in it
        if admin_phones and phone in admin_phones:
            request.data['is_admin'] = True
            logger.info(f"Phone {phone} recognized as admin")

        serializer = UserSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()

            # Double check if this is an admin user and set permissions
            if admin_phones and phone in admin_phones:
                user.is_admin = True
                user.is_staff = True
                user.is_superuser = True
                user.save()
                logger.info(f"User {user.id} set as admin with phone {phone}")

            # Generate tokens for automatic login
            refresh = RefreshToken.for_user(user)

            # Remove the phone verification from cache after successful registration
            cache.delete(verified_phone_cache_key(phone))

            return Response({
                'success': True,
                'user': UserSerializer(user).data,
                'refresh': str(refresh),
                'access': str(refresh.access_token),
                'message': 'Registration successful!'
            })
        return Response({'success': False, 'errors': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)


class StudentViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing student profiles
    """
    serializer_class = StudentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # Only return students belonging to the authenticated user (parent)
        return Student.objects.filter(parent=self.request.user)


class ProfileView(APIView):
    """
    Get or update the authenticated user's profile
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        serializer = UserSerializer(request.user)
        return Response(serializer.data)

    def put(self, request):
        serializer = UserSerializer(request.user, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response({'success': True, 'data': serializer.data})
        return Response({'success': False, 'errors': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
