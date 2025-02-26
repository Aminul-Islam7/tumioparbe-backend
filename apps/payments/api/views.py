from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from django.utils.crypto import get_random_string
from django.utils import timezone
from django.http import HttpResponseRedirect
from django.conf import settings

from apps.payments.models import Invoice, Payment
from apps.payments.api.serializers import PaymentSerializer, PaymentInitiateSerializer, InvoiceSerializer
from services.bkash import bkash_client

import logging
import datetime

logger = logging.getLogger(__name__)


class PaymentViewSet(viewsets.ModelViewSet):
    """
    ViewSet for handling payment operations
    """
    queryset = Payment.objects.all()
    serializer_class = PaymentSerializer
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['post'])
    def initiate_bkash(self, request):
        """
        Initiate a bKash payment for an invoice
        """
        serializer = PaymentInitiateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Get validated data
        invoice_id = serializer.validated_data['invoice_id']
        callback_url = serializer.validated_data['callback_url']
        customer_phone = serializer.validated_data['customer_phone']

        # Get the invoice
        try:
            invoice = Invoice.objects.get(id=invoice_id)
        except Invoice.DoesNotExist:
            return Response({"error": "Invoice not found."}, status=status.HTTP_404_NOT_FOUND)

        # Check if invoice is already paid
        if invoice.is_paid:
            return Response({"error": "Invoice is already paid."}, status=status.HTTP_400_BAD_REQUEST)

        # Generate a unique merchant invoice number
        merchant_invoice_number = f"INV-{invoice.id}-{get_random_string(6).upper()}"

        try:
            # Call bKash API to create payment
            payment_response = bkash_client.create_payment(
                amount=str(invoice.amount),
                invoice_number=merchant_invoice_number,
                customer_phone=customer_phone,
                callback_url=callback_url
            )

            if payment_response.get("statusCode") != "0000":
                return Response({
                    "error": "bKash payment initiation failed",
                    "status_code": payment_response.get("statusCode"),
                    "status_message": payment_response.get("statusMessage")
                }, status=status.HTTP_400_BAD_REQUEST)

            # Create a payment record
            payment = Payment.objects.create(
                invoice=invoice,
                transaction_id=merchant_invoice_number,
                amount=invoice.amount,
                payment_method='bKash',
                status=Payment.INITIATED,
                payment_id=payment_response.get('paymentID'),
                payer_reference=customer_phone,
                payment_create_time=timezone.now()
            )

            # Return the bKash URL for redirecting the user
            return Response({
                "payment_id": payment.id,
                "bkash_payment_id": payment_response.get('paymentID'),
                "bkash_url": payment_response.get('bkashURL'),
                "callback_urls": {
                    "success": payment_response.get('successCallbackURL'),
                    "failure": payment_response.get('failureCallbackURL'),
                    "cancelled": payment_response.get('cancelledCallbackURL')
                }
            })

        except Exception as e:
            logger.error(f"Error initiating bKash payment: {str(e)}")
            return Response({"error": "Failed to initiate bKash payment."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'])
    def execute_bkash_payment(self, request):
        """
        Execute a bKash payment after user has authorized it
        """
        payment_id = request.data.get('paymentID')
        if not payment_id:
            return Response({"error": "Payment ID is required."}, status=status.HTTP_400_BAD_REQUEST)

        # Find the payment record
        try:
            payment = Payment.objects.get(payment_id=payment_id)
        except Payment.DoesNotExist:
            return Response({"error": "Payment not found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            # Call bKash API to execute payment
            execute_response = bkash_client.execute_payment(payment_id)

            if execute_response.get("statusCode") == "0000" and execute_response.get("transactionStatus") == "Completed":
                # Update payment record
                payment.status = Payment.COMPLETED
                payment.payment_execute_time = timezone.now()
                payment.transaction_id = execute_response.get('trxID', payment.transaction_id)
                payment.save()

                # Mark invoice as paid
                invoice = payment.invoice
                invoice.is_paid = True
                invoice.save()

                return Response({
                    "status": "success",
                    "transaction_id": payment.transaction_id,
                    "payment_status": payment.status,
                    "message": "Payment completed successfully."
                })
            else:
                payment.status = Payment.FAILED
                payment.save()

                return Response({
                    "status": "failed",
                    "message": execute_response.get('statusMessage', 'Payment execution failed'),
                    "bkash_status_code": execute_response.get('statusCode')
                }, status=status.HTTP_400_BAD_REQUEST)

        except Exception as e:
            logger.error(f"Error executing bKash payment: {str(e)}")
            return Response({"error": "Failed to execute bKash payment."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'])
    def query_bkash_payment(self, request):
        """
        Query the status of a bKash payment
        """
        payment_id = request.data.get('paymentID')
        if not payment_id:
            return Response({"error": "Payment ID is required."}, status=status.HTTP_400_BAD_REQUEST)

        # Find the payment record
        try:
            payment = Payment.objects.get(payment_id=payment_id)
        except Payment.DoesNotExist:
            return Response({"error": "Payment not found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            # Call bKash API to query payment status
            query_response = bkash_client.query_payment(payment_id)

            if query_response.get("statusCode") == "0000":
                transaction_status = query_response.get('transactionStatus')

                # Update payment status based on bKash response
                if transaction_status == "Completed":
                    payment.status = Payment.COMPLETED
                    payment.transaction_id = query_response.get('trxID', payment.transaction_id)

                    # Mark invoice as paid
                    invoice = payment.invoice
                    invoice.is_paid = True
                    invoice.save()

                elif transaction_status == "Initiated":
                    payment.status = Payment.INITIATED
                else:
                    payment.status = Payment.FAILED

                payment.save()

                return Response({
                    "payment_id": payment.id,
                    "bkash_payment_id": payment_id,
                    "transaction_status": transaction_status,
                    "payment_status": payment.status
                })
            else:
                return Response({
                    "error": "Failed to query payment status",
                    "bkash_status_code": query_response.get('statusCode'),
                    "bkash_status_message": query_response.get('statusMessage')
                }, status=status.HTTP_400_BAD_REQUEST)

        except Exception as e:
            logger.error(f"Error querying bKash payment: {str(e)}")
            return Response({"error": "Failed to query bKash payment."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'])
    def pay_invoice(self, request):
        """
        Pay a specific invoice using bKash
        """
        invoice_id = request.data.get('invoice_id')
        callback_url = request.data.get('callback_url')
        customer_phone = request.data.get('customer_phone')

        if not all([invoice_id, callback_url, customer_phone]):
            return Response(
                {"error": "invoice_id, callback_url, and customer_phone are required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Get the invoice
        try:
            invoice = Invoice.objects.get(id=invoice_id)
        except Invoice.DoesNotExist:
            return Response({"error": "Invoice not found"}, status=status.HTTP_404_NOT_FOUND)

        # Check if invoice belongs to the requesting user's student
        enrollment = invoice.enrollment
        student = enrollment.student
        if not request.user.is_staff and student.parent != request.user:
            return Response(
                {"error": "You don't have permission to pay this invoice"},
                status=status.HTTP_403_FORBIDDEN
            )

        # Check if invoice is already paid
        if invoice.is_paid:
            return Response({"error": "This invoice is already paid"}, status=status.HTTP_400_BAD_REQUEST)

        # Generate a unique merchant invoice number
        merchant_invoice_number = f"INV-{invoice.id}-{get_random_string(6).upper()}"

        try:
            # Call bKash API to create payment
            payment_response = bkash_client.create_payment(
                amount=str(invoice.amount),
                invoice_number=merchant_invoice_number,
                customer_phone=customer_phone,
                callback_url=callback_url
            )

            if payment_response.get("statusCode") != "0000":
                return Response({
                    "error": "bKash payment initiation failed",
                    "status_code": payment_response.get("statusCode"),
                    "status_message": payment_response.get("statusMessage")
                }, status=status.HTTP_400_BAD_REQUEST)

            # Create a payment record
            payment = Payment.objects.create(
                invoice=invoice,
                transaction_id=merchant_invoice_number,
                amount=invoice.amount,
                payment_method='bKash',
                status=Payment.INITIATED,
                payment_id=payment_response.get('paymentID'),
                payer_reference=customer_phone,
                payment_create_time=timezone.now()
            )

            # Return the bKash URL for redirecting the user
            return Response({
                "payment_id": payment.id,
                "bkash_payment_id": payment_response.get('paymentID'),
                "bkash_url": payment_response.get('bkashURL'),
                "callback_urls": {
                    "success": payment_response.get('successCallbackURL'),
                    "failure": payment_response.get('failureCallbackURL'),
                    "cancelled": payment_response.get('cancelledCallbackURL')
                }
            })

        except Exception as e:
            logger.error(f"Error initiating bKash payment for invoice {invoice_id}: {str(e)}")
            return Response({"error": "Failed to initiate bKash payment"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'])
    def pending_invoices(self, request):
        """
        Get all pending invoices for the current user's students
        """
        user = request.user

        # For regular users, only show invoices for their students
        if not user.is_staff:
            pending_invoices = Invoice.objects.filter(
                enrollment__student__parent=user,
                is_paid=False
            ).select_related(
                'enrollment',
                'enrollment__student',
                'enrollment__batch',
                'enrollment__batch__course',
                'coupon'
            )
        else:
            # For staff, show all pending invoices
            pending_invoices = Invoice.objects.filter(
                is_paid=False
            ).select_related(
                'enrollment',
                'enrollment__student',
                'enrollment__batch',
                'enrollment__batch__course',
                'coupon'
            )

        serializer = InvoiceSerializer(pending_invoices, many=True)

        # Add additional fields to make it more user-friendly
        result = []
        for invoice_data, invoice in zip(serializer.data, pending_invoices):
            invoice_data['student_name'] = invoice.enrollment.student.name
            invoice_data['course_name'] = invoice.enrollment.batch.course.name
            invoice_data['batch_name'] = invoice.enrollment.batch.name
            invoice_data['month_display'] = invoice.month.strftime('%B %Y')
            result.append(invoice_data)

        return Response(result)

    @action(detail=False, methods=['get'])
    def payment_history(self, request):
        """
        Get payment history for the current user's students
        """
        user = request.user

        # For regular users, only show payments for their students
        if not user.is_staff:
            payments = Payment.objects.filter(
                invoice__enrollment__student__parent=user
            ).select_related(
                'invoice',
                'invoice__enrollment',
                'invoice__enrollment__student',
                'invoice__enrollment__batch',
                'invoice__enrollment__batch__course'
            ).order_by('-created_at')
        else:
            # For staff, show all payments
            payments = Payment.objects.all().select_related(
                'invoice',
                'invoice__enrollment',
                'invoice__enrollment__student',
                'invoice__enrollment__batch',
                'invoice__enrollment__batch__course'
            ).order_by('-created_at')

        serializer = PaymentSerializer(payments, many=True)

        # Add additional fields to make it more user-friendly
        result = []
        for payment_data, payment in zip(serializer.data, payments):
            payment_data['student_name'] = payment.invoice.enrollment.student.name
            payment_data['course_name'] = payment.invoice.enrollment.batch.course.name
            payment_data['batch_name'] = payment.invoice.enrollment.batch.name
            payment_data['month'] = payment.invoice.month.strftime('%B %Y')
            result.append(payment_data)

        return Response(result)


class BkashCallbackView(APIView):
    """
    Handle bKash callbacks (success, failure, cancelled)
    """
    permission_classes = []  # No authentication required for callbacks

    def get(self, request):
        """
        Handle GET callbacks from bKash
        """
        # Extract parameters from the request
        payment_id = request.GET.get('paymentID')
        status = request.GET.get('status')

        if not payment_id:
            logger.error("bKash callback received without payment ID")
            return HttpResponseRedirect(settings.BKASH_CALLBACK_FAILURE_URL)

        # Find the payment in our system
        try:
            payment = Payment.objects.get(payment_id=payment_id)
        except Payment.DoesNotExist:
            logger.error(f"bKash callback received for unknown payment ID: {payment_id}")
            return HttpResponseRedirect(settings.BKASH_CALLBACK_FAILURE_URL)

        # Process based on status
        if status == 'success':
            # Execute the payment
            try:
                # Immediately redirect to frontend success URL
                # The actual payment execution will be handled by a separate API call
                return HttpResponseRedirect(f"{settings.BKASH_CALLBACK_SUCCESS_URL}?paymentID={payment_id}")
            except Exception as e:
                logger.error(f"Error processing bKash success callback: {str(e)}")
                return HttpResponseRedirect(settings.BKASH_CALLBACK_FAILURE_URL)

        elif status == 'failure':
            payment.status = Payment.FAILED
            payment.save()
            logger.info(f"bKash payment {payment_id} failed")
            return HttpResponseRedirect(settings.BKASH_CALLBACK_FAILURE_URL)

        elif status == 'cancel':
            payment.status = Payment.CANCELLED
            payment.save()
            logger.info(f"bKash payment {payment_id} cancelled")
            return HttpResponseRedirect(settings.BKASH_CALLBACK_CANCEL_URL)

        else:
            logger.error(f"bKash callback received with unknown status: {status}")
            return HttpResponseRedirect(settings.BKASH_CALLBACK_FAILURE_URL)
