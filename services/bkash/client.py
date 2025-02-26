from django.conf import settings
import requests
import logging
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class BkashClient:
    """
    Client for interacting with bKash Checkout URL API
    """

    def __init__(self):
        self.base_url = settings.BKASH_BASE_URL
        self.app_key = settings.BKASH_APP_KEY
        self.app_secret = settings.BKASH_APP_SECRET
        self.username = settings.BKASH_USERNAME
        self.password = settings.BKASH_PASSWORD
        self.token = None
        self.token_expiration = None
        self.refresh_token = None
        self.timeout = 30  # 30 seconds timeout as recommended by bKash

    def _ensure_token(self):
        """Ensure a valid token is available. Get a new one if it doesn't exist or is expired."""
        current_time = datetime.now()

        # Check if token exists and is not about to expire (margin of 5 minutes)
        if (not self.token or not self.token_expiration or
                self.token_expiration <= current_time + timedelta(minutes=5)):
            self._get_token()

    def _get_token(self):
        """Get a new token using the Grant Token API."""
        url = f"{self.base_url}/tokenized/checkout/token/grant"

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "username": self.username,
            "password": self.password,
        }

        data = {
            "app_key": self.app_key,
            "app_secret": self.app_secret
        }

        try:
            response = requests.post(url, json=data, headers=headers, timeout=self.timeout)
            response.raise_for_status()

            response_data = response.json()
            self.token = response_data.get("id_token")
            self.refresh_token = response_data.get("refresh_token")

            # Calculate token expiration time (default is 3600 seconds)
            expires_in = response_data.get("expires_in", 3600)
            self.token_expiration = datetime.now() + timedelta(seconds=expires_in)

            logger.info("Successfully obtained bKash token")

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to get bKash token: {str(e)}")
            if hasattr(e, 'response') and e.response:
                logger.error(f"Response: {e.response.text}")
            raise

    def _refresh_token(self):
        """Refresh the token using the Refresh Token API."""
        if not self.refresh_token:
            logger.warning("No refresh token available, getting new token instead")
            return self._get_token()

        url = f"{self.base_url}/tokenized/checkout/token/refresh"

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "username": self.username,
            "password": self.password,
        }

        data = {
            "app_key": self.app_key,
            "app_secret": self.app_secret,
            "refresh_token": self.refresh_token
        }

        try:
            response = requests.post(url, json=data, headers=headers, timeout=self.timeout)
            response.raise_for_status()

            response_data = response.json()
            self.token = response_data.get("id_token")
            self.refresh_token = response_data.get("refresh_token")

            # Calculate token expiration time
            expires_in = response_data.get("expires_in", 3600)
            self.token_expiration = datetime.now() + timedelta(seconds=expires_in)

            logger.info("Successfully refreshed bKash token")

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to refresh bKash token: {str(e)}")
            if hasattr(e, 'response') and e.response:
                logger.error(f"Response: {e.response.text}")
            # If refresh fails, try to get a new token
            self._get_token()

    def create_payment(self, amount, invoice_number, customer_phone, callback_url):
        """
        Create a payment using bKash Create Payment API

        Args:
            amount (str): Amount to pay
            invoice_number (str): Merchant's invoice number
            customer_phone (str): Customer's phone number
            callback_url (str): Base URL for callbacks

        Returns:
            dict: Response data containing paymentID and bkashURL
        """
        self._ensure_token()

        url = f"{self.base_url}/tokenized/checkout/create"

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": self.token,
            "X-App-Key": self.app_key
        }

        data = {
            "mode": "0011",  # Checkout URL mode
            "payerReference": customer_phone,
            "callbackURL": callback_url,
            "amount": str(amount),
            "currency": "BDT",
            "intent": "sale",
            "merchantInvoiceNumber": invoice_number
        }

        try:
            response = requests.post(url, json=data, headers=headers, timeout=self.timeout)
            response.raise_for_status()

            response_data = response.json()
            if response_data.get("statusCode") == "0000":  # Success code
                logger.info(f"Successfully created bKash payment for invoice {invoice_number}")
                return response_data
            else:
                logger.error(f"bKash payment creation failed with status: {response_data.get('statusCode')} - {response_data.get('statusMessage')}")
                return response_data

        except requests.exceptions.RequestException as e:
            logger.error(f"Error creating bKash payment: {str(e)}")
            if hasattr(e, 'response') and e.response:
                logger.error(f"Response: {e.response.text}")
            raise

    def execute_payment(self, payment_id):
        """
        Execute a payment after user authorization

        Args:
            payment_id (str): Payment ID from create payment response

        Returns:
            dict: Response data with transaction details
        """
        self._ensure_token()

        url = f"{self.base_url}/tokenized/checkout/execute"

        headers = {
            "Accept": "application/json",
            "Authorization": self.token,
            "X-App-Key": self.app_key
        }

        data = {
            "paymentID": payment_id
        }

        try:
            response = requests.post(url, json=data, headers=headers, timeout=self.timeout)
            response.raise_for_status()

            response_data = response.json()
            if response_data.get("statusCode") == "0000":  # Success code
                logger.info(f"Successfully executed bKash payment {payment_id}")
            else:
                logger.error(f"bKash payment execution failed with status: {response_data.get('statusCode')} - {response_data.get('statusMessage')}")

            return response_data

        except requests.exceptions.RequestException as e:
            logger.error(f"Error executing bKash payment: {str(e)}")
            if hasattr(e, 'response') and e.response:
                logger.error(f"Response: {e.response.text}")
            raise

    def query_payment(self, payment_id):
        """
        Query payment status

        Args:
            payment_id (str): Payment ID to query

        Returns:
            dict: Response data with payment status
        """
        self._ensure_token()

        url = f"{self.base_url}/tokenized/checkout/payment/status"

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": self.token,
            "X-App-Key": self.app_key
        }

        data = {
            "paymentID": payment_id
        }

        try:
            response = requests.post(url, json=data, headers=headers, timeout=self.timeout)
            response.raise_for_status()

            response_data = response.json()
            if response_data.get("statusCode") == "0000":  # Success code
                logger.info(f"Successfully queried bKash payment {payment_id}")
            else:
                logger.error(f"bKash payment query failed with status: {response_data.get('statusCode')} - {response_data.get('statusMessage')}")

            return response_data

        except requests.exceptions.RequestException as e:
            logger.error(f"Error querying bKash payment: {str(e)}")
            if hasattr(e, 'response') and e.response:
                logger.error(f"Response: {e.response.text}")
            raise
