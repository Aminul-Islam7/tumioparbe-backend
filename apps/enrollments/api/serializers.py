from rest_framework import serializers
from apps.enrollments.models import Enrollment, Coupon
from apps.accounts.api.serializers import StudentSerializer
from apps.courses.api.serializers import BatchSerializer
from datetime import date
from django.utils import timezone  # Use Django's timezone utilities instead of datetime


class CouponSerializer(serializers.ModelSerializer):
    is_expired = serializers.SerializerMethodField()
    is_valid = serializers.SerializerMethodField()
    course_name = serializers.SerializerMethodField()

    class Meta:
        model = Coupon
        fields = [
            'id', 'code', 'description', 'offer_message', 'course', 'course_name',
            'is_public', 'admission_fee_discount', 'tuition_fee_discount', 
            'first_month_discount', 'expires_at', 'is_active', 'is_expired', 'is_valid',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'is_expired', 'is_valid', 'course_name', 'created_at', 'updated_at']

    def get_is_expired(self, obj):
        """Check if the coupon is expired"""
        if obj.expires_at is None:
            return False
        return obj.expires_at < timezone.now()
    
    def get_is_valid(self, obj):
        """Check if the coupon is valid (active and not expired)"""
        return obj.is_valid
    
    def get_course_name(self, obj):
        """Get the course name or 'All Courses' for null"""
        return obj.course.name if obj.course else "All Courses"

    def validate_expires_at(self, value):
        """Validate that the expiration date is in the future for new coupons"""
        if value and self.instance is None and value < timezone.now():
            raise serializers.ValidationError("Expiration date must be in the future")
        return value
    
    def validate(self, data):
        """Validate that at least one discount amount is provided"""
        admission = data.get('admission_fee_discount', 0)
        tuition = data.get('tuition_fee_discount', 0)
        first_month = data.get('first_month_discount', 0)
        
        if admission == 0 and tuition == 0 and first_month == 0:
            raise serializers.ValidationError(
                "At least one discount amount must be greater than 0"
            )
        
        return data


class PublicCouponSerializer(serializers.ModelSerializer):
    """Simplified serializer for public coupons shown to parents"""
    class Meta:
        model = Coupon
        fields = [
            'id', 'code', 'offer_message', 'description',
            'admission_fee_discount', 'tuition_fee_discount', 'first_month_discount'
        ]


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
        if self.instance is None:
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
        """Validate that the coupon exists, is valid, and applies to the batch's course"""
        if not value:
            return value

        try:
            # Case-insensitive coupon lookup
            coupon = Coupon.objects.get(code__iexact=value)
            if not coupon.is_valid:
                if not coupon.is_active:
                    raise serializers.ValidationError("This coupon is no longer active")
                raise serializers.ValidationError("This coupon has expired")
            return value
        except Coupon.DoesNotExist:
            raise serializers.ValidationError("Invalid coupon code")
    
    def validate(self, data):
        """Additional validation to ensure coupon applies to the batch's course"""
        coupon_code = data.get('coupon_code')
        batch_id = data.get('batch')
        
        if coupon_code and batch_id:
            from apps.courses.models import Batch
            try:
                batch = Batch.objects.get(id=batch_id)
                # Case-insensitive coupon lookup
                coupon = Coupon.objects.get(code__iexact=coupon_code)
                if not coupon.applies_to_course(batch.course_id):
                    raise serializers.ValidationError({
                        "coupon_code": "This coupon does not apply to this course"
                    })
            except (Batch.DoesNotExist, Coupon.DoesNotExist):
                pass  # Let other validators handle these errors
        
        return data
