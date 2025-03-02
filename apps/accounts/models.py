from django.db import models
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.validators import RegexValidator, URLValidator
from simple_history.models import HistoricalRecords


class UserManager(BaseUserManager):
    """
    Custom User Manager to use phone number as the USERNAME_FIELD.
    """

    def create_user(self, phone, name, address, facebook_profile, password=None, **extra_fields):
        """
        Create and save a User with the given phone, name, address, facebook_profile, and password.
        """
        if not phone:
            raise ValueError('The Phone number field must be set')

        # Set the username to be the same as the phone number
        extra_fields.setdefault('username', phone)
        user = self.model(
            phone=phone,
            name=name,
            address=address,
            facebook_profile=facebook_profile,
            **extra_fields
        )
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, phone, name, address, facebook_profile, password=None, **extra_fields):
        """
        Create and save a SuperUser with the given phone, name, address, facebook_profile, and password.
        """
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_admin', True)
        extra_fields.setdefault('is_active', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        return self.create_user(
            phone=phone,
            name=name,
            address=address,
            facebook_profile=facebook_profile,
            password=password,
            **extra_fields
        )


class User(AbstractUser):
    name = models.CharField(max_length=100)  # Parent's Name
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
    history = HistoricalRecords()  # Add history tracking

    objects = UserManager()

    USERNAME_FIELD = 'phone'  # Use phone as the username field
    REQUIRED_FIELDS = ['name', 'address', 'facebook_profile']  # Required for creating a superuser

    def __str__(self):
        return f"{self.name} ({self.phone})"

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
    history = HistoricalRecords()  # Add history tracking

    def __str__(self):
        return f"{self.name} (Child of {self.parent.name})"

    class Meta:
        db_table = 'students'
