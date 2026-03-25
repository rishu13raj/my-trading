"""
Phase 2 — Incubator

Confirmation gate that sits between a raw signal and an order.

When a CandidateSignal fires we do NOT enter immediately. Instead we start
watching the stock for N ticks and require the price to actually move in the
signal direction before committing capital.

Two-layer gate:

  Layer 1 — Trend Gate (runs once when watching starts)
    Look back INCUBATION_TREND_LOOKBACK_SECS seconds in tick history.
    If price has already moved >= INCUBATION_TREND_BLOCK_PCT *against* the
    signal direction, the stock is trending the wrong way — block immediately
    without even starting the watch clock.

    Example: HFCL trending UP all morning; SELL signal fires at 10:25.
    Trend gate: price moved +0.25% in last 5 min → block SELL → done.

  Layer 2 — Tick Confirmation (runs each tick while watching)
    Require INCUBATION_MIN_TICKS consecutive (net) ticks where the price
    moved in the signal direction AND the total price move from trigger is
    >= INCUBATION_PRICE_MOVE_PCT.

    Abandon if no confirmation within INCUBATION_TIMEOUT_SECS seconds.

State: one entry per symbol currently being watched.
"""

from datetime import datetime
from typing import Dict, Optional

from config import config
from core.logger import log_event
from strategy.types import CandidateSignal, ConfirmedSignal


class Incubator:
    def __init__(self):
        # symbol -> watch state dict
        self._watching: Dict[str, dict] = {}

    # ── public API ───────────────────────────────────────────────────────────

    def update(
        self,
        symbol: str,
        candidate: Optional[CandidateSignal],
        current_price: float,
        tick_history: list,
    ) -> Optional[ConfirmedSignal]:
        """
        Call once per tick for a symbol.

        - candidate: output of Scanner.evaluate() for this tick (None = no signal)
        - Returns ConfirmedSignal when all confirmation criteria are met, else None.
        """
        watching = symbol in self._watching

        # ── No signal this tick ───────────────────────────────────────────────
        if candidate is None:
            if watching:
                # Signal briefly disappeared (ratio dipped below threshold).
                # Keep the clock running — transient dips happen.
                # The timeout will clean it up if it never comes back.
                pass
            return None

        # ── Signal fired; not yet watching ───────────────────────────────────
        if not watching:
            if self._trend_gate_blocks(candidate.action, current_price, tick_history):
                log_event(
                    "signal",
                    f"{symbol} TREND_GATE: {candidate.action} blocked — "
                    f"price trending opposite to signal over last "
                    f"{config.INCUBATION_TREND_LOOKBACK_SECS}s",
                    symbol=symbol,
                )
                return None

            self._watching[symbol] = {
                "action": candidate.action,
                "trigger_price": current_price,
                "first_seen": datetime.now(),
                "confirming_ticks": 0,
                "candidate": candidate,
            }
            log_event(
                "signal",
                f"{symbol} INCUBATING {candidate.action} @ ₹{current_price} "
                f"(need {config.INCUBATION_MIN_TICKS}+ ticks + "
                f"{config.INCUBATION_PRICE_MOVE_PCT * 100:.1f}% move)",
                symbol=symbol,
            )
            return None

        # ── Already watching ──────────────────────────────────────────────────
        watch = self._watching[symbol]

        # Direction changed — the signal flipped; reset and start fresh next tick
        if watch["action"] != candidate.action:
            log_event(
                "signal",
                f"{symbol} incubation reset: signal direction changed "
                f"{watch['action']} → {candidate.action}",
                symbol=symbol,
            )
            del self._watching[symbol]
            return None

        # Timeout
        age = (datetime.now() - watch["first_seen"]).total_seconds()
        if age > config.INCUBATION_TIMEOUT_SECS:
            log_event(
                "signal",
                f"{symbol} incubation timed out after {age:.0f}s — signal not confirmed",
                symbol=symbol,
            )
            del self._watching[symbol]
            return None

        # Count this tick
        trigger = watch["trigger_price"]
        if candidate.action == "BUY" and current_price > trigger:
            watch["confirming_ticks"] += 1
        elif candidate.action == "SELL" and current_price < trigger:
            watch["confirming_ticks"] += 1
        else:
            # Price moving against signal — decay confidence (don't go below 0)
            watch["confirming_ticks"] = max(0, watch["confirming_ticks"] - 1)

        move_pct = abs(current_price - trigger) / trigger if trigger > 0 else 0
        confirmed = (
            watch["confirming_ticks"] >= config.INCUBATION_MIN_TICKS
            and move_pct >= config.INCUBATION_PRICE_MOVE_PCT
        )

        log_event(
            "signal",
            f"{symbol} WATCHING {watch['action']}  "
            f"ticks={watch['confirming_ticks']}/{config.INCUBATION_MIN_TICKS}  "
            f"move={move_pct * 100:.2f}%/{config.INCUBATION_PRICE_MOVE_PCT * 100:.1f}%  "
            f"age={age:.0f}s/{config.INCUBATION_TIMEOUT_SECS}s",
            symbol=symbol,
        )

        if not confirmed:
            return None

        # ── Confirmed ─────────────────────────────────────────────────────────
        log_event(
            "signal",
            f"{symbol} CONFIRMED {watch['action']} @ ₹{current_price} "
            f"(trigger ₹{trigger}, move +{move_pct * 100:.2f}%, "
            f"{watch['confirming_ticks']} confirming ticks)",
            symbol=symbol,
        )
        result = ConfirmedSignal(
            symbol=symbol,
            action=watch["action"],
            entry_price=current_price,
            ratio=candidate.ratio,
            bid_qty=candidate.bid_qty,
            ask_qty=candidate.ask_qty,
            signal_meta=watch["candidate"].signal_meta,
        )
        del self._watching[symbol]
        return result

    def clear(self, symbol: str) -> None:
        """Discard any pending watch for a symbol (e.g. stock removed from watchlist)."""
        self._watching.pop(symbol, None)

    def watching_symbols(self) -> list:
        return list(self._watching.keys())

    # ── private ───────────────────────────────────────────────────────────────

    @staticmethod
    def _trend_gate_blocks(action: str, current_price: float, tick_history: list) -> bool:
        """
        Returns True if the stock has been trending persistently AGAINST
        the signal direction for the last INCUBATION_TREND_LOOKBACK_SECS seconds.

        Uses tick timestamps (Unix epoch integers stored in the DB).
        """
        if not tick_history or len(tick_history) < 5:
            return False  # not enough history — benefit of the doubt

        latest_ts = tick_history[0].get("timestamp", 0)
        cutoff_ts = latest_ts - config.INCUBATION_TREND_LOOKBACK_SECS

        window = [t for t in tick_history if t.get("timestamp", 0) >= cutoff_ts]
        if len(window) < 5:
            return False  # lookback window not populated yet

        # tick_history is newest-first, so window[-1] is the oldest in window
        past_price = window[-1].get("ltp", current_price)
        if past_price == 0:
            return False

        price_change_pct = (current_price - past_price) / past_price

        # Block SELL if price has been rising
        if action == "SELL" and price_change_pct > config.INCUBATION_TREND_BLOCK_PCT:
            return True

        # Block BUY if price has been falling
        if action == "BUY" and price_change_pct < -config.INCUBATION_TREND_BLOCK_PCT:
            return True

        return False
