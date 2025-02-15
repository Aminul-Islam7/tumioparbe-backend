from django.db import models


class Course(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to='courses/', blank=True)
    admission_fee = models.DecimalField(max_digits=10, decimal_places=2)
    tuition_fee = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'courses'


class Batch(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='batches')
    name = models.CharField(max_length=100)
    timing = models.CharField(max_length=100)
    group_link = models.URLField(blank=True)
    class_link = models.URLField(blank=True)
    tuition_fee = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    is_visible = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'batches'
