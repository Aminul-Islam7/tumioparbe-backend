from .client import BkashClient

# Create a singleton instance for use throughout the app
bkash_client = BkashClient()

__all__ = ['bkash_client']
