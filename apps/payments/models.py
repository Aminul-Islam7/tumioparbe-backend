from django.db import models
from django.db.models import JSONField
from apps.enrollments.models import Enrollment, Coupon


class Invoice(models.Model):
    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE, related_name='invoices', null=True)
    month = models.DateField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    is_paid = models.BooleanField(default=False)
    coupon = models.ForeignKey(Coupon, null=True, blank=True, on_delete=models.SET_NULL)
    temp_invoice = models.BooleanField(default=False, help_text="Whether this is a temporary invoice for enrollment")
    temp_invoice_data = JSONField(null=True, blank=True, help_text="Temporary enrollment data for webhook processing")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'invoices'
        unique_together = ['enrollment', 'month']

    def __str__(self):
        if self.enrollment:
            return f"INV-{self.id} ({self.enrollment.student.name}, {self.month.strftime('%b %Y')})"
        return f"INV-{self.id} (Temporary Invoice)"


class Payment(models.Model):
    INITIATED = 'Initiated'
    COMPLETED = 'Completed'
    FAILED = 'Failed'
    CANCELLED = 'Cancelled'

    STATUS_CHOICES = [
        (INITIATED, 'Initiated'),
        (COMPLETED, 'Completed'),
        (FAILED, 'Failed'),
        (CANCELLED, 'Cancelled')
    ]

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='payments')
    transaction_id = models.CharField(max_length=100, unique=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=20, default='bKash')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=INITIATED)
    payment_id = models.CharField(max_length=100, blank=True, null=True, help_text="bKash payment ID")
    payer_reference = models.CharField(max_length=255, blank=True, null=True, help_text="Reference to the payer (usually phone number)")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now_add=True)
    payment_create_time = models.DateTimeField(null=True, blank=True)
    payment_execute_time = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.transaction_id} ({self.status})"

    class Meta:
        db_table = 'payments'
