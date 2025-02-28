"""
Celery beat schedule configuration.

This file defines scheduled tasks that should run periodically.
The tasks are registered with the Django admin interface via django-celery-beat,
which allows dynamic modification of the schedule.
"""
from celery.schedules import crontab

# Define the periodic tasks
CELERYBEAT_SCHEDULE = {
    'daily-invoice-generation-check': {
        'task': 'tasks.payments.generate_monthly_invoices',
        'schedule': crontab(hour=0, minute=30),  # Run at 00:30 AM every day
        'options': {
            'expires': 3600,  # 1 hour expiry time
        },
    },
    'daily-payment-reminder-check': {
        'task': 'tasks.payments.send_payment_reminders',
        'schedule': crontab(hour=9, minute=0),  # Run at 9:00 AM every day
        'options': {
            'expires': 3600,  # 1 hour expiry time
        },
    },
}
