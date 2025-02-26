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
        logger.info(f"BkashClient initialized with base_url: {self.base_url}")

    def debug_token(self):
        """Debug the token information and credentials"""
        logger.info(f"Token status: {'Valid' if self.token else 'None'}")
        logger.info(f"Token expiration: {self.token_expiration}")
        logger.info(f"Base URL: {self.base_url}")
        # Log masked credentials for debugging (never log full credentials)
        logger.info(f"App key: {self.app_key[:4]}...{self.app_key[-4:] if len(self.app_key) > 8 else ''}")
        logger.info(f"Username: {self.username}")
        return {
            "token_exists": bool(self.token),
            "expiration": self.token_expiration.isoformat() if self.token_expiration else None,
            "base_url": self.base_url
        }

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
            logger.info(f"Requesting new token from {url}")
            response = requests.post(url, json=data, headers=headers, timeout=self.timeout)

            # Log detailed response information for debugging
            logger.info(f"Token request status code: {response.status_code}")

            response.raise_for_status()

            response_data = response.json()

            # Verify we received an id_token
            self.token = response_data.get("id_token")
            if not self.token:
                logger.error(f"No id_token in response: {response_data}")
                raise ValueError("No token provided in bKash response")

            self.refresh_token = response_data.get("refresh_token")

            # Calculate token expiration time (default is 3600 seconds)
            expires_in = response_data.get("expires_in", 3600)
            self.token_expiration = datetime.now() + timedelta(seconds=expires_in)

            logger.info(f"Successfully obtained bKash token, expires in {expires_in} seconds")
            return True

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to get bKash token: {str(e)}")
            if hasattr(e, 'response') and e.response:
                logger.error(f"Response status code: {e.response.status_code}")
                logger.error(f"Response body: {e.response.text}")

            # Reset token information when request fails
            self.token = None
            self.token_expiration = None
            self.refresh_token = None

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
        try:
            self._ensure_token()

            url = f"{self.base_url}/tokenized/checkout/create"

            # Debug information
            logger.info(f"Creating payment for invoice {invoice_number} with amount {amount}")
            logger.info(f"Using token prefix: {self.token[:10]}... (token exists: {bool(self.token)})")

            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {self.token}",  # Fix: Add 'Bearer ' prefix
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

            logger.info(f"Sending request to {url}")
            response = requests.post(url, json=data, headers=headers, timeout=self.timeout)

            # Log response status
            logger.info(f"bKash create payment response status: {response.status_code}")

            if response.status_code == 401:
                # Handle unauthorized error specifically - token might have expired even with our checks
                logger.warning("Received 401 Unauthorized from bKash. Refreshing token and retrying...")
                # Force get new token
                self.token = None
                self._ensure_token()

                # Retry with new token
                headers["Authorization"] = f"Bearer {self.token}"  # Fix: Add 'Bearer ' prefix
                logger.info("Retrying with new token...")
                response = requests.post(url, json=data, headers=headers, timeout=self.timeout)
                logger.info(f"Retry response status: {response.status_code}")

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
                logger.error(f"Response status: {e.response.status_code}")
                logger.error(f"Response body: {e.response.text}")
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
            "Authorization": f"Bearer {self.token}",  # Fix: Add 'Bearer ' prefix
            "X-App-Key": self.app_key
        }

        data = {
            "paymentID": payment_id
        }

        try:
            response = requests.post(url, json=data, headers=headers, timeout=self.timeout)

            # Handle 401 errors with token refresh
            if response.status_code == 401:
                logger.warning("Received 401 Unauthorized. Refreshing token and retrying...")
                self.token = None
                self._ensure_token()
                headers["Authorization"] = f"Bearer {self.token}"
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
                logger.error(f"Response status: {e.response.status_code}")
                logger.error(f"Response body: {e.response.text}")
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
            "Authorization": f"Bearer {self.token}",  # Fix: Add 'Bearer ' prefix
            "X-App-Key": self.app_key
        }

        data = {
            "paymentID": payment_id
        }

        try:
            response = requests.post(url, json=data, headers=headers, timeout=self.timeout)

            # Handle 401 errors with token refresh
            if response.status_code == 401:
                logger.warning("Received 401 Unauthorized. Refreshing token and retrying...")
                self.token = None
                self._ensure_token()
                headers["Authorization"] = f"Bearer {self.token}"
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
                logger.error(f"Response status: {e.response.status_code}")
                logger.error(f"Response body: {e.response.text}")
            raise
