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
        validators=[URLValidator(message='Please enter a valid Facebook profile URL.')]
    )
    email = serializers.EmailField(required=False, allow_blank=True)

    class Meta:
        model = User
        fields = ('id', 'phone', 'name', 'address', 'facebook_profile',
                  'email', 'password', 'confirm_password', 'is_admin')
        read_only_fields = ('id', 'is_admin')

    def validate(self, data):
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
