from kiteconnect import KiteConnect
from config import config
import os

class ZerodhaAuth:
    """Handle Zerodha KitConnect authentication"""

    def __init__(self):
        self.api_key = config.API_KEY
        self.api_secret = config.API_SECRET
        self.access_token = config.ACCESS_TOKEN
        self.kite = None

        # Auto-initialize kite if token already exists (e.g. after restart)
        if self.access_token and self.api_key:
            self.kite = KiteConnect(api_key=self.api_key)
            self.kite.set_access_token(self.access_token)

    def generate_login_url(self) -> str:
        """
        Generate Zerodha login URL

        Returns:
            Login URL for user to authenticate
        """
        kite = KiteConnect(api_key=self.api_key)
        login_url = kite.login_url()
        return login_url

    def generate_access_token(self, request_token: str) -> str:
        """
        Exchange request token for access token

        Args:
            request_token: Request token from login URL callback

        Returns:
            Access token (also updates .env)
        """
        try:
            kite = KiteConnect(api_key=self.api_key)
            response = kite.generate_session(request_token, api_secret=self.api_secret)

            access_token = response['access_token']
            self.access_token = access_token

            # Initialize kite client immediately
            self.kite = KiteConnect(api_key=self.api_key)
            self.kite.set_access_token(access_token)

            # Update .env file
            self._update_env('ACCESS_TOKEN', access_token)

            print(f"✓ Access token generated and saved to .env")
            return access_token

        except Exception as e:
            print(f"✗ Error generating access token: {e}")
            return None

    def get_kite_client(self) -> KiteConnect:
        """
        Get authenticated KitConnect client

        Returns:
            KitConnect instance (authenticated)
        """
        if self.kite is not None:
            return self.kite

        if not self.access_token:
            print("✗ No access token available. Run authentication first.")
            return None

        try:
            kite = KiteConnect(api_key=self.api_key)
            kite.set_access_token(self.access_token)

            # Test connection
            profile = kite.profile()
            print(f"✓ Connected to Zerodha. User: {profile.get('user_id')}")

            self.kite = kite
            return kite

        except Exception as e:
            print(f"✗ Error connecting to Zerodha: {e}")
            return None

    def _update_env(self, key: str, value: str):
        """
        Update .env file with new value

        Args:
            key: Configuration key
            value: Configuration value
        """
        env_path = os.path.join(os.path.dirname(__file__), '..', '..', '.env')

        if not os.path.exists(env_path):
            with open(env_path, 'w') as f:
                f.write(f"{key}={value}\n")
            return

        # Read existing content
        with open(env_path, 'r') as f:
            lines = f.readlines()

        # Update or add the key
        updated = False
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = f"{key}={value}\n"
                updated = True
                break

        if not updated:
            lines.append(f"{key}={value}\n")

        # Write back
        with open(env_path, 'w') as f:
            f.writelines(lines)

    def is_authenticated(self) -> bool:
        """Check if authenticated"""
        return self.access_token is not None and self.kite is not None

# Global auth instance
auth = ZerodhaAuth()
