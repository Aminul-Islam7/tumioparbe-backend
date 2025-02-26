from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse, path
from django.http import HttpResponseRedirect
from django.contrib import messages
from django.utils import timezone
from django.utils.crypto import get_random_string
from django import forms

from apps.payments.models import Invoice, Payment
from apps.accounts.models import Student
from apps.courses.models import Batch
from services.bkash import bkash_client
import logging

logger = logging.getLogger(__name__)

# Create a form to collect enrollment data for recovery (kept for backward compatibility)


class EnrollmentRecoveryForm(forms.Form):
    student = forms.ModelChoiceField(
        queryset=Student.objects.all(),
        label="Student",
        widget=forms.Select(attrs={'class': 'select2'})
    )
    batch = forms.ModelChoiceField(
        queryset=Batch.objects.all(),
        label="Batch",
        widget=forms.Select(attrs={'class': 'select2'})
    )
    start_month = forms.DateField(
        label="Start Month (YYYY-MM-DD)",
        widget=forms.DateInput(attrs={'type': 'date'})
    )
    coupon_code = forms.CharField(
        max_length=20,
        label="Coupon Code (Optional)",
        required=False
    )


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ('invoice_id', 'student_name', 'parent_phone', 'course_name', 'batch_name', 'month_display',
                    'amount_display', 'payment_status', 'created_at')
    list_filter = ('is_paid', 'month', 'enrollment__batch__course', 'enrollment__batch')
    search_fields = ('enrollment__student__name', 'enrollment__student__parent__name', 'enrollment__student__parent__phone')
    readonly_fields = ('created_at', 'updated_at', 'payment_details')
    fieldsets = (
        ('Invoice Information', {
            'fields': ('enrollment', 'month', 'amount', 'is_paid')
        }),
        ('Coupon', {
            'fields': ('coupon',)
        }),
        ('Payment Details', {
            'fields': ('payment_details',)
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    raw_id_fields = ('enrollment', 'coupon')

    def invoice_id(self, obj):
        return f"INV-{obj.id}"
    invoice_id.short_description = 'Invoice ID'

    def student_name(self, obj):
        if obj.enrollment is None:
            return format_html('<span style="color:grey;">No enrollment</span>')

        url = reverse('admin:accounts_student_change', args=[obj.enrollment.student.id])
        return format_html('<a href="{}">{}</a>', url, obj.enrollment.student.name)
    student_name.short_description = 'Student'
    student_name.admin_order_field = 'enrollment__student__name'

    def parent_phone(self, obj):
        if obj.enrollment is None:
            return format_html('<span style="color:grey;">No enrollment</span>')

        url = reverse('admin:accounts_user_change', args=[obj.enrollment.student.parent.id])
        parent = obj.enrollment.student.parent
        return format_html('<a href="{}">{} ({})</a>', url, parent.name, parent.phone)
    parent_phone.short_description = 'Parent'
    parent_phone.admin_order_field = 'enrollment__student__parent__phone'

    def course_name(self, obj):
        if obj.enrollment is None:
            return format_html('<span style="color:grey;">No enrollment</span>')

        url = reverse('admin:courses_course_change', args=[obj.enrollment.batch.course.id])
        return format_html('<a href="{}">{}</a>', url, obj.enrollment.batch.course.name)
    course_name.short_description = 'Course'
    course_name.admin_order_field = 'enrollment__batch__course__name'

    def batch_name(self, obj):
        if obj.enrollment is None:
            return format_html('<span style="color:grey;">No enrollment</span>')

        url = reverse('admin:courses_batch_change', args=[obj.enrollment.batch.id])
        return format_html('<a href="{}">{}</a>', url, obj.enrollment.batch.name)
    batch_name.short_description = 'Batch'
    batch_name.admin_order_field = 'enrollment__batch__name'

    def month_display(self, obj):
        return obj.month.strftime('%b %Y')
    month_display.short_description = 'Month'
    month_display.admin_order_field = 'month'

    def amount_display(self, obj):
        return format_html('৳{}', obj.amount)
    amount_display.short_description = 'Amount'
    amount_display.admin_order_field = 'amount'

    def payment_status(self, obj):
        if (obj.is_paid):
            if obj.payments.exists():
                # Look for a completed payment first, then fall back to the most recent payment
                completed_payment = obj.payments.filter(status=Payment.COMPLETED).first()
                payment = completed_payment if completed_payment else obj.payments.order_by('-created_at').first()
                
                status_text = "Paid"
                if payment.status == Payment.COMPLETED:
                    status_color = "green"
                elif payment.status == Payment.FAILED:
                    status_color = "red"
                    status_text = "Failed"
                elif payment.status == Payment.CANCELLED:
                    status_color = "orange"
                    status_text = "Cancelled"
                else:
                    status_color = "blue"
                    status_text = "In Process"

                return format_html(
                    '<span style="color:{}; font-weight:bold;">{}</span> <span style="color:#666; font-size:0.8em;">({} via {})</span>',
                    status_color,
                    status_text,
                    payment.created_at.strftime('%d-%b-%Y'),
                    payment.payment_method
                )
            else:
                return format_html('<span style="color:green; font-weight:bold;">Marked Paid</span>')
        return format_html('<span style="color:red; font-weight:bold;">Unpaid</span>')
    payment_status.short_description = 'Status'

    def payment_details(self, obj):
        """Show payment details if paid, or provide option to mark as paid"""
        if not obj.is_paid:
            mark_paid_url = f"/admin/payments/invoice/{obj.id}/mark-paid/"
            return format_html(
                '<a class="button" href="{}">Mark as Paid</a>',
                mark_paid_url
            )

        if obj.payments.exists():
            payments = obj.payments.all()
            html = '<ul>'
            for payment in payments:
                bkash_info = ""
                if payment.payment_method == 'bKash':
                    bkash_info = format_html(
                        ' | bKash ID: <strong>{}</strong> | Status: <strong>{}</strong>',
                        payment.payment_id or 'N/A',
                        payment.status
                    )

                html += format_html(
                    '<li>Transaction ID: <strong>{}</strong> | Amount: ৳{} | Method: {}{} | Date: {}</li>',
                    payment.transaction_id,
                    payment.amount,
                    payment.payment_method,
                    bkash_info,
                    payment.created_at.strftime('%d-%b-%Y %H:%M')
                )
            html += '</ul>'

            # Add button to mark as unpaid
            mark_unpaid_url = f"/admin/payments/invoice/{obj.id}/mark-unpaid/"
            html += format_html(
                '<div style="margin-top:10px;"><a class="button" style="background:red; color:white;" href="{}">Mark as Unpaid</a></div>',
                mark_unpaid_url
            )

            return format_html(html)
        else:
            mark_unpaid_url = f"/admin/payments/invoice/{obj.id}/mark-unpaid/"
            return format_html(
                '<div>No payment record, but marked as paid.</div>'
                '<div style="margin-top:10px;"><a class="button" style="background:red; color:white;" href="{}">Mark as Unpaid</a></div>',
                mark_unpaid_url
            )
    payment_details.short_description = 'Payment Information'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'enrollment__student',
            'enrollment__student__parent',
            'enrollment__batch',
            'enrollment__batch__course',
            'coupon'
        )

    def has_delete_permission(self, request, obj=None):
        # Allow deletion only if no payment is attached
        if obj and obj.payments.exists():
            return False
        return super().has_delete_permission(request, obj)

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('<int:invoice_id>/mark-paid/',
                 self.admin_site.admin_view(self.mark_as_paid_view),
                 name='invoice-mark-paid'),
            path('<int:invoice_id>/mark-unpaid/',
                 self.admin_site.admin_view(self.mark_as_unpaid_view),
                 name='invoice-mark-unpaid'),
        ]
        return custom_urls + urls

    def mark_as_paid_view(self, request, invoice_id):
        """Mark an invoice as paid manually"""
        invoice = Invoice.objects.get(id=invoice_id)

        if invoice.is_paid:
            messages.warning(request, f"Invoice #{invoice_id} is already marked as paid.")
        else:
            # Generate a manual payment record
            transaction_id = f"MANUAL-{timezone.now().strftime('%Y%m%d')}-{get_random_string(6).upper()}"
            Payment.objects.create(
                invoice=invoice,
                transaction_id=transaction_id,
                amount=invoice.amount,
                payment_method='Manual',
                status=Payment.COMPLETED,
                payment_create_time=timezone.now(),
                payment_execute_time=timezone.now()
            )

            invoice.is_paid = True
            invoice.save()
            messages.success(request, f"Invoice #{invoice_id} has been marked as paid successfully.")

        return HttpResponseRedirect(reverse('admin:payments_invoice_change', args=[invoice_id]))

    def mark_as_unpaid_view(self, request, invoice_id):
        """Mark an invoice as unpaid"""
        invoice = Invoice.objects.get(id=invoice_id)

        if not invoice.is_paid:
            messages.warning(request, f"Invoice #{invoice_id} is already marked as unpaid.")
        else:
            # Delete all payment records for this invoice or just mark them as failed
            payments = Payment.objects.filter(invoice=invoice)
            for payment in payments:
                payment.status = Payment.FAILED
                payment.save()

            invoice.is_paid = False
            invoice.save()
            messages.success(request, f"Invoice #{invoice_id} has been marked as unpaid. Existing payments have been marked as failed.")

        return HttpResponseRedirect(reverse('admin:payments_invoice_change', args=[invoice_id]))


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('transaction_id', 'invoice_details', 'amount_display', 'payment_method', 'payment_status_display', 'created_at')
    list_filter = ('payment_method', 'status', 'created_at')
    search_fields = ('transaction_id', 'payment_id', 'invoice__enrollment__student__name', 'invoice__enrollment__student__parent__phone')
    readonly_fields = ('created_at', 'updated_at', 'bkash_details', 'recovery_actions')
    fieldsets = (
        ('Payment Information', {
            'fields': ('invoice', 'transaction_id', 'amount', 'payment_method', 'status')
        }),
        ('bKash Details', {
            'fields': ('payment_id', 'payer_reference', 'bkash_details'),
            'classes': ('collapse',),
        }),
        ('Recovery Actions', {
            'fields': ('recovery_actions',),
            'description': 'Use these actions to fix payments and enrollments that may have failed or are incomplete.'
        }),
        ('Timestamps', {
            'fields': ('payment_create_time', 'payment_execute_time', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    raw_id_fields = ('invoice',)

    def payment_status_display(self, obj):
        if obj.status == Payment.COMPLETED:
            return format_html('<span style="color:green; font-weight:bold;">{}</span>', obj.status)
        elif obj.status == Payment.FAILED:
            return format_html('<span style="color:red; font-weight:bold;">{}</span>', obj.status)
        elif obj.status == Payment.CANCELLED:
            return format_html('<span style="color:orange; font-weight:bold;">{}</span>', obj.status)
        else:
            return format_html('<span style="color:blue; font-weight:bold;">{}</span>', obj.status)
    payment_status_display.short_description = 'Status'

    def recovery_actions(self, obj):
        """Show recovery actions for bKash payments"""
        if obj.payment_method != 'bKash' or not obj.payment_id:
            return "Recovery options are only available for bKash payments."

        html = ""

        # First action: Verify and Complete Payment
        verify_url = f"/admin/payments/payment/{obj.id}/auto-recover/"
        html += format_html(
            '<div style="margin-bottom:15px;">'
            '<h4>Payment Recovery</h4>'
            '<p>If the payment was successful but the enrollment was not completed, use this action:</p>'
            '<a class="button" style="background:#2271b1; color:white;" href="{}">'
            '<i class="fas fa-sync"></i> Automatically Recover Enrollment'
            '</a>'
            '<p style="color:#666; font-size:0.9em; margin-top:5px;">This will check the payment status with bKash and '
            'automatically create the enrollment using stored data.</p>'
            '</div>',
            verify_url
        )

        # Second action: Search Transaction
        if obj.status == Payment.COMPLETED:
            html += format_html(
                '<div style="margin-top:15px;">'
                '<h4>Associated Enrollment</h4>'
                '{}'
                '</div>',
                self._get_enrollment_status(obj)
            )

        return format_html(html)
    recovery_actions.short_description = 'Recovery Actions'

    def _get_enrollment_status(self, payment):
        """Check if payment has an associated enrollment"""
        from apps.enrollments.models import Enrollment

        if not payment.invoice or not payment.invoice.enrollment:
            return format_html(
                '<p style="color:red;">⚠️ No enrollment is associated with this payment!</p>'
                '<p>This could indicate a failed enrollment process.</p>'
            )

        enrollment = payment.invoice.enrollment
        url = reverse('admin:enrollments_enrollment_change', args=[enrollment.id])

        return format_html(
            '<p style="color:green;">✅ Enrollment found:</p>'
            '<p><strong>Student:</strong> {} | <strong>Batch:</strong> {} | <strong>Start Month:</strong> {}</p>'
            '<a href="{}" class="button">View Enrollment</a>',
            enrollment.student.name,
            enrollment.batch.name,
            enrollment.start_month.strftime('%b %Y'),
            url
        )

    def bkash_details(self, obj):
        # Existing implementation...
        if obj.payment_method != 'bKash' or not obj.payment_id:
            return "Not a bKash payment or no bKash details available."

        html = f"""
        <table style="width:100%; border-collapse:collapse;">
            <tr>
                <th style="text-align:left; padding:5px; border-bottom:1px solid #ddd;">Field</th>
                <th style="text-align:left; padding:5px; border-bottom:1px solid #ddd;">Value</th>
            </tr>
            <tr>
                <td style="padding:5px; border-bottom:1px solid #eee;">bKash Payment ID</td>
                <td style="padding:5px; border-bottom:1px solid #eee;">{obj.payment_id or 'N/A'}</td>
            </tr>
            <tr>
                <td style="padding:5px; border-bottom:1px solid #eee;">Customer Reference</td>
                <td style="padding:5px; border-bottom:1px solid #eee;">{obj.payer_reference or 'N/A'}</td>
            </tr>
            <tr>
                <td style="padding:5px; border-bottom:1px solid #eee;">Create Time</td>
                <td style="padding:5px; border-bottom:1px solid #eee;">{obj.payment_create_time.strftime('%d-%b-%Y %H:%M:%S') if obj.payment_create_time else 'N/A'}</td>
            </tr>
            <tr>
                <td style="padding:5px; border-bottom:1px solid #eee;">Execute Time</td>
                <td style="padding:5px; border-bottom:1px solid #eee;">{obj.payment_execute_time.strftime('%d-%b-%Y %H:%M:%S') if obj.payment_execute_time else 'N/A'}</td>
            </tr>
        </table>
        """

        if obj.status == Payment.INITIATED:
            query_url = f"/admin/payments/payment/{obj.id}/query-status/"
            html += format_html(
                '<div style="margin-top:10px;"><a class="button" href="{}">Query bKash Status</a></div>',
                query_url
            )

        return format_html(html)

    def amount_display(self, obj):
        return format_html('৳{}', obj.amount)
    amount_display.short_description = 'Amount'
    amount_display.admin_order_field = 'amount'

    def invoice_details(self, obj):
        url = reverse('admin:payments_invoice_change', args=[obj.invoice.id])

        if obj.invoice.enrollment is None:
            return format_html(
                '<a href="{}">Invoice #{} | No enrollment</a>',
                url, obj.invoice.id
            )

        student_name = obj.invoice.enrollment.student.name
        month = obj.invoice.month.strftime('%b %Y')
        course = obj.invoice.enrollment.batch.course.name

        return format_html(
            '<a href="{}">{} | {} | {}</a>',
            url, student_name, course, month
        )
    invoice_details.short_description = 'Invoice Details'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'invoice__enrollment__student',
            'invoice__enrollment__batch__course'
        )

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('<int:payment_id>/query-status/',
                 self.admin_site.admin_view(self.query_bkash_status_view),
                 name='payment-query-status'),
            path('<int:payment_id>/verify-complete/',
                 self.admin_site.admin_view(self.verify_complete_payment_view),
                 name='payment-verify-complete'),
            path('<int:payment_id>/complete-enrollment/',
                 self.admin_site.admin_view(self.complete_enrollment_view),
                 name='payment-complete-enrollment'),
            path('<int:payment_id>/auto-recover/',
                 self.admin_site.admin_view(self.auto_recover_enrollment),
                 name='payment-auto-recover'),
        ]
        return custom_urls + urls

    def query_bkash_status_view(self, request, payment_id):
        """Query bKash payment status for an initiated payment"""
        payment = Payment.objects.get(id=payment_id)

        if payment.payment_method != 'bKash' or not payment.payment_id:
            messages.error(request, "This is not a bKash payment or missing bKash payment ID.")
            return HttpResponseRedirect(reverse('admin:payments_payment_change', args=[payment_id]))

        try:
            # Query bKash API for payment status
            query_response = bkash_client.query_payment(payment.payment_id)

            if query_response.get("statusCode") == "0000":
                transaction_status = query_response.get('transactionStatus')

                # Update payment status based on bKash response
                old_status = payment.status

                if transaction_status == "Completed":
                    payment.status = Payment.COMPLETED
                    payment.transaction_id = query_response.get('trxID', payment.transaction_id)

                    # Mark invoice as paid
                    invoice = payment.invoice
                    invoice.is_paid = True
                    invoice.save()

                elif transaction_status == "Initiated":
                    payment.status = Payment.INITIATED
                else:
                    payment.status = Payment.FAILED

                payment.save()

                messages.success(
                    request,
                    f"Payment status updated from '{old_status}' to '{payment.status}' based on bKash response. "
                    f"Transaction status from bKash: {transaction_status}"
                )
            else:
                messages.warning(
                    request,
                    f"bKash query returned an error. Status Code: {query_response.get('statusCode')}, "
                    f"Message: {query_response.get('statusMessage')}"
                )
        except Exception as e:
            messages.error(request, f"Error querying bKash payment status: {str(e)}")

        return HttpResponseRedirect(reverse('admin:payments_payment_change', args=[payment_id]))

    def verify_complete_payment_view(self, request, payment_id):
        """Verify a payment status with bKash and show enrollment recovery form if successful"""
        from apps.enrollments.models import Enrollment
        from django.template import Context, Template

        payment = Payment.objects.get(id=payment_id)

        if payment.payment_method != 'bKash' or not payment.payment_id:
            messages.error(request, "This is not a bKash payment or missing bKash payment ID.")
            return HttpResponseRedirect(reverse('admin:payments_payment_change', args=[payment_id]))

        try:
            # If the payment already has an enrollment associated with its invoice, inform the admin
            if payment.invoice and payment.invoice.enrollment:
                enrollment = payment.invoice.enrollment
                messages.info(
                    request,
                    f"This payment already has an associated enrollment. "
                    f"Student: {enrollment.student.name}, Batch: {enrollment.batch.name}"
                )
                return HttpResponseRedirect(reverse('admin:payments_payment_change', args=[payment_id]))

            # First, verify the payment with bKash to make sure it's actually successful
            query_response = bkash_client.query_payment(payment.payment_id)

            if query_response.get("statusCode") != "0000" or query_response.get("transactionStatus") != "Completed":
                messages.error(
                    request,
                    f"This payment is not confirmed as successful by bKash. Status: {query_response.get('transactionStatus', 'Unknown')}"
                )
                return HttpResponseRedirect(reverse('admin:payments_payment_change', args=[payment_id]))

            # If not already completed, update payment status
            if payment.status != Payment.COMPLETED:
                payment.status = Payment.COMPLETED
                payment.transaction_id = query_response.get('trxID', payment.transaction_id)
                payment.payment_execute_time = timezone.now()
                payment.save()
                messages.success(request, f"Payment status updated to COMPLETED based on bKash verification.")

            # Payment is verified, collect enrollment data
            form = EnrollmentRecoveryForm()

            # Check for potential temp_invoice_id
            temp_invoice_id = None
            if payment.invoice and not payment.invoice.enrollment:
                temp_invoice_id = payment.invoice.id

            # Helper function to properly render form fields
            def render_field(field):
                return str(field)

            # Render a form for collecting enrollment data
            recovery_form_html = f"""
            <h1>Complete Enrollment Recovery</h1>
            <p>Payment ID: {payment.payment_id} has been confirmed as successful by bKash.</p>
            <p>Transaction ID: {payment.transaction_id}</p>
            <p>Amount: ৳{payment.amount}</p>
            
            <form method="post" action="{reverse('admin:payment-complete-enrollment', args=[payment_id])}">
                {{% csrf_token %}}
                <input type="hidden" name="temp_invoice_id" value="{temp_invoice_id}">
                
                <div style="margin-bottom:15px;">
                    <label for="id_student">Student:</label>
                    {render_field(form['student'])}
                </div>
                
                <div style="margin-bottom:15px;">
                    <label for="id_batch">Batch:</label>
                    {render_field(form['batch'])}
                </div>
                
                <div style="margin-bottom:15px;">
                    <label for="id_start_month">Start Month:</label>
                    {render_field(form['start_month'])}
                </div>
                
                <div style="margin-bottom:15px;">
                    <label for="id_coupon_code">Coupon Code (Optional):</label>
                    {render_field(form['coupon_code'])}
                </div>
                
                <button type="submit" class="button button-primary">Complete Enrollment</button>
            </form>
            """

            # Process the template to include the CSRF token
            template = Template(recovery_form_html)
            context = Context({'csrf_token': request.META.get('CSRF_COOKIE', '')})
            final_html = template.render(context)

            # Custom admin view response
            from django.http import HttpResponse
            return HttpResponse(final_html)

        except Exception as e:
            logger.error(f"Error in verify_complete_payment_view: {str(e)}")
            messages.error(request, f"Error verifying payment status: {str(e)}")
            return HttpResponseRedirect(reverse('admin:payments_payment_change', args=[payment_id]))

    def complete_enrollment_view(self, request, payment_id):
        """Process the enrollment recovery form and complete the enrollment"""
        from apps.enrollments.api.views import EnrollmentViewSet
        from rest_framework.test import APIRequestFactory
        from rest_framework.request import Request

        if request.method != 'POST':
            messages.error(request, "Invalid request method.")
            return HttpResponseRedirect(reverse('admin:payments_payment_change', args=[payment_id]))

        payment = Payment.objects.get(id=payment_id)

        try:
            # Collect form data
            student_id = request.POST.get('student')
            batch_id = request.POST.get('batch')
            start_month = request.POST.get('start_month')
            coupon_code = request.POST.get('coupon_code', '')
            temp_invoice_id = request.POST.get('temp_invoice_id')

            # Validate required fields
            if not all([student_id, batch_id, start_month]):
                messages.error(request, "All required fields must be provided.")
                return HttpResponseRedirect(reverse('admin:payment-verify-complete', args=[payment_id]))

            # Create enrollment data
            enrollment_data = {
                'student': student_id,
                'batch': batch_id,
                'start_month': start_month
            }

            if coupon_code:
                enrollment_data['coupon_code'] = coupon_code

            # Create a proper request object for the viewset
            factory = APIRequestFactory()
            api_request = factory.post('/api/enrollments/verify-and-complete-payment/')
            api_request.data = {
                'bkash_payment_id': payment.payment_id,
                'enrollment_data': enrollment_data,
                'temp_invoice_id': temp_invoice_id or payment.invoice.id
            }
            api_request.user = request.user  # Pass the admin user context

            # Use the EnrollmentViewSet to complete the enrollment
            viewset = EnrollmentViewSet()
            viewset.request = Request(api_request)
            response = viewset.verify_and_complete_payment(api_request)

            # Check if enrollment was successful
            if response.status_code in [200, 201]:
                # Get the enrollment ID from the response
                enrollment_id = None
                if 'enrollment' in response.data:
                    if isinstance(response.data['enrollment'], dict) and 'id' in response.data['enrollment']:
                        enrollment_id = response.data['enrollment']['id']

                success_message = f"Enrollment successfully completed! "
                if enrollment_id:
                    enrollment_url = reverse('admin:enrollments_enrollment_change', args=[enrollment_id])
                    success_message += f'<a href="{enrollment_url}">View Enrollment</a>'

                messages.success(request, format_html(success_message))
            else:
                error_message = "Failed to complete enrollment."
                if 'error' in response.data:
                    error_message += f" Error: {response.data['error']}"
                messages.error(request, error_message)

        except Exception as e:
            logger.error(f"Error in complete_enrollment_view: {str(e)}")
            messages.error(request, f"Error completing enrollment: {str(e)}")

        return HttpResponseRedirect(reverse('admin:payments_payment_change', args=[payment_id]))

    def auto_recover_enrollment(self, request, payment_id):
        """
        Automatically recover an enrollment using the stored temporary invoice data
        This is a fully automated version of the verify_complete_payment_view
        """
        from apps.enrollments.api.views import EnrollmentViewSet
        from rest_framework.test import APIRequestFactory
        from rest_framework.request import Request

        payment = Payment.objects.get(id=payment_id)

        if payment.payment_method != 'bKash' or not payment.payment_id:
            messages.error(request, "This is not a bKash payment or missing bKash payment ID.")
            return HttpResponseRedirect(reverse('admin:payments_payment_change', args=[payment_id]))

        try:
            # If the payment already has an enrollment associated with its invoice, inform the admin
            if payment.invoice and payment.invoice.enrollment:
                enrollment = payment.invoice.enrollment
                messages.info(
                    request,
                    f"This payment already has an associated enrollment. "
                    f"Student: {enrollment.student.name}, Batch: {enrollment.batch.name}"
                )
                return HttpResponseRedirect(reverse('admin:payments_payment_change', args=[payment_id]))

            # First, verify the payment with bKash to make sure it's actually successful
            query_response = bkash_client.query_payment(payment.payment_id)

            if query_response.get("statusCode") != "0000" or query_response.get("transactionStatus") != "Completed":
                messages.error(
                    request,
                    f"This payment is not confirmed as successful by bKash. Status: {query_response.get('transactionStatus', 'Unknown')}"
                )
                return HttpResponseRedirect(reverse('admin:payments_payment_change', args=[payment_id]))

            # Update payment status if not already completed
            if payment.status != Payment.COMPLETED:
                payment.status = Payment.COMPLETED
                payment.transaction_id = query_response.get('trxID', payment.transaction_id)
                payment.payment_execute_time = timezone.now()
                payment.save()
                messages.success(request, f"Payment status updated to COMPLETED based on bKash verification.")

            # Get the enrollment data from the temp invoice
            temp_invoice_id = payment.invoice.id
            temp_invoice = Invoice.objects.get(id=temp_invoice_id)

            if not temp_invoice.temp_invoice_data:
                messages.error(request, "No enrollment data found in the temporary invoice. Please use the manual recovery option.")
                return HttpResponseRedirect(reverse('admin:payment-verify-complete', args=[payment_id]))

            enrollment_data = temp_invoice.temp_invoice_data

            # Create a proper request object for the viewset
            factory = APIRequestFactory()
            api_request = factory.post('/api/enrollments/verify-and-complete-payment/')
            api_request.data = {
                'bkash_payment_id': payment.payment_id,
                'enrollment_data': enrollment_data,
                'temp_invoice_id': temp_invoice_id
            }
            api_request.user = request.user  # Pass the admin user context

            # Use the EnrollmentViewSet to complete the enrollment
            viewset = EnrollmentViewSet()
            viewset.request = Request(api_request)
            response = viewset.verify_and_complete_payment(api_request)

            # Check if enrollment was successful
            if response.status_code in [200, 201]:
                # Get the enrollment ID from the response
                enrollment_id = None
                if 'enrollment' in response.data:
                    if isinstance(response.data['enrollment'], dict) and 'id' in response.data['enrollment']:
                        enrollment_id = response.data['enrollment']['id']

                success_message = f"Enrollment automatically recovered successfully! "
                if enrollment_id:
                    enrollment_url = reverse('admin:enrollments_enrollment_change', args=[enrollment_id])
                    success_message += f'<a href="{enrollment_url}">View Enrollment</a>'

                messages.success(request, format_html(success_message))
            else:
                error_message = "Failed to complete enrollment automatically."
                if 'error' in response.data:
                    error_message += f" Error: {response.data['error']}"

                # Fall back to manual recovery if automatic fails
                error_message += " Falling back to manual recovery option."
                messages.warning(request, error_message)
                return HttpResponseRedirect(reverse('admin:payment-verify-complete', args=[payment_id]))

        except Exception as e:
            logger.error(f"Error in auto_recover_enrollment: {str(e)}")
            messages.error(request, f"Error automatically recovering enrollment: {str(e)}. Falling back to manual recovery.")
            return HttpResponseRedirect(reverse('admin:payment-verify-complete', args=[payment_id]))

        return HttpResponseRedirect(reverse('admin:payments_payment_change', args=[payment_id]))

    # Keep existing methods for backward compatibility
