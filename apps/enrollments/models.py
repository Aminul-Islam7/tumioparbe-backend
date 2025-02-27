from django.db import models
from django.db.models import Q
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

    @classmethod
    def student_has_active_enrollment_in_course(cls, student_id, course_id):
        """
        Check if a student has an active enrollment in any batch of a specific course
        Returns the enrollment if found, otherwise None
        """
        return cls.objects.filter(
            student_id=student_id,
            batch__course_id=course_id,
            is_active=True
        ).first()

    def save(self, *args, **kwargs):
        """Override save to handle validation of enrollments in same course"""
        if self.is_active:
            # Get course ID from batch
            course_id = self.batch.course_id

            # Check for other active enrollments in the same course
            existing_enrollment = Enrollment.objects.filter(
                student=self.student,
                batch__course_id=course_id,
                is_active=True
            ).exclude(pk=self.pk if self.pk else None).first()

            if existing_enrollment:
                from django.core.exceptions import ValidationError
                raise ValidationError(
                    f"Student {self.student.name} is already enrolled in batch "
                    f"{existing_enrollment.batch.name} of the same course. "
                    f"A student cannot be enrolled in multiple batches of the same course simultaneously."
                )

        super().save(*args, **kwargs)


class Coupon(models.Model):
    DISCOUNT_TYPE_CHOICES = [
        ('TUITION', 'Tuition Discount'),
        ('ADMISSION', 'Admission Fee Waiver'),
        ('FIRST_MONTH', 'First Month Waiver'),
    ]

    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100)
    discount_types = models.JSONField()  # List of discount types
    discount_value = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)  # For percentage discounts
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'coupons'

    def __str__(self):
        return f"{self.name} ({self.code})"
