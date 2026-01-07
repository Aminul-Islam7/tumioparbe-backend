from rest_framework import serializers
from apps.courses.models import Course, Batch
from django.db.models import Count, Sum
from django.db.models import Q


class BatchSerializer(serializers.ModelSerializer):
    student_count = serializers.SerializerMethodField()
    course_name = serializers.ReadOnlyField(source='course.name')

    class Meta:
        model = Batch
        fields = ['id', 'name', 'timing', 'group_link', 'class_link', 'tuition_fee',
                  'is_visible', 'course', 'course_name', 'student_count', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at', 'student_count', 'course_name']

    def get_student_count(self, obj):
        """Get the number of active enrolled students in this batch"""
        return obj.enrollments.filter(is_active=True).count()


class CourseSerializer(serializers.ModelSerializer):
    batches = BatchSerializer(many=True, read_only=True)
    batch_count = serializers.SerializerMethodField()
    student_count = serializers.SerializerMethodField()
    featured_coupon_details = serializers.SerializerMethodField()

    class Meta:
        model = Course
        fields = ['id', 'name', 'description', 'image', 'admission_fee', 'monthly_fee',
                  'is_active', 'featured_coupon', 'featured_coupon_details', 
                  'created_at', 'updated_at', 'batches', 'batch_count', 'student_count']
        read_only_fields = ['id', 'created_at', 'updated_at', 'batch_count', 'student_count', 'featured_coupon_details']

    def get_batch_count(self, obj):
        """Get the number of batches for this course"""
        return obj.batches.count()

    def get_student_count(self, obj):
        """Get the number of active enrolled students across all batches"""
        from apps.enrollments.models import Enrollment
        return Enrollment.objects.filter(
            batch__course=obj,
            is_active=True
        ).count()
    
    def get_featured_coupon_details(self, obj):
        """Get featured coupon details with calculated discounted prices"""
        if not obj.featured_coupon or not obj.featured_coupon.is_valid:
            return None
        
        coupon = obj.featured_coupon
        return {
            'id': coupon.id,
            'code': coupon.code,
            'offer_message': coupon.offer_message,
            'admission_fee_discount': float(coupon.admission_fee_discount),
            'tuition_fee_discount': float(coupon.tuition_fee_discount),
            'first_month_discount': float(coupon.first_month_discount),
            'discounted_admission_fee': max(0, float(obj.admission_fee) - float(coupon.admission_fee_discount)),
            'discounted_monthly_fee': max(0, float(obj.monthly_fee) - float(coupon.tuition_fee_discount)),
        }



class BatchDetailSerializer(BatchSerializer):
    """Extended serializer for batch details with enrollment information"""

    class Meta(BatchSerializer.Meta):
        fields = BatchSerializer.Meta.fields
