from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from apps.enrollments.models import Enrollment, Coupon


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ('enrollment_id', 'student_name', 'parent_name', 'course_name', 'batch_name', 'start_month_display', 'tuition_fee_display', 'is_active', 'created_at')
    list_filter = ('is_active', 'start_month', 'batch__course', 'batch')
    search_fields = ('student__name', 'student__parent__name', 'batch__name', 'batch__course__name')
    readonly_fields = ('created_at', 'updated_at', 'invoices_link')
    fieldsets = (
        ('Enrollment Information', {
            'fields': ('student', 'batch', 'start_month', 'tuition_fee')
        }),
        ('Status', {
            'fields': ('is_active',)
        }),
        ('Related Information', {
            'fields': ('invoices_link',)
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    raw_id_fields = ('student', 'batch')
    list_select_related = ('student', 'student__parent', 'batch', 'batch__course')

    def enrollment_id(self, obj):
        url = reverse('admin:enrollments_enrollment_change', args=[obj.id])
        return format_html('<a href="{}">{}</a>', url, f"ENR-{obj.id}")
    enrollment_id.short_description = 'Enrollment ID'
    enrollment_id.admin_order_field = 'id'

    def student_name(self, obj):
        url = reverse('admin:accounts_student_change', args=[obj.student.id])
        return format_html('<a href="{}">{}</a>', url, obj.student.name)
    student_name.short_description = 'Student'
    student_name.admin_order_field = 'student__name'

    def parent_name(self, obj):
        url = reverse('admin:accounts_user_change', args=[obj.student.parent.id])
        return format_html('<a href="{}">{} ({})</a>',
                           url,
                           obj.student.parent.name,
                           obj.student.parent.phone)
    parent_name.short_description = 'Parent'
    parent_name.admin_order_field = 'student__parent__name'

    def course_name(self, obj):
        url = reverse('admin:courses_course_change', args=[obj.batch.course.id])
        return format_html('<a href="{}">{}</a>', url, obj.batch.course.name)
    course_name.short_description = 'Course'
    course_name.admin_order_field = 'batch__course__name'

    def batch_name(self, obj):
        url = reverse('admin:courses_batch_change', args=[obj.batch.id])
        return format_html('<a href="{}">{}</a>', url, obj.batch.name)
    batch_name.short_description = 'Batch'
    batch_name.admin_order_field = 'batch__name'

    def start_month_display(self, obj):
        return obj.start_month.strftime('%b %Y')
    start_month_display.short_description = 'Start Month'
    start_month_display.admin_order_field = 'start_month'

    def tuition_fee_display(self, obj):
        # Check if the fee is different from the batch/course fee
        batch_fee = obj.batch.tuition_fee or obj.batch.course.monthly_fee

        if obj.tuition_fee is None:
            # Show the inherited fee if tuition_fee is not set
            return format_html('৳{} <span style="color: #888; font-size: 0.8em;">(from {})</span>',
                               batch_fee,
                               "batch" if obj.batch.tuition_fee else "course")
        elif obj.tuition_fee != batch_fee:
            return format_html('<span style="color: green; font-weight: bold;">৳{}</span> <span style="color: #888; font-size: 0.8em;">(Custom)</span>', obj.tuition_fee)
        else:
            return format_html('৳{}', obj.tuition_fee)
    tuition_fee_display.short_description = 'Monthly Fee'
    tuition_fee_display.admin_order_field = 'tuition_fee'

    def invoices_link(self, obj):
        url = f"/admin/payments/invoice/?enrollment__id__exact={obj.id}"
        return format_html('<a class="button" href="{}">View Invoices</a>', url)
    invoices_link.short_description = 'Invoices'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'student', 'student__parent', 'batch', 'batch__course'
        )

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if 'tuition_fee' in form.base_fields:
            form.base_fields['tuition_fee'].required = False
        return form

    def save_model(self, request, obj, form, change):
        # If tuition_fee is empty/None, don't save it (let it be null)
        # The view layer will handle fallbacks to batch or course fee
        if form.cleaned_data.get('tuition_fee') is None:
            obj.tuition_fee = None
        super().save_model(request, obj, form, change)


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'discount_types_display', 'discount_value_display', 'expires_at', 'is_expired')
    list_filter = ('expires_at',)
    search_fields = ('code', 'name')
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        ('Coupon Information', {
            'fields': ('code', 'name', 'discount_types', 'discount_value', 'expires_at')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def discount_types_display(self, obj):
        type_map = {
            'TUITION': 'Tuition Discount',
            'ADMISSION': 'Admission Waiver',
            'FIRST_MONTH': 'First Month Waiver'
        }

        types = []
        for discount_type in obj.discount_types:
            if discount_type in type_map:
                types.append(type_map[discount_type])

        return ", ".join(types)
    discount_types_display.short_description = 'Discount Types'

    def discount_value_display(self, obj):
        if 'TUITION' in obj.discount_types and obj.discount_value:
            return f"{obj.discount_value}%"
        return "-"
    discount_value_display.short_description = 'Discount Value'

    def is_expired(self, obj):
        from datetime import datetime
        expired = obj.expires_at < datetime.now()
        if expired:
            return format_html('<span style="color:red;">Yes</span>')
        return format_html('<span style="color:green;">No</span>')
    is_expired.short_description = 'Expired'
    is_expired.boolean = True
