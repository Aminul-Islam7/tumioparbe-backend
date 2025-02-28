from django.db import models
from apps.accounts.models import User
from django.contrib.auth import get_user_model
from django.db.models import JSONField
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator

User = get_user_model()


class ActivityLog(models.Model):
    ACTION_TYPES = [
        ('ACCOUNT_CREATION', 'Account Creation'),
        ('ENROLLMENT', 'Student Enrollment'),
        ('PAYMENT', 'Payment'),
        ('COURSE_MODIFICATION', 'Course Modification'),
        ('BATCH_MODIFICATION', 'Batch Modification'),
        ('FEE_MODIFICATION', 'Fee Modification'),
        ('BATCH_TRANSFER', 'Batch Transfer'),
        ('REMINDER_SENT', 'Payment Reminder Sent'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    action_type = models.CharField(max_length=50, choices=ACTION_TYPES)
    metadata = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'activity_logs'


class SystemSettings(models.Model):
    """
    System-wide settings for customization

    There should only be one row in this table
    """
    # Reminder days (1-28, comma separated)
    payment_reminder_days = models.CharField(
        max_length=50,
        default="3,7",
        help_text="Days of the month to send payment reminders (comma-separated, e.g. '3,7,15')"
    )

    # Number of days before month end to generate next month's invoices
    invoice_generation_days = models.IntegerField(
        default=7,
        validators=[MinValueValidator(1), MaxValueValidator(15)],
        help_text="Number of days before month end to generate next month's invoices"
    )

    # Whether to generate invoices automatically
    auto_generate_invoices = models.BooleanField(
        default=True,
        help_text="Whether to automatically generate invoices"
    )

    # Whether to send SMS reminders automatically
    auto_send_reminders = models.BooleanField(
        default=True,
        help_text="Whether to automatically send SMS payment reminders"
    )

    # Creator and timestamps
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='created_settings'
    )
    updated_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='updated_settings'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'system_settings'
        verbose_name = 'System Setting'
        verbose_name_plural = 'System Settings'

    def __str__(self):
        return "System Settings"

    def clean(self):
        """Validate reminder days"""
        try:
            days = [int(day.strip()) for day in self.payment_reminder_days.split(',')]
            for day in days:
                if day < 1 or day > 28:
                    raise ValidationError({'payment_reminder_days': 'Each day must be between 1 and 28.'})
        except ValueError:
            raise ValidationError({'payment_reminder_days': 'Invalid format. Use comma-separated numbers (e.g., "3,7,15").'})

    @classmethod
    def get_settings(cls):
        """
        Get the system settings or create default if none exists
        """
        settings, created = cls.objects.get_or_create(
            id=1,
            defaults={
                'payment_reminder_days': '3,7',
                'invoice_generation_days': 7,
                'auto_generate_invoices': True,
                'auto_send_reminders': True,
            }
        )
        return settings

    @classmethod
    def get_reminder_days(cls):
        """
        Get the payment reminder days as a list of integers
        """
        settings = cls.get_settings()
        return [int(day.strip()) for day in settings.payment_reminder_days.split(',')]

    @classmethod
    def get_invoice_generation_days(cls):
        """
        Get the number of days before month end to generate next month's invoices
        """
        settings = cls.get_settings()
        return settings.invoice_generation_days

    @classmethod
    def is_auto_generate_invoices(cls):
        """
        Check if auto invoice generation is enabled
        """
        settings = cls.get_settings()
        return settings.auto_generate_invoices

    @classmethod
    def is_auto_send_reminders(cls):
        """
        Check if auto SMS reminders are enabled
        """
        settings = cls.get_settings()
        return settings.auto_send_reminders


class SMSLog(models.Model):
    """
    Model to track sent SMS messages
    """
    OTP = 'OTP'
    PAYMENT_REMINDER = 'PAYMENT_REMINDER'
    ENROLLMENT_CONFIRMATION = 'ENROLLMENT_CONFIRMATION'
    CUSTOM = 'CUSTOM'
    BULK = 'BULK'

    SMS_TYPE_CHOICES = [
        (OTP, 'OTP Verification'),
        (PAYMENT_REMINDER, 'Payment Reminder'),
        (ENROLLMENT_CONFIRMATION, 'Enrollment Confirmation'),
        (CUSTOM, 'Custom Message'),
        (BULK, 'Bulk Message')
    ]

    SUCCESS = 'SUCCESS'
    FAILED = 'FAILED'
    PARTIAL = 'PARTIAL_SUCCESS'
    PENDING = 'PENDING'
    DISABLED = 'DISABLED'

    STATUS_CHOICES = [
        (SUCCESS, 'Success'),
        (FAILED, 'Failed'),
        (PARTIAL, 'Partial Success'),
        (PENDING, 'Pending'),
        (DISABLED, 'Disabled')
    ]

    phone_number = models.CharField(
        max_length=20,
        help_text="Recipient phone number(s). For bulk SMS, this will contain the first few recipients."
    )
    message = models.TextField(help_text="Content of the SMS message")
    message_type = models.CharField(max_length=25, choices=SMS_TYPE_CHOICES, default=CUSTOM)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    sent_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sent_sms',
        help_text="User who initiated sending the SMS"
    )
    recipient_count = models.IntegerField(default=1, help_text="Number of recipients for bulk messages")
    successful_count = models.IntegerField(default=0, help_text="Number of successful deliveries")
    failed_count = models.IntegerField(default=0, help_text="Number of failed deliveries")
    api_response = JSONField(null=True, blank=True, help_text="Response from the SMS gateway API")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'sms_logs'
        ordering = ['-created_at']
        verbose_name = 'SMS Log'
        verbose_name_plural = 'SMS Logs'

    def __str__(self):
        if self.message_type == self.BULK:
            return f"Bulk SMS to {self.recipient_count} recipients on {self.created_at.strftime('%Y-%m-%d %H:%M')}"
        return f"SMS to {self.phone_number} ({self.get_message_type_display()}) on {self.created_at.strftime('%Y-%m-%d %H:%M')}"
