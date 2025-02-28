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
from apps.common.models import SystemSettings, ActivityLog
from services.sms.client import send_payment_reminder, send_enhanced_payment_reminder

logger = logging.getLogger(__name__)


@shared_task
def generate_monthly_invoices():
    """
    Generate monthly invoices for active enrollments.
    This task should be scheduled to run a few days before the end of each month.
    """
    today = timezone.localdate()

    # Check if auto-generation is enabled
    if not SystemSettings.is_auto_generate_invoices():
        logger.info(f"[{today}] Automatic invoice generation is disabled. Skipping.")
        return {
            "status": "skipped",
            "reason": "Auto-generation disabled",
            "date": today.isoformat()
        }

    days_before = SystemSettings.get_invoice_generation_days()

    # Calculate the last day of the current month
    _, last_day = monthrange(today.year, today.month)
    end_of_month = date(today.year, today.month, last_day)

    # Check if today is within the days_before window
    if (end_of_month - today).days > days_before:
        logger.info(f"[{today}] Not within {days_before} days before end of month. Skipping invoice generation.")
        return {
            "status": "skipped",
            "reason": f"Not within {days_before} days of month end",
            "date": today.isoformat()
        }

    # Determine the next month (for which we're generating invoices)
    if today.month == 12:
        next_month = date(today.year + 1, 1, 1)
    else:
        next_month = date(today.year, today.month + 1, 1)

    logger.info(f"[{today}] Starting automatic invoice generation for {next_month.strftime('%B %Y')}")

    # Get all active enrollments
    active_enrollments = Enrollment.objects.filter(
        is_active=True,
        enrollment_date__lt=next_month  # Only include enrollments before next month
    )

    if not active_enrollments.exists():
        logger.info(f"No active enrollments found for invoice generation")
        return {
            "status": "completed",
            "invoices_generated": 0,
            "date": today.isoformat()
        }

    # Create new invoices for each active enrollment
    created_count = 0
    error_count = 0
    errors = []

    for enrollment in active_enrollments:
        try:
            # Check if invoice already exists for the enrollment and next month
            existing_invoice = Invoice.objects.filter(
                enrollment=enrollment,
                month=next_month
            ).exists()

            if existing_invoice:
                logger.debug(f"Invoice already exists for enrollment #{enrollment.id} for {next_month.strftime('%B %Y')}")
                continue

            # Get the fee (use override if exists, otherwise batch fee, otherwise course fee)
            if enrollment.fee_override is not None:
                fee = enrollment.fee_override
            elif enrollment.batch.fee_override is not None:
                fee = enrollment.batch.fee_override
            else:
                fee = enrollment.batch.course.tuition_fee

            # Create the invoice (unpaid by default)
            invoice = Invoice.objects.create(
                enrollment=enrollment,
                month=next_month,
                amount=fee,
                is_paid=False
            )

            logger.info(f"Created invoice #{invoice.id} for {enrollment.student.name} - {next_month.strftime('%B %Y')} - {fee}")
            created_count += 1

        except Exception as e:
            logger.error(f"Error creating invoice for enrollment #{enrollment.id}: {str(e)}")
            errors.append({
                "enrollment_id": enrollment.id,
                "student_name": enrollment.student.name,
                "error": str(e)
            })
            error_count += 1

    logger.info(f"Automatic invoice generation completed: {created_count} created, {error_count} errors")

    return {
        "status": "completed",
        "invoices_generated": created_count,
        "errors": error_count,
        "error_details": errors if errors else None,
        "date": today.isoformat()
    }


@shared_task
def send_payment_reminders():
    """
    Send SMS reminders for pending payments.
    This task should run daily and check if reminders should be sent today.
    Groups unpaid invoices by student and includes all previous due months.
    """
    today = timezone.localdate()
    current_day = today.day

    # Check if today is a reminder day based on settings
    reminder_days = SystemSettings.get_reminder_days()

    # Check if auto-reminders are enabled
    if not SystemSettings.is_auto_send_reminders():
        logger.info(f"[{today}] Automatic payment reminders are disabled. Skipping.")
        return {
            "status": "skipped",
            "reason": "Auto-reminders disabled",
            "date": today.isoformat()
        }

    if current_day not in reminder_days:
        logger.info(f"[{today}] Not a configured reminder day. Skipping reminder sending.")
        return {
            "status": "skipped",
            "reason": f"Day {current_day} not in reminder days {reminder_days}",
            "date": today.isoformat()
        }

    logger.info(f"[{today}] Starting payment reminder sending for day {current_day} of month")

    # Get all unpaid invoices for the current month or earlier (don't include future months)
    unpaid_invoices = Invoice.objects.filter(
        is_paid=False,
        month__lte=today,  # Only include invoices for current month or earlier
        enrollment__isnull=False  # Exclude temporary invoices
    ).select_related(
        'enrollment',
        'enrollment__student',
        'enrollment__student__parent',
        'enrollment__batch',
        'enrollment__batch__course'
    )

    if not unpaid_invoices.exists():
        logger.info(f"No unpaid invoices found for reminder sending")
        return {
            "status": "completed",
            "reminders_sent": 0,
            "date": today.isoformat()
        }

    # Group invoices by student and parent
    # This allows us to send one SMS per student with all their due months
    student_invoices = defaultdict(list)  # student_id -> list of invoices

    for invoice in unpaid_invoices:
        student = invoice.enrollment.student
        student_invoices[student.id].append(invoice)

    # Send reminder for each student with unpaid invoices
    sent_count = 0
    error_count = 0
    errors = []

    # Process each student
    for student_id, invoices in student_invoices.items():
        try:
            # Get the first invoice to extract student/parent info (all invoices are for the same student)
            first_invoice = invoices[0]
            student = first_invoice.enrollment.student
            parent = student.parent
            phone_number = parent.phone

            if not phone_number:
                logger.warning(f"No phone number found for parent of student #{student_id}")
                continue

            # Sort invoices by month to ensure correct ordering
            invoices = sorted(invoices, key=lambda inv: inv.month)

            # Extract course info from most recent invoice
            course_name = invoices[-1].enrollment.batch.course.name

            # Calculate total due amount
            total_due = sum(invoice.amount for invoice in invoices)

            # Extract month names for the message
            due_months = [invoice.month.strftime("%B %Y") for invoice in invoices]

            # Send the enhanced reminder SMS with all due months
            result = send_enhanced_payment_reminder(
                phone_number=phone_number,
                student_name=student.name,
                course_name=course_name,
                due_months=due_months,
                total_due=total_due,
                user=None  # System-generated
            )

            if result.get("success"):
                # Log the activity
                ActivityLog.objects.create(
                    user=parent,
                    action_type='REMINDER_SENT',
                    metadata={
                        "student_id": student.id,
                        "student_name": student.name,
                        "course_name": course_name,
                        "due_months": due_months,
                        "total_due": float(total_due),
                        "reminder_day": current_day,
                        "invoice_count": len(invoices)
                    }
                )

                sent_count += 1
                logger.info(f"Enhanced payment reminder sent to {phone_number} for student {student.name} with {len(invoices)} due months")
            else:
                logger.error(f"Failed to send payment reminder for student #{student_id}: {result.get('message')}")
                errors.append({
                    "student_id": student_id,
                    "student_name": student.name,
                    "parent_phone": phone_number,
                    "error": result.get("message")
                })
                error_count += 1

        except Exception as e:
            logger.error(f"Error sending payment reminder for student #{student_id}: {str(e)}")
            errors.append({
                "student_id": student_id,
                "error": str(e)
            })
            error_count += 1

    logger.info(f"Enhanced payment reminder sending completed: {sent_count} students notified, {error_count} errors")

    return {
        "status": "completed",
        "reminders_sent": sent_count,
        "errors": error_count,
        "error_details": errors if errors else None,
        "date": today.isoformat()
    }
