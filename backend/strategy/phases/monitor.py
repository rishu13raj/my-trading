"""
strategy/phases/monitor.py — Phase 4: Open Position Monitoring

PURPOSE
───────
Monitor runs on EVERY tick for a symbol, as long as there is an open position.
It does three things each tick:

  1. Update peak price — track the best price seen since entry per trade_id.
     This is the input to PROFIT_TRAIL and TRAIL_STOP exit conditions.

  2. Check exit conditions — call exit_logic.should_exit() and surface an
     ExitDecision if any condition fires.  Exit conditions in priority order:
       a. Stop loss (always active, no minimum hold)
       b. PROFIT_TRAIL (early trail: activates once profit >= 0.5% of entry)
       c. Time exit split (losing → hard exit at TIME_EXIT_MINUTES;
                           winning → activate TRAIL_STOP instead)
       d. MOMENTUM_REVERSAL (after 60s minimum hold)

  3. Check momentum flip — call reversal_detector.check_flip() after
     MIN_HOLD_BEFORE_FLIP seconds.  Returns ExitDecision(reason='MOMENTUM_FLIP')
     which tells the pipeline to close AND immediately open the reverse trade.

WHY EXIT CHECKING RUNS BEFORE ENTRY EVALUATION
────────────────────────────────────────────────
In pipeline.on_tick(), monitor.evaluate() is called FIRST, before any entry
logic.  If it returns an ExitDecision, the pipeline executes the exit and
returns immediately (no entry that tick).

Rationale: an exit and a new entry on the same tick would mean we close a
position and simultaneously enter another one.  In live trading this creates
ambiguous order sequencing.  Better to let the exit settle, let the cooldown
start, and let the incubator re-evaluate on the next tick.

Exception: MOMENTUM_FLIP is handled specially — we want to enter the reverse
direction immediately after the flip close, before the market moves.  The
pipeline handles this as a special case in _execute_exit().

PEAK PRICE TRACKING — THE PROFIT_TRAIL DEPENDENCY
───────────────────────────────────────────────────
PROFIT_TRAIL and TRAIL_STOP (in exit_logic.py) require knowing the best price
seen since entry.  This is tracked per trade_id in _peaks.

REAL EXAMPLE — HCLTECH (2026-03-25, Trade 126)
  Entry: BUY @ ₹1371.5 (09:40)
  Timeline:
    09:40  Entry @ ₹1371.5  → peak = ₹1371.5 (initialised on first tick)
    10:00  Price ₹1385.0    → peak = ₹1385.0 (updated)
    10:30  Price ₹1395.0    → peak = ₹1395.0
    ~11:00 Price ₹1399.5    → peak = ₹1399.5  ← previous session peak
    ~12:30 Price ₹1406.5    → peak = ₹1406.5  ← new session peak (after restart)

  With peak = ₹1406.5:
    peak_gain = 1406.5 - 1371.5 = ₹35.0
    activation_threshold = 1371.5 * 0.005 = ₹6.86 (already far exceeded)
    trail_stop = 1371.5 + 35.0 * (1 - 0.25) = 1371.5 + 26.25 = ₹1397.75
    If price drops to ₹1397.75 → PROFIT_TRAIL fires.
    This locks in ₹(1397.75 - 1371.5) * 72 = ₹1,890 minimum profit.

KNOWN LIMITATION — PEAK LOST ON RESTART
─────────────────────────────────────────
_peaks lives only in memory.  If the backend restarts (e.g. to deploy new code),
peak is reset to None, and PROFIT_TRAIL / TRAIL_STOP will not fire correctly
until a new peak is established on the next uptick.

REAL IMPACT (2026-03-25):
  LODHA Trade 1: peaked at ~₹758.85 before restart.  After restart, peak was
  None → PROFIT_TRAIL didn't fire at the ₹758.85 level.  Stock consolidated
  then fell → eventual MOMENTUM_REVERSAL exit at lower profit.

  This is documented in TODO.md as "Persist peak prices to DB".
  Fix: write peak to a `trade_peaks` table or add `peak_price` column to
  trades on each tick update, so it survives restarts.

FLIP DETECTION — THREE-LAYER CONFIRMATION
──────────────────────────────────────────
The momentum flip detector in reversal_detector.check_flip() requires all three:
  Layer 1 — OFI flip: opposite imbalance for FLIP_CONFIRM_TICKS (4) consecutive ticks
  Layer 2 — Delta exhaustion: cumulative delta slope has turned against trade direction
  Layer 3 — Absorption: dominant side was pushing but price didn't move

All three must confirm simultaneously.  Layer 3 alone fires constantly in choppy
markets (absorption is common intra-range behaviour).

Minimum hold before flip is evaluated: MIN_HOLD_BEFORE_FLIP = 60 seconds.
Also requires a minimum adverse price move of FLIP_MIN_ADVERSE_PCT = 0.3%
before the flip is even considered (prevents flipping on noise in flat markets).

WHY flip_states LIVES IN Monitor (NOT in pipeline or reversal_detector)
──────────────────────────────────────────────────────────────────────────
check_flip() is a pure function — it takes position + tick_history + a mutable
state dict and updates the state dict in-place (cumulative_delta, consecutive
flip_ticks).  Someone needs to own that mutable dict per trade_id.

Before refactor: _position_flip_state lived as a global in main.py.
After refactor: it lives in Monitor._flip_states[trade_id].
This is the natural owner because Monitor already knows about active positions
and their trade_ids.

STATE OWNED BY MONITOR
────────────────────────
_peaks           : dict[trade_id → float]
                   Best price seen since entry (max for BUY, min for SELL).
                   Used as peak_price argument to exit_logic.should_exit().

_flip_states     : dict[trade_id → {cumulative_delta: float,
                                     consecutive_flip_ticks: int}]
                   Mutable accumulator for the flip detector.
                   Cleared by clear_position() when a trade closes.

_log_tick_count  : dict[symbol → int]
                   Tick counter for throttled position logging.
                   Reset to 0 when no active position for that symbol.
"""

from datetime import datetime
from typing import Dict, Optional

from config import config
from core.logger import log_event
from data.database import db
from strategy.exit_logic import get_exit_analysis, should_exit
from strategy.reversal_detector import check_flip
from strategy.types import ExitDecision

# How many ticks between full position status log lines.
# With ~2 ticks/second, POSITION_LOG_INTERVAL=10 means logs every ~5 seconds.
# The first tick always logs (10 % 10 == 0 is false, but 1 % 10 == 1 is true
# with the "% 10 == 1" check), then every 10 ticks after.
# Increase this value if the activity log is too noisy during long holds.
POSITION_LOG_INTERVAL = 10


class Monitor:

    def __init__(self):
        self._peaks: Dict[int, float] = {}           # trade_id → best price
        self._flip_states: Dict[int, dict] = {}      # trade_id → flip state
        self._log_tick_count: Dict[str, int] = {}    # symbol   → tick count

    # ── public API ────────────────────────────────────────────────────────────

    def evaluate(
        self,
        symbol: str,
        current_price: float,
        tick_history: list,
    ) -> Optional[ExitDecision]:
        """
        Evaluate all open positions for this symbol on the current tick.

        Called from pipeline.on_tick() BEFORE any entry-phase logic.
        Returns the first ExitDecision that fires, or None to hold.

        Exit conditions are checked in exit_logic.should_exit() priority order:
          1. Stop loss (no minimum hold, always active)
          2. PROFIT_TRAIL (once profit >= 0.5% of entry)
          3. Time exit / TRAIL_STOP (after TIME_EXIT_MINUTES)
          4. MOMENTUM_REVERSAL (after 60s minimum hold)

        Flip detection runs AFTER exit conditions — if a stop fires on the
        same tick as a potential flip signal, the stop takes priority.
        """
        active = [t for t in db.get_active_trades() if t["symbol"] == symbol]

        if not active:
            # No open position for this symbol.
            # Reset log counter so it restarts from tick 1 if a new position opens.
            self._log_tick_count.pop(symbol, None)
            return None

        # Throttled status log — shows P&L, stop distance, time held, etc.
        # Runs on tick 1, 11, 21, ... (every POSITION_LOG_INTERVAL ticks)
        # so the operator can see live position state without flooding the log.
        self._log_tick_count[symbol] = self._log_tick_count.get(symbol, 0) + 1
        if self._log_tick_count[symbol] % POSITION_LOG_INTERVAL == 1:
            self._log_positions(active, symbol, current_price, tick_history)

        for pos in active:
            trade_id = pos["id"]

            # Step 1: Update peak price BEFORE checking exits.
            # should_exit() uses peak_price for PROFIT_TRAIL calculation.
            # If we checked exits before updating the peak, we'd miss the
            # case where the current tick IS the new peak AND the trail fires
            # on the same tick (rare but possible in fast markets).
            self._update_peak(trade_id, pos["direction"], current_price)

            # Step 2: Check exit conditions
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

            # Step 3: Check momentum flip (only after minimum hold time)
            # Flip detection is lower priority than stop/trail — we don't
            # want a flip to fire when we should have stopped out.
            flip = self._check_flip(pos, tick_history, current_price)
            if flip:
                return flip

        return None

    def get_peak(self, trade_id: int) -> Optional[float]:
        """
        Return the current peak price for a trade_id.
        Used by pipeline._execute_exit() for logging purposes.
        """
        return self._peaks.get(trade_id)

    def clear_position(self, trade_id: int) -> None:
        """
        Free memory after a trade closes.
        Must be called by pipeline._execute_exit() for every close event.
        If not called, _peaks and _flip_states will accumulate forever.
        """
        self._peaks.pop(trade_id, None)
        self._flip_states.pop(trade_id, None)

    # ── private ───────────────────────────────────────────────────────────────

    def _update_peak(self, trade_id: int, direction: str, current_price: float) -> None:
        """
        Maintain the high-water mark (BUY) or low-water mark (SELL).

        For BUY trades: peak = maximum LTP seen since entry.
          If stock goes 100→105→103, peak = 105.
          PROFIT_TRAIL: exit if price falls below entry + 75% of (105-entry).

        For SELL trades: peak = minimum LTP seen since entry.
          If stock goes 100→95→97, peak = 95.
          PROFIT_TRAIL: exit if price rises above entry - 75% of (entry-95).

        First tick: peak is initialised to current_price.
        On restart: peak is None until the first tick after restart resets it
        to current_price.  This means the trail only tracks from the restart
        point, not from the original entry high.  See class docstring for the
        HCLTECH example and the TODO item for DB persistence.
        """
        if trade_id not in self._peaks:
            # First tick for this trade_id since startup.
            # Initialise to current price (not entry price) to be conservative:
            # we might be restarting mid-trade with price already at peak.
            self._peaks[trade_id] = current_price
        elif direction == "BUY":
            self._peaks[trade_id] = max(self._peaks[trade_id], current_price)
        else:  # SELL
            self._peaks[trade_id] = min(self._peaks[trade_id], current_price)

    def _check_flip(
        self, pos: dict, tick_history: list, current_price: float
    ) -> Optional[ExitDecision]:
        """
        Check whether the momentum for this position has reversed enough to
        warrant closing and immediately entering the opposite direction.

        MINIMUM HOLD GATE
        ─────────────────
        config.MIN_HOLD_BEFORE_FLIP (60 seconds by default).
        Prevents flip detection from firing in the first minute of a trade
        when signals are noisy and the position hasn't had time to develop.

        REAL EXAMPLE — WHY THIS GATE EXISTS
        ─────────────────────────────────────
        Without the minimum hold, the flip detector fires on the very first
        tick where momentum briefly looks like it's reversing — which happens
        constantly in volatile stocks like EASEMYTRIP (₹7 range, ₹0.01 ticks,
        very fast bid/ask oscillation).  Entering a flip within the first 30
        seconds would just be noise trading.

        HOW check_flip() WORKS (reversal_detector.py)
        ───────────────────────────────────────────────
        check_flip() takes the flip_state dict and mutates it each tick:
          flip_state['cumulative_delta']     accumulates bid_qty - ask_qty per tick
          flip_state['consecutive_flip_ticks'] counts consecutive ticks where the
                                               OPPOSITE imbalance exceeds FLIP_RATIO

        Returns 'BUY' or 'SELL' (the new direction) only when ALL THREE layers
        confirm simultaneously.  Returns None otherwise.

        When flip fires, we clear the flip_state for this trade_id (it will
        be a new trade after the flip, so it gets a fresh flip_state).
        """
        trade_id = pos["id"]

        # Parse entry_time to calculate hold duration
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
                f"{pos['symbol']} [holding {pos['direction']}] flip skipped: "
                f"{held_seconds:.0f}s < {config.MIN_HOLD_BEFORE_FLIP}s min hold",
                symbol=pos["symbol"],
            )
            return None

        # Initialise flip state on first eligible tick for this trade_id
        if trade_id not in self._flip_states:
            self._flip_states[trade_id] = {
                "cumulative_delta": 0.0,
                "consecutive_flip_ticks": 0,
            }

        reverse_direction = check_flip(pos, tick_history, self._flip_states[trade_id])

        if not reverse_direction:
            # Not flipping yet — log current flip progress so operator can see
            # how close to a flip we are.
            flip_state = self._flip_states[trade_id]
            log_event(
                "signal",
                f"{pos['symbol']} [holding {pos['direction']}] "
                f"flip_ticks={flip_state.get('consecutive_flip_ticks', 0)}/{config.FLIP_CONFIRM_TICKS}  "
                f"cum_delta={flip_state.get('cumulative_delta', 0):.0f}",
                symbol=pos["symbol"],
            )
            return None

        # Flip confirmed — clear state (new trade gets fresh state)
        self._flip_states.pop(trade_id, None)
        return ExitDecision(
            trade_id=trade_id,
            symbol=pos["symbol"],
            exit_price=current_price,
            reason="MOMENTUM_FLIP",
            direction=pos["direction"],  # original direction; pipeline opens reverse
        )

    def _log_positions(
        self, positions: list, symbol: str, current_price: float, tick_history: list
    ) -> None:
        """
        Log a full position status line for each open position.
        Throttled to once per POSITION_LOG_INTERVAL ticks by the caller.

        Format:
          POSITION {direction} {symbol}  qty={qty}  entry=₹{entry}  now=₹{current}
          peak=₹{peak}  P&L={pnl}  SL=₹{sl}(dist={distance})
          {time_flag}  {momentum_flag}  → HOLD or EXIT

        The → EXIT/HOLD at the end is forward-looking: get_exit_analysis()
        re-evaluates should_exit() so the log line shows what WILL happen
        on the NEXT tick if conditions persist.  This is intentionally
        conservative — the actual exit decision this tick was already made
        by evaluate() above, so if this log says EXIT it means the next tick
        will likely trigger an exit.

        Including peak in the log line was added after the HCLTECH monitoring
        session: we couldn't see in the logs how close the PROFIT_TRAIL was to
        firing without knowing what the peak was.
        """
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
            peak_str = f"  peak=₹{peak:.2f}" if peak is not None else ""

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
                f"POSITION {pos['direction']} {symbol}  "
                f"qty={qty}  entry=₹{entry}  now=₹{current_price}{peak_str}  "
                f"P&L={pnl_str}  SL=₹{sl}(dist={sl_dist})  "
                f"{time_flag}  {momentum_flag}  "
                f"→ {'EXIT' if analysis['should_exit'] else 'HOLD'}",
                symbol=symbol,
            )
