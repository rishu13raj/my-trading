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
    TIME_EXIT_MINUTES = int(os.getenv("TIME_EXIT_MINUTES", 120))      # exit losing trades after this long
    TRAIL_STOP_RETRACEMENT = float(os.getenv("TRAIL_STOP_RETRACEMENT", 0.25))  # exit winner if it retraces 25% of peak gain (locks 75% of peak)
    PROFIT_TRAIL_ACTIVATION_PCT = float(os.getenv("PROFIT_TRAIL_ACTIVATION_PCT", 0.005))  # activate early trail stop once profit >= 0.5% of entry

    # Bid-Ask Strategy
    BID_ASK_THRESHOLD_RATIO = float(os.getenv("BID_ASK_THRESHOLD_RATIO", 2.0))
    MOMENTUM_WINDOW_SECONDS = int(os.getenv("MOMENTUM_WINDOW_SECONDS", 60))
    MOMENTUM_REVERSAL_THRESHOLD = float(os.getenv("MOMENTUM_REVERSAL_THRESHOLD", 0.3))
    ENTRY_WAIT_SECONDS = int(os.getenv("ENTRY_WAIT_SECONDS", 60))

    # Momentum Flip (smart reversal + immediate reverse trade)
    FLIP_RATIO = float(os.getenv("FLIP_RATIO", 1.4))           # opposite imbalance threshold to confirm flip
    FLIP_CONFIRM_TICKS = int(os.getenv("FLIP_CONFIRM_TICKS", 4))   # consecutive ticks required
    FLIP_DELTA_WINDOW = int(os.getenv("FLIP_DELTA_WINDOW", 5))     # ticks to look back for delta slope
    FLIP_ABSORPTION_WINDOW = int(os.getenv("FLIP_ABSORPTION_WINDOW", 8))  # ticks for absorption check
    MIN_HOLD_BEFORE_FLIP = int(os.getenv("MIN_HOLD_BEFORE_FLIP", 60))     # seconds before flip allowed
    FLIP_MIN_ADVERSE_PCT = float(os.getenv("FLIP_MIN_ADVERSE_PCT", 0.003))  # min adverse move (0.3%) before flip considered

    # Signal Quality Filter (shared by scanner + monitoring)
    SQ_MIN_TOTAL_QUEUE        = int(os.getenv("SQ_MIN_TOTAL_QUEUE", 10000))    # minimum bid+ask total queue
    SQ_MIN_SIDE_QUEUE         = int(os.getenv("SQ_MIN_SIDE_QUEUE", 2000))      # minimum on the weaker side
    SQ_EXTREME_RATIO_THRESHOLD= float(os.getenv("SQ_EXTREME_RATIO_THRESHOLD", 8.0))   # ratios above this need deep book
    SQ_EXTREME_RATIO_MIN_DEPTH= int(os.getenv("SQ_EXTREME_RATIO_MIN_DEPTH", 25000))   # required depth when ratio is extreme
    SQ_BUILDUP_TICKS          = int(os.getenv("SQ_BUILDUP_TICKS", 3))          # min confirming ticks in window
    SQ_BUILDUP_WINDOW         = int(os.getenv("SQ_BUILDUP_WINDOW", 5))         # window to check for buildup

    # Incubation (signal confirmation gate)
    INCUBATION_MIN_TICKS         = int(os.getenv("INCUBATION_MIN_TICKS", 4))
    INCUBATION_PRICE_MOVE_PCT    = float(os.getenv("INCUBATION_PRICE_MOVE_PCT", 0.001))  # 0.1%
    INCUBATION_TIMEOUT_SECS      = int(os.getenv("INCUBATION_TIMEOUT_SECS", 120))
    INCUBATION_TREND_LOOKBACK_SECS = int(os.getenv("INCUBATION_TREND_LOOKBACK_SECS", 300))  # 5 min
    INCUBATION_TREND_BLOCK_PCT   = float(os.getenv("INCUBATION_TREND_BLOCK_PCT", 0.002))  # 0.2%

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
