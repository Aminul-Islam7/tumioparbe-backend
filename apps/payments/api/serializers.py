from rest_framework import serializers
from apps.payments.models import Payment, Invoice


class PaymentInitiateSerializer(serializers.Serializer):
    invoice_id = serializers.IntegerField()
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
