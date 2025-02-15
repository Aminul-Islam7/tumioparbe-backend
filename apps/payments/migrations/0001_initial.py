# Generated by Django 5.1.6 on 2025-02-15 11:57

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('enrollments', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Invoice',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('month', models.DateField()),
                ('amount', models.DecimalField(decimal_places=2, max_digits=10)),
                ('is_paid', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('coupon', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to='enrollments.coupon')),
                ('enrollment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='invoices', to='enrollments.enrollment')),
            ],
            options={
                'db_table': 'invoices',
                'unique_together': {('enrollment', 'month')},
            },
        ),
        migrations.CreateModel(
            name='Payment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('transaction_id', models.CharField(max_length=100, unique=True)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=10)),
                ('payment_method', models.CharField(default='bKash', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('invoice', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='payments', to='payments.invoice')),
            ],
            options={
                'db_table': 'payments',
            },
        ),
    ]
