from django.db import models
from apps.accounts.models import Student
from apps.courses.models import Batch


class Enrollment(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='enrollments')
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name='enrollments')
    start_month = models.DateField()
    tuition_fee = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'enrollments'
        unique_together = ['student', 'batch']

    def __str__(self):
        return f"{self.student.name} - {self.batch.name}"


class Coupon(models.Model):
    DISCOUNT_TYPE_CHOICES = [
        ('TUITION', 'Tuition Discount'),
        ('ADMISSION', 'Admission Fee Waiver'),
        ('FIRST_MONTH', 'First Month Waiver'),
    ]

    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100)
    discount_types = models.JSONField()  # List of discount types
    discount_value = models.DecimalField(max_digits=5, decimal_places=2, null=True)  # For percentage discounts
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'coupons'

    def __str__(self):
        return f"{self.name} ({self.code})"
