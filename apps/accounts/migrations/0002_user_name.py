# Generated by Django 5.1.6 on 2025-02-25 03:02

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='name',
            field=models.CharField(default='temp', max_length=100),
            preserve_default=False,
        ),
    ]
