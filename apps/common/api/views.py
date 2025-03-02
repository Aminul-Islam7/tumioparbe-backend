from rest_framework import views, viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters
from django.db.models import Sum, Count, Q
from django.utils import timezone
from datetime import timedelta, date
from decimal import Decimal

from apps.common.models import SMSLog, SystemSettings
from apps.common.api.serializers import SMSLogSerializer, SingleSMSSerializer, BulkSMSSerializer
from services.sms.client import send_custom_notification, send_bulk_message, sms_client
from tasks.payments import generate_monthly_invoices, send_payment_reminders
from apps.payments.models import Invoice
from apps.enrollments.models import Enrollment
from apps.accounts.models import Student
from apps.courses.models import Course, Batch

import logging

logger = logging.getLogger(__name__)


class SMSViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for SMS functionality

    list:
        Get a list of SMS logs (admin only)

    retrieve:
        Get details of a specific SMS log (admin only)
    """
    queryset = SMSLog.objects.all().select_related('sent_by')
    serializer_class = SMSLogSerializer
    permission_classes = [IsAdminUser]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['message_type', 'status', 'created_at']
    search_fields = ['phone_number', 'message']
    ordering_fields = ['created_at', 'status', 'message_type']
    ordering = ['-created_at']

    def get_queryset(self):
        """Filter queryset based on query parameters"""
        queryset = super().get_queryset()

        # Filter by date range if provided
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')

        if start_date:
            queryset = queryset.filter(created_at__gte=start_date)
        if end_date:
            queryset = queryset.filter(created_at__lte=end_date)

        return queryset

    @action(detail=False, methods=['post'], permission_classes=[IsAdminUser])
    def send_single(self, request):
        """
        Send a single SMS
        """
        serializer = SingleSMSSerializer(data=request.data)
        if serializer.is_valid():
            phone_number = serializer.validated_data['phone_number']
            message = serializer.validated_data['message']

            result = send_custom_notification(phone_number, message, user=request.user)

            if result.get('success'):
                return Response({
                    'success': True,
                    'message': f'SMS sent successfully to {phone_number}',
                    'log_id': result.get('log_id')
                })
            else:
                return Response({
                    'success': False,
                    'message': f"Failed to send SMS: {result.get('message')}",
                    'log_id': result.get('log_id')
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'], permission_classes=[IsAdminUser])
    def send_bulk(self, request):
        """
        Send the same SMS to multiple recipients
        """
        serializer = BulkSMSSerializer(data=request.data)
        if serializer.is_valid():
            phone_numbers = serializer.validated_data['phone_numbers']
            message = serializer.validated_data['message']

            result = send_bulk_message(phone_numbers, message, user=request.user)

            if result.get('success'):
                return Response({
                    'success': True,
                    'message': f'SMS sent successfully to {len(phone_numbers)} recipients',
                    'log_id': result.get('log_id')
                })
            elif result.get('status') == 'PARTIAL_SUCCESS':
                return Response({
                    'success': True,
                    'message': f"SMS sent partially: {result.get('sent')} succeeded, {result.get('failed')} failed",
                    'failures': result.get('failures'),
                    'log_id': result.get('log_id')
                }, status=status.HTTP_207_MULTI_STATUS)
            else:
                return Response({
                    'success': False,
                    'message': f"Failed to send SMS: {result.get('message')}",
                    'log_id': result.get('log_id')
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'], permission_classes=[IsAdminUser])
    def check_balance(self, request):
        """
        Check current SMS balance
        """
        result = sms_client.check_balance()

        if 'error' in result:
            return Response({
                'success': False,
                'message': f"Failed to check balance: {result.get('error')}"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({
            'success': True,
            'balance': result.get('balance', 'Not available'),
        })

    @action(detail=False, methods=['get'], permission_classes=[IsAdminUser])
    def get_stats(self, request):
        """
        Get SMS usage statistics from provider
        """
        result = sms_client.get_sms_stats()

        if 'error' in result:
            return Response({
                'success': False,
                'message': f"Failed to get statistics: {result.get('error')}"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({
            'success': True,
            'statistics': result
        })

    @action(detail=False, methods=['get'])
    def dashboard_stats(self, request):
        """
        Get SMS dashboard statistics from our database
        """
        # Total counts by type
        total_count = SMSLog.objects.count()
        otp_count = SMSLog.objects.filter(message_type=SMSLog.OTP).count()
        payment_reminder_count = SMSLog.objects.filter(message_type=SMSLog.PAYMENT_REMINDER).count()
        custom_count = SMSLog.objects.filter(message_type=SMSLog.CUSTOM).count()
        bulk_count = SMSLog.objects.filter(message_type=SMSLog.BULK).count()

        # Get success rate
        success_count = SMSLog.objects.filter(status=SMSLog.SUCCESS).count()
        failed_count = SMSLog.objects.filter(status=SMSLog.FAILED).count()

        # Calculate statistics
        success_rate = (success_count / total_count * 100) if total_count > 0 else 0

        return Response({
            'success': True,
            'total_sms_sent': total_count,
            'success_rate': round(success_rate, 2),
            'by_type': {
                'otp': otp_count,
                'payment_reminder': payment_reminder_count,
                'custom': custom_count,
                'bulk': bulk_count
            },
            'by_status': {
                'success': success_count,
                'failed': failed_count,
                'partial': SMSLog.objects.filter(status=SMSLog.PARTIAL).count(),
                'pending': SMSLog.objects.filter(status=SMSLog.PENDING).count(),
                'disabled': SMSLog.objects.filter(status=SMSLog.DISABLED).count()
            }
        })


class AutomationViewSet(viewsets.GenericViewSet):
    """ViewSet for managing and triggering automated tasks"""
    permission_classes = [IsAdminUser]

    @action(detail=False, methods=['post'])
    def generate_invoices(self, request):
        """
        Manually trigger invoice generation for next month.
        By default, this task checks if it's within the configured window before month-end,
        but when triggered manually, it will force generate invoices regardless.
        """
        task_result = generate_monthly_invoices.delay()

        return Response({
            'success': True,
            'message': 'Invoice generation task started',
            'task_id': task_result.id,
        })

    @action(detail=False, methods=['post'])
    def send_reminders(self, request):
        """
        Manually trigger payment reminder sending.
        By default, this task checks if today is a configured reminder day,
        but when triggered manually, it will send reminders regardless.
        """
        task_result = send_payment_reminders.delay()

        return Response({
            'success': True,
            'message': 'Payment reminder task started',
            'task_id': task_result.id,
        })

    @action(detail=False, methods=['get'])
    def get_settings(self, request):
        """
        Get current automation settings
        """
        settings = SystemSettings.get_settings()

        return Response({
            'success': True,
            'payment_reminder_days': settings.payment_reminder_days,
            'invoice_generation_days': settings.invoice_generation_days,
            'auto_generate_invoices': settings.auto_generate_invoices,
            'auto_send_reminders': settings.auto_send_reminders,
        })


class ReportsViewSet(viewsets.ViewSet):
    """
    Viewset for generating various reports and analytics
    Admin-only endpoints for dashboard statistics and reporting
    """
    permission_classes = [IsAuthenticated, IsAdminUser]

    @action(detail=False, methods=['get'])
    def financial_summary(self, request):
        """
        Get financial summary data for dashboard
        Includes total dues, total earnings, and monthly breakdowns
        """
        try:
            # Get query parameters for date filtering
            today = timezone.now().date()
            current_month_start = date(today.year, today.month, 1)

            # Calculate total dues (all unpaid invoices)
            total_dues = Invoice.objects.filter(is_paid=False).aggregate(
                total=Sum('amount')
            )['total'] or Decimal('0.00')

            # Calculate current month dues
            current_month_dues = Invoice.objects.filter(
                is_paid=False,
                month__year=today.year,
                month__month=today.month
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

            # Calculate total earnings (all paid invoices)
            total_earnings = Invoice.objects.filter(is_paid=True).aggregate(
                total=Sum('amount')
            )['total'] or Decimal('0.00')

            # Calculate current month earnings
            current_month_earnings = Invoice.objects.filter(
                is_paid=True,
                month__year=today.year,
                month__month=today.month
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

            # Get recent payment trends (last 6 months)
            last_6_months = []
            for i in range(5, -1, -1):  # 5 months ago to current month
                month_date = current_month_start - timedelta(days=30*i)
                month_name = month_date.strftime('%b %Y')

                # Earnings that month
                month_earnings = Invoice.objects.filter(
                    is_paid=True,
                    month__year=month_date.year,
                    month__month=month_date.month
                ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

                # Collection rate
                month_invoices = Invoice.objects.filter(
                    month__year=month_date.year,
                    month__month=month_date.month
                )
                month_total_invoices = month_invoices.count()
                month_paid_invoices = month_invoices.filter(is_paid=True).count()
                collection_rate = (month_paid_invoices / month_total_invoices * 100) if month_total_invoices > 0 else 0

                last_6_months.append({
                    'month': month_name,
                    'earnings': float(month_earnings),
                    'collection_rate': round(collection_rate, 1)
                })

            return Response({
                'total_dues': float(total_dues),
                'current_month_dues': float(current_month_dues),
                'total_earnings': float(total_earnings),
                'current_month_earnings': float(current_month_earnings),
                'monthly_trends': last_6_months,
                'as_of_date': today.strftime('%Y-%m-%d')
            })

        except Exception as e:
            logger.error(f"Error generating financial summary: {str(e)}")
            return Response(
                {"error": f"Failed to generate report: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['get'])
    def enrollment_statistics(self, request):
        """
        Get enrollment statistics and metrics
        Includes student counts, course/batch distribution, and trends
        """
        try:
            # Calculate total counts
            total_students = Student.objects.count()
            total_active_enrollments = Enrollment.objects.filter(is_active=True).count()
            total_courses = Course.objects.filter(is_active=True).count()
            total_batches = Batch.objects.filter(is_visible=True).count()

            # Get enrollment counts per course
            course_enrollment_data = []
            for course in Course.objects.filter(is_active=True):
                enrollment_count = Enrollment.objects.filter(
                    batch__course=course,
                    is_active=True
                ).count()

                # Get batch-level data for this course
                batch_data = []
                for batch in course.batches.filter(is_visible=True):
                    batch_enrollment_count = Enrollment.objects.filter(
                        batch=batch,
                        is_active=True
                    ).count()

                    batch_data.append({
                        'batch_id': batch.id,
                        'batch_name': batch.name,
                        'enrollment_count': batch_enrollment_count
                    })

                course_enrollment_data.append({
                    'course_id': course.id,
                    'course_name': course.name,
                    'enrollment_count': enrollment_count,
                    'batches': batch_data
                })

            # Get enrollment trends (last 6 months)
            today = timezone.now().date()
            current_month_start = date(today.year, today.month, 1)
            enrollment_trends = []

            for i in range(5, -1, -1):  # 5 months ago to current month
                month_date = current_month_start - timedelta(days=30*i)
                month_name = month_date.strftime('%b %Y')
                month_start = date(month_date.year, month_date.month, 1)

                # Get next month for date range
                if month_date.month == 12:
                    next_month = date(month_date.year + 1, 1, 1)
                else:
                    next_month = date(month_date.year, month_date.month + 1, 1)

                # Count new enrollments that month
                new_enrollments = Enrollment.objects.filter(
                    start_month__gte=month_start,
                    start_month__lt=next_month
                ).count()

                enrollment_trends.append({
                    'month': month_name,
                    'new_enrollments': new_enrollments
                })

            return Response({
                'total_students': total_students,
                'active_enrollments': total_active_enrollments,
                'total_courses': total_courses,
                'total_batches': total_batches,
                'course_enrollment_data': course_enrollment_data,
                'enrollment_trends': enrollment_trends,
                'as_of_date': today.strftime('%Y-%m-%d')
            })

        except Exception as e:
            logger.error(f"Error generating enrollment statistics: {str(e)}")
            return Response(
                {"error": f"Failed to generate statistics: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['get'])
    def sms_statistics(self, request):
        """
        Get SMS delivery statistics
        Includes counts by type, delivery success rates
        """
        from apps.common.models import SMSLog

        try:
            # Get totals by message type
            sms_stats = {}

            message_types = SMSLog.SMS_TYPE_CHOICES
            for type_code, type_name in message_types:
                type_logs = SMSLog.objects.filter(message_type=type_code)

                # Calculate counts
                total_sent = sum(log.recipient_count for log in type_logs)
                successful = sum(log.successful_count for log in type_logs)
                failed = sum(log.failed_count for log in type_logs)

                # Calculate success rate
                success_rate = (successful / total_sent * 100) if total_sent > 0 else 0

                sms_stats[type_code] = {
                    'type_name': type_name,
                    'total_sent': total_sent,
                    'successful': successful,
                    'failed': failed,
                    'success_rate': round(success_rate, 1)
                }

            # Get overall statistics
            all_logs = SMSLog.objects.all()
            total_recipients = sum(log.recipient_count for log in all_logs)
            total_successful = sum(log.successful_count for log in all_logs)
            total_failed = sum(log.failed_count for log in all_logs)
            overall_success_rate = (total_successful / total_recipients * 100) if total_recipients > 0 else 0

            # Monthly breakdown (last 3 months)
            today = timezone.now().date()
            monthly_breakdown = []

            for i in range(2, -1, -1):  # 2 months ago to current month
                month_date = date(
                    today.year if today.month - i > 0 else today.year - 1,
                    ((today.month - i - 1) % 12) + 1,
                    1
                )
                month_name = month_date.strftime('%b %Y')

                month_logs = SMSLog.objects.filter(
                    created_at__year=month_date.year,
                    created_at__month=month_date.month
                )

                month_recipients = sum(log.recipient_count for log in month_logs)
                month_successful = sum(log.successful_count for log in month_logs)
                month_failed = sum(log.failed_count for log in month_logs)
                month_success_rate = (month_successful / month_recipients * 100) if month_recipients > 0 else 0

                monthly_breakdown.append({
                    'month': month_name,
                    'total_sent': month_recipients,
                    'successful': month_successful,
                    'failed': month_failed,
                    'success_rate': round(month_success_rate, 1)
                })

            return Response({
                'by_type': sms_stats,
                'overall': {
                    'total_sent': total_recipients,
                    'successful': total_successful,
                    'failed': total_failed,
                    'success_rate': round(overall_success_rate, 1)
                },
                'monthly_breakdown': monthly_breakdown,
                'as_of_date': today.strftime('%Y-%m-%d')
            })

        except Exception as e:
            logger.error(f"Error generating SMS statistics: {str(e)}")
            return Response(
                {"error": f"Failed to generate statistics: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
