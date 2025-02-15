from django.db import models
from apps.accounts.models import User


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


class SMSLog(models.Model):
    recipient = models.CharField(max_length=11)
    message = models.TextField()
    status = models.CharField(max_length=20)
    provider_response = models.JSONField(null=True)
    retry_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'sms_logs'
