import os
from dotenv import load_dotenv

# Load .env from project root (one level up from backend/)
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

class Config:
    # Zerodha API
    API_KEY = os.getenv("API_KEY")
    API_SECRET = os.getenv("API_SECRET")
    ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")

    # Trading Configuration
    PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"
    CAPITAL_PER_TRADE = int(os.getenv("CAPITAL_PER_TRADE", 50000))
    MAX_ACTIVE_TRADES = int(os.getenv("MAX_ACTIVE_TRADES", 2))
    MAX_WATCHED_STOCKS = int(os.getenv("MAX_WATCHED_STOCKS", 10))
    DAILY_CAPITAL = int(os.getenv("DAILY_CAPITAL", 100000))
    STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", 0.05))
    TIME_EXIT_MINUTES = int(os.getenv("TIME_EXIT_MINUTES", 60))

    # Bid-Ask Strategy
    BID_ASK_THRESHOLD_RATIO = float(os.getenv("BID_ASK_THRESHOLD_RATIO", 2.0))
    MOMENTUM_WINDOW_SECONDS = int(os.getenv("MOMENTUM_WINDOW_SECONDS", 60))
    MOMENTUM_REVERSAL_THRESHOLD = float(os.getenv("MOMENTUM_REVERSAL_THRESHOLD", 0.3))
    ENTRY_WAIT_SECONDS = int(os.getenv("ENTRY_WAIT_SECONDS", 60))

    # Database
    DATABASE_PATH = os.getenv("DATABASE_PATH", "trading.db")

    # API Server
    SERVER_HOST = os.getenv("SERVER_HOST", "127.0.0.1")
    SERVER_PORT = int(os.getenv("SERVER_PORT", 8000))

    @classmethod
    def validate(cls):
        """Validate that required configs are set"""
        if not cls.API_KEY:
            print("⚠️  API_KEY not set in .env")
        if not cls.API_SECRET:
            print("⚠️  API_SECRET not set in .env (needed for auth token generation)")
        print(f"✓ Config loaded: Paper Trading={'ON' if cls.PAPER_TRADING else 'OFF'}")
        print(f"✓ Capital per trade: ₹{cls.CAPITAL_PER_TRADE}")
        print(f"✓ Max active trades: {cls.MAX_ACTIVE_TRADES}")
        print(f"✓ Stop loss: {cls.STOP_LOSS_PCT*100}%")

# Create config instance
config = Config()
