"""
Payment Recovery Service

This service handles all payment recovery scenarios:
1. Payments that succeeded but enrollment failed
2. Orphaned temporary invoices
3. Payments stuck in pending state
4. Inconsistent payment/invoice states
"""

import logging
from datetime import timedelta
from decimal import Decimal
from django.utils import timezone
from django.db import transaction
from django.db.models import Q

from apps.payments.models import Invoice, Payment
from apps.enrollments.models import Enrollment, Coupon
from apps.enrollments.api.serializers import EnrollmentSerializer
from services.bkash import bkash_client
from apps.common.utils import log_activity

logger = logging.getLogger(__name__)


class PaymentRecoveryService:
    """
    Service class for handling payment recovery and consistency checks.
    """
    
    @staticmethod
    def verify_and_recover_payment(payment_id: str, user=None) -> dict:
        """
        Verify a payment with bKash and attempt to recover enrollment if needed.
        This is the main recovery method that should be called when:
        - Frontend reports an error after payment
        - Admin needs to manually recover a payment
        - Background job detects an inconsistency
        
        Returns:
            dict with status, message, and relevant data
        """
        logger.info(f"Starting payment recovery for payment_id: {payment_id}")
        
        result = {
            "status": "error",
            "message": "",
            "payment": None,
            "enrollment": None,
            "transaction_id": None,
            "recovery_action": None
        }
        
        try:
            # Step 1: Check if payment exists in our database
            try:
                payment = Payment.objects.select_related('invoice').get(payment_id=payment_id)
                result["payment"] = {
                    "id": payment.id,
                    "status": payment.status,
                    "transaction_id": payment.transaction_id
                }
                result["transaction_id"] = payment.transaction_id
                
                # If payment is already completed and has a valid enrollment, return success
                if payment.status == Payment.COMPLETED:
                    if payment.invoice and payment.invoice.enrollment:
                        result["status"] = "success"
                        result["message"] = "Payment and enrollment are already complete"
                        result["enrollment"] = {
                            "id": payment.invoice.enrollment.id,
                            "student_name": payment.invoice.enrollment.student.name,
                            "course_name": payment.invoice.enrollment.batch.course.name,
                            "batch_name": payment.invoice.enrollment.batch.name
                        }
                        result["recovery_action"] = "none_needed"
                        return result
                    else:
                        # Payment complete but no enrollment - needs recovery
                        logger.warning(f"Payment {payment_id} is complete but has no enrollment - attempting recovery")
                        
            except Payment.DoesNotExist:
                logger.info(f"Payment {payment_id} not found in database")
                payment = None
        
            # Step 2: Query bKash to get the actual payment status
            logger.info(f"Querying bKash for payment status: {payment_id}")
            query_response = bkash_client.query_payment(payment_id)
            
            bkash_status = query_response.get("transactionStatus")
            bkash_trx_id = query_response.get("trxID")
            
            logger.info(f"bKash query result: status={bkash_status}, trxID={bkash_trx_id}")
            
            if bkash_status != "Completed":
                # Payment not completed at bKash - try to execute it
                if bkash_status in ["Initiated", "Authorized"]:
                    logger.info(f"Payment {payment_id} is in {bkash_status} state - attempting execution")
                    execute_response = bkash_client.execute_payment(payment_id)
                    
                    if execute_response.get("statusCode") == "0000":
                        bkash_status = execute_response.get("transactionStatus")
                        bkash_trx_id = execute_response.get("trxID")
                        logger.info(f"Payment executed successfully: {bkash_trx_id}")
                    else:
                        result["message"] = f"Payment execution failed: {execute_response.get('statusMessage', 'Unknown error')}"
                        result["recovery_action"] = "execution_failed"
                        return result
                else:
                    result["message"] = f"Payment is in non-recoverable state: {bkash_status}"
                    result["recovery_action"] = "unrecoverable_state"
                    return result
            
            # Step 3: Payment is confirmed - ensure our records are updated
            if payment:
                if payment.status != Payment.COMPLETED:
                    payment.status = Payment.COMPLETED
                    payment.transaction_id = bkash_trx_id
                    payment.payment_execute_time = timezone.now()
                    payment.save()
                    logger.info(f"Updated payment {payment.id} to COMPLETED")
                
                # Ensure invoice is marked as paid
                if payment.invoice and not payment.invoice.is_paid:
                    payment.invoice.is_paid = True
                    payment.invoice.save()
                    logger.info(f"Marked invoice {payment.invoice.id} as paid")
            
            # Step 4: Check if enrollment needs to be created
            invoice = payment.invoice if payment else None
            
            if invoice and invoice.temp_invoice and invoice.temp_invoice_data and not invoice.enrollment:
                # This is a new enrollment payment - create the enrollment
                logger.info(f"Creating enrollment from temp invoice data")
                
                enrollment_result = PaymentRecoveryService._create_enrollment_from_temp_invoice(
                    invoice, payment, user
                )
                
                if enrollment_result["success"]:
                    result["status"] = "success"
                    result["message"] = "Payment recovered and enrollment created successfully"
                    result["enrollment"] = enrollment_result["enrollment"]
                    result["recovery_action"] = "enrollment_created"
                else:
                    result["status"] = "partial_success"
                    result["message"] = f"Payment confirmed but enrollment failed: {enrollment_result['error']}"
                    result["recovery_action"] = "enrollment_failed"
                    
            elif invoice and invoice.enrollment:
                result["status"] = "success"
                result["message"] = "Payment and enrollment verified successfully"
                result["enrollment"] = {
                    "id": invoice.enrollment.id,
                    "student_name": invoice.enrollment.student.name,
                    "course_name": invoice.enrollment.batch.course.name,
                    "batch_name": invoice.enrollment.batch.name
                }
                result["recovery_action"] = "already_complete"
            else:
                # Regular invoice payment (not enrollment)
                result["status"] = "success"
                result["message"] = "Payment verified and invoice marked as paid"
                result["recovery_action"] = "invoice_paid"
            
            return result
            
        except Exception as e:
            logger.error(f"Error in payment recovery: {str(e)}", exc_info=True)
            result["message"] = f"Recovery failed with error: {str(e)}"
            result["recovery_action"] = "exception"
            return result
    
    @staticmethod
    def _create_enrollment_from_temp_invoice(invoice, payment, user=None) -> dict:
        """
        Create an enrollment from a temporary invoice's stored data.
        This is the core enrollment creation logic that should be atomic.
        """
        from datetime import date
        
        result = {"success": False, "enrollment": None, "error": None}
        
        try:
            with transaction.atomic():
                enrollment_data = invoice.temp_invoice_data
                
                student_id = enrollment_data.get('student')
                batch_id = enrollment_data.get('batch')
                
                # Check for existing enrollment
                existing = Enrollment.objects.filter(
                    student_id=student_id,
                    batch_id=batch_id,
                    is_active=True
                ).first()
                
                if existing:
                    # Link payment to existing enrollment
                    first_invoice = Invoice.objects.filter(
                        enrollment=existing,
                        month=existing.start_month
                    ).first()
                    
                    if first_invoice:
                        payment.invoice = first_invoice
                        payment.save()
                    
                    # Delete temp invoice
                    invoice.delete()
                    
                    result["success"] = True
                    result["enrollment"] = {
                        "id": existing.id,
                        "student_name": existing.student.name,
                        "course_name": existing.batch.course.name,
                        "batch_name": existing.batch.name
                    }
                    return result
                
                # Create new enrollment
                serializer = EnrollmentSerializer(data=enrollment_data)
                if not serializer.is_valid():
                    result["error"] = f"Validation error: {serializer.errors}"
                    return result
                
                enrollment = serializer.save()
                batch = enrollment.batch
                course = batch.course
                
                # Calculate fees
                tuition_fee = enrollment.tuition_fee or batch.tuition_fee or course.monthly_fee
                first_month_str = enrollment_data.get('start_month')
                first_month = date.fromisoformat(first_month_str)
                
                coupon_code = enrollment_data.get('coupon_code')
                coupon = None
                first_month_waiver = enrollment_data.get('first_month_waiver', False)
                first_month_fee = Decimal('0.00') if first_month_waiver else Decimal(str(tuition_fee))
                
                if coupon_code:
                    try:
                        coupon = Coupon.objects.get(code__iexact=coupon_code)
                        if coupon.first_month_discount > 0 and not first_month_waiver:
                            first_month_fee = max(Decimal('0.00'), first_month_fee - coupon.first_month_discount)
                            if first_month_fee == 0:
                                first_month_waiver = True
                    except Coupon.DoesNotExist:
                        pass
                
                # Create first month invoice
                first_month_invoice = Invoice.objects.create(
                    enrollment=enrollment,
                    month=first_month,
                    amount=first_month_fee,
                    is_paid=True,
                    coupon=coupon
                )
                
                # Create next month invoice
                next_month_year = first_month.year + (first_month.month // 12)
                next_month_month = (first_month.month % 12) + 1
                next_month = date(next_month_year, next_month_month, 1)
                next_month_fee = Decimal(str(tuition_fee)) if tuition_fee else Decimal('0.00')
                next_month_is_paid = first_month_waiver
                
                next_month_invoice = Invoice.objects.create(
                    enrollment=enrollment,
                    month=next_month,
                    amount=next_month_fee,
                    is_paid=next_month_is_paid,
                    coupon=coupon if next_month_is_paid else None
                )
                
                # Link payment to correct invoice
                payment.invoice = next_month_invoice if first_month_waiver else first_month_invoice
                payment.save()
                
                # Delete temp invoice
                invoice.delete()
                
                # Log activity
                if user:
                    log_activity(
                        user=user,
                        action_type='ENROLLMENT',
                        enrollment_id=enrollment.id,
                        student_id=enrollment.student.id,
                        student_name=enrollment.student.name,
                        course=course.name,
                        batch=batch.name,
                        start_month=first_month.strftime('%B %Y'),
                        tuition_fee=str(tuition_fee),
                        coupon_code=coupon_code,
                        has_first_month_waiver=first_month_waiver,
                        payment_id=payment.id,
                        transaction_id=payment.transaction_id,
                        payment_method="bKash",
                        is_recovery=True
                    )
                
                result["success"] = True
                result["enrollment"] = {
                    "id": enrollment.id,
                    "student_name": enrollment.student.name,
                    "course_name": course.name,
                    "batch_name": batch.name
                }
                
        except Exception as e:
            logger.error(f"Error creating enrollment: {str(e)}", exc_info=True)
            result["error"] = str(e)
        
        return result
    
    @staticmethod
    def cleanup_orphaned_temp_invoices(hours_old: int = 24) -> dict:
        """
        Clean up temporary invoices that were never completed.
        Should be run periodically via a management command or celery task.
        """
        cutoff = timezone.now() - timedelta(hours=hours_old)
        
        orphaned = Invoice.objects.filter(
            temp_invoice=True,
            enrollment__isnull=True,
            created_at__lt=cutoff
        )
        
        count = orphaned.count()
        
        # Check if any have completed payments before deleting
        for invoice in orphaned:
            payments = Payment.objects.filter(invoice=invoice, status=Payment.COMPLETED)
            if payments.exists():
                logger.warning(
                    f"Found orphaned temp invoice {invoice.id} with completed payment - "
                    f"requires manual review"
                )
                continue
            
            # Safe to delete - no completed payments
            invoice.delete()
        
        logger.info(f"Cleaned up {count} orphaned temporary invoices")
        
        return {
            "cleaned": count,
            "message": f"Cleaned up {count} orphaned temporary invoices older than {hours_old} hours"
        }
    
    @staticmethod
    def find_inconsistent_payments() -> list:
        """
        Find payments that are in an inconsistent state:
        - Completed payments with no enrollment (for enrollment payments)
        - Paid invoices with no payment record
        - Pending payments older than expected
        """
        issues = []
        
        # 1. Completed payments on temp invoices (enrollment should have been created)
        completed_temp = Payment.objects.filter(
            status=Payment.COMPLETED,
            invoice__temp_invoice=True,
            invoice__enrollment__isnull=True
        ).select_related('invoice')
        
        for payment in completed_temp:
            issues.append({
                "type": "completed_no_enrollment",
                "payment_id": payment.payment_id,
                "transaction_id": payment.transaction_id,
                "amount": str(payment.amount),
                "created_at": payment.created_at.isoformat(),
                "severity": "high"
            })
        
        # 2. Pending payments older than 2 hours
        two_hours_ago = timezone.now() - timedelta(hours=2)
        stale_pending = Payment.objects.filter(
            status=Payment.PENDING,
            created_at__lt=two_hours_ago
        )
        
        for payment in stale_pending:
            issues.append({
                "type": "stale_pending",
                "payment_id": payment.payment_id,
                "amount": str(payment.amount),
                "created_at": payment.created_at.isoformat(),
                "severity": "medium"
            })
        
        return issues
    
    @staticmethod
    def auto_recover_all_inconsistent() -> dict:
        """
        Attempt to automatically recover all inconsistent payments.
        Returns a summary of recovery actions taken.
        """
        issues = PaymentRecoveryService.find_inconsistent_payments()
        
        results = {
            "total_issues": len(issues),
            "recovered": 0,
            "failed": 0,
            "details": []
        }
        
        for issue in issues:
            if issue["type"] == "completed_no_enrollment":
                recovery = PaymentRecoveryService.verify_and_recover_payment(issue["payment_id"])
                
                if recovery["status"] == "success":
                    results["recovered"] += 1
                else:
                    results["failed"] += 1
                
                results["details"].append({
                    "payment_id": issue["payment_id"],
                    "result": recovery
                })
        
        return results
