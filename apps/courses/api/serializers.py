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

    class Meta:
        model = Course
        fields = ['id', 'name', 'description', 'image', 'admission_fee', 'monthly_fee',
                  'is_active', 'created_at', 'updated_at', 'batches', 'batch_count', 'student_count']
        read_only_fields = ['id', 'created_at', 'updated_at', 'batch_count', 'student_count']

    def get_batch_count(self, obj):
        """Get the number of batches for this course"""
        return obj.batches.count()

    def get_student_count(self, obj):
        """Get the number of active enrolled students across all batches"""
        from apps.enrollments.models import Enrollment
        # Use a more direct approach
        return Enrollment.objects.filter(
            batch__course=obj,
            is_active=True
        ).count()


class BatchDetailSerializer(BatchSerializer):
    """Extended serializer for batch details with enrollment information"""

    class Meta(BatchSerializer.Meta):
        fields = BatchSerializer.Meta.fields
