from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse, path
from django.http import HttpResponseRedirect
from django.contrib import messages
from django.utils import timezone
from django.utils.crypto import get_random_string

from apps.payments.models import Invoice, Payment
from services.bkash import bkash_client


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
                payment = obj.payments.first()
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
    readonly_fields = ('created_at', 'updated_at', 'bkash_details')
    fieldsets = (
        ('Payment Information', {
            'fields': ('invoice', 'transaction_id', 'amount', 'payment_method', 'status')
        }),
        ('bKash Details', {
            'fields': ('payment_id', 'payer_reference', 'bkash_details'),
            'classes': ('collapse',),
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

    def bkash_details(self, obj):
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
    bkash_details.short_description = 'bKash Transaction Details'

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
