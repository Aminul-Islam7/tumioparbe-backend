# Generated by Django 5.1.6 on 2025-02-28 03:11

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('common', '0002_alter_smslog_options_remove_smslog_provider_response_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SystemSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('payment_reminder_days', models.CharField(default='3,7', help_text="Days of the month to send payment reminders (comma-separated, e.g. '3,7,15')", max_length=50)),
                ('invoice_generation_days', models.IntegerField(default=7, help_text="Number of days before month end to generate next month's invoices", validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(15)])),
                ('auto_generate_invoices', models.BooleanField(default=True, help_text='Whether to automatically generate invoices')),
                ('auto_send_reminders', models.BooleanField(default=True, help_text='Whether to automatically send SMS payment reminders')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_settings', to=settings.AUTH_USER_MODEL)),
                ('updated_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='updated_settings', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'System Setting',
                'verbose_name_plural': 'System Settings',
                'db_table': 'system_settings',
            },
        ),
    ]
