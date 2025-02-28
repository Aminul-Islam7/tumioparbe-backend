import requests
import logging
import json
from django.conf import settings
from typing import Union, List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class GreenwebSMSClient:
    """Client for interacting with Greenweb SMS API"""

    def __init__(self, token=None, use_json=True):
        """
        Initialize the SMS client

        Args:
            token (str, optional): API token for Greenweb. Defaults to settings.GREENWEB_API_TOKEN.
            use_json (bool, optional): Whether to use JSON response format. Defaults to True.
        """
        self.token = token or settings.GREENWEB_API_TOKEN
        self.use_json = use_json
        self.base_url = "http://api.greenweb.com.bd/api.php"
        if use_json:
            self.base_url += "?json"

    def send_sms(self, to: Union[str, List[str]], message: str, message_type: str = 'CUSTOM', user=None) -> Dict[str, Any]:
        """
        Send SMS to one or more recipients

        Args:
            to (str or list): Single phone number or list of phone numbers
            message (str): Message content
            message_type (str): Type of message (OTP, PAYMENT_REMINDER, etc.)
            user: The user who is sending the message

        Returns:
            dict: Response details including status
        """
        # Import here to avoid circular imports
        from apps.common.models import SMSLog

        is_bulk = isinstance(to, list)
        recipient_count = len(to) if is_bulk else 1
        primary_recipient = to[0] if is_bulk else to

        # Create an initial log entry
        sms_log = SMSLog.objects.create(
            phone_number=primary_recipient[:20],  # Truncate if needed
            message=message,
            message_type=message_type,
            status=SMSLog.PENDING,
            sent_by=user,
            recipient_count=recipient_count
        )

        if not settings.SMS_ENABLED:
            logger.info(f"SMS is disabled. Would have sent message to {to}: {message}")
            sms_log.status = SMSLog.DISABLED
            sms_log.save()
            return {
                "success": True,
                "status": "DISABLED",
                "message": "SMS service is disabled",
                "log_id": sms_log.id
            }

        # Format phone numbers if list
        if is_bulk:
            to = ",".join(to)

        # Ensure correct Bangladesh number format
        to = self._format_phone_numbers(to)

        payload = {
            'token': self.token,
            'to': to,
            'message': message
        }

        if self.use_json:
            payload['json'] = '1'

        try:
            response = requests.post(self.base_url, data=payload, timeout=10)

            # Validate response
            if response.status_code != 200:
                logger.error(f"SMS API request failed: Status {response.status_code}, Response: {response.text}")
                sms_log.status = SMSLog.FAILED
                sms_log.api_response = {"error": response.text, "status_code": response.status_code}
                sms_log.save()
                return {
                    "success": False,
                    "status": "ERROR",
                    "message": f"API request failed: {response.text}",
                    "log_id": sms_log.id
                }

            # Process response and update log
            result = self._process_response(response.text, to)

            # Update SMS log with the result
            sms_log.status = result["status"]
            sms_log.successful_count = result.get("sent", 0)
            sms_log.failed_count = result.get("failed", 0)
            sms_log.api_response = result.get("raw_response", {})
            sms_log.save()

            result["log_id"] = sms_log.id
            return result

        except Exception as e:
            logger.error(f"Error sending SMS: {str(e)}")
            sms_log.status = SMSLog.FAILED
            sms_log.api_response = {"error": str(e)}
            sms_log.save()
            return {
                "success": False,
                "status": "ERROR",
                "message": str(e),
                "log_id": sms_log.id
            }

    def _process_response(self, response_text: str, to: str) -> Dict[str, Any]:
        """Process API response and determine success/failure"""
        if self.use_json:
            try:
                results = json.loads(response_text)

                # Count successes and failures
                successful = 0
                failed = 0
                failures = []

                # Process each result based on Greenweb API response format
                for result in results:
                    # Message status will be in format "Message Sent Successfully"
                    # or will contain error details
                    msg_status = result.get('statusmsg', '').lower()
                    if 'sent successfully' in msg_status:
                        successful += 1
                    else:
                        failed += 1
                        failures.append(result.get('statusmsg', 'Unknown error'))

                status = "SUCCESS"
                if failed > 0:
                    status = "PARTIAL_SUCCESS" if successful > 0 else "FAILED"

                # Map to our model's status choices
                from apps.common.models import SMSLog
                status_map = {
                    "SUCCESS": SMSLog.SUCCESS,
                    "PARTIAL_SUCCESS": SMSLog.PARTIAL,
                    "FAILED": SMSLog.FAILED
                }

                return {
                    "success": failed == 0,
                    "status": status_map.get(status, SMSLog.FAILED),
                    "sent": successful,
                    "failed": failed,
                    "failures": failures,
                    "raw_response": results
                }

            except json.JSONDecodeError:
                logger.error(f"Failed to parse JSON response: {response_text}")
                return {"success": False, "status": "FAILED", "message": "Invalid JSON response"}
        else:
            # Process line-by-line text response (format: "Ok: Message sent to 880xxxxx")
            lines = response_text.strip().split('\n')
            successful = 0
            failed = 0
            failures = []

            for line in lines:
                if line.startswith('Ok:'):
                    successful += 1
                elif line.startswith('Error:'):
                    failed += 1
                    failures.append(line)

            status = "SUCCESS"
            if failed > 0:
                status = "PARTIAL_SUCCESS" if successful > 0 else "FAILED"

            # Map to our model's status choices
            from apps.common.models import SMSLog
            status_map = {
                "SUCCESS": SMSLog.SUCCESS,
                "PARTIAL_SUCCESS": SMSLog.PARTIAL,
                "FAILED": SMSLog.FAILED
            }

            return {
                "success": failed == 0,
                "status": status_map.get(status, SMSLog.FAILED),
                "sent": successful,
                "failed": failed,
                "failures": failures,
                "raw_response": response_text
            }

    def _format_phone_numbers(self, numbers: str) -> str:
        """
        Ensure phone numbers are in correct Bangladesh format

        Args:
            numbers (str): Comma-separated phone numbers

        Returns:
            str: Properly formatted phone numbers
        """
        formatted = []
        for num in numbers.split(','):
            num = num.strip()
            # If doesn't start with +880 or 880, add +880
            if num.startswith('+880'):
                formatted.append(num)
            elif num.startswith('880'):
                formatted.append(f"+{num}")
            elif num.startswith('0'):
                formatted.append(f"+88{num}")
            else:
                formatted.append(f"+880{num}")

        return ','.join(formatted)

    def check_balance(self) -> Dict[str, Any]:
        """Check SMS account balance"""
        url = f"http://api.greenweb.com.bd/g_api.php?token={self.token}&balance"
        if self.use_json:
            url += "&json"

        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                if self.use_json:
                    return json.loads(response.text)
                return {"balance": response.text.strip()}
            else:
                return {"error": f"Failed to check balance: {response.text}"}
        except Exception as e:
            logger.error(f"Error checking balance: {str(e)}")
            return {"error": str(e)}

    def get_sms_stats(self) -> Dict[str, Any]:
        """Get SMS usage statistics"""
        url = f"http://api.greenweb.com.bd/g_api.php?token={self.token}&totalsms&monthlysms"
        if self.use_json:
            url += "&json"

        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                if self.use_json:
                    return json.loads(response.text)
                return {"stats": response.text.strip()}
            else:
                return {"error": f"Failed to get SMS stats: {response.text}"}
        except Exception as e:
            logger.error(f"Error getting SMS stats: {str(e)}")
            return {"error": str(e)}


# Create a default client instance
sms_client = GreenwebSMSClient()


def send_otp(phone_number: str, otp: str) -> Dict[str, Any]:
    """
    Send OTP to a phone number using Greenweb SMS service

    Args:
        phone_number (str): The recipient's phone number
        otp (str): The OTP to send

    Returns:
        dict: Response details including success status
    """
    from apps.common.models import SMSLog

    # Message content
    message = f"(Tumio Parbe) Your OTP is {otp}. Valid for 5 minutes."

    # Send the message
    return sms_client.send_sms(phone_number, message, message_type=SMSLog.OTP)


def send_payment_reminder(phone_number: str, student_name: str, course_name: str,
                          month: str, amount: float, user=None) -> Dict[str, Any]:
    """
    Send payment reminder to a phone number

    Args:
        phone_number (str): The recipient's phone number
        student_name (str): Name of the student
        course_name (str): Name of the course
        month (str): Month for which payment is due
        amount (float): Amount due
        user: User sending the reminder

    Returns:
        dict: Response details including success status
    """
    from apps.common.models import SMSLog

    # Message content
    message = (f"Payment reminder for {student_name}'s {course_name} course. "
               f"Amount {amount} Tk for {month} is due. Please pay to avoid interruption.")

    # Send the message
    return sms_client.send_sms(phone_number, message, message_type=SMSLog.PAYMENT_REMINDER, user=user)


def send_enrollment_confirmation(phone_number: str, student_name: str, course_name: str,
                                 batch_name: str, user=None) -> Dict[str, Any]:
    """
    Send enrollment confirmation message

    Args:
        phone_number (str): The recipient's phone number
        student_name (str): Name of the student
        course_name (str): Name of the course
        batch_name (str): Name of the batch
        user: User sending the message

    Returns:
        dict: Response details including success status
    """
    from apps.common.models import SMSLog

    message = (f"Congratulations! {student_name} has been successfully enrolled in "
               f"{course_name} ({batch_name}). Welcome to TumioParbe!")

    return sms_client.send_sms(phone_number, message, message_type=SMSLog.ENROLLMENT_CONFIRMATION, user=user)


def send_bulk_message(phone_numbers: List[str], message: str, user=None) -> Dict[str, Any]:
    """
    Send the same message to multiple recipients

    Args:
        phone_numbers (list): List of phone numbers
        message (str): Message content
        user: User sending the message

    Returns:
        dict: Response details including success status
    """
    from apps.common.models import SMSLog

    return sms_client.send_sms(phone_numbers, message, message_type=SMSLog.BULK, user=user)


def send_custom_notification(phone_number: str, message: str, user=None) -> Dict[str, Any]:
    """
    Send a custom notification message

    Args:
        phone_number (str): The recipient's phone number
        message (str): Message content
        user: User sending the message

    Returns:
        dict: Response details including success status
    """
    from apps.common.models import SMSLog

    return sms_client.send_sms(phone_number, message, message_type=SMSLog.CUSTOM, user=user)
