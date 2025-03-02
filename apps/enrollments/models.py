from django.db import models
from django.db.models import Q
from django.core.exceptions import ValidationError
from apps.accounts.models import Student
from apps.courses.models import Batch
from simple_history.models import HistoricalRecords


class Enrollment(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='enrollments')
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name='enrollments')
    start_month = models.DateField()
    tuition_fee = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    history = HistoricalRecords()  # Add history tracking

    class Meta:
        db_table = 'enrollments'
        unique_together = ['student', 'batch', 'is_active']
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.student.name} - {self.batch.name} ({self.start_month.strftime('%b %Y')})"

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
            # Exclude both current enrollment ID and inactive enrollments
            existing_enrollment = Enrollment.objects.filter(
                student=self.student,
                batch__course_id=course_id,
                is_active=True
            ).exclude(pk=self.pk if self.pk else None).first()

            if existing_enrollment:
                # If this is a reactivation of an existing enrollment and it conflicts
                # with another active enrollment, inform the admin clearly
                if self.pk:
                    raise ValidationError(
                        f"Cannot reactivate this enrollment because student {self.student.name} "
                        f"is already enrolled in batch {existing_enrollment.batch.name} of "
                        f"this course. Please deactivate the other enrollment first."
                    )
                else:
                    raise ValidationError(
                        f"Student {self.student.name} is already enrolled in batch "
                        f"{existing_enrollment.batch.name} of the same course. "
                        f"A student cannot be enrolled in multiple batches of the same course simultaneously."
                    )

        super().save(*args, **kwargs)


class Coupon(models.Model):
    code = models.CharField(max_length=20, unique=True)
    description = models.TextField()
    discount_types = models.JSONField(default=list)  # Options: ADMISSION, FIRST_MONTH, TUITION
    discount_value = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)  # For percentage discounts
    expires_at = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    history = HistoricalRecords()  # Also add history tracking to coupons

    class Meta:
        db_table = 'coupons'

    def __str__(self):
        return self.code
