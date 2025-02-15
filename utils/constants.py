from django.core.validators import RegexValidator

phone_regex = RegexValidator(
    regex=r'^01[2-9]\d{8}$',
    message="Phone number must be in the format: '01XXXXXXXXX'."
)
