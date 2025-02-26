from rest_framework import serializers
from apps.enrollments.models import Enrollment, Coupon
from apps.accounts.api.serializers import StudentSerializer
from apps.courses.api.serializers import BatchSerializer
from datetime import date, datetime


class CouponSerializer(serializers.ModelSerializer):
    class Meta:
        model = Coupon
        fields = ['id', 'code', 'name', 'discount_types', 'discount_value', 'expires_at']
        read_only_fields = ['id']

    def validate_expires_at(self, value):
        """Validate that the expiration date is in the future"""
        if value < datetime.now():
            raise serializers.ValidationError("Expiration date must be in the future")
        return value


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
            if coupon.expires_at < datetime.now():
                raise serializers.ValidationError("This coupon has expired")
            return value
        except Coupon.DoesNotExist:
            raise serializers.ValidationError("Invalid coupon code")
