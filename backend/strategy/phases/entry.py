"""
Phase 3 — Entry

Portfolio gate and order placement.

By the time a ConfirmedSignal reaches this phase the OFI signal has already
been checked for quality (Phase 1) and confirmed by price movement (Phase 2).
Entry's job is purely operational:

  1. Is trading paused for today?
  2. Is this symbol still within its post-exit cooldown?
  3. Is there portfolio capacity for one more trade?
  4. If all clear → open the position.

State owned here:
  _last_exit_time   — per-symbol timestamp of last position close, drives cooldown.

Callers:
  pipeline.py calls entry.open() for normal confirmed signals.
  pipeline.py calls entry.open_flip() for momentum-flip reverse trades
  (bypasses cooldown since a flip is intentional direction reversal).
"""

from datetime import datetime
from typing import Dict, Optional

from config import config
from core.logger import log_event
from data.database import db
from strategy.stop_loss import calculate_stop_loss
from strategy.types import ConfirmedSignal, OpenPosition
from trading.order_manager import order_manager
from trading.portfolio import portfolio


class EntryPhase:
    COOLDOWN_SECONDS = 60  # don't re-enter a symbol within 60 s of last exit

    def __init__(self):
        self._last_exit_time: Dict[str, datetime] = {}

    # ── public API ───────────────────────────────────────────────────────────

    def open(self, confirmed: ConfirmedSignal, current_price: float) -> Optional[OpenPosition]:
        """
        Attempt to open a position for a confirmed signal.
        Returns OpenPosition on success, None if any gate blocks it.
        """
        symbol = confirmed.symbol

        if db.get_trading_paused():
            log_event("signal", f"{symbol} entry blocked: trading paused for today", symbol=symbol)
            return None

        if self._in_cooldown(symbol):
            remaining = self.COOLDOWN_SECONDS - self._elapsed_since_exit(symbol)
            log_event("signal", f"{symbol} entry blocked: cooldown {remaining:.0f}s remaining", symbol=symbol)
            return None

        status = order_manager.get_portfolio_status()
        if not status["can_open_new"]:
            log_event(
                "signal",
                f"{symbol} entry blocked: portfolio at capacity "
                f"({status['active_trades']}/{config.MAX_ACTIVE_TRADES} trades)",
                symbol=symbol,
            )
            return None

        return self._place_order(confirmed, current_price, is_flip=False)

    def open_flip(self, confirmed: ConfirmedSignal, current_price: float) -> Optional[OpenPosition]:
        """
        Open a flip (momentum reversal) trade — bypasses cooldown check.
        All other gates (trading paused, portfolio capacity) still apply.
        """
        symbol = confirmed.symbol

        if db.get_trading_paused():
            log_event("signal", f"{symbol} flip blocked: trading paused for today", symbol=symbol)
            return None

        status = order_manager.get_portfolio_status()
        if not status["can_open_new"]:
            log_event("signal", f"{symbol} flip blocked: portfolio at capacity", symbol=symbol)
            return None

        return self._place_order(confirmed, current_price, is_flip=True)

    def record_exit(self, symbol: str) -> None:
        """Must be called whenever a position in `symbol` is closed."""
        self._last_exit_time[symbol] = datetime.now()

    # ── private ───────────────────────────────────────────────────────────────

    def _place_order(
        self, confirmed: ConfirmedSignal, current_price: float, is_flip: bool
    ) -> Optional[OpenPosition]:
        symbol = confirmed.symbol
        qty = portfolio.calculate_quantity(current_price)
        if qty <= 0:
            log_event("signal", f"{symbol} entry blocked: qty=0 at ₹{current_price}", symbol=symbol)
            return None

        signal_dict = {
            "action": confirmed.action,
            "confidence": 1.0,  # already confirmed upstream — bypass confidence gate
        }
        trade_id = order_manager.process_signal(
            symbol,
            signal_dict,
            current_price,
            signal_meta=confirmed.signal_meta,
            is_flip=is_flip,
        )
        if not trade_id:
            return None

        stop_loss = calculate_stop_loss(current_price, confirmed.action)
        label = "FLIP OPENED" if is_flip else "OPENED"
        log_event(
            "trade",
            f"{label} {confirmed.action} {symbol} @ ₹{current_price}  "
            f"qty={qty}  SL=₹{stop_loss}  ratio={confirmed.ratio:.2f}x  "
            f"(trade #{trade_id})",
            symbol=symbol,
        )
        return OpenPosition(
            trade_id=trade_id,
            symbol=symbol,
            direction=confirmed.action,
            entry_price=current_price,
            qty=qty,
            stop_loss=stop_loss,
        )

    def _in_cooldown(self, symbol: str) -> bool:
        return self._elapsed_since_exit(symbol) < self.COOLDOWN_SECONDS

    def _elapsed_since_exit(self, symbol: str) -> float:
        if symbol not in self._last_exit_time:
            return float("inf")
        return (datetime.now() - self._last_exit_time[symbol]).total_seconds()
