from django.db import models
from django.contrib.auth.models import AbstractUser
from django.core.validators import RegexValidator, URLValidator


class User(AbstractUser):
    phone = models.CharField(
        max_length=11,
        unique=True,
        validators=[RegexValidator(regex=r'^01[2-9]\d{8}$')]
    )
    address = models.TextField()
    facebook_profile = models.URLField(validators=[URLValidator()])
    is_admin = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'users'


class Student(models.Model):
    parent = models.ForeignKey(User, on_delete=models.CASCADE, related_name='students')
    name = models.CharField(max_length=100)
    date_of_birth = models.DateField()
    school = models.CharField(max_length=100, blank=True)
    current_class = models.CharField(max_length=20, blank=True)
    father_name = models.CharField(max_length=100)
    mother_name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'students'
