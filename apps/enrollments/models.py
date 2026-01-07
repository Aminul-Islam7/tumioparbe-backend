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
    description = models.TextField(blank=True, help_text="Internal description for admin reference")
    offer_message = models.CharField(max_length=255, blank=True, help_text="Promotional message shown to parents (e.g., '50% off admission fee!')")
    
    # Course association - null means applies to all courses
    course = models.ForeignKey(
        'courses.Course', 
        on_delete=models.CASCADE, 
        related_name='coupons',
        null=True, 
        blank=True,
        help_text="Leave empty to apply to all courses"
    )
    
    # Visibility
    is_public = models.BooleanField(default=False, help_text="Public coupons are shown in the list during enrollment")
    
    # Discount amounts (exact values, not percentages)
    admission_fee_discount = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        default=0,
        help_text="Amount to deduct from admission fee"
    )
    tuition_fee_discount = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        default=0,
        help_text="Amount to deduct from monthly tuition fee"
    )
    first_month_discount = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        default=0,
        help_text="Additional discount for the first month only"
    )
    
    # Status
    # Status
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    history = HistoricalRecords()

    class Meta:
        db_table = 'coupons'

    def __str__(self):
        course_name = self.course.name if self.course else "All Courses"
        return f"{self.code} ({course_name})"
    
    @property
    def is_valid(self):
        """Check if coupon is currently valid (active and not expired)"""
        from django.utils import timezone
        if not self.is_active:
            return False
        if self.expires_at and self.expires_at <= timezone.now():
            return False
        return True
    
    def applies_to_course(self, course_id):
        """Check if this coupon applies to a specific course"""
        return self.course_id is None or self.course_id == course_id

