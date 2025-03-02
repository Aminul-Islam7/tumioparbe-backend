# Generated by Django 5.1.6 on 2025-02-28 14:23

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_historicalstudent_historicaluser'),
        ('courses', '0002_rename_tuition_fee_course_monthly_fee'),
        ('enrollments', '0003_historicalcoupon_historicalenrollment'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='enrollment',
            options={'ordering': ['-created_at']},
        ),
        migrations.AlterUniqueTogether(
            name='enrollment',
            unique_together={('student', 'batch', 'is_active')},
        ),
        migrations.RemoveField(
            model_name='coupon',
            name='name',
        ),
        migrations.RemoveField(
            model_name='historicalcoupon',
            name='name',
        ),
        migrations.AddField(
            model_name='coupon',
            name='description',
            field=models.TextField(default='One-off Default'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='coupon',
            name='is_active',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='historicalcoupon',
            name='description',
            field=models.TextField(default='One-off Default'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='historicalcoupon',
            name='is_active',
            field=models.BooleanField(default=True),
        ),
        migrations.AlterField(
            model_name='coupon',
            name='discount_types',
            field=models.JSONField(default=list),
        ),
        migrations.AlterField(
            model_name='enrollment',
            name='tuition_fee',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AlterField(
            model_name='historicalcoupon',
            name='discount_types',
            field=models.JSONField(default=list),
        ),
        migrations.AlterField(
            model_name='historicalenrollment',
            name='tuition_fee',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
    ]
