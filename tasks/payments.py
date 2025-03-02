import logging
from datetime import datetime, timedelta, date
from calendar import monthrange
from typing import List, Dict, Any
from django.db.models import Q, Sum
from django.db.models.functions import TruncMonth
from collections import defaultdict
from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.enrollments.models import Enrollment
from apps.payments.models import Invoice
from apps.common.models import SystemSettings
from services.sms.client import send_enhanced_payment_reminder, sms_client
from apps.common.models import SMSLog

logger = logging.getLogger(__name__)


@shared_task
def generate_monthly_invoices():
    """
    Generate invoices for the next month for all active enrollments
    Should run a few days before the end of the month
    """
    # Only proceed if auto-generate is enabled in settings
    settings = SystemSettings.get_settings()
    if not settings.is_auto_generate_invoices():
        logger.info("Auto-generate invoices is disabled in system settings. Skipping invoice generation.")
        return

    today = timezone.now().date()
    # Get the first day of next month
    if today.month == 12:
        next_month = date(today.year + 1, 1, 1)
    else:
        next_month = date(today.year, today.month + 1, 1)

    logger.info(f"Generating invoices for {next_month.strftime('%B %Y')}")

    # Get all active enrollments
    enrollments = Enrollment.objects.filter(is_active=True)

    invoice_count = 0
    for enrollment in enrollments:
        # Check if an invoice for this month already exists
        existing_invoice = Invoice.objects.filter(
            enrollment=enrollment,
            month=next_month
        ).exists()

        if not existing_invoice:
            # Get the appropriate fee for this enrollment
            # First check if there's a student-specific override
            if enrollment.tuition_fee is not None:
                fee = enrollment.tuition_fee
            # Then check if there's a batch-specific fee
            elif enrollment.batch.tuition_fee is not None:
                fee = enrollment.batch.tuition_fee
            # Finally fall back to course's monthly fee
            else:
                fee = enrollment.batch.course.monthly_fee

            # Create the invoice
            Invoice.objects.create(
                enrollment=enrollment,
                month=next_month,
                amount=fee,
                is_paid=False
            )
            invoice_count += 1

    logger.info(f"Generated {invoice_count} invoices for {next_month.strftime('%B %Y')}")
    return invoice_count


@shared_task
def send_payment_reminders():
    """
    Send SMS reminders for unpaid invoices
    Should run on specific days of the month (e.g., 3rd and 7th)
    """
    # Only proceed if auto-reminders are enabled in settings
    settings = SystemSettings.get_settings()
    if not settings.is_auto_send_reminders():
        logger.info("Auto-send reminders is disabled in system settings. Skipping reminder sending.")
        return

    # Check if today is a reminder day (e.g., 3rd or 7th of the month)
    today = timezone.now().date()
    day_of_month = today.day
    reminder_days = SystemSettings.get_reminder_days()

    if day_of_month not in reminder_days:
        logger.info(f"Today ({day_of_month}) is not a reminder day {reminder_days}. Skipping.")
        return

    # Get all unpaid invoices for the current month or earlier
    current_month = date(today.year, today.month, 1)
    unpaid_invoices = Invoice.objects.filter(
        month__lte=current_month,  # Current month or earlier
        is_paid=False,
        enrollment__is_active=True  # Only for active enrollments
    ).select_related(
        'enrollment__student__parent',
        'enrollment__batch__course'
    )

    # Group invoices by parent to avoid sending multiple SMS to the same parent
    parent_invoices = {}
    for invoice in unpaid_invoices:
        parent = invoice.enrollment.student.parent
        if parent.phone not in parent_invoices:
            parent_invoices[parent.phone] = {
                'parent': parent,
                'students': {},
                'total_due': 0
            }

        student = invoice.enrollment.student
        if student.id not in parent_invoices[parent.phone]['students']:
            parent_invoices[parent.phone]['students'][student.id] = {
                'name': student.name,
                'courses': [],
                'months': [],
                'due': 0
            }

        # Add course info and month info and amount
        course_name = invoice.enrollment.batch.course.name
        month_name = invoice.month.strftime('%B %Y')

        parent_invoices[parent.phone]['students'][student.id]['courses'].append(course_name)
        parent_invoices[parent.phone]['students'][student.id]['months'].append(month_name)
        parent_invoices[parent.phone]['students'][student.id]['due'] += invoice.amount
        parent_invoices[parent.phone]['total_due'] += invoice.amount

    # Send SMS to each parent
    message_count = 0
    for phone, data in parent_invoices.items():
        parent = data['parent']
        total_due = data['total_due']

        # For single student case
        if len(data['students']) == 1:
            student_data = list(data['students'].values())[0]
            student_name = student_data['name']
            courses = ', '.join(set(student_data['courses']))
            due_months = list(set(student_data['months']))

            # Send the SMS using the enhanced payment reminder function
            try:
                sms_result = send_enhanced_payment_reminder(
                    phone_number=phone,
                    student_name=student_name,
                    course_name=courses,
                    due_months=due_months,
                    total_due=total_due,
                    user=None  # System generated
                )

                if sms_result.get('success'):
                    message_count += 1
                    logger.info(f"Payment reminder sent to {phone} for student {student_name}")
                else:
                    logger.error(f"Failed to send payment reminder to {phone}: {sms_result.get('message')}")

            except Exception as e:
                logger.error(f"Error sending payment reminder to {phone}: {str(e)}")
        else:
            # For multiple students - combine their months into one list
            all_due_months = set()
            for student_data in data['students'].values():
                all_due_months.update(set(student_data['months']))

            # Format in a way that's suitable for the enhanced payment reminder
            # Create a comma-separated list of student names
            student_names = ", ".join([data['name'] for _, data in data['students'].items()])

            # Use the same messaging service but with combined student names
            try:
                sms_result = send_enhanced_payment_reminder(
                    phone_number=phone,
                    student_name=student_names,  # All student names combined
                    course_name="multiple courses",  # Generic text
                    due_months=list(all_due_months),
                    total_due=total_due,
                    user=None  # System generated
                )

                if sms_result.get('success'):
                    message_count += 1
                    logger.info(f"Payment reminder sent to {phone} for {len(data['students'])} students")
                else:
                    logger.error(f"Failed to send payment reminder to {phone}: {sms_result.get('message')}")

            except Exception as e:
                logger.error(f"Error sending payment reminder to {phone}: {str(e)}")

    logger.info(f"Sent {message_count} payment reminders out of {len(parent_invoices)} parents")
    return message_count
