from django.core.management.base import BaseCommand
from django.core.management import call_command
from django.db import connection
from django.db.utils import OperationalError


class Command(BaseCommand):
    help = 'Sets up the database cache table and Celery tables'

    def handle(self, *args, **kwargs):
        # Create cache table
        self.stdout.write('Creating cache table...')
        call_command('createcachetable')

        # Create Celery tables
        try:
            self.stdout.write('Creating Celery database tables...')
            call_command('migrate', 'django_celery_results')
            call_command('migrate', 'django_celery_beat')
            self.stdout.write(self.style.SUCCESS('Cache and Celery tables created successfully!'))
        except OperationalError as e:
            self.stdout.write(
                self.style.WARNING(
                    f'Error creating Celery tables: {str(e)}. '
                    'You may need to run migrations first.'
                )
            )
