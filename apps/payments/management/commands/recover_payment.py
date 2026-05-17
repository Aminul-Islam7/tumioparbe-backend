"""
Management command to recover failed payments.

Usage:
    # Recover a specific payment
    python manage.py recover_payment --payment-id TR00114xxxxx
    
    # Find all inconsistent payments
    python manage.py recover_payment --find-issues
    
    # Auto-recover all inconsistent payments
    python manage.py recover_payment --auto-recover
    
    # Clean up orphaned temp invoices
    python manage.py recover_payment --cleanup --hours 24
"""

from django.core.management.base import BaseCommand, CommandError
from apps.payments.services.payment_recovery import PaymentRecoveryService


class Command(BaseCommand):
    help = 'Recover failed payments and fix inconsistent records'

    def add_arguments(self, parser):
        parser.add_argument(
            '--payment-id',
            type=str,
            help='Specific bKash payment ID to recover'
        )
        parser.add_argument(
            '--find-issues',
            action='store_true',
            help='Find all inconsistent payment records'
        )
        parser.add_argument(
            '--auto-recover',
            action='store_true',
            help='Automatically attempt to recover all inconsistent payments'
        )
        parser.add_argument(
            '--cleanup',
            action='store_true',
            help='Clean up orphaned temporary invoices'
        )
        parser.add_argument(
            '--hours',
            type=int,
            default=24,
            help='Hours threshold for cleanup (default: 24)'
        )

    def handle(self, *args, **options):
        if options['payment_id']:
            self._recover_single_payment(options['payment_id'])
        elif options['find_issues']:
            self._find_issues()
        elif options['auto_recover']:
            self._auto_recover()
        elif options['cleanup']:
            self._cleanup(options['hours'])
        else:
            self.stdout.write(self.style.WARNING(
                'Please specify an action: --payment-id, --find-issues, --auto-recover, or --cleanup'
            ))
    
    def _recover_single_payment(self, payment_id):
        self.stdout.write(f'Attempting to recover payment: {payment_id}')
        
        result = PaymentRecoveryService.verify_and_recover_payment(payment_id)
        
        if result['status'] == 'success':
            self.stdout.write(self.style.SUCCESS(f"✓ {result['message']}"))
            if result.get('enrollment'):
                self.stdout.write(f"  Enrollment: {result['enrollment']}")
            if result.get('transaction_id'):
                self.stdout.write(f"  Transaction ID: {result['transaction_id']}")
        elif result['status'] == 'partial_success':
            self.stdout.write(self.style.WARNING(f"⚠ {result['message']}"))
        else:
            self.stdout.write(self.style.ERROR(f"✗ {result['message']}"))
        
        self.stdout.write(f"  Recovery action: {result.get('recovery_action', 'unknown')}")
    
    def _find_issues(self):
        self.stdout.write('Scanning for inconsistent payment records...\n')
        
        issues = PaymentRecoveryService.find_inconsistent_payments()
        
        if not issues:
            self.stdout.write(self.style.SUCCESS('No inconsistent payments found! ✓'))
            return
        
        self.stdout.write(self.style.WARNING(f'Found {len(issues)} issues:\n'))
        
        for i, issue in enumerate(issues, 1):
            severity_color = self.style.ERROR if issue['severity'] == 'high' else self.style.WARNING
            self.stdout.write(severity_color(
                f"{i}. [{issue['severity'].upper()}] {issue['type']}"
            ))
            self.stdout.write(f"   Payment ID: {issue['payment_id']}")
            self.stdout.write(f"   Amount: {issue['amount']}")
            self.stdout.write(f"   Created: {issue['created_at']}")
            if issue.get('transaction_id'):
                self.stdout.write(f"   Transaction ID: {issue['transaction_id']}")
            self.stdout.write('')
    
    def _auto_recover(self):
        self.stdout.write('Starting automatic recovery...\n')
        
        results = PaymentRecoveryService.auto_recover_all_inconsistent()
        
        self.stdout.write(f"Total issues found: {results['total_issues']}")
        self.stdout.write(self.style.SUCCESS(f"Successfully recovered: {results['recovered']}"))
        if results['failed'] > 0:
            self.stdout.write(self.style.ERROR(f"Failed to recover: {results['failed']}"))
        
        if results['details']:
            self.stdout.write('\nDetails:')
            for detail in results['details']:
                status = '✓' if detail['result']['status'] == 'success' else '✗'
                self.stdout.write(f"  {status} {detail['payment_id']}: {detail['result']['message']}")
    
    def _cleanup(self, hours):
        self.stdout.write(f'Cleaning up orphaned invoices older than {hours} hours...\n')
        
        result = PaymentRecoveryService.cleanup_orphaned_temp_invoices(hours)
        
        self.stdout.write(self.style.SUCCESS(result['message']))
