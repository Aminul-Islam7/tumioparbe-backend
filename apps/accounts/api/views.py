from rest_framework import status, viewsets
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.cache import cache
import random
import logging
import time
import re

from apps.accounts.models import Student
from apps.accounts.api.serializers import (
    UserSerializer, 
    StudentSerializer, 
    ChangePasswordSerializer, 
    ResetPasswordSerializer
)
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


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    Custom serializer that adds is_staff claim to the JWT token.
    This is necessary for frontend middleware to identify admin users.
    """
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        
        # Add custom claims to the token
        token['is_staff'] = user.is_staff
        token['is_admin'] = user.is_admin if hasattr(user, 'is_admin') else user.is_staff
        token['name'] = user.name
        
        return token


class CustomTokenObtainPairView(TokenObtainPairView):
    """
    Custom token view that provides better error messages for login failures
    and includes is_staff claim in the token.
    - If phone number doesn't exist: "No account exists with this phone number"
    - If phone exists but password is wrong: "Wrong phone number or password"
    """
    serializer_class = CustomTokenObtainPairSerializer
    
    def post(self, request, *args, **kwargs):
        phone = request.data.get('phone')
        
        # First, check if the phone number exists
        if phone and not User.objects.filter(phone=phone).exists():
            return Response(
                {'detail': 'No account exists with this phone number.'},
                status=status.HTTP_401_UNAUTHORIZED
            )
        
        # If phone exists, try to authenticate
        serializer = self.get_serializer(data=request.data)
        
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as e:
            raise InvalidToken(e.args[0])
        except Exception as e:
            # If authentication fails (wrong password), return a generic message
            # to not reveal that the phone number exists
            return Response(
                {'detail': 'Incorrect password or phone number.'},
                status=status.HTTP_401_UNAUTHORIZED
            )
        
        return Response(serializer.validated_data, status=status.HTTP_200_OK)

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

    # Check if user already exists with this phone number
    if User.objects.filter(phone=phone).exists():
        return Response({
            'success': False,
            'message': 'An account with this phone number already exists. Please login instead.'
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

            # Generate tokens for automatic login using custom serializer to include is_staff claim
            refresh = CustomTokenObtainPairSerializer.get_token(user)

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


class ChangePasswordView(APIView):
    """
    Change password for authenticated users
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            # Set the new password
            request.user.set_password(serializer.validated_data['new_password'])
            request.user.save()
            
            return Response({
                'success': True,
                'message': 'Password changed successfully.'
            })
        return Response({
            'success': False,
            'errors': serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([AllowAny])
@authentication_classes([])
def request_password_reset_otp(request):
    """
    Request an OTP for password reset (forgot password)
    """
    phone = request.data.get('phone')

    # Validate phone number with regex
    if not phone or not BD_PHONE_REGEX.match(phone):
        return Response({
            'success': False,
            'message': 'Invalid phone number format. Must be a valid Bangladesh number (e.g., 01841257770).'
        }, status=status.HTTP_400_BAD_REQUEST)

    # Check if user exists with this phone number
    if not User.objects.filter(phone=phone).exists():
        return Response({
            'success': False,
            'message': 'No account found with this phone number.'
        }, status=status.HTTP_404_NOT_FOUND)

    # Check rate limiting (prevent OTP flooding)
    last_request_time = cache.get(f"last_reset_otp_request:{phone}")
    current_time = int(time.time())

    if last_request_time and (current_time - last_request_time < 60):  # 1 minute limit
        return Response({
            'success': False,
            'message': 'Please wait before requesting another OTP.',
            'retry_after': 60 - (current_time - last_request_time)
        }, status=status.HTTP_429_TOO_MANY_REQUESTS)

    # Update last request time
    cache.set(f"last_reset_otp_request:{phone}", current_time, 300)

    # Generate a 6-digit OTP
    otp = str(random.randint(100000, 999999))

    # Store OTP in cache with expiration (using different key for password reset)
    cache.set(f"password_reset_otp:{phone}", otp, OTP_EXPIRY)

    # Send the OTP via SMS
    sms_result = send_otp(phone, otp)

    if sms_result.get('success'):
        logger.info(f"Password reset OTP sent successfully to {phone}")
    else:
        logger.error(f"Failed to send password reset OTP to {phone}: {sms_result.get('message')}")

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
@authentication_classes([])
def reset_password(request):
    """
    Reset password after OTP verification
    """
    phone = request.data.get('phone')
    otp = request.data.get('otp')
    new_password = request.data.get('new_password')
    confirm_password = request.data.get('confirm_password')

    # Validate inputs
    if not phone:
        return Response({'success': False, 'message': 'Phone number is required.'},
                        status=status.HTTP_400_BAD_REQUEST)

    if not otp:
        return Response({'success': False, 'message': 'OTP is required.'},
                        status=status.HTTP_400_BAD_REQUEST)

    if not new_password or not confirm_password:
        return Response({'success': False, 'message': 'New password and confirmation are required.'},
                        status=status.HTTP_400_BAD_REQUEST)

    if new_password != confirm_password:
        return Response({'success': False, 'message': "Passwords don't match."},
                        status=status.HTTP_400_BAD_REQUEST)

    if len(new_password) < 6:
        return Response({'success': False, 'message': 'Password must be at least 6 characters.'},
                        status=status.HTTP_400_BAD_REQUEST)

    # Check for failed attempts
    failed_attempts = cache.get(f"failed_reset_otp_attempts:{phone}") or 0
    if failed_attempts >= 5:
        return Response({
            'success': False,
            'message': 'Too many failed attempts. Please request a new OTP.'
        }, status=status.HTTP_429_TOO_MANY_REQUESTS)

    # Get stored OTP from cache
    stored_otp = cache.get(f"password_reset_otp:{phone}")

    # Check if OTP exists and is valid
    if stored_otp and stored_otp == otp:
        # Clear the OTP and failed attempts after successful verification
        cache.delete(f"password_reset_otp:{phone}")
        cache.delete(f"failed_reset_otp_attempts:{phone}")

        # Get user and update password
        try:
            user = User.objects.get(phone=phone)
            user.set_password(new_password)
            user.save()
            
            logger.info(f"Password reset successful for user {phone}")

            return Response({
                'success': True,
                'message': 'Password reset successfully. You can now login with your new password.'
            })
        except User.DoesNotExist:
            return Response({
                'success': False,
                'message': 'User not found.'
            }, status=status.HTTP_404_NOT_FOUND)
    else:
        # Increment failed attempts
        cache.set(f"failed_reset_otp_attempts:{phone}", failed_attempts + 1, 300)

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
