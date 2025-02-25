from rest_framework import status, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import get_user_model
from django.conf import settings
import random
import logging

from apps.accounts.models import Student
from apps.accounts.api.serializers import UserSerializer, StudentSerializer
from services.sms.client import send_otp

# Get logger
logger = logging.getLogger(__name__)

# Get User model
User = get_user_model()

# Store OTPs temporarily (in production, use Redis or another cache)
otp_store = {}


@api_view(['POST'])
@permission_classes([AllowAny])
def request_otp(request):
    """
    Request an OTP sent to the provided phone number
    """
    phone = request.data.get('phone')

    # Validate phone number
    if not phone or not phone.startswith('01') or not len(phone) == 11:
        return Response({'error': 'Valid phone number is required.'}, status=status.HTTP_400_BAD_REQUEST)

    # Generate a 6-digit OTP
    otp = str(random.randint(100000, 999999))
    otp_store[phone] = otp

    # Send the OTP via SMS
    send_otp(phone, otp)

    # For development, return the OTP in the response
    if settings.DEBUG:
        return Response({'phone': phone, 'otp': otp, 'message': 'OTP generated successfully. In production, this would be sent via SMS.'})
    else:
        return Response({'phone': phone, 'message': 'OTP sent successfully.'})


@api_view(['POST'])
@permission_classes([AllowAny])
def verify_otp(request):
    """
    Verify the OTP for the provided phone number
    """
    phone = request.data.get('phone')
    otp = request.data.get('otp')

    if not phone or not otp:
        return Response({'error': 'Phone and OTP are required.'}, status=status.HTTP_400_BAD_REQUEST)

    # Check if OTP exists and is valid
    if phone in otp_store and otp_store[phone] == otp:
        # Clear the OTP after successful verification
        del otp_store[phone]
        return Response({'success': True, 'message': 'OTP verified successfully.'})
    else:
        return Response({'error': 'Invalid OTP.'}, status=status.HTTP_400_BAD_REQUEST)


class RegisterView(APIView):
    """
    Register a new user after OTP verification
    """
    permission_classes = [AllowAny]

    def post(self, request):
        # Check if the phone number has been verified first
        phone = request.data.get('phone')
        otp_verified = request.data.get('otp_verified')

        # In production, we'd check if the phone has been verified recently
        # For now, we're allowing registration with a simple flag
        if not otp_verified:
            return Response({'error': 'Phone number must be verified first.'}, status=status.HTTP_400_BAD_REQUEST)

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

            return Response({
                'user': UserSerializer(user).data,
                'refresh': str(refresh),
                'access': str(refresh.access_token),
                'message': 'Registration successful!'
            })
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


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
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
