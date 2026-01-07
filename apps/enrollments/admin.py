from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse, path
from django.forms import ModelForm, Select, MultipleChoiceField, CheckboxSelectMultiple
from django.http import HttpResponseRedirect
from django.contrib import messages
from django.core.exceptions import ValidationError
from apps.enrollments.models import Enrollment, Coupon
from django.utils import timezone
from simple_history.admin import SimpleHistoryAdmin


@admin.register(Enrollment)
class EnrollmentAdmin(SimpleHistoryAdmin):
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
    actions = ['unenroll_students']
    history_list_display = ['student', 'batch', 'is_active', 'tuition_fee']

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

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('<int:enrollment_id>/unenroll/',
                 self.admin_site.admin_view(self.unenroll_view),
                 name='enrollment-unenroll'),
        ]
        return custom_urls + urls

    def unenroll_view(self, request, enrollment_id):
        """Unenroll a student from admin interface"""
        enrollment = Enrollment.objects.get(id=enrollment_id)

        if not enrollment.is_active:
            messages.warning(request, f"Enrollment #{enrollment_id} is already inactive.")
        else:
            student_name = enrollment.student.name
            batch_name = enrollment.batch.name
            course_name = enrollment.batch.course.name

            # Mark as inactive
            enrollment.is_active = False
            enrollment.save()

            messages.success(
                request,
                f"Successfully unenrolled {student_name} from {course_name} - {batch_name}. "
                "All historical payment records have been preserved."
            )

        return HttpResponseRedirect(reverse('admin:enrollments_enrollment_change', args=[enrollment_id]))

    def unenroll_students(self, request, queryset):
        """Bulk action to unenroll multiple students at once"""
        active_enrollments = queryset.filter(is_active=True)
        count = active_enrollments.count()

        # Update to inactive
        active_enrollments.update(is_active=False)

        if count == 0:
            messages.warning(request, "No active enrollments were found to unenroll.")
        else:
            messages.success(request, f"Successfully unenrolled {count} student(s). All historical payment records have been preserved.")

    unenroll_students.short_description = "Unenroll selected students (preserve payment history)"

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
        """Handle enrollment validation in admin interface"""
        # If tuition_fee is empty/None, don't save it (let it be null)
        # The view layer will handle fallbacks to batch or course fee
        if form.cleaned_data.get('tuition_fee') is None:
            obj.tuition_fee = None

        try:
            # Attempt to save the model
            super().save_model(request, obj, form, change)
        except ValidationError as e:
            # Add the validation error to the form's non-field errors
            form.add_error(None, e.message if hasattr(e, 'message') else str(e))
            raise

    def change_view(self, request, object_id, form_url='', extra_context=None):
        extra_context = extra_context or {}

        try:
            enrollment = self.get_queryset(request).get(pk=object_id)

            # Add custom action button for unenroll
            if enrollment.is_active:
                unenroll_url = reverse('admin:enrollment-unenroll', args=[object_id])
                extra_context['unenroll_url'] = unenroll_url

            return super().change_view(request, object_id, form_url, extra_context=extra_context)
        except ValidationError as e:
            # Handle validation error specifically for POST requests (form submissions)
            if request.method == 'POST':
                messages.error(request, str(e))
                # Get the object again
                enrollment = self.get_queryset(request).get(pk=object_id)
                # Redirect back to the same form
                opts = self.model._meta
                redirect_url = reverse(
                    f'admin:{opts.app_label}_{opts.model_name}_change',
                    args=(object_id,),
                    current_app=self.admin_site.name,
                )
                return HttpResponseRedirect(redirect_url)
            # Re-raise for GET requests or other cases
            raise


@admin.register(Coupon)
class CouponAdmin(SimpleHistoryAdmin):
    list_display = ('code', 'course_display', 'is_public', 'offer_message_preview', 'expires_at', 'is_active', 'is_expired')
    list_filter = ('is_active', 'is_public', 'course', 'expires_at')
    search_fields = ('code', 'description', 'offer_message', 'course__name')
    readonly_fields = ('created_at', 'updated_at')
    history_list_display = ['code', 'is_active', 'expires_at']

    fieldsets = (
        ('Coupon Information', {
            'fields': ('code', 'course', 'description', 'offer_message', 'is_public')
        }),
        ('Discount Details', {
            'fields': ('admission_fee_discount', 'tuition_fee_discount', 'first_month_discount'),
            'description': 'Enter exact amounts to be deducted.'
        }),
        ('Status', {
            'fields': ('expires_at', 'is_active')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def course_display(self, obj):
        return obj.course.name if obj.course else "All Courses"
    course_display.short_description = 'Course'
    course_display.admin_order_field = 'course__name'

    def offer_message_preview(self, obj):
        if obj.offer_message and len(obj.offer_message) > 50:
            return obj.offer_message[:50] + "..."
        return obj.offer_message
    offer_message_preview.short_description = 'Offer Message'

    def is_expired(self, obj):
        """Check if the coupon is expired"""
        if obj.expires_at is None:
            return False
        return obj.expires_at < timezone.now()
    is_expired.short_description = 'Expired'
    is_expired.boolean = True
