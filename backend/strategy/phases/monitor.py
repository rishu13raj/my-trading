"""
Phase 4 — Monitor

Per-tick monitoring of all open positions for a symbol.

Responsibilities:
  1. Peak price tracking — maintains the best price seen since entry for each
     trade_id.  Used by exit_logic.should_exit() for PROFIT_TRAIL and
     TRAIL_STOP.  Lives in memory; lost on restart (tracked in TODO).

  2. Exit evaluation — calls exit_logic.should_exit() each tick and surfaces
     an ExitDecision when any exit condition fires.

  3. Momentum flip detection — calls reversal_detector.check_flip() after
     MIN_HOLD_BEFORE_FLIP seconds.  A flip exit is returned as an ExitDecision
     with reason='MOMENTUM_FLIP'; the pipeline handles the reverse-entry logic.

  4. Throttled position logging — logs a full position status line every
     POSITION_LOG_INTERVAL ticks so the operator can see live P&L without
     drowning the log.

State owned here:
  _peaks          — trade_id → best price seen since entry
  _flip_states    — trade_id → {cumulative_delta, consecutive_flip_ticks}
  _log_tick_count — symbol  → tick counter for throttled logging
"""

from datetime import datetime
from typing import Dict, Optional

from config import config
from core.logger import log_event
from data.database import db
from strategy.exit_logic import get_exit_analysis, should_exit
from strategy.reversal_detector import check_flip
from strategy.types import ExitDecision

POSITION_LOG_INTERVAL = 10  # log position status every N ticks


class Monitor:
    def __init__(self):
        self._peaks: Dict[int, float] = {}
        self._flip_states: Dict[int, dict] = {}
        self._log_tick_count: Dict[str, int] = {}

    # ── public API ───────────────────────────────────────────────────────────

    def evaluate(
        self,
        symbol: str,
        current_price: float,
        tick_history: list,
    ) -> Optional[ExitDecision]:
        """
        Evaluate all open positions for `symbol` on this tick.

        Returns the first ExitDecision that should be acted on, or None if
        all positions should be held.  Exits are checked before flips so that
        stop-loss / PROFIT_TRAIL always takes priority.
        """
        active = [t for t in db.get_active_trades() if t["symbol"] == symbol]

        if not active:
            self._log_tick_count.pop(symbol, None)
            return None

        # Throttled status log
        self._log_tick_count[symbol] = self._log_tick_count.get(symbol, 0) + 1
        if self._log_tick_count[symbol] % POSITION_LOG_INTERVAL == 1:
            self._log_positions(active, symbol, current_price, tick_history)

        for pos in active:
            trade_id = pos["id"]

            # 1. Update peak price
            self._update_peak(trade_id, pos["direction"], current_price)

            # 2. Check exit conditions (stop, trail, time, momentum)
            peak = self._peaks.get(trade_id)
            exit_flag, reason = should_exit(pos, tick_history, current_price, peak_price=peak)
            if exit_flag:
                return ExitDecision(
                    trade_id=trade_id,
                    symbol=symbol,
                    exit_price=current_price,
                    reason=reason,
                    direction=pos["direction"],
                )

            # 3. Check momentum flip (after minimum hold time)
            flip = self._check_flip(pos, tick_history, current_price)
            if flip:
                return flip

        return None

    def get_peak(self, trade_id: int) -> Optional[float]:
        return self._peaks.get(trade_id)

    def clear_position(self, trade_id: int) -> None:
        """Free memory after a position is closed."""
        self._peaks.pop(trade_id, None)
        self._flip_states.pop(trade_id, None)

    # ── private ───────────────────────────────────────────────────────────────

    def _update_peak(self, trade_id: int, direction: str, current_price: float) -> None:
        if trade_id not in self._peaks:
            self._peaks[trade_id] = current_price
        elif direction == "BUY":
            self._peaks[trade_id] = max(self._peaks[trade_id], current_price)
        else:
            self._peaks[trade_id] = min(self._peaks[trade_id], current_price)

    def _check_flip(
        self, pos: dict, tick_history: list, current_price: float
    ) -> Optional[ExitDecision]:
        trade_id = pos["id"]

        # Enforce minimum hold before flip is evaluated
        et = pos["entry_time"]
        if isinstance(et, (int, float)):
            entry_dt = datetime.fromtimestamp(et)
        elif isinstance(et, str):
            entry_dt = datetime.fromisoformat(et)
        else:
            entry_dt = et

        held_seconds = (datetime.now() - entry_dt).total_seconds()
        if held_seconds < config.MIN_HOLD_BEFORE_FLIP:
            log_event(
                "signal",
                f"{pos['symbol']} [holding {pos['direction']}] flip check skipped "
                f"({held_seconds:.0f}s < {config.MIN_HOLD_BEFORE_FLIP}s min hold)",
                symbol=pos["symbol"],
            )
            return None

        if trade_id not in self._flip_states:
            self._flip_states[trade_id] = {
                "cumulative_delta": 0.0,
                "consecutive_flip_ticks": 0,
            }

        reverse_direction = check_flip(pos, tick_history, self._flip_states[trade_id])
        if not reverse_direction:
            flip_state = self._flip_states[trade_id]
            log_event(
                "signal",
                f"{pos['symbol']} [holding {pos['direction']}] "
                f"flip_ticks={flip_state.get('consecutive_flip_ticks', 0)}  "
                f"cum_delta={flip_state.get('cumulative_delta', 0):.0f}",
                symbol=pos["symbol"],
            )
            return None

        self._flip_states.pop(trade_id, None)
        return ExitDecision(
            trade_id=trade_id,
            symbol=pos["symbol"],
            exit_price=current_price,
            reason="MOMENTUM_FLIP",
            direction=pos["direction"],
        )

    def _log_positions(
        self, positions: list, symbol: str, current_price: float, tick_history: list
    ) -> None:
        for pos in positions:
            qty = pos["entry_qty"]
            entry = pos["entry_price"]
            pnl = (
                (current_price - entry) * qty
                if pos["direction"] == "BUY"
                else (entry - current_price) * qty
            )
            analysis = get_exit_analysis(pos, tick_history, current_price)
            held_min = analysis["duration_minutes"]
            sl = pos["stop_loss_price"]
            sl_dist = round(abs(current_price - sl), 2)
            peak = self._peaks.get(pos["id"])
            peak_str = f"  peak=₹{peak:.2f}" if peak else ""

            momentum_flag = (
                "⚠️ momentum reversing" if analysis["momentum_slowing"] else "momentum OK"
            )
            time_flag = (
                f"⚠️ time limit ({held_min:.0f}m)"
                if analysis["time_exceeded"]
                else f"held {held_min:.0f}m/{config.TIME_EXIT_MINUTES}m"
            )
            pnl_str = f"+₹{pnl:.2f}" if pnl >= 0 else f"-₹{abs(pnl):.2f}"

            log_event(
                "signal",
                f"POSITION {pos['direction']} {symbol}  qty={qty}  "
                f"entry=₹{entry}  now=₹{current_price}{peak_str}  "
                f"P&L={pnl_str}  SL=₹{sl}(dist={sl_dist})  "
                f"{time_flag}  {momentum_flag}  "
                f"→ {'EXIT' if analysis['should_exit'] else 'HOLD'}",
                symbol=symbol,
            )
