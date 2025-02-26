from rest_framework import viewsets, status, generics
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.crypto import get_random_string
from datetime import date
import calendar
from decimal import Decimal

from apps.enrollments.models import Enrollment, Coupon
from apps.enrollments.api.serializers import EnrollmentSerializer, CouponSerializer, EnrollmentInitiateSerializer
from apps.accounts.models import Student
from apps.courses.models import Batch, Course
from apps.payments.models import Invoice, Payment
from services.bkash import bkash_client

import logging

logger = logging.getLogger(__name__)


class EnrollmentViewSet(viewsets.ModelViewSet):
    """
    Viewset for handling enrollments
    """
    queryset = Enrollment.objects.all()
    serializer_class = EnrollmentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Filter enrollments based on user role"""
        user = self.request.user
        # For regular users, only show enrollments for their students
        if not user.is_staff:
            return Enrollment.objects.filter(student__parent=user)
        # For staff/admin users, show all enrollments
        return Enrollment.objects.all()

    @action(detail=False, methods=['post'])
    def initiate(self, request):
        """
        Initiate an enrollment - calculate fees, check eligibility, apply coupon
        Returns payment details to be processed by frontend
        """
        serializer = EnrollmentInitiateSerializer(data=request.data)
        if serializer.is_valid():
            # Get validated data
            student_id = serializer.validated_data['student']
            batch_id = serializer.validated_data['batch']
            start_month = serializer.validated_data['start_month']
            coupon_code = serializer.validated_data.get('coupon_code', '')

            # Fetch the student and batch
            student = get_object_or_404(Student, id=student_id)
            batch = get_object_or_404(Batch, id=batch_id)
            course = batch.course

            # Check if the student belongs to the requesting user (unless admin)
            if not request.user.is_staff and student.parent != request.user:
                return Response(
                    {"error": "You don't have permission to enroll this student"},
                    status=status.HTTP_403_FORBIDDEN
                )

            # Check if there's already an active enrollment for this student in this batch
            if Enrollment.objects.filter(student=student, batch=batch, is_active=True).exists():
                return Response(
                    {"error": "This student is already enrolled in this batch"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Calculate fees
            admission_fee = course.admission_fee
            tuition_fee = batch.tuition_fee or course.monthly_fee

            # Apply coupon if provided
            coupon = None
            if coupon_code:
                try:
                    coupon = Coupon.objects.get(code=coupon_code)

                    # Check if coupon is expired
                    if coupon.expires_at < date.today():
                        return Response(
                            {"error": "This coupon has expired"},
                            status=status.HTTP_400_BAD_REQUEST
                        )

                    # Apply coupon discounts
                    discount_types = coupon.discount_types

                    # Apply admission fee waiver if applicable
                    if 'ADMISSION' in discount_types:
                        admission_fee = Decimal('0.00')

                    # Apply first month fee waiver if applicable
                    first_month_waiver = False
                    if 'FIRST_MONTH' in discount_types:
                        tuition_fee = Decimal('0.00')
                        first_month_waiver = True

                    # Apply tuition discount if applicable
                    if 'TUITION' in discount_types and coupon.discount_value:
                        # Apply percentage discount to tuition fee
                        if not first_month_waiver:  # Only apply if first month isn't already waived
                            discount = (tuition_fee * coupon.discount_value) / 100
                            tuition_fee = tuition_fee - discount

                except Coupon.DoesNotExist:
                    return Response(
                        {"error": "Invalid coupon code"},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            # Calculate total amount to pay for enrollment
            total_amount = admission_fee + tuition_fee

            # Create a response with enrollment details
            response_data = {
                "student_id": student.id,
                "student_name": student.name,
                "batch_id": batch.id,
                "batch_name": batch.name,
                "course_name": course.name,
                "start_month": start_month,
                "admission_fee": admission_fee,
                "tuition_fee": tuition_fee,
                "total_amount": total_amount,
                "coupon_applied": bool(coupon),
                "payment_required": total_amount > 0,
                "enrollment_data": {
                    "student": student.id,
                    "batch": batch.id,
                    "start_month": start_month,
                    "tuition_fee": tuition_fee if tuition_fee > 0 else batch.tuition_fee or course.monthly_fee,
                    "coupon_code": coupon_code if coupon else None
                }
            }

            return Response(response_data, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'])
    def complete(self, request):
        """
        Complete an enrollment after successful payment
        """
        # This would be called after successful payment via bKash
        # The payment confirmation would be handled separately
        enrollment_data = request.data.get('enrollment_data')
        payment_data = request.data.get('payment_data', {})

        if not enrollment_data:
            return Response(
                {"error": "Enrollment data is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Create the enrollment
        serializer = self.get_serializer(data=enrollment_data)
        if serializer.is_valid():
            enrollment = serializer.save()

            # Get the batch and course details
            batch = enrollment.batch
            course = batch.course

            # Create invoice for the first month (marked as paid if first_month waiver was applied)
            first_month = enrollment.start_month

            # Check if a coupon was used
            coupon_code = enrollment_data.get('coupon_code')
            coupon = None
            first_month_waiver = False

            if coupon_code:
                try:
                    coupon = Coupon.objects.get(code=coupon_code)
                    if 'FIRST_MONTH' in coupon.discount_types:
                        first_month_waiver = True
                except Coupon.DoesNotExist:
                    pass

            # Create the first month invoice (which is included in the enrollment payment)
            Invoice.objects.create(
                enrollment=enrollment,
                month=first_month,
                amount=Decimal('0.00') if first_month_waiver else enrollment.tuition_fee,
                is_paid=True,  # First month is paid as part of enrollment
                coupon=coupon
            )

            # Calculate next month
            last_day = calendar.monthrange(first_month.year, first_month.month)[1]
            next_month = date(
                first_month.year + (first_month.month // 12),
                (first_month.month % 12) + 1,
                1
            )

            # Create next month's invoice (which will be due)
            Invoice.objects.create(
                enrollment=enrollment,
                month=next_month,
                amount=enrollment.tuition_fee,
                is_paid=False
            )

            return Response(serializer.data, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'])
    def initiate_payment(self, request):
        """
        Initiate a bKash payment for enrollment
        """
        # Get enrollment details from request
        enrollment_data = request.data.get('enrollment_data')
        callback_url = request.data.get('callback_url')
        customer_phone = request.data.get('customer_phone')

        if not enrollment_data or not callback_url or not customer_phone:
            return Response(
                {"error": "enrollment_data, callback_url, and customer_phone are required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Extract student and batch details
        student_id = enrollment_data.get('student')
        batch_id = enrollment_data.get('batch')
        start_month = enrollment_data.get('start_month')
        coupon_code = enrollment_data.get('coupon_code')

        if not student_id or not batch_id or not start_month:
            return Response(
                {"error": "Student, batch, and start_month are required in enrollment_data"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Fetch the student and batch
            student = get_object_or_404(Student, id=student_id)
            batch = get_object_or_404(Batch, id=batch_id)
            course = batch.course

            # Check if the student belongs to the requesting user (unless admin)
            if not request.user.is_staff and student.parent != request.user:
                return Response(
                    {"error": "You don't have permission to enroll this student"},
                    status=status.HTTP_403_FORBIDDEN
                )

            # Check if there's already an active enrollment for this student in this batch
            if Enrollment.objects.filter(student=student, batch=batch, is_active=True).exists():
                return Response(
                    {"error": "This student is already enrolled in this batch"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Calculate fees
            admission_fee = course.admission_fee
            tuition_fee = batch.tuition_fee or course.monthly_fee

            # Apply coupon if provided
            coupon = None
            if coupon_code:
                try:
                    coupon = Coupon.objects.get(code=coupon_code)
                    if coupon.expires_at < date.today():
                        return Response(
                            {"error": "This coupon has expired"},
                            status=status.HTTP_400_BAD_REQUEST
                        )

                    # Apply coupon discounts
                    discount_types = coupon.discount_types
                    if 'ADMISSION' in discount_types:
                        admission_fee = Decimal('0.00')

                    first_month_waiver = False
                    if 'FIRST_MONTH' in discount_types:
                        tuition_fee = Decimal('0.00')
                        first_month_waiver = True

                    if 'TUITION' in discount_types and coupon.discount_value:
                        if not first_month_waiver:
                            discount = (tuition_fee * coupon.discount_value) / 100
                            tuition_fee = tuition_fee - discount
                except Coupon.DoesNotExist:
                    return Response(
                        {"error": "Invalid coupon code"},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            # Calculate total amount
            total_amount = admission_fee + tuition_fee
            if total_amount <= 0:
                return Response(
                    {
                        "message": "No payment required. You can complete the enrollment directly.",
                        "total_amount": 0,
                        "payment_required": False,
                        "enrollment_data": enrollment_data
                    },
                    status=status.HTTP_200_OK
                )

            # Create a temporary invoice
            try:
                temp_invoice = Invoice.objects.create(
                    enrollment=None,
                    month=date.fromisoformat(start_month),
                    amount=total_amount,
                    is_paid=False,
                    coupon=coupon
                )

                # Generate merchant invoice number
                merchant_invoice_number = f"ENR-{student_id}-{batch_id}-{get_random_string(6).upper()}"

                # Call bKash API
                try:
                    payment_response = bkash_client.create_payment(
                        amount=str(total_amount),
                        invoice_number=merchant_invoice_number,
                        customer_phone=customer_phone,
                        callback_url=callback_url
                    )

                    if payment_response.get("statusCode") != "0000":
                        temp_invoice.delete()
                        return Response({
                            "error": "bKash payment initiation failed",
                            "status_code": payment_response.get("statusCode"),
                            "status_message": payment_response.get("statusMessage")
                        }, status=status.HTTP_400_BAD_REQUEST)

                    # Create payment record
                    payment = Payment.objects.create(
                        invoice=temp_invoice,
                        transaction_id=merchant_invoice_number,
                        amount=total_amount,
                        payment_method='bKash',
                        status=Payment.INITIATED,
                        payment_id=payment_response.get('paymentID'),
                        payer_reference=customer_phone,
                        payment_create_time=timezone.now()
                    )

                    return Response({
                        "payment_id": payment.id,
                        "temp_invoice_id": temp_invoice.id,
                        "bkash_payment_id": payment_response.get('paymentID'),
                        "bkash_url": payment_response.get('bkashURL'),
                        "total_amount": str(total_amount),
                        "callback_urls": {
                            "success": payment_response.get('successCallbackURL'),
                            "failure": payment_response.get('failureCallbackURL'),
                            "cancelled": payment_response.get('cancelledCallbackURL')
                        },
                        "enrollment_data": enrollment_data
                    })

                except Exception as e:
                    temp_invoice.delete()
                    logger.error(f"Error calling bKash API: {str(e)}")
                    if hasattr(e, 'response'):
                        logger.error(f"bKash API Response: {e.response.text}")
                    return Response({
                        "error": f"Failed to initiate bKash payment: {str(e)}"
                    }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            except Exception as e:
                logger.error(f"Error creating temporary invoice: {str(e)}")
                return Response({
                    "error": f"Failed to create temporary invoice: {str(e)}"
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        except Exception as e:
            logger.error(f"Error in initiate_payment: {str(e)}")
            return Response({
                "error": f"An error occurred while processing your request: {str(e)}"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'])
    def complete_with_payment(self, request):
        """
        Complete an enrollment after successful bKash payment
        """
        enrollment_data = request.data.get('enrollment_data')
        payment_id = request.data.get('bkash_payment_id')
        temp_invoice_id = request.data.get('temp_invoice_id')

        if not enrollment_data or not payment_id or not temp_invoice_id:
            return Response(
                {"error": "enrollment_data, bkash_payment_id, and temp_invoice_id are required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Verify the payment with bKash
        try:
            # Get the payment record
            try:
                payment = Payment.objects.get(payment_id=payment_id)
                temp_invoice = Invoice.objects.get(id=temp_invoice_id)
            except (Payment.DoesNotExist, Invoice.DoesNotExist):
                return Response(
                    {"error": "Invalid payment or temp invoice ID"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Call bKash API to execute payment
            execute_response = bkash_client.execute_payment(payment_id)

            if execute_response.get("statusCode") != "0000" or execute_response.get("transactionStatus") != "Completed":
                return Response({
                    "error": "Payment execution failed",
                    "status_code": execute_response.get("statusCode"),
                    "status_message": execute_response.get("statusMessage")
                }, status=status.HTTP_400_BAD_REQUEST)

            # Update payment record
            payment.status = Payment.COMPLETED
            payment.payment_execute_time = timezone.now()
            payment.transaction_id = execute_response.get('trxID', payment.transaction_id)
            payment.save()

        except Exception as e:
            logger.error(f"Error executing bKash payment: {str(e)}")
            return Response({"error": "Failed to verify payment with bKash."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # Create the enrollment
        serializer = self.get_serializer(data=enrollment_data)
        if serializer.is_valid():
            enrollment = serializer.save()

            # Get the batch and course details
            batch = enrollment.batch
            course = batch.course

            # Create invoice for the first month
            first_month = date.fromisoformat(enrollment_data.get('start_month'))

            # Check if a coupon was used
            coupon_code = enrollment_data.get('coupon_code')
            coupon = None
            first_month_waiver = False

            if coupon_code:
                try:
                    coupon = Coupon.objects.get(code=coupon_code)
                    if 'FIRST_MONTH' in coupon.discount_types:
                        first_month_waiver = True
                except Coupon.DoesNotExist:
                    pass

            # Create the first month invoice (which is included in the enrollment payment)
            first_month_invoice = Invoice.objects.create(
                enrollment=enrollment,
                month=first_month,
                amount=Decimal('0.00') if first_month_waiver else enrollment.tuition_fee,
                is_paid=True,  # First month is paid as part of enrollment
                coupon=coupon
            )

            # Associate the payment with the actual invoice and delete temporary invoice
            payment.invoice = first_month_invoice
            payment.save()
            temp_invoice.delete()

            # Calculate next month
            last_day = calendar.monthrange(first_month.year, first_month.month)[1]
            next_month = date(
                first_month.year + (first_month.month // 12),
                (first_month.month % 12) + 1,
                1
            )

            # Create next month's invoice (which will be due)
            next_month_invoice = Invoice.objects.create(
                enrollment=enrollment,
                month=next_month,
                amount=enrollment.tuition_fee,
                is_paid=False
            )

            return Response({
                "enrollment": serializer.data,
                "payment_status": "Completed",
                "transaction_id": payment.transaction_id,
                "payment_method": "bKash",
                "first_month_invoice_id": first_month_invoice.id,
                "next_month_invoice_id": next_month_invoice.id
            }, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class CouponViewSet(viewsets.ModelViewSet):
    """
    Viewset for managing coupons
    """
    queryset = Coupon.objects.all()
    serializer_class = CouponSerializer
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        """
        Only staff users can create, update or delete coupons
        Regular users can only view available coupons
        """
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            # Only staff can create or modify coupons
            return [IsAuthenticated()]  # Replace with proper staff permission
        return super().get_permissions()

    @action(detail=False, methods=['get'])
    def validate(self, request):
        """Validate a coupon code and return discount information"""
        code = request.query_params.get('code')
        if not code:
            return Response(
                {"error": "Coupon code is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            coupon = Coupon.objects.get(code=code)

            # Check if coupon is expired
            if coupon.expires_at < date.today():
                return Response(
                    {"error": "This coupon has expired"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            return Response(
                CouponSerializer(coupon).data,
                status=status.HTTP_200_OK
            )
        except Coupon.DoesNotExist:
            return Response(
                {"error": "Invalid coupon code"},
                status=status.HTTP_404_NOT_FOUND
            )
