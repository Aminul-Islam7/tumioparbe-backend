from django.contrib import admin
from django.utils.html import format_html
from apps.courses.models import Course, Batch


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ('name', 'admission_fee', 'monthly_fee', 'batch_count', 'student_count', 'is_active', 'created_at')
    list_filter = ('is_active', 'created_at')
    search_fields = ('name', 'description')
    readonly_fields = ('created_at', 'updated_at', 'batch_count', 'student_count')
    fieldsets = (
        ('Course Information', {
            'fields': ('name', 'description', 'image')
        }),
        ('Financial Details', {
            'fields': ('admission_fee', 'monthly_fee')
        }),
        ('Status', {
            'fields': ('is_active',)
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def batch_count(self, obj):
        """Get the number of batches for this course"""
        count = obj.batches.count()
        url = f"/admin/courses/batch/?course__id__exact={obj.id}"
        return format_html('<a href="{}">{}</a>', url, count)
    batch_count.short_description = 'Batches'

    def student_count(self, obj):
        """Get the number of active enrolled students across all batches"""
        from apps.enrollments.models import Enrollment
        count = Enrollment.objects.filter(batch__course=obj, is_active=True).count()
        return count
    student_count.short_description = 'Students'


@admin.register(Batch)
class BatchAdmin(admin.ModelAdmin):
    list_display = ('name', 'course_link', 'timing', 'tuition_fee_display', 'student_count', 'is_visible', 'created_at')
    list_filter = ('is_visible', 'course', 'created_at')
    search_fields = ('name', 'course__name', 'timing')
    readonly_fields = ('created_at', 'updated_at', 'student_count')
    fieldsets = (
        ('Batch Information', {
            'fields': ('course', 'name', 'timing')
        }),
        ('Links', {
            'fields': ('group_link', 'class_link')
        }),
        ('Financial Details', {
            'fields': ('tuition_fee',)
        }),
        ('Status', {
            'fields': ('is_visible',)
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def course_link(self, obj):
        """Display course name with link to course"""
        url = f"/admin/courses/course/{obj.course.id}/change/"
        return format_html('<a href="{}">{}</a>', url, obj.course.name)
    course_link.short_description = 'Course'
    course_link.admin_order_field = 'course__name'

    def tuition_fee_display(self, obj):
        """Display tuition fee or inherited fee from course"""
        if obj.tuition_fee:
            return f"৳{obj.tuition_fee}"
        return f"৳{obj.course.monthly_fee} (from course)"
    tuition_fee_display.short_description = 'Tuition Fee'
    tuition_fee_display.admin_order_field = 'tuition_fee'

    def student_count(self, obj):
        """Get the number of active enrolled students in this batch"""
        from apps.enrollments.models import Enrollment
        count = Enrollment.objects.filter(batch=obj, is_active=True).count()
        if count > 0:
            url = f"/admin/enrollments/enrollment/?batch__id__exact={obj.id}"
            return format_html('<a href="{}">{}</a>', url, count)
        return 0
    student_count.short_description = 'Students'
