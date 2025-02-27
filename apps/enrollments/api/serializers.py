from rest_framework import serializers
from apps.enrollments.models import Enrollment, Coupon
from apps.accounts.api.serializers import StudentSerializer
from apps.courses.api.serializers import BatchSerializer
from datetime import date
from django.utils import timezone  # Use Django's timezone utilities instead of datetime


class CouponSerializer(serializers.ModelSerializer):
    discount_types_display = serializers.SerializerMethodField()
    is_expired = serializers.SerializerMethodField()

    class Meta:
        model = Coupon
        fields = ['id', 'code', 'name', 'discount_types', 'discount_types_display',
                  'discount_value', 'expires_at', 'is_expired']
        read_only_fields = ['id', 'discount_types_display', 'is_expired']

    def get_discount_types_display(self, obj):
        """Return human-readable descriptions of the discount types"""
        type_map = {
            'TUITION': 'Tuition Discount',
            'ADMISSION': 'Admission Fee Waiver',
            'FIRST_MONTH': 'First Month Waiver'
        }

        types = []
        for discount_type in obj.discount_types:
            if discount_type in type_map:
                types.append(type_map[discount_type])

        return types

    def get_is_expired(self, obj):
        """Check if the coupon is expired"""
        return obj.expires_at < timezone.now()  # Use timezone.now() instead of datetime.now()

    def validate_expires_at(self, value):
        """Validate that the expiration date is in the future"""
        if value < timezone.now():  # Use timezone.now() instead of datetime.now()
            raise serializers.ValidationError("Expiration date must be in the future")
        return value

    def validate_discount_types(self, value):
        """Validate that discount types are valid"""
        valid_types = ['TUITION', 'ADMISSION', 'FIRST_MONTH']

        if not isinstance(value, list):
            raise serializers.ValidationError("Discount types must be a list")

        if not value:
            raise serializers.ValidationError("At least one discount type must be provided")

        for discount_type in value:
            if discount_type not in valid_types:
                raise serializers.ValidationError(f"Invalid discount type: {discount_type}")

        # If TUITION is selected, discount_value is required
        if 'TUITION' in value and self.initial_data.get('discount_value') is None:
            raise serializers.ValidationError("Discount value is required for tuition discount")

        return value

    def validate(self, data):
        """Additional cross-field validation"""
        discount_types = data.get('discount_types', [])
        discount_value = data.get('discount_value')

        # If TUITION is not selected but discount_value is provided, warn
        if 'TUITION' not in discount_types and discount_value is not None:
            raise serializers.ValidationError({
                "discount_value": "Discount value is only used for tuition discounts"
            })

        return data


class EnrollmentSerializer(serializers.ModelSerializer):
    student_details = StudentSerializer(source='student', read_only=True)
    batch_details = BatchSerializer(source='batch', read_only=True)

    class Meta:
        model = Enrollment
        fields = ['id', 'student', 'batch', 'start_month', 'tuition_fee',
                  'is_active', 'created_at', 'updated_at', 'student_details',
                  'batch_details']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_start_month(self, value):
        """Validate that start_month is not in the past"""
        today = date.today()
        # Get the first day of current month
        current_month = date(today.year, today.month, 1)

        if value < current_month:
            raise serializers.ValidationError("Start month cannot be in the past")
        return value

    def validate(self, data):
        """Validate that student and batch combination is unique for active enrollments"""
        # Only check if this is a new enrollment (not an update)
        if self.instance is None:
            student = data.get('student')
            batch = data.get('batch')

            # Check if there's an active enrollment for this student in this batch
            existing_enrollment = Enrollment.objects.filter(
                student=student,
                batch=batch,
                is_active=True
            ).exists()

            if existing_enrollment:
                raise serializers.ValidationError(
                    "This student is already enrolled in this batch"
                )

        return data


class EnrollmentInitiateSerializer(serializers.Serializer):
    student = serializers.IntegerField()
    batch = serializers.IntegerField()
    start_month = serializers.DateField()
    coupon_code = serializers.CharField(required=False, allow_blank=True)

    def validate_coupon_code(self, value):
        """Validate that the coupon exists and is not expired"""
        if not value:
            return value

        try:
            coupon = Coupon.objects.get(code=value)
            if coupon.expires_at < timezone.now():  # Use timezone.now() instead of datetime.now()
                raise serializers.ValidationError("This coupon has expired")
            return value
        except Coupon.DoesNotExist:
            raise serializers.ValidationError("Invalid coupon code")
