"""
Pydantic models shared across the app and SQLAlchemy ORM models for persistence.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy import Column, Integer, Float, String, DateTime, Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase


# ── SQLAlchemy base ───────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


# ── Enums ─────────────────────────────────────────────────────────────────────

class Direction(str, enum.Enum):
    LONG = "LONG"       # bought expecting rise
    SHORT = "SHORT"     # sold short expecting fall


class TradeStatus(str, enum.Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    STOPPED = "STOPPED"   # closed by stop-loss


# ── ORM models ────────────────────────────────────────────────────────────────

class TradeRecord(Base):
    """Persisted record of every completed trade."""
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, nullable=False)
    direction = Column(SAEnum(Direction), nullable=False)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)
    quantity = Column(Integer, nullable=False)
    entry_obi = Column(Float, nullable=False)        # OBI at entry
    exit_obi = Column(Float, nullable=True)
    pnl = Column(Float, nullable=True)               # realised P&L
    status = Column(SAEnum(TradeStatus), default=TradeStatus.OPEN)
    entry_time = Column(DateTime, default=datetime.utcnow)
    exit_time = Column(DateTime, nullable=True)
    notes = Column(String, nullable=True)            # e.g. "stop-loss hit"


class DailyStats(Base):
    """Aggregated daily P&L and trade count, one row per trading day."""
    __tablename__ = "daily_stats"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(String, nullable=False, unique=True)  # "YYYY-MM-DD"
    total_pnl = Column(Float, default=0.0)
    trade_count = Column(Integer, default=0)
    winning_trades = Column(Integer, default=0)
    losing_trades = Column(Integer, default=0)


# ── Pydantic schemas (API layer) ──────────────────────────────────────────────

class TickData(BaseModel):
    """Real-time market snapshot for a single symbol."""
    symbol: str
    ltp: float                       # last traded price
    bid_price: float
    bid_qty: int
    ask_price: float
    ask_qty: int
    total_buy_qty: int               # depth level-1 totals from Kite
    total_sell_qty: int
    obi: float                       # computed Order Book Imbalance
    obi_rate: float                  # dOBI/dt (per tick)
    volume: int
    timestamp: datetime


class TradeSignal(BaseModel):
    symbol: str
    direction: Direction
    obi: float
    ltp: float
    quantity: int
    reason: str


class TradeOut(BaseModel):
    id: int
    symbol: str
    direction: Direction
    entry_price: float
    exit_price: Optional[float]
    quantity: int
    pnl: Optional[float]
    status: TradeStatus
    entry_time: datetime
    exit_time: Optional[datetime]
    notes: Optional[str]

    class Config:
        from_attributes = True


class BasketItem(BaseModel):
    symbol: str     # e.g. "NSE:INFY"
    exchange: str = "NSE"


class SessionConfig(BaseModel):
    basket: list[BasketItem] = Field(..., min_length=1, max_length=5)
    capital_per_trade: float = Field(10_000.0, gt=0)
    per_trade_stop_loss: float = Field(400.0, gt=0)
    daily_stop_loss: float = Field(1_000.0, gt=0)
    obi_threshold: float = Field(0.30, gt=0, lt=1)
    observation_seconds: int = Field(60, ge=10)


class AppStatus(BaseModel):
    trading_halted: bool
    daily_pnl: float
    daily_trades: int
    active_positions: int
    message: str


# ── WebSocket broadcast payload ───────────────────────────────────────────────

class WSMessage(BaseModel):
    type: str           # "tick" | "trade_opened" | "trade_closed" | "status" | "error"
    payload: dict
