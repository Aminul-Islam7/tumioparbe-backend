from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters

from apps.common.models import SMSLog, SystemSettings
from apps.common.api.serializers import SMSLogSerializer, SingleSMSSerializer, BulkSMSSerializer
from services.sms.client import send_custom_notification, send_bulk_message, sms_client
from tasks.payments import generate_monthly_invoices, send_payment_reminders

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
