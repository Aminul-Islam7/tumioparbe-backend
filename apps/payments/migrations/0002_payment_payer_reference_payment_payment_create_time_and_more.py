# Generated by Django 5.1.6 on 2025-02-25 08:31

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='payment',
            name='payer_reference',
            field=models.CharField(blank=True, help_text='Reference to the payer (usually phone number)', max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='payment',
            name='payment_create_time',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='payment',
            name='payment_execute_time',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='payment',
            name='payment_id',
            field=models.CharField(blank=True, help_text='bKash payment ID', max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='payment',
            name='status',
            field=models.CharField(choices=[('Initiated', 'Initiated'), ('Completed', 'Completed'), ('Failed', 'Failed'), ('Cancelled', 'Cancelled')], default='Initiated', max_length=20),
        ),
        migrations.AddField(
            model_name='payment',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
    ]
