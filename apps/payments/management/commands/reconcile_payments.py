"""
Django management command to reconcile stale bKash payments.

This command should be run periodically (every 5-10 minutes) via cron job to:
1. Find payments stuck in 'Initiated' status
2. Query bKash API for their actual status
3. Update our database accordingly
4. Clean up expired payments and temp invoices

Usage:
    python manage.py reconcile_payments
    python manage.py reconcile_payments --dry-run  # Preview without making changes
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
import logging

from apps.payments.models import Payment, Invoice
from services.bkash import bkash_client

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Reconcile stale bKash payments that are stuck in Initiated status'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview changes without actually saving to database',
        )
        parser.add_argument(
            '--stale-minutes',
            type=int,
            default=5,
            help='Consider payments stale after this many minutes (default: 5)',
        )
        parser.add_argument(
            '--expiry-hours',
            type=int,
            default=24,
            help='Mark payments as expired after this many hours (default: 24)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        stale_minutes = options['stale_minutes']
        expiry_hours = options['expiry_hours']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be saved'))

        now = timezone.now()
        stale_threshold = now - timedelta(minutes=stale_minutes)
        expiry_threshold = now - timedelta(hours=expiry_hours)

        # Get all stale initiated payments
        stale_payments = Payment.objects.filter(
            status=Payment.INITIATED,
            created_at__lt=stale_threshold
        )

        total_count = stale_payments.count()
        self.stdout.write(f'Found {total_count} stale payments to check')

        if total_count == 0:
            self.stdout.write(self.style.SUCCESS('No stale payments found. All clear!'))
            return

        # bkash_client is already imported from services.bkash

        reconciled_count = 0
        expired_count = 0
        error_count = 0

        for payment in stale_payments:
            try:
                # Query bKash for actual status FIRST, even for old payments
                # This ensures we never accidentally expire a payment that was actually completed
                if not payment.payment_id:
                    self.stdout.write(
                        self.style.WARNING(f'Payment {payment.id} has no bKash payment_id, skipping')
                    )
                    continue

                result = self._query_and_update_payment(payment, bkash_client, dry_run, expiry_threshold)
                if result:
                    reconciled_count += 1

            except Exception as e:
                error_count += 1
                self.stdout.write(
                    self.style.ERROR(f'Error processing payment {payment.payment_id}: {str(e)}')
                )
                logger.error(f'Reconciliation error for payment {payment.payment_id}: {str(e)}')

        # Summary
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=== Reconciliation Complete ==='))
        self.stdout.write(f'  Total checked: {total_count}')
        self.stdout.write(f'  Reconciled: {reconciled_count}')
        self.stdout.write(f'  Expired: {expired_count}')
        self.stdout.write(f'  Errors: {error_count}')

        if dry_run:
            self.stdout.write(self.style.WARNING('\nDRY RUN - No actual changes were made'))

    def _handle_expired_payment(self, payment, dry_run):
        """Mark old payments as failed and clean up temp invoices"""
        self.stdout.write(
            f'  Expiring payment {payment.payment_id} (created {payment.created_at})'
        )

        if not dry_run:
            payment.status = Payment.FAILED
            payment.save()

            # Clean up temp invoices that are now orphaned
            if payment.invoice and payment.invoice.temp_invoice:
                self.stdout.write(f'    -> Deleting orphaned temp invoice {payment.invoice.id}')
                payment.invoice.delete()

    def _query_and_update_payment(self, payment, bkash_client, dry_run, expiry_threshold):
        """Query bKash for payment status and update accordingly"""
        try:
            query_response = bkash_client.query_payment(payment.payment_id)

            if query_response.get("statusCode") != "0000":
                self.stdout.write(
                    self.style.WARNING(
                        f'  bKash query failed for {payment.payment_id}: '
                        f'{query_response.get("statusMessage", "Unknown error")}'
                    )
                )
                return False

            transaction_status = query_response.get('transactionStatus')
            self.stdout.write(f'  Payment {payment.payment_id}: bKash status = {transaction_status}')

            if transaction_status == "Completed":
                # Payment was actually completed on bKash! Update our records
                return self._mark_payment_completed(payment, query_response, dry_run)
            elif transaction_status in ["Failed", "Cancelled"]:
                return self._mark_payment_failed(payment, transaction_status, dry_run)
            else:
                # Still "Initiated" on bKash side
                # If it's very old AND bKash confirms it's still Initiated, safe to expire
                if payment.created_at < expiry_threshold:
                    self.stdout.write(f'    -> bKash confirms Initiated, but payment is >24h old. Expiring...')
                    self._handle_expired_payment(payment, dry_run)
                    return True
                else:
                    # Recent payment, will check again later
                    self.stdout.write(f'    -> Still {transaction_status}, will retry later')
                    return False

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'    -> Query error: {str(e)}'))
            return False

    def _mark_payment_completed(self, payment, query_response, dry_run):
        """Mark a payment as completed and update the invoice"""
        self.stdout.write(self.style.SUCCESS(f'    -> COMPLETED! Updating records...'))

        if not dry_run:
            payment.status = Payment.COMPLETED
            payment.transaction_id = query_response.get('trxID', payment.transaction_id)
            payment.payment_execute_time = timezone.now()
            payment.save()

            # Mark invoice as paid
            if payment.invoice:
                payment.invoice.is_paid = True
                payment.invoice.save()
                self.stdout.write(f'       Invoice {payment.invoice.id} marked as paid')

        return True

    def _mark_payment_failed(self, payment, status, dry_run):
        """Mark a payment as failed/cancelled"""
        self.stdout.write(self.style.WARNING(f'    -> {status}. Marking as {status}...'))

        if not dry_run:
            payment.status = Payment.FAILED if status == "Failed" else Payment.CANCELLED
            payment.save()

        return True
