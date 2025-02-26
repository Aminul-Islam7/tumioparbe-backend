# Tumio Parbe Backend

Backend for the Tumio Parbe Learning Management System.

## Development Setup

### Prerequisites

1. Python 3.12+
2. pipenv (optional but recommended)

### Project Setup

1. Clone the repository
2. Create and activate virtual environment:

   ```bash
   pipenv install
   pipenv shell
   ```

   Or with venv:

   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Copy `.env.example` to `.env` and update the values

5. Run migrations:

   ```bash
   python manage.py migrate
   ```

6. Set up database cache:

   ```bash
   python manage.py setup_cache
   ```

7. Create a superuser:

   ```bash
   python manage.py createsuperuser
   ```

8. Run the development server:
   ```bash
   python manage.py runserver
   ```

## Cache System

The application uses Django's database cache backend for:

- OTP storage and verification
- Session storage
- Celery results

The cache table is automatically created when you run `python manage.py setup_cache`. This needs to be run only once after setting up the database.

## Environment Variables

Key environment variables (see `.env` file for full list):

- `DEBUG`: Set to True for development
- `ADMIN_PHONE_NUMBERS`: Comma-separated list of phone numbers that get admin access
- `SMS_ENABLED`: Enable/disable SMS sending
- `BKASH_SANDBOX_MODE`: Use bKash sandbox for development

## Production Deployment

When deploying to production:

1. Run database migrations:

   ```bash
   python manage.py migrate
   ```

2. Set up the cache table:

   ```bash
   python manage.py setup_cache
   ```

3. Collect static files:

   ```bash
   python manage.py collectstatic --no-input
   ```

4. Configure your web server (Apache/Nginx) to serve the application

5. Make sure DEBUG is set to False in production
