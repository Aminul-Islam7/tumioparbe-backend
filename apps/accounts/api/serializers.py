from rest_framework import serializers
from django.contrib.auth import get_user_model
from apps.accounts.models import Student
from django.core.validators import RegexValidator, URLValidator
from rest_framework.validators import UniqueValidator

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=True)
    confirm_password = serializers.CharField(write_only=True, required=True)
    phone = serializers.CharField(
        validators=[
            RegexValidator(regex=r'^01[2-9]\d{8}$', message='Phone number must be in the format: 01XXXXXXXXX.'),
            UniqueValidator(queryset=User.objects.all(), message='A user with this phone number already exists.')
        ]
    )
    facebook_profile = serializers.URLField(
        required=False,
        allow_blank=True,
        allow_null=True,
        validators=[URLValidator(message='Please enter a valid Facebook profile URL.')]
    )
    email = serializers.EmailField(required=False, allow_blank=True)

    class Meta:
        model = User
        fields = ('id', 'phone', 'name', 'address', 'facebook_profile',
                  'email', 'password', 'confirm_password', 'is_admin', 'date_joined')
        read_only_fields = ('id', 'is_admin', 'date_joined')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance is not None:
            self.fields['password'].required = False
            self.fields['confirm_password'].required = False

    def validate(self, data):
        if 'password' in data or 'confirm_password' in data:
            if data.get('password') != data.get('confirm_password'):
                raise serializers.ValidationError({"confirm_password": "Passwords don't match."})
        return data

    def create(self, validated_data):
        # Remove confirm_password from the data
        validated_data.pop('confirm_password', None)

        # Let our custom UserManager handle user creation
        user = User.objects.create_user(
            phone=validated_data.get('phone'),
            name=validated_data.get('name'),
            address=validated_data.get('address'),
            facebook_profile=validated_data.get('facebook_profile'),
            password=validated_data.get('password'),
            email=validated_data.get('email', '')
        )
        return user


class StudentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Student
        fields = ('id', 'parent', 'name', 'date_of_birth', 'school',
                  'current_class', 'father_name', 'mother_name')
        read_only_fields = ('id', 'parent')

    def create(self, validated_data):
        # Get the parent (authenticated user) from the context
        parent = self.context['request'].user
        validated_data['parent'] = parent
        return super().create(validated_data)


class ChangePasswordSerializer(serializers.Serializer):
    """Serializer for changing password while logged in"""
    current_password = serializers.CharField(required=True, write_only=True)
    new_password = serializers.CharField(required=True, write_only=True, min_length=6)
    confirm_password = serializers.CharField(required=True, write_only=True)

    def validate(self, data):
        if data.get('new_password') != data.get('confirm_password'):
            raise serializers.ValidationError({"confirm_password": "New passwords don't match."})
        return data

    def validate_current_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value


class ResetPasswordSerializer(serializers.Serializer):
    """Serializer for resetting password after OTP verification"""
    phone = serializers.CharField(required=True)
    new_password = serializers.CharField(required=True, write_only=True, min_length=6)
    confirm_password = serializers.CharField(required=True, write_only=True)

    def validate(self, data):
        if data.get('new_password') != data.get('confirm_password'):
            raise serializers.ValidationError({"confirm_password": "Passwords don't match."})
        return data

