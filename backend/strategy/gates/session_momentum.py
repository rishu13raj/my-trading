"""
strategy/gates/session_momentum.py — Session Momentum Conviction Gate

THE PROBLEM THIS SOLVES
────────────────────────
OFI fires SELL signals throughout the day. But a SELL OFI signal on a stock
that established a clear upward bias in the first 30 minutes of the session
has a structurally lower win rate than one aligned with the session's early
direction.

Academic basis: Gao, Han, Li, Zhou (JFE 2018) — "Market Intraday Momentum":
the first half-hour return predicts the directional bias for the rest of the
session. The mechanism is informed institutional flow in the opening period
that persists due to late-arriving participants acting on the same signal.

HOW IT WORKS
────────────
Phase 1 (09:15–09:45, first SESSION_MOMENTUM_WINDOW_MINS minutes):
  Record the opening price and track price evolution.
  No entries are blocked — we're still observing.

Phase 2 (after 09:45):
  Compute bias = (price_at_window_end - opening_price) / opening_price
  If bias > SESSION_MOMENTUM_THRESHOLD_PCT (0.2%): stock established UP bias.
    → Block SELL entries for the rest of the session.
  If bias < -SESSION_MOMENTUM_THRESHOLD_PCT: stock established DOWN bias.
    → Block BUY entries for the rest of the session.
  If within ±0.2%: no clear bias, allow both directions.

IMPORTANT: This gate only fires AFTER the window closes. Before 09:45, all
signals pass through. This preserves early-morning profitable entries.

STATE: per-symbol, in-memory, reset at start of each session.
"""

from datetime import datetime, date
from typing import Dict, Optional
from config import config


class SessionMomentumGate:

    def __init__(self):
        # symbol → { opening_price, bias_price, bias_computed, bias_direction, session_date }
        self._state: Dict[str, dict] = {}

    def on_tick(self, symbol: str, ltp: float, timestamp: int) -> None:
        """
        Called on every tick for every symbol. Updates the session state.
        Must be called from pipeline before any gate checks.
        """
        tick_dt = datetime.fromtimestamp(timestamp)
        today = tick_dt.date()
        session_open = tick_dt.replace(hour=9, minute=15, second=0, microsecond=0)
        mins_since_open = (tick_dt - session_open).total_seconds() / 60

        state = self._state.get(symbol)

        # Reset state on new session day
        if state and state.get("session_date") != today:
            state = None

        if state is None:
            self._state[symbol] = {
                "session_date": today,
                "opening_price": ltp,
                "bias_computed": False,
                "bias_direction": None,  # 'UP', 'DOWN', or None
            }
            return

        # Once bias is computed for this session, nothing more to do
        if state["bias_computed"]:
            return

        # Window not closed yet — keep updating the "current" price
        # (We compute bias when the window closes, using whatever price we're at)
        if mins_since_open >= config.SESSION_MOMENTUM_WINDOW_MINS:
            opening = state["opening_price"]
            if opening and opening > 0:
                bias_pct = (ltp - opening) / opening
                if bias_pct > config.SESSION_MOMENTUM_THRESHOLD_PCT:
                    state["bias_direction"] = "UP"
                elif bias_pct < -config.SESSION_MOMENTUM_THRESHOLD_PCT:
                    state["bias_direction"] = "DOWN"
                else:
                    state["bias_direction"] = None  # no clear bias
            state["bias_computed"] = True

    def blocks(self, symbol: str, action: str) -> bool:
        """
        Returns True if this symbol's session bias blocks the given action.

        Does NOT block if:
        - No state for symbol (first tick of the day)
        - Bias window not yet closed (< 30 min into session)
        - No clear bias established (price moved < 0.2%)
        """
        state = self._state.get(symbol)
        if not state or not state["bias_computed"]:
            return False  # window not closed yet — allow all

        bias = state["bias_direction"]
        if bias is None:
            return False  # no clear bias

        # Block entries that go AGAINST the session's established direction
        if bias == "UP" and action == "SELL":
            return True
        if bias == "DOWN" and action == "BUY":
            return True
        return False

    def get_bias(self, symbol: str) -> Optional[str]:
        """Returns 'UP', 'DOWN', None (no bias), or 'UNKNOWN' (window not closed)."""
        state = self._state.get(symbol)
        if not state:
            return "UNKNOWN"
        if not state["bias_computed"]:
            return "UNKNOWN"
        return state["bias_direction"]


# Singleton — shared across scanner and pipeline
session_momentum_gate = SessionMomentumGate()
