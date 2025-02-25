import requests
import logging
from django.conf import settings

logger = logging.getLogger(__name__)


def send_otp(phone_number, otp):
    """
    Send OTP to a phone number using Greenweb SMS service

    Args:
        phone_number (str): The recipient's phone number
        otp (str): The OTP to send

    Returns:
        bool: True if successful, False otherwise
    """
    if not settings.SMS_ENABLED:
        logger.info(f"SMS is disabled. Would have sent OTP {otp} to {phone_number}")
        return True

    try:
        # Message content
        message = f"Your TumioParbe OTP is {otp}. Valid for 5 minutes."

        # Greenweb API endpoint
        url = "http://api.greenweb.com.bd/api.php"

        # Payload for the API request
        payload = {
            'token': settings.GREENWEB_API_TOKEN,
            'to': phone_number,
            'message': message
        }

        # Send the request
        response = requests.post(url, data=payload, timeout=10)

        # Check if the request was successful
        if response.status_code == 200:
            logger.info(f"Successfully sent OTP to {phone_number}")
            return True
        else:
            logger.error(f"Failed to send OTP to {phone_number}. Status code: {response.status_code}, Response: {response.text}")
            return False

    except Exception as e:
        logger.error(f"Error sending OTP to {phone_number}: {str(e)}")
        return False


def send_payment_reminder(phone_number, student_name, course_name, month, amount):
    """
    Send payment reminder to a phone number

    Args:
        phone_number (str): The recipient's phone number
        student_name (str): Name of the student
        course_name (str): Name of the course
        month (str): Month for which payment is due
        amount (float): Amount due

    Returns:
        bool: True if successful, False otherwise
    """
    if not settings.SMS_ENABLED:
        logger.info(f"SMS is disabled. Would have sent payment reminder to {phone_number}")
        return True

    try:
        # Message content
        message = (f"Payment reminder for {student_name}'s {course_name} course. "
                   f"Amount {amount} Tk for {month} is due. Please pay to avoid interruption.")

        # Greenweb API endpoint
        url = "http://api.greenweb.com.bd/api.php"

        # Payload for the API request
        payload = {
            'token': settings.GREENWEB_API_TOKEN,
            'to': phone_number,
            'message': message
        }

        # Send the request
        response = requests.post(url, data=payload, timeout=10)

        # Check if the request was successful
        if response.status_code == 200:
            logger.info(f"Successfully sent payment reminder to {phone_number}")
            return True
        else:
            logger.error(f"Failed to send payment reminder to {phone_number}. Status code: {response.status_code}, Response: {response.text}")
            return False

    except Exception as e:
        logger.error(f"Error sending payment reminder to {phone_number}: {str(e)}")
        return False
