"""
Central configuration for the Zerodha Momentum Trader.
All sensitive values are read from a .env file (never committed to git).
"""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # ── Zerodha credentials ───────────────────────────────────────────────────
    KITE_API_KEY: str = Field(..., description="Zerodha API key from developer console")
    KITE_API_SECRET: str = Field(..., description="Zerodha API secret")
    # Access token is generated fresh every session via the login flow.
    # The frontend will POST it after the user completes Zerodha login.
    KITE_ACCESS_TOKEN: str = Field("", description="Session access token (set at runtime)")

    # ── Strategy thresholds ───────────────────────────────────────────────────
    # Minimum OBI value to even consider a trade  (-1 to +1 scale)
    OBI_ENTRY_THRESHOLD: float = Field(0.30, description="Min absolute OBI to trigger observation")
    # OBI must be sustained above threshold for this many consecutive ticks
    OBI_SUSTAIN_TICKS: int = Field(6, description="Consecutive ticks OBI must hold above threshold")
    # Observation window (seconds) before committing to a trade after Start is pressed
    OBSERVATION_SECONDS: int = Field(60, description="How long to watch before entry")

    # ── Position / risk ───────────────────────────────────────────────────────
    CAPITAL_PER_TRADE: float = Field(10_000.0, description="₹ allocated per individual trade")
    DAILY_PROFIT_TARGET: float = Field(1_000.0, description="₹ daily profit target")
    PER_TRADE_STOP_LOSS: float = Field(400.0, description="₹ max loss per trade before auto-exit")
    DAILY_STOP_LOSS: float = Field(1_000.0, description="₹ total daily loss before halting all trading")

    # ── Data polling ──────────────────────────────────────────────────────────
    # Interval for REST-based quote polling when WebSocket is unavailable
    POLL_INTERVAL_SECONDS: float = Field(2.0, description="Seconds between quote polls (REST fallback)")
    # How many OBI data-points to keep for rate-of-change calculation
    OBI_WINDOW_SIZE: int = Field(20, description="Rolling window size for OBI history")

    # ── App ───────────────────────────────────────────────────────────────────
    DATABASE_URL: str = Field("sqlite+aiosqlite:///./trading.db", description="SQLite DB path")
    LOG_LEVEL: str = Field("INFO", description="Logging level")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
