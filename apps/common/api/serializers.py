from rest_framework import serializers
from apps.common.models import SMSLog
from django.contrib.auth import get_user_model

User = get_user_model()


class SMSLogSerializer(serializers.ModelSerializer):
    """Serializer for SMS logs"""
    sent_by_name = serializers.SerializerMethodField()
    message_type_display = serializers.CharField(source='get_message_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = SMSLog
        fields = [
            'id', 'phone_number', 'message', 'message_type', 'message_type_display',
            'status', 'status_display', 'sent_by', 'sent_by_name', 'recipient_count',
            'successful_count', 'failed_count', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'sent_by_name', 'created_at', 'updated_at']

    def get_sent_by_name(self, obj):
        """Get the name of the user who sent the SMS"""
        if obj.sent_by:
            return obj.sent_by.name
        return None


class SingleSMSSerializer(serializers.Serializer):
    """Serializer for sending a single SMS"""
    phone_number = serializers.CharField(
        max_length=20,
        help_text="Recipient phone number"
    )
    message = serializers.CharField(
        help_text="SMS message content"
    )


class BulkSMSSerializer(serializers.Serializer):
    """Serializer for sending bulk SMS"""
    phone_numbers = serializers.ListField(
        child=serializers.CharField(max_length=20),
        help_text="List of recipient phone numbers",
        min_length=1
    )
    message = serializers.CharField(
        help_text="SMS message content"
    )

    def validate_phone_numbers(self, value):
        """Validate that the phone numbers are in a valid format"""
        import re
        bd_phone_regex = re.compile(r'^01[2-9]\d{8}$')

        invalid_numbers = []
        for phone in value:
            if not bd_phone_regex.match(phone) and not phone.startswith('+'):
                invalid_numbers.append(phone)

        if invalid_numbers:
            raise serializers.ValidationError(
                f"Invalid phone number format for: {', '.join(invalid_numbers)}. "
                f"Numbers should be in Bangladesh format (e.g., 01712345678) or international format with + prefix."
            )

        # Limit the number of recipients to prevent abuse
        if len(value) > 100:
            raise serializers.ValidationError(
                "Cannot send to more than 100 recipients in a single request."
            )

        return value
