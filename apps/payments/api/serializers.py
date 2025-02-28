from rest_framework import serializers
from apps.payments.models import Payment, Invoice
from apps.enrollments.models import Enrollment
from django.utils import timezone
from datetime import date


class PaymentInitiateSerializer(serializers.Serializer):
    invoice_id = serializers.IntegerField()
    callback_url = serializers.URLField()
    customer_phone = serializers.CharField(max_length=20)


class BulkPaymentInitiateSerializer(serializers.Serializer):
    invoice_ids = serializers.ListField(
        child=serializers.IntegerField(),
        min_length=1,
        help_text="List of invoice IDs to pay together"
    )
    callback_url = serializers.URLField()
    customer_phone = serializers.CharField(max_length=20)


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ['id', 'invoice', 'transaction_id', 'amount', 'payment_method',
                  'status', 'payment_id', 'payer_reference', 'created_at']
        read_only_fields = ['id', 'created_at']


class InvoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invoice
        fields = ['id', 'enrollment', 'month', 'amount', 'is_paid', 'coupon', 'created_at']
        read_only_fields = ['id', 'created_at']


class ManualInvoiceCreateSerializer(serializers.Serializer):
    """
    Serializer for manual invoice creation by admins
    """
    enrollment = serializers.PrimaryKeyRelatedField(
        queryset=Enrollment.objects.all(),
        help_text="The enrollment ID this invoice is associated with"
    )
    month = serializers.DateField(
        help_text="Month for which this invoice is created (YYYY-MM-DD)"
    )
    amount = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Invoice amount"
    )
    is_paid = serializers.BooleanField(
        default=False,
        help_text="Whether the invoice is already paid"
    )
    coupon = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text="Optional coupon ID to apply to this invoice"
    )
    description = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Optional description for this manual invoice"
    )

    def validate(self, data):
        """
        Additional validation for manual invoice creation
        """
        # Check if an invoice already exists for this enrollment and month
        enrollment = data.get('enrollment')
        month = data.get('month')

        # Ensure month is set to the first day of the month
        if month and month.day != 1:
            # Convert to first day of month
            month = date(month.year, month.month, 1)
            data['month'] = month

        # Check for existing invoice
        if enrollment and month:
            existing_invoice = Invoice.objects.filter(
                enrollment=enrollment,
                month=month
            ).first()

            if existing_invoice:
                raise serializers.ValidationError(
                    f"An invoice already exists for this enrollment and month (Invoice ID: {existing_invoice.id})"
                )

        return data
