from apps.enrollments.api.views import EnrollmentViewSet
import json
import base64
import hashlib
import hmac
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from django.shortcuts import get_object_or_404
from django.utils.crypto import get_random_string
from django.utils import timezone
from django.http import HttpResponseRedirect
from django.conf import settings
from django.db import transaction
from decimal import Decimal

from apps.payments.models import Invoice, Payment
from apps.enrollments.models import Enrollment, Coupon
from apps.payments.api.serializers import PaymentSerializer, PaymentInitiateSerializer, InvoiceSerializer, ManualInvoiceCreateSerializer, BulkPaymentInitiateSerializer
from services.bkash import bkash_client
from apps.common.utils import log_activity

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
    def bulk_pay_invoices(self, request):
        """
        Pay multiple invoices at once using bKash
        """
        serializer = BulkPaymentInitiateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Get validated data
        invoice_ids = serializer.validated_data['invoice_ids']
        callback_url = serializer.validated_data['callback_url']
        customer_phone = serializer.validated_data['customer_phone']

        if not invoice_ids:
            return Response({"error": "No invoice IDs provided"}, status=status.HTTP_400_BAD_REQUEST)

        # Get all the invoices
        invoices = Invoice.objects.filter(id__in=invoice_ids)

        # Check if all the invoices exist
        if invoices.count() != len(invoice_ids):
            return Response({"error": "One or more invoices not found"}, status=status.HTTP_404_NOT_FOUND)

        # Check if all invoices belong to the requesting user's students
        if not request.user.is_staff:
            unauthorized_invoices = invoices.exclude(enrollment__student__parent=request.user)
            if unauthorized_invoices.exists():
                return Response(
                    {"error": "You don't have permission to pay one or more of these invoices"},
                    status=status.HTTP_403_FORBIDDEN
                )

        # Check if any invoice is already paid
        already_paid = invoices.filter(is_paid=True)
        if already_paid.exists():
            paid_ids = list(already_paid.values_list('id', flat=True))
            return Response({
                "error": "One or more invoices are already paid",
                "paid_invoice_ids": paid_ids
            }, status=status.HTTP_400_BAD_REQUEST)

        # Calculate the total amount
        total_amount = sum(invoice.amount for invoice in invoices)

        # Generate a unique merchant invoice number
        merchant_invoice_number = f"MULTI-{'-'.join(str(id) for id in invoice_ids)}-{get_random_string(6).upper()}"

        try:
            # Start a transaction to ensure all operations succeed or fail together
            with transaction.atomic():
                # Create a "parent" invoice to track the multi-invoice payment
                parent_invoice = Invoice.objects.create(
                    enrollment=None,  # No specific enrollment
                    month=timezone.now().date().replace(day=1),  # First day of current month
                    amount=total_amount,
                    is_paid=False,
                    temp_invoice=True,
                    temp_invoice_data={
                        "type": "multi_invoice_payment",
                        "invoice_ids": invoice_ids,
                        "payment_date": timezone.now().isoformat()
                    }
                )

                # Call bKash API to create payment
                payment_response = bkash_client.create_payment(
                    amount=str(total_amount),
                    invoice_number=merchant_invoice_number,
                    customer_phone=customer_phone,
                    callback_url=callback_url
                )

                if payment_response.get("statusCode") != "0000":
                    # Transaction will be rolled back if this fails
                    return Response({
                        "error": "bKash payment initiation failed",
                        "status_code": payment_response.get("statusCode"),
                        "status_message": payment_response.get("statusMessage")
                    }, status=status.HTTP_400_BAD_REQUEST)

                # Create a payment record linked to the parent invoice
                payment = Payment.objects.create(
                    invoice=parent_invoice,
                    transaction_id=merchant_invoice_number,
                    amount=total_amount,
                    payment_method='bKash',
                    status=Payment.INITIATED,
                    payment_id=payment_response.get('paymentID'),
                    payer_reference=customer_phone,
                    payment_create_time=timezone.now()
                )

                # Log the activity
                log_activity(
                    user=request.user,
                    action_type='PAYMENT',
                    payment_id=payment.id,
                    payment_method='bKash',
                    amount=str(total_amount),
                    invoice_count=len(invoice_ids),
                    status='INITIATED',
                    invoice_ids=invoice_ids
                )

                # Return the bKash URL for redirecting the user
                return Response({
                    "payment_id": payment.id,
                    "bkash_payment_id": payment_response.get('paymentID'),
                    "bkash_url": payment_response.get('bkashURL'),
                    "total_amount": str(total_amount),
                    "invoice_count": len(invoice_ids),
                    "callback_urls": {
                        "success": payment_response.get('successCallbackURL'),
                        "failure": payment_response.get('failureCallbackURL'),
                        "cancelled": payment_response.get('cancelledCallbackURL')
                    }
                })

        except Exception as e:
            logger.error(f"Error initiating bulk bKash payment: {str(e)}")
            return Response({"error": f"Failed to initiate bulk payment: {str(e)}"},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'])
    def execute_bkash_payment(self, request):
        """
        Execute a bKash payment after user has authorized it and create enrollment if needed
        """
        payment_id = request.data.get('paymentID')
        if not payment_id:
            return Response({"error": "Payment ID is required."}, status=status.HTTP_400_BAD_REQUEST)

        # Find the payment record
        try:
            payment = Payment.objects.get(payment_id=payment_id)
        except Payment.DoesNotExist:
            return Response({"error": "Payment not found."}, status=status.HTTP_404_NOT_FOUND)

        # IDEMPOTENCY: If payment is already completed, return success immediately
        # This handles duplicate requests (e.g., user refresh, network retry)
        if payment.status == Payment.COMPLETED:
            logger.info(f"Payment {payment_id} already completed, returning cached result")
            
            # Check if there's an associated enrollment to return
            enrollment_info = None
            if payment.invoice and payment.invoice.enrollment:
                enrollment = payment.invoice.enrollment
                enrollment_info = {
                    "id": enrollment.id,
                    "student_name": enrollment.student.name,
                    "course_name": enrollment.batch.course.name,
                    "batch_name": enrollment.batch.name,
                }
            elif payment.invoice and payment.invoice.temp_invoice and payment.invoice.temp_invoice_data:
                # RECOVERY: Payment was completed but enrollment wasn't created
                # This can happen if the previous request failed after payment but before enrollment
                logger.warning(f"Payment {payment_id} is complete but has no enrollment - triggering recovery")
                
                from apps.payments.services.payment_recovery import PaymentRecoveryService
                
                recovery_result = PaymentRecoveryService._create_enrollment_from_temp_invoice(
                    payment.invoice, payment, request.user
                )
                
                if recovery_result["success"]:
                    enrollment_info = recovery_result["enrollment"]
                    logger.info(f"Successfully recovered enrollment for payment {payment_id}")
                else:
                    logger.error(f"Failed to recover enrollment for payment {payment_id}: {recovery_result['error']}")
                    return Response({
                        "status": "payment_succeeded_enrollment_failed",
                        "transaction_id": payment.transaction_id,
                        "message": f"Payment was successful but enrollment recovery failed: {recovery_result['error']}",
                    }, status=status.HTTP_207_MULTI_STATUS)
            
            return Response({
                "status": "success",
                "transaction_id": payment.transaction_id,
                "payment_status": payment.status,
                "message": "Payment was already completed successfully.",
                "enrollment": enrollment_info
            })

        # If payment failed or was cancelled, don't try to execute again
        if payment.status in [Payment.FAILED, Payment.CANCELLED]:
            return Response({
                "status": "failed",
                "message": f"Payment was previously marked as {payment.status}. Please initiate a new payment.",
                "payment_status": payment.status
            }, status=status.HTTP_400_BAD_REQUEST)

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

                # Check if this is a new enrollment payment (temporary invoice)
                if invoice.temp_invoice and invoice.temp_invoice_data and not invoice.enrollment:
                    try:
                        # Use a database transaction to ensure atomicity
                        from django.db import transaction
                        with transaction.atomic():
                            # Check if this is a multi-invoice payment
                            if (invoice.temp_invoice_data.get('type') == 'multi_invoice_payment' and
                                    'invoice_ids' in invoice.temp_invoice_data):
                                # Process each invoice in the bulk payment
                                invoice_ids = invoice.temp_invoice_data['invoice_ids']
                                processed_invoices = []

                                # Mark all individual invoices as paid and create payment records
                                for inv_id in invoice_ids:
                                    try:
                                        individual_invoice = Invoice.objects.get(id=inv_id)
                                        individual_invoice.is_paid = True
                                        individual_invoice.save()

                                        # Create a derived but unique transaction ID for each individual invoice
                                        # Format: original_trx_id-invoice_id
                                        unique_transaction_id = f"{payment.transaction_id}-{individual_invoice.id}"

                                        # Create individual payment record linked to this invoice
                                        individual_payment = Payment.objects.create(
                                            invoice=individual_invoice,
                                            transaction_id=unique_transaction_id,  # Use derived unique transaction ID
                                            amount=individual_invoice.amount,
                                            payment_method='bKash',
                                            status=Payment.COMPLETED,
                                            payment_id=payment.payment_id,  # Reference the same bKash payment
                                            payer_reference=payment.payer_reference,
                                            payment_create_time=payment.payment_create_time,
                                            payment_execute_time=payment.payment_execute_time
                                        )

                                        processed_invoices.append(inv_id)
                                        logger.info(f"Marked invoice #{inv_id} as paid and created payment record in bulk payment {payment_id}")
                                    except Invoice.DoesNotExist:
                                        logger.error(f"Invoice #{inv_id} not found in bulk payment {payment_id}")

                                # Delete the temporary invoice and its payment as they're no longer needed
                                temp_invoice_id = invoice.id
                                payment_id_to_return = payment.payment_id
                                trx_id_to_return = payment.transaction_id
                                payment.delete()
                                invoice.delete()

                                logger.info(f"Temporary invoice #{temp_invoice_id} and its payment deleted after distributing payments to individual invoices")

                                return Response({
                                    "status": "success",
                                    "transaction_id": trx_id_to_return,
                                    "payment_status": Payment.COMPLETED,
                                    "message": f"Bulk payment completed successfully. {len(processed_invoices)} invoices marked as paid.",
                                    "processed_invoices": processed_invoices
                                })
                            else:
                                # Regular enrollment payment
                                # Directly create the enrollment using the stored data
                                from apps.enrollments.api.serializers import EnrollmentSerializer
                                from apps.accounts.models import Student
                                from apps.courses.models import Batch
                                from datetime import date

                                enrollment_data = invoice.temp_invoice_data
                                logger.info(f"Creating enrollment directly with data: {enrollment_data}")

                                # First check if enrollment already exists
                                student_id = enrollment_data.get('student')
                                batch_id = enrollment_data.get('batch')

                                existing_enrollment = Enrollment.objects.filter(
                                    student_id=student_id,
                                    batch_id=batch_id,
                                    is_active=True
                                ).first()

                                if existing_enrollment:
                                    logger.info(f"Found existing enrollment for student {student_id} in batch {batch_id}")
                                    # Link payment to the existing enrollment's invoice
                                    first_month_invoice = Invoice.objects.filter(
                                        enrollment=existing_enrollment,
                                        month=existing_enrollment.start_month
                                    ).first()
                                    if first_month_invoice:
                                        payment.invoice = first_month_invoice
                                        payment.save()
                                    
                                    # Delete temp invoice
                                    invoice.delete()

                                    return Response({
                                        "status": "success",
                                        "transaction_id": payment.transaction_id,
                                        "payment_status": payment.status,
                                        "message": "Payment completed. Enrollment already exists.",
                                        "enrollment": {
                                            "id": existing_enrollment.id,
                                            "student_name": existing_enrollment.student.name,
                                            "course_name": existing_enrollment.batch.course.name,
                                            "batch_name": existing_enrollment.batch.name,
                                        }
                                    })

                                # Create new enrollment
                                serializer = EnrollmentSerializer(data=enrollment_data)
                                if serializer.is_valid():
                                    enrollment = serializer.save()
                                    logger.info(f"Created new enrollment with ID {enrollment.id}")

                                    batch = enrollment.batch
                                    course = batch.course

                                    # Get start month
                                    first_month_str = enrollment_data.get('start_month')
                                    first_month = date.fromisoformat(first_month_str)

                                    # Calculate fees
                                    tuition_fee = enrollment.tuition_fee
                                    if tuition_fee is None:
                                        tuition_fee = batch.tuition_fee or course.monthly_fee

                                    coupon_code = enrollment_data.get('coupon_code')
                                    coupon = None
                                    first_month_waiver = enrollment_data.get('first_month_waiver', False)
                                    first_month_fee = Decimal(str(tuition_fee)) if tuition_fee else Decimal('0.00')

                                    if coupon_code:
                                        try:
                                            coupon = Coupon.objects.get(code__iexact=coupon_code)
                                            if coupon.first_month_discount > 0 and not first_month_waiver:
                                                first_month_fee = max(Decimal('0.00'), first_month_fee - coupon.first_month_discount)
                                                if first_month_fee == 0:
                                                    first_month_waiver = True
                                                    logger.info("First month fee waived due to coupon (calculated)")
                                                else:
                                                    logger.info(f"First month fee reduced to {first_month_fee}")
                                        except Coupon.DoesNotExist:
                                            logger.warning(f"Coupon code {coupon_code} not found")

                                    # Create first month invoice (paid)
                                    first_month_invoice = Invoice.objects.create(
                                        enrollment=enrollment,
                                        month=first_month,
                                        amount=first_month_fee,
                                        is_paid=True,
                                        coupon=coupon
                                    )
                                    logger.info(f"Created first month invoice {first_month_invoice.id}")

                                    # Create next month invoice
                                    next_month_year = first_month.year + (first_month.month // 12)
                                    next_month_month = (first_month.month % 12) + 1
                                    next_month = date(next_month_year, next_month_month, 1)
                                    next_month_fee = Decimal(str(tuition_fee)) if tuition_fee else Decimal('0.00')
                                    next_month_is_paid = first_month_waiver  # If first month waived, payment was for next month

                                    next_month_invoice = Invoice.objects.create(
                                        enrollment=enrollment,
                                        month=next_month,
                                        amount=next_month_fee,
                                        is_paid=next_month_is_paid,
                                        coupon=coupon if next_month_is_paid else None
                                    )
                                    logger.info(f"Created next month invoice {next_month_invoice.id}")

                                    # Link payment to the correct invoice
                                    if first_month_waiver:
                                        payment.invoice = next_month_invoice
                                    else:
                                        payment.invoice = first_month_invoice
                                    payment.save()

                                    # Delete the temp invoice
                                    invoice.delete()

                                    # Log activity
                                    log_activity(
                                        user=request.user,
                                        action_type='ENROLLMENT',
                                        enrollment_id=enrollment.id,
                                        student_id=enrollment.student.id,
                                        student_name=enrollment.student.name,
                                        course=course.name,
                                        batch=batch.name,
                                        start_month=first_month.strftime('%B %Y'),
                                        tuition_fee=str(tuition_fee),
                                        coupon_code=coupon_code if coupon_code else None,
                                        has_first_month_waiver=first_month_waiver,
                                        payment_id=payment.id,
                                        transaction_id=payment.transaction_id,
                                        payment_method="bKash"
                                    )

                                    return Response({
                                        "status": "success",
                                        "transaction_id": payment.transaction_id,
                                        "payment_status": payment.status,
                                        "message": "Payment completed and enrollment created successfully.",
                                        "enrollment": {
                                            "id": enrollment.id,
                                            "student_name": enrollment.student.name,
                                            "course_name": course.name,
                                            "batch_name": batch.name,
                                        }
                                    })
                                else:
                                    logger.error(f"Enrollment serializer errors: {serializer.errors}")
                                    raise Exception(f"Enrollment validation failed: {serializer.errors}")
                    except Exception as e:
                        logger.error(f"Error creating enrollment after payment: {str(e)}")
                        # Since we're using atomic transaction, both payment execution and enrollment
                        # creation will be rolled back if an error occurs
                        return Response({
                            "status": "payment_succeeded_enrollment_failed",
                            "message": f"Payment was successful but enrollment creation failed: {str(e)}",
                            "transaction_id": payment.transaction_id
                        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

                # Regular payment (not for new enrollment)
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
    def recover_payment(self, request):
        """
        Verify and recover a payment that may have failed during enrollment.
        This endpoint:
        1. Queries bKash to verify payment status
        2. If payment is complete, ensures enrollment is created
        3. Returns detailed status for the frontend to display
        
        Use cases:
        - Frontend execute call failed but payment may have succeeded
        - User refreshed page during payment processing
        - Network error after payment authorization
        """
        from apps.payments.services.payment_recovery import PaymentRecoveryService
        
        payment_id = request.data.get('paymentID')
        if not payment_id:
            return Response(
                {"error": "Payment ID is required."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        logger.info(f"Payment recovery requested for: {payment_id}")
        
        result = PaymentRecoveryService.verify_and_recover_payment(
            payment_id=payment_id,
            user=request.user
        )
        
        if result['status'] == 'success':
            return Response({
                "status": "success",
                "message": result['message'],
                "transaction_id": result.get('transaction_id'),
                "enrollment": result.get('enrollment'),
                "recovery_action": result.get('recovery_action')
            })
        elif result['status'] == 'partial_success':
            return Response({
                "status": "partial_success",
                "message": result['message'],
                "transaction_id": result.get('transaction_id'),
                "recovery_action": result.get('recovery_action')
            }, status=status.HTTP_207_MULTI_STATUS)
        else:
            return Response({
                "status": "error",
                "message": result['message'],
                "recovery_action": result.get('recovery_action')
            }, status=status.HTTP_400_BAD_REQUEST)

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

        # For regular users, only show COMPLETED payments for their students
        if not user.is_staff:
            payments = Payment.objects.filter(
                invoice__enrollment__student__parent=user,
                status=Payment.COMPLETED  # Only show completed payments
            ).select_related(
                'invoice',
                'invoice__enrollment',
                'invoice__enrollment__student',
                'invoice__enrollment__batch',
                'invoice__enrollment__batch__course'
            ).order_by('-created_at')
        else:
            # For staff, show all COMPLETED payments (can add admin view for all statuses separately)
            payments = Payment.objects.filter(
                status=Payment.COMPLETED  # Only show completed payments
            ).select_related(
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
            # Check if payment has an invoice with enrollment before accessing student info
            if payment.invoice and payment.invoice.enrollment:
                payment_data['student_name'] = payment.invoice.enrollment.student.name
                payment_data['course_name'] = payment.invoice.enrollment.batch.course.name
                payment_data['batch_name'] = payment.invoice.enrollment.batch.name
                payment_data['month'] = payment.invoice.month.strftime('%B %Y')
            else:
                # Handle payments without enrollments (e.g., temporary invoices, failed payments)
                payment_data['student_name'] = 'No student (Temporary/Pending)'
                payment_data['course_name'] = 'N/A'
                payment_data['batch_name'] = 'N/A'
                payment_data['month'] = payment.invoice.month.strftime('%B %Y') if payment.invoice else 'N/A'

            result.append(payment_data)

        return Response(result)

    @action(detail=False, methods=['post'], permission_classes=[IsAdminUser])
    def create_manual_invoice(self, request):
        """
        Create a manual invoice (admin only)
        """
        serializer = ManualInvoiceCreateSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Get validated data
            enrollment_id = serializer.validated_data['enrollment'].id
            month = serializer.validated_data['month']
            amount = serializer.validated_data['amount']
            is_paid = serializer.validated_data['is_paid']
            coupon_id = serializer.validated_data.get('coupon')
            description = serializer.validated_data.get('description', 'Manual invoice created by admin')

            # Get the enrollment
            enrollment = get_object_or_404(Enrollment, id=enrollment_id)

            # Get coupon if specified
            coupon = None
            if coupon_id:
                coupon = get_object_or_404(Coupon, id=coupon_id)

            # Create the invoice
            invoice = Invoice.objects.create(
                enrollment=enrollment,
                month=month,
                amount=amount,
                is_paid=is_paid,
                coupon=coupon
            )

            # If marked as paid, create a manual payment record
            payment = None
            if is_paid:
                transaction_id = f"MANUAL-{timezone.now().strftime('%Y%m%d')}-{get_random_string(6).upper()}"
                payment = Payment.objects.create(
                    invoice=invoice,
                    transaction_id=transaction_id,
                    amount=amount,
                    payment_method='Manual',
                    status=Payment.COMPLETED,
                    payment_create_time=timezone.now(),
                    payment_execute_time=timezone.now()
                )

                # Add payment info to response
                payment_info = PaymentSerializer(payment).data
            else:
                payment_info = None

            # Log the activity
            log_activity(
                user=request.user,
                action_type='PAYMENT' if is_paid else 'FEE_MODIFICATION',
                invoice_id=invoice.id,
                student_id=enrollment.student.id,
                student_name=enrollment.student.name,
                enrollment_id=enrollment.id,
                course=enrollment.batch.course.name,
                batch=enrollment.batch.name,
                month=month.strftime('%B %Y'),
                amount=str(amount),
                is_paid=is_paid,
                payment_id=payment.id if payment else None,
                description=description
            )

            response_data = {
                "invoice": InvoiceSerializer(invoice).data,
                "payment": payment_info,
                "message": "Manual invoice created successfully"
            }

            return Response(response_data, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.error(f"Error creating manual invoice: {str(e)}")
            return Response({"error": f"Failed to create invoice: {str(e)}"},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'], permission_classes=[IsAdminUser])
    def reconcile_stale_payments(self, request):
        """
        Reconcile payments stuck in 'Initiated' status by querying bKash.
        This handles cases where:
        - Frontend callback failed (network error, user closed browser)
        - Webhook failed to deliver
        - Any other sync issues
        
        Should be run periodically (e.g., via cron job or manually by admin)
        """
        from datetime import timedelta
        
        # Get all payments that have been in 'Initiated' status for more than 5 minutes
        stale_threshold = timezone.now() - timedelta(minutes=5)
        # Payments older than 24 hours are likely expired (bKash tokens usually expire)
        expiry_threshold = timezone.now() - timedelta(hours=24)
        
        stale_payments = Payment.objects.filter(
            status=Payment.INITIATED,
            created_at__lt=stale_threshold
        )
        
        reconciled = []
        expired = []
        errors = []
        
        for payment in stale_payments:
            # If payment is very old, mark as expired/failed
            if payment.created_at < expiry_threshold:
                payment.status = Payment.FAILED
                payment.save()
                
                # Also ensure the temp invoice doesn't block the user
                if payment.invoice and payment.invoice.temp_invoice:
                    payment.invoice.delete()  # Clean up temp invoices
                    
                expired.append({
                    "payment_id": payment.payment_id,
                    "reason": "Expired (older than 24 hours)"
                })
                logger.info(f"Marked stale payment {payment.payment_id} as expired")
                continue
            
            # Try to query bKash for the actual status
            try:
                if not payment.payment_id:
                    continue
                    
                query_response = bkash_client.query_payment(payment.payment_id)
                
                if query_response.get("statusCode") == "0000":
                    transaction_status = query_response.get('transactionStatus')
                    
                    if transaction_status == "Completed":
                        # Payment was actually completed! Update our records
                        payment.status = Payment.COMPLETED
                        payment.transaction_id = query_response.get('trxID', payment.transaction_id)
                        payment.payment_execute_time = timezone.now()
                        payment.save()
                        
                        # Mark invoice as paid
                        invoice = payment.invoice
                        if invoice:
                            invoice.is_paid = True
                            invoice.save()
                        
                        reconciled.append({
                            "payment_id": payment.payment_id,
                            "transaction_id": payment.transaction_id,
                            "status": "Completed",
                            "action": "Marked as paid"
                        })
                        logger.info(f"Reconciled payment {payment.payment_id} - was actually completed")
                        
                    elif transaction_status in ["Failed", "Cancelled"]:
                        payment.status = Payment.FAILED if transaction_status == "Failed" else Payment.CANCELLED
                        payment.save()
                        
                        reconciled.append({
                            "payment_id": payment.payment_id,
                            "status": transaction_status,
                            "action": f"Marked as {transaction_status}"
                        })
                        logger.info(f"Reconciled payment {payment.payment_id} - was {transaction_status}")
                        
                else:
                    errors.append({
                        "payment_id": payment.payment_id,
                        "error": query_response.get('statusMessage', 'Unknown error')
                    })
                    
            except Exception as e:
                errors.append({
                    "payment_id": payment.payment_id,
                    "error": str(e)
                })
                logger.error(f"Error reconciling payment {payment.payment_id}: {str(e)}")
        
        return Response({
            "message": "Reconciliation complete",
            "summary": {
                "total_checked": stale_payments.count(),
                "reconciled": len(reconciled),
                "expired": len(expired),
                "errors": len(errors)
            },
            "details": {
                "reconciled": reconciled,
                "expired": expired,
                "errors": errors
            }
        })


@method_decorator(csrf_exempt, name='dispatch')
class BkashWebhookView(APIView):
    """Handle real-time payment notifications from bKash"""
    permission_classes = []  # No auth required for webhooks

    def verify_signature(self, request):
        """Verify webhook signature using bKash's signing certificate"""
        try:
            # Get signature from headers
            signature = request.headers.get('x-bkash-signature')
            if not signature:
                return False

            # Get the raw request body
            body = request.body.decode('utf-8')

            # Create HMAC SHA256 hash using your bKash app secret
            expected_signature = base64.b64encode(
                hmac.new(
                    settings.BKASH_APP_SECRET.encode('utf-8'),
                    body.encode('utf-8'),
                    hashlib.sha256
                ).digest()
            ).decode('utf-8')

            # Compare signatures
            return hmac.compare_digest(signature, expected_signature)

        except Exception as e:
            logger.error(f"Error verifying webhook signature: {str(e)}")
            return False

    def process_completed_payment(self, payload):
        """Process a completed payment notification"""
        try:
            payment_id = payload.get('paymentID')
            merchant_invoice_number = payload.get('merchantInvoiceNumber')
            transaction_status = payload.get('transactionStatus')
            trx_id = payload.get('trxID')

            if not payment_id or transaction_status != 'Completed':
                logger.error(f"Invalid webhook payload: {payload}")
                return False

            # Find the payment in our system
            try:
                payment = Payment.objects.get(payment_id=payment_id)
            except Payment.DoesNotExist:
                logger.error(f"Payment not found for ID: {payment_id}")
                return False

            # If payment is already completed, skip processing
            if payment.status == Payment.COMPLETED:
                logger.info(f"Payment {payment_id} is already completed")
                return True

            # Update payment status
            payment.status = Payment.COMPLETED
            payment.payment_execute_time = timezone.now()
            payment.transaction_id = trx_id
            payment.save()

            # Update invoice
            invoice = payment.invoice
            invoice.is_paid = True
            invoice.save()

            # Check if this is a bulk payment (multi-invoice payment)
            if invoice.temp_invoice and invoice.temp_invoice_data and invoice.temp_invoice_data.get('type') == 'multi_invoice_payment':
                try:
                    with transaction.atomic():
                        invoice_ids = invoice.temp_invoice_data.get('invoice_ids', [])

                        # Process each invoice in the bulk payment
                        for inv_id in invoice_ids:
                            try:
                                individual_invoice = Invoice.objects.get(id=inv_id)
                                individual_invoice.is_paid = True
                                individual_invoice.save()

                                # Create a derived but unique transaction ID for each individual invoice
                                # Format: original_trx_id-invoice_id
                                unique_transaction_id = f"{trx_id}-{individual_invoice.id}"

                                # Create individual payment record linked to this invoice
                                individual_payment = Payment.objects.create(
                                    invoice=individual_invoice,
                                    transaction_id=unique_transaction_id,  # Use derived unique transaction ID
                                    amount=individual_invoice.amount,
                                    payment_method='bKash',
                                    status=Payment.COMPLETED,
                                    payment_id=payment_id,  # Reference the same bKash payment
                                    payer_reference=payment.payer_reference,
                                    payment_create_time=payment.payment_create_time,
                                    payment_execute_time=payment.payment_execute_time
                                )

                                logger.info(f"Webhook: Marked invoice #{inv_id} as paid and created payment record for bulk payment {payment_id}")
                            except Invoice.DoesNotExist:
                                logger.error(f"Webhook: Invoice #{inv_id} not found in bulk payment {payment_id}")

                        # Delete the temporary invoice and its payment as they're no longer needed
                        temp_invoice_id = invoice.id
                        payment.delete()
                        invoice.delete()

                        logger.info(f"Webhook: Temporary invoice #{temp_invoice_id} and its payment deleted after distributing payments")
                        return True
                except Exception as e:
                    logger.error(f"Webhook: Error processing bulk payment: {str(e)}")
                    # Don't return False here, the payment was still recorded
            # If this is an enrollment payment (no enrollment yet), complete the enrollment
            elif not invoice.enrollment and invoice.temp_invoice:
                try:
                    # Get enrollment data from temporary invoice
                    enrollment_data = invoice.temp_invoice_data
                    if enrollment_data:
                        viewset = EnrollmentViewSet()
                        # Complete the enrollment
                        response = viewset.verify_and_complete_payment(
                            request=None,  # Not needed for internal call
                            data={
                                'bkash_payment_id': payment_id,
                                'enrollment_data': enrollment_data,
                                'temp_invoice_id': invoice.id
                            }
                        )
                        logger.info(f"Enrollment completed via webhook for payment {payment_id}")
                        return True
                except Exception as e:
                    logger.error(f"Error completing enrollment via webhook: {str(e)}")
                    # Don't return False here, the payment was still successful

            logger.info(f"Successfully processed webhook for payment {payment_id}")
            return True

        except Exception as e:
            logger.error(f"Error processing webhook: {str(e)}")
            return False

    def post(self, request):
        """Handle POST notifications from bKash"""
        # First verify the webhook signature
        if not self.verify_signature(request):
            logger.error("Invalid webhook signature")
            return Response({"error": "Invalid signature"}, status=status.HTTP_403_FORBIDDEN)

        try:
            # Parse the notification payload
            payload = json.loads(request.body)

            # Log the webhook payload for debugging
            logger.info(f"Received bKash webhook: {payload}")

            # Process based on notification type
            notification_type = payload.get('Type')

            if notification_type == 'SubscriptionConfirmation':
                # Handle subscription confirmation
                subscribe_url = payload.get('SubscribeURL')
                if subscribe_url:
                    # You would typically make a GET request to this URL to confirm
                    logger.info(f"Webhook subscription URL: {subscribe_url}")
                    return Response({"status": "Subscription noted"})

            elif notification_type == 'Notification':
                # Handle payment notification
                message = json.loads(payload.get('Message', '{}'))
                if self.process_completed_payment(message):
                    return Response({"status": "Processed"})
                else:
                    return Response(
                        {"error": "Failed to process payment"},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR
                    )

            return Response({"error": "Unknown notification type"}, status=status.HTTP_400_BAD_REQUEST)

        except json.JSONDecodeError:
            logger.error("Invalid JSON in webhook payload")
            return Response({"error": "Invalid JSON"}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error(f"Error processing webhook: {str(e)}")
            return Response(
                {"error": "Internal server error"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


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
