"""
Order Book Imbalance (OBI) Momentum Strategy Engine.

Flow:
  1. User adds stocks to basket and presses "Start Monitoring".
  2. Engine enters OBSERVING state for `observation_seconds`.
  3. If OBI is strong and growing → enters TRADING state, fires a TradeSignal.
  4. Position is held until OBI reverses or stop-loss is triggered.
  5. After exit, returns to OBSERVING (or HALTED if daily stop-loss hit).

OBI formula:
  OBI = (total_buy_qty - total_sell_qty) / (total_buy_qty + total_sell_qty)

  Range: -1.0 to +1.0
    > 0  → more buying pressure  → potential LONG
    < 0  → more selling pressure → potential SHORT
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import NamedTuple

from models import Direction, TickData, TradeSignal

logger = logging.getLogger(__name__)


# ── State machine ─────────────────────────────────────────────────────────────

class StrategyState(Enum):
    IDLE = auto()         # not started / no basket configured
    OBSERVING = auto()    # watching, building confidence before entry
    TRADING = auto()      # position is open
    HALTED = auto()       # daily stop-loss hit; requires manual restart


# ── Per-symbol tracker ────────────────────────────────────────────────────────

class SymbolStats(NamedTuple):
    obi_values: deque          # rolling OBI history
    tick_times: deque          # timestamps matching obi_values


def _compute_obi(buy_qty: int, sell_qty: int) -> float:
    total = buy_qty + sell_qty
    if total == 0:
        return 0.0
    return (buy_qty - sell_qty) / total


def _obi_rate(obi_values: deque, tick_times: deque) -> float:
    """
    Slope of OBI over the rolling window using simple linear regression
    (rise / run in OBI-per-second).  Returns 0 if fewer than 2 points.
    """
    if len(obi_values) < 2:
        return 0.0
    n = len(obi_values)
    x = [(t - tick_times[0]).total_seconds() for t in tick_times]
    y = list(obi_values)
    x_mean = sum(x) / n
    y_mean = sum(y) / n
    numerator = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, y))
    denominator = sum((xi - x_mean) ** 2 for xi in x)
    if denominator == 0:
        return 0.0
    return numerator / denominator


# ── Main Strategy Engine ──────────────────────────────────────────────────────

class MomentumStrategy:
    """
    Stateful strategy engine for one trading session.
    One instance lives for the lifetime of the server process.
    Reset `reset_session()` at the start of each day.
    """

    def __init__(
        self,
        obi_threshold: float = 0.30,
        obi_sustain_ticks: int = 6,
        observation_seconds: int = 60,
        window_size: int = 20,
    ) -> None:
        self.obi_threshold = obi_threshold
        self.obi_sustain_ticks = obi_sustain_ticks
        self.observation_seconds = observation_seconds
        self.window_size = window_size

        self.state: StrategyState = StrategyState.IDLE
        self._basket: list[str] = []

        # Per-symbol rolling data
        self._stats: dict[str, SymbolStats] = {}
        # Tick counts where OBI was above/below threshold (for sustain check)
        self._sustain_count: dict[str, int] = {}

        # Observation window start time (per symbol, or single session start)
        self._observe_start: datetime | None = None

        # Daily risk tracking
        self.daily_pnl: float = 0.0
        self.daily_trades: int = 0
        self.daily_stop_loss: float = 1_000.0        # overridden via configure()
        self.per_trade_stop_loss: float = 400.0

        # Currently open positions: symbol → direction
        self.open_positions: dict[str, Direction] = {}
        # Entry OBI for open positions
        self.entry_obi: dict[str, float] = {}

    # ── Configuration ─────────────────────────────────────────────────────────

    def configure(
        self,
        basket: list[str],
        obi_threshold: float,
        observation_seconds: int,
        daily_stop_loss: float,
        per_trade_stop_loss: float,
    ) -> None:
        self._basket = basket
        self.obi_threshold = obi_threshold
        self.observation_seconds = observation_seconds
        self.daily_stop_loss = daily_stop_loss
        self.per_trade_stop_loss = per_trade_stop_loss
        self._stats = {
            sym: SymbolStats(
                obi_values=deque(maxlen=self.window_size),
                tick_times=deque(maxlen=self.window_size),
            )
            for sym in basket
        }
        self._sustain_count = {sym: 0 for sym in basket}
        logger.info("Strategy configured: basket=%s threshold=%.2f", basket, obi_threshold)

    def start_observing(self) -> None:
        if self.state == StrategyState.HALTED:
            logger.warning("Cannot start: daily stop-loss hit. Reset first.")
            return
        self._observe_start = datetime.utcnow()
        self.state = StrategyState.OBSERVING
        logger.info("Strategy entered OBSERVING state.")

    def stop(self) -> None:
        """Manually halt monitoring (e.g. user presses Stop)."""
        if self.state not in (StrategyState.TRADING,):
            self.state = StrategyState.IDLE
        logger.info("Strategy stopped by user.")

    def reset_session(self) -> None:
        """Call at the start of each trading day."""
        self.state = StrategyState.IDLE
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.open_positions.clear()
        self.entry_obi.clear()
        for sym in self._stats:
            self._stats[sym].obi_values.clear()
            self._stats[sym].tick_times.clear()
            self._sustain_count[sym] = 0
        logger.info("Strategy session reset.")

    # ── Core tick processing ──────────────────────────────────────────────────

    def on_tick(self, tick: TickData) -> TradeSignal | None:
        """
        Process a single real-time tick for one symbol.
        Returns a TradeSignal if an entry or exit action is required,
        or None if we should just keep watching.
        """
        if self.state == StrategyState.IDLE or self.state == StrategyState.HALTED:
            return None
        if tick.symbol not in self._basket:
            return None

        sym = tick.symbol
        stats = self._stats[sym]

        # Update rolling OBI history
        stats.obi_values.append(tick.obi)
        stats.tick_times.append(tick.timestamp)

        rate = _obi_rate(stats.obi_values, stats.tick_times)

        # ── Exit check (if we have an open position) ───────────────────────
        if sym in self.open_positions:
            return self._check_exit(sym, tick, rate)

        # ── Entry check ────────────────────────────────────────────────────
        if self.state == StrategyState.OBSERVING:
            return self._check_entry(sym, tick, rate)

        return None

    def _check_entry(self, sym: str, tick: TickData, rate: float) -> TradeSignal | None:
        obi = tick.obi
        abs_obi = abs(obi)

        # Must be above threshold
        if abs_obi >= self.obi_threshold:
            self._sustain_count[sym] += 1
        else:
            self._sustain_count[sym] = 0

        # Not enough consecutive ticks above threshold yet
        if self._sustain_count[sym] < self.obi_sustain_ticks:
            return None

        # Observation window must have passed
        if self._observe_start is None:
            return None
        elapsed = (datetime.utcnow() - self._observe_start).total_seconds()
        if elapsed < self.observation_seconds:
            logger.debug(
                "%s OBI=%.3f strong but still in observation window (%.0fs / %ds)",
                sym, obi, elapsed, self.observation_seconds,
            )
            return None

        # OBI must be growing in the correct direction
        direction = Direction.LONG if obi > 0 else Direction.SHORT
        growing = (obi > 0 and rate > 0) or (obi < 0 and rate < 0)
        if not growing:
            logger.debug("%s OBI=%.3f not growing (rate=%.5f), waiting.", sym, obi, rate)
            return None

        # All conditions met — generate entry signal
        logger.info(
            "ENTRY SIGNAL: %s %s  OBI=%.3f  rate=%.5f  ltp=%.2f",
            direction, sym, obi, rate, tick.ltp,
        )
        self.open_positions[sym] = direction
        self.entry_obi[sym] = obi
        self.state = StrategyState.TRADING

        return TradeSignal(
            symbol=sym,
            direction=direction,
            obi=obi,
            ltp=tick.ltp,
            quantity=0,  # quantity is filled by OrderManager based on capital
            reason=f"OBI={obi:.3f} sustained {self._sustain_count[sym]} ticks, rate={rate:.5f}",
        )

    def _check_exit(self, sym: str, tick: TickData, rate: float) -> TradeSignal | None:
        direction = self.open_positions[sym]
        obi = tick.obi

        # Determine exit condition
        exit_reason: str | None = None

        if direction == Direction.LONG:
            # Exit long if OBI turns negative (sellers taking over)
            if obi < 0:
                exit_reason = f"OBI flipped negative ({obi:.3f}): momentum reversed"
            # Exit if OBI was positive but is now decelerating strongly
            elif obi > 0 and rate < -0.002:
                exit_reason = f"OBI growth stalled (rate={rate:.5f})"

        elif direction == Direction.SHORT:
            # Exit short if OBI turns positive (buyers taking over)
            if obi > 0:
                exit_reason = f"OBI flipped positive ({obi:.3f}): momentum reversed"
            # Exit if OBI was negative but decelerating
            elif obi < 0 and rate > 0.002:
                exit_reason = f"OBI growth stalled (rate={rate:.5f})"

        if exit_reason:
            logger.info("EXIT SIGNAL: %s %s — %s", direction, sym, exit_reason)
            exit_direction = Direction.SHORT if direction == Direction.LONG else Direction.LONG
            del self.open_positions[sym]
            del self.entry_obi[sym]
            if not self.open_positions:
                self.state = StrategyState.OBSERVING
            return TradeSignal(
                symbol=sym,
                direction=exit_direction,   # opposite to close position
                obi=obi,
                ltp=tick.ltp,
                quantity=0,
                reason=exit_reason,
            )

        return None

    # ── Risk hooks called by OrderManager after fills ─────────────────────────

    def record_trade_pnl(self, pnl: float) -> bool:
        """
        Record realised P&L from a closed trade.
        Returns True if trading should be halted (daily stop-loss hit).
        """
        self.daily_pnl += pnl
        self.daily_trades += 1
        logger.info("Trade PnL=%.2f | Daily PnL=%.2f", pnl, self.daily_pnl)

        if self.daily_pnl <= -self.daily_stop_loss:
            self.state = StrategyState.HALTED
            logger.warning(
                "DAILY STOP-LOSS HIT: %.2f. All trading halted.", self.daily_pnl
            )
            return True
        return False

    # ── Snapshot for API / dashboard ─────────────────────────────────────────

    def snapshot(self) -> dict:
        """Return a lightweight dict suitable for broadcasting over WebSocket."""
        obi_map: dict[str, float] = {}
        rate_map: dict[str, float] = {}
        for sym, stats in self._stats.items():
            obi_map[sym] = stats.obi_values[-1] if stats.obi_values else 0.0
            rate_map[sym] = _obi_rate(stats.obi_values, stats.tick_times)
        return {
            "state": self.state.name,
            "daily_pnl": self.daily_pnl,
            "daily_trades": self.daily_trades,
            "open_positions": {k: v.value for k, v in self.open_positions.items()},
            "obi": obi_map,
            "obi_rate": rate_map,
        }


# ── Global singleton ──────────────────────────────────────────────────────────
strategy = MomentumStrategy()
