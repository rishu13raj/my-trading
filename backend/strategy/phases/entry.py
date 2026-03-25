"""
strategy/phases/entry.py — Phase 3: Portfolio Gate and Order Placement

PURPOSE
───────
By the time a ConfirmedSignal reaches this phase, the OFI signal has been
checked for quality (Phase 1 — Scanner) and confirmed by actual price movement
(Phase 2 — Incubator).  Entry's job is purely operational — three gates and
then an order:

  Gate 1 — Is trading paused for today? (user-triggered via /trading/pause)
  Gate 2 — Is this symbol within its post-exit cooldown window?
  Gate 3 — Does the portfolio have capacity for another trade?
  Then  — Place the order and return OpenPosition.

WHY THESE GATES ARE HERE AND NOT IN THE INCUBATOR
───────────────────────────────────────────────────
Cooldown and portfolio capacity are OPERATIONAL constraints, not signal quality
constraints.  They have nothing to do with whether the OFI signal is good.
Mixing them into the Incubator would make the Incubator responsible for both
"is this signal trustworthy?" AND "can we actually trade right now?" — two
separate concerns.

This separation also makes testing easier: you can test the Incubator by
feeding it ticks and checking what it confirms, without needing to mock a
portfolio or database state.

THE COOLDOWN — WHY 60 SECONDS?
────────────────────────────────
After a position closes, we don't want to immediately re-enter the same stock.
The market state that caused the exit (stop loss, momentum reversal) is still
in effect.  Entering immediately is chasing a move that already burned us.

REAL EXAMPLE — LODHA CASCADE (2026-03-25)
  Trade 123: BUY @ 758.75 (09:32) → STOP_LOSS @ 751.15 (09:34, 2 min) -₹995
  Trade 124: BUY @ 751.85 (09:35) → MOMENTUM_REVERSAL @ 750.50 (09:37) -₹179
  Trade 125: BUY @ 749.30 (09:38) → MOMENTUM_REVERSAL @ 752.90 (10:13) +₹478
  Total first two: -₹1,174 in 6 minutes on the same falling stock.

Without cooldown: 3 entries in 6 minutes on a declining stock.  The LODHA
iceberg was actively absorbing buyers all morning — every new BUY entry walked
straight into the hidden seller.

With 60-second cooldown:
  Trade 123 exits at 09:34 → cooldown until 09:35 → Trade 124 still fires
  at 09:35 (just after cooldown expired).  So the 60s cooldown isn't long
  enough to block Trade 124 in this specific case.

However, the INCUBATOR (Phase 2) would have blocked Trade 124 too: price was
still falling from 751.85 toward 750.5 at 09:35 (it had only been 1 minute
since the 09:34 stop).  The incubation's tick-confirmation check would see
falling prices → no confirming ticks → abandoned.

So the cooldown and incubation work TOGETHER: cooldown prevents the very
fastest re-entries, incubation catches the rest.

WHY 60s SPECIFICALLY (NOT 120s, NOT 30s)
  - 30s: too short, doesn't give the market time to settle after a stop
  - 60s: matches ENTRY_WAIT_SECONDS (the OFI consistency window); by the time
    cooldown expires, we've had a full new monitoring window of fresh ticks
  - 120s: too conservative; if a stock genuinely reverses and shows a strong
    new signal, we'd miss it.  PATANJALI often had valid re-entries within
    2 minutes on 2026-03-25.
  If re-entry losses are still high after incubation is running, consider
  increasing to 90s or 120s.

OPEN_FLIP vs OPEN — TWO ENTRY PATHS
──────────────────────────────────────
open()       : Normal confirmed entry.  All three gates apply.
open_flip()  : Momentum flip reverse entry.  Gate 2 (cooldown) is bypassed.

Bypassing cooldown for flips is intentional:
  A MOMENTUM_FLIP means we just CLOSED a position because momentum reversed.
  The very act of closing confirmed the reversal.  We immediately want to
  enter in the new direction — waiting 60s would miss the entire flip move.
  The flip was itself an exit signal; entering the reverse direction is a new
  ENTRY driven by confirmed momentum, not a re-entry chasing a failed trade.

  Gates 1 and 3 still apply to flips:
    - Trading paused: user explicitly stopped trading → flip should not override
    - Portfolio capacity: we won't open if already at max trades

STATE
──────
_last_exit_time  : dict[symbol → datetime]
  Reset on each close (record_exit() called by pipeline._execute_exit()).
  Drives the cooldown check.
  NOT persisted to DB — lost on restart.  This is acceptable: on restart
  there's a gap in monitoring anyway, so any cooldown from before the restart
  is irrelevant.
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

    # Don't re-enter a symbol within 60s of its last exit.
    # See module docstring for rationale and trade-off analysis.
    COOLDOWN_SECONDS = 60

    def __init__(self):
        self._last_exit_time: Dict[str, datetime] = {}

    # ── public API ────────────────────────────────────────────────────────────

    def open(self, confirmed: ConfirmedSignal, current_price: float) -> Optional[OpenPosition]:
        """
        Attempt to open a position for a confirmed signal.

        All three gates (trading paused, cooldown, portfolio capacity) are
        checked before placing the order.

        Returns OpenPosition on success.
        Returns None if any gate blocks the entry (with a log line per gate).
        """
        symbol = confirmed.symbol

        # Gate 1: Trading paused for today
        # User sets this via /trading/pause when they want to stop entries
        # without stopping monitoring (exits still run).
        if db.get_trading_paused():
            log_event("signal", f"{symbol} entry blocked: trading paused for today", symbol=symbol)
            return None

        # Gate 2: Per-symbol cooldown
        # See module docstring for rationale (LODHA cascade example).
        if self._in_cooldown(symbol):
            remaining = self.COOLDOWN_SECONDS - self._elapsed_since_exit(symbol)
            log_event(
                "signal",
                f"{symbol} entry blocked: cooldown {remaining:.0f}s remaining "
                f"(last exit {self._elapsed_since_exit(symbol):.0f}s ago, "
                f"cooldown = {self.COOLDOWN_SECONDS}s)",
                symbol=symbol,
            )
            return None

        # Gate 3: Portfolio capacity
        # MAX_ACTIVE_TRADES = 2 by default.  Having too many simultaneous open
        # trades amplifies losses in correlated moves (e.g., all stocks selling
        # off together) and fragments attention during manual monitoring.
        status = order_manager.get_portfolio_status()
        if not status["can_open_new"]:
            log_event(
                "signal",
                f"{symbol} entry blocked: portfolio at capacity "
                f"({status['active_trades']}/{config.MAX_ACTIVE_TRADES} active trades)",
                symbol=symbol,
            )
            return None

        return self._place_order(confirmed, current_price, is_flip=False)

    def open_flip(self, confirmed: ConfirmedSignal, current_price: float) -> Optional[OpenPosition]:
        """
        Open a momentum-flip reverse trade.  Cooldown is bypassed — see module
        docstring for why this is intentional.

        Gates 1 (trading paused) and 3 (portfolio capacity) still apply.
        """
        symbol = confirmed.symbol

        if db.get_trading_paused():
            log_event("signal", f"{symbol} flip blocked: trading paused for today", symbol=symbol)
            return None

        status = order_manager.get_portfolio_status()
        if not status["can_open_new"]:
            log_event(
                "signal",
                f"{symbol} flip blocked: portfolio at capacity "
                f"({status['active_trades']}/{config.MAX_ACTIVE_TRADES} active trades)",
                symbol=symbol,
            )
            return None

        return self._place_order(confirmed, current_price, is_flip=True)

    def record_exit(self, symbol: str) -> None:
        """
        Record that a position in this symbol just closed.  Starts the cooldown
        clock.  Must be called by pipeline._execute_exit() after every close
        (including flips, manual exits, and stops).

        For flip exits: record_exit() is called but cooldown is immediately
        bypassed by the subsequent open_flip() call.  So recording it is
        technically harmless but keeps state consistent for future entries
        after the flip position closes.
        """
        self._last_exit_time[symbol] = datetime.now()

    # ── private ───────────────────────────────────────────────────────────────

    def _place_order(
        self, confirmed: ConfirmedSignal, current_price: float, is_flip: bool
    ) -> Optional[OpenPosition]:
        """
        Construct and submit the order.  Shared by open() and open_flip().

        Passes confidence=1.0 to order_manager.process_signal() because the
        signal has already been confirmed upstream — the confidence threshold
        gate in should_place_order() (>= 0.6) is redundant here, but we pass
        1.0 to make the intent clear rather than hacking around it.
        """
        symbol = confirmed.symbol
        qty = portfolio.calculate_quantity(current_price)
        if qty <= 0:
            # This should not happen in normal operation (capital > price), but
            # if CAPITAL_PER_TRADE is very low or price is very high, floor()
            # can give 0.  Log and skip rather than crashing.
            log_event(
                "signal",
                f"{symbol} entry blocked: qty=0 at ₹{current_price} "
                f"(CAPITAL_PER_TRADE=₹{config.CAPITAL_PER_TRADE})",
                symbol=symbol,
            )
            return None

        signal_dict = {
            "action": confirmed.action,
            "confidence": 1.0,  # already confirmed by Incubator
        }
        trade_id = order_manager.process_signal(
            symbol,
            signal_dict,
            current_price,
            signal_meta=confirmed.signal_meta,
            is_flip=is_flip,
        )
        if not trade_id:
            # process_signal() returns None if paper_trader.place_order() failed.
            # In practice this rarely happens in paper trading mode.
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
        """
        Seconds since the last exit for this symbol.
        Returns infinity if no exit has been recorded (first entry of the day).
        """
        if symbol not in self._last_exit_time:
            return float("inf")
        return (datetime.now() - self._last_exit_time[symbol]).total_seconds()
