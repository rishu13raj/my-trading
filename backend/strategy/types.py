"""
Pipeline data types — the "envelopes" passed between phases.

Each phase receives one type and produces the next:

  Scanner      → CandidateSignal
  Incubator    → ConfirmedSignal
  Entry        → OpenPosition
  Monitor      → ExitDecision
  (pipeline)   → executes ExitDecision via paper_trader
"""

from dataclasses import dataclass, field
from typing import Optional, Dict


@dataclass
class CandidateSignal:
    """
    A raw signal that has passed the OFI threshold and signal-quality filter.
    Not yet confirmed by price movement — lives in the Incubator until confirmed
    or abandoned.
    """
    symbol: str
    action: str           # 'BUY' or 'SELL'
    trigger_price: float  # LTP at the moment the signal fired
    ratio: float          # dominant-side ratio (always >= 1)
    bid_qty: int
    ask_qty: int
    signal_meta: Dict = field(default_factory=dict)  # ML features captured at signal time


@dataclass
class ConfirmedSignal:
    """
    A signal that survived the Incubator: price moved in the signal direction
    for at least INCUBATION_MIN_TICKS ticks AND >= INCUBATION_PRICE_MOVE_PCT.
    Ready for order placement.
    """
    symbol: str
    action: str
    entry_price: float    # LTP at confirmation (slightly different from trigger_price)
    ratio: float
    bid_qty: int
    ask_qty: int
    signal_meta: Dict = field(default_factory=dict)


@dataclass
class OpenPosition:
    """Returned by Entry after successfully opening a trade."""
    trade_id: int
    symbol: str
    direction: str
    entry_price: float
    qty: int
    stop_loss: float


@dataclass
class ExitDecision:
    """
    Returned by Monitor when an open position should be closed.
    reason='MOMENTUM_FLIP' is a special case: the pipeline will also
    immediately open a reverse trade after executing the close.
    """
    trade_id: int
    symbol: str
    exit_price: float
    reason: str
    direction: str        # original trade direction — used for logging and flip logic
