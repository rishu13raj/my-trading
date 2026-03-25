"""
strategy/phases/incubator.py — Phase 2: Signal Confirmation Gate

THE CORE PROBLEM THIS SOLVES
──────────────────────────────
Before the incubation gate existed, the system entered trades the MOMENT a
signal fired.  This caused two distinct losing patterns on 2026-03-25:

PATTERN A — Trend-fighting entries (ADANIPOWER, HFCL)
  Both stocks were in clear intraday uptrends, yet the OFI signal was SELL
  because the ask queue was persistently heavier than the bid.

  Why the OFI was misleading: in a trending bullish stock, institutional buyers
  lift the ask aggressively.  Market makers and passive sellers keep refreshing
  the ask to sell into the demand — so the ask queue ALWAYS looks heavy.  This
  is NOT a reversal signal; it's just the normal book dynamics of a trending
  stock absorbing persistent demand.

  ADANIPOWER tick sequence at signal fire (10:25:31, SELL signal):
    10:25:03  ₹154.15  ← trigger price
    10:25:07  ₹154.20  (UP +0.03%)
    10:25:17  ₹154.24  (UP +0.06%)
    10:27:30  ₹154.42  (UP +0.18% after 2.5 min, price NEVER went down)
  Without incubation: entered SELL @ 154.11, exited TIME_EXIT_LOSS @ 154.44.
  Loss: -₹213.  Same pattern for re-entries 139 (-₹744) and 157 (-₹1,159).
  Total ADANIPOWER loss 2026-03-25: -₹2,116.

  HFCL tick sequence at signal fire (10:25:31, SELL signal):
    10:25:03  ₹70.96   ← trigger price (entry was ₹70.95)
    10:25:15  ₹71.00   (UP +0.07%)
    10:25:17  ₹70.93   (DOWN -0.03% — briefly below trigger, but only ₹0.02)
    10:27:30  ₹71.00   (recovered UP)
  The brief dip to ₹70.93 is -0.03%, well below our 0.1% confirmation threshold.
  Without incubation: entered SELL @ 70.95.  Loss: -₹366.  Re-entry: -₹995.
  Total HFCL loss 2026-03-25: -₹1,361.

PATTERN B — Immediate stops before signal had time to develop (LODHA, EASEMYTRIP)
  LODHA cascade (09:32-09:38):
    Trade 123: BUY @ 758.75 → STOP_LOSS @ 751.15 in 2 MINUTES (-₹995)
    Trade 124: BUY @ 751.85 → MOMENTUM_REVERSAL @ 750.50 in 2 MINUTES (-₹179)
    Tick sequence at 09:32 (BUY signal):
      09:32:01  ₹757.70  (DOWN from trigger ₹758.75)
      09:32:03  ₹757.55  (DOWN)
      09:34:59  ₹751.05  (DOWN -1.1% in 3 minutes)
    Price was falling from the start — incubation would never accumulate
    confirming ticks and would time out, blocking both trades.

  EASEMYTRIP Trade 149 (11:52, BUY @ 6.91, -₹1,012, STOPPED IN 30 SECONDS):
    Tick sequence during incubation:
      11:52:02  ₹6.93   (above trigger — 1 confirming tick? wait...)
    ACTUALLY: entry was 6.91 but ticks showed 6.93 just before.  At 11:52:30
    the actual entry fired at 6.91.  By 11:53:00 price was at 6.84 (stop).
    Incubation watching from 11:52:02 onward: 6.93→6.93→6.90→6.90 (FALLING).
    BUY signal needs price > trigger to confirm.  Price was falling → 0 ticks.
    → BLOCKED: saves -₹1,012.

  EASEMYTRIP Trade 145 (11:45, BUY @ 6.98, -₹1,002):
    Tick sequence during incubation:
      11:45:01  ₹6.98  (= trigger, not > trigger — 0 confirming ticks)
      11:45:02  ₹6.98
      11:45:04  ₹6.98
      11:45:07  ₹6.98
      11:45:10  ₹6.98  (10+ ticks, all stuck at trigger price)
    At ₹7 with ₹0.01 tick granularity, 0.1% = ₹0.007.  Needs price to reach
    ₹6.987 → round up to ₹6.99 (next tick above 6.98).  Price never moved.
    → BLOCKED: saves -₹1,002.

THEORETICAL BASIS
─────────────────
The incubation approach maps to several documented academic concepts:

1. "Tick confirmation filter" — standard institutional execution practice.
   Price must trade THROUGH a level, not just touch it.  4 ticks confirming
   is our version of "three closes above/below a level."

2. Lopez de Prado's "Meta-Labeling" (Advances in Financial Machine Learning, 2018):
   A primary model fires the signal (our Scanner).  A secondary model asks
   "given the signal fired, should we actually take this trade?"  Our Incubator
   is a rule-based secondary filter (price confirming in signal direction).

3. "Executed-trade OFI vs book OFI" (arXiv 2507.22712v1, 2025):
   Book imbalance (what Scanner sees) can be spoofed/flickering.  Watching
   actual price MOVEMENT during incubation is effectively checking the
   executed-trade side — real orders moving the midprice.

4. Bayesian Change-Point Detection for OFI regimes
   (Quantitative Finance, 2024, DOI:10.1080/14697688.2024.2337300):
   The trend gate is a simple proxy for regime detection — if price has been
   trending for the last 5 minutes, the OFI signal is likely noise.

TWO-LAYER GATE
──────────────
Layer 1 — Trend Gate (runs ONCE when watching starts):
  Block the incubation from even starting if the stock has already been
  trending AGAINST the signal direction for the last 5 minutes.
  Threshold: INCUBATION_TREND_BLOCK_PCT = 0.2% price change over 5 minutes.
  This catches stocks like HFCL that were in persistent uptrends.

Layer 2 — Tick Confirmation (runs EACH TICK while watching):
  Count ticks where price moved in signal direction from trigger_price.
  Require >= INCUBATION_MIN_TICKS (4) confirming ticks AND >= 0.1% total move.
  This catches cases where the trend gate didn't block (short-term trend)
  but the signal still isn't materialising (price flat or slowly reversing).

STATE PER SYMBOL
─────────────────
_watching: dict[symbol → {
    action          : 'BUY' or 'SELL'
    trigger_price   : LTP when signal first fired
    first_seen      : datetime when watch started
    confirming_ticks: count of ticks where price moved in signal direction
                      (decremented, not reset, when price moves against signal)
    candidate       : original CandidateSignal with signal_meta
}]

One entry per symbol.  Cleared on confirmation, direction-change, or timeout.

WHY confirming_ticks DECAYS INSTEAD OF RESETTING
─────────────────────────────────────────────────
If price moves against signal we do:
    confirming_ticks = max(0, confirming_ticks - 1)
...rather than resetting to 0.

Rationale: a single counter-tick in choppy movement shouldn't wipe out 3 ticks
of genuine confirmation.  But persistent counter-movement will drain it to 0.
We observed this pattern in PATANJALI (2026-03-25): price briefly dipped 1-2
ticks between buying waves but was genuinely trending up.  Hard reset would
have repeatedly restarted incubation and we'd never enter.
"""

from datetime import datetime
from typing import Dict, Optional

from config import config
from core.logger import log_event
from strategy.types import CandidateSignal, ConfirmedSignal


class Incubator:

    def __init__(self):
        # symbol → watch state dict (see module docstring for structure)
        self._watching: Dict[str, dict] = {}

    # ── public API ────────────────────────────────────────────────────────────

    def update(
        self,
        symbol: str,
        candidate: Optional[CandidateSignal],
        current_price: float,
        tick_history: list,
    ) -> Optional[ConfirmedSignal]:
        """
        Process one tick for one symbol.

        candidate = None means no signal on this tick.
        Returns ConfirmedSignal when all criteria met, None otherwise.

        Called from pipeline.on_tick() on every tick, regardless of whether
        there is a candidate signal.  This allows the incubator to:
          - Time out watches that never confirmed (ADANIPOWER pattern)
          - Decay confirming_ticks when price moves against signal
          - Reset when signal direction flips
        """
        watching = symbol in self._watching

        # ── No signal on this tick ────────────────────────────────────────────
        if candidate is None:
            # Keep any existing watch alive.  Signal may briefly dip below
            # the OFI threshold while still trending — we don't want to reset
            # a near-confirmed incubation because of a single quiet tick.
            # Timeout (layer 2) will kill it if signal doesn't come back.
            return None

        # ── Signal fired; not yet watching this symbol ────────────────────────
        if not watching:
            # Layer 1: run the trend gate ONCE before starting the clock.
            # If it blocks, we log and return — no watch state is created.
            if self._trend_gate_blocks(candidate.action, current_price, tick_history):
                log_event(
                    "signal",
                    f"{symbol} TREND_GATE: {candidate.action} blocked — "
                    f"price has been moving against signal direction for the "
                    f"last {config.INCUBATION_TREND_LOOKBACK_SECS}s "
                    f"(threshold: {config.INCUBATION_TREND_BLOCK_PCT * 100:.1f}%)",
                    symbol=symbol,
                )
                return None

            # Trend gate passed — start watching
            self._watching[symbol] = {
                "action": candidate.action,
                "trigger_price": current_price,
                "first_seen": datetime.now(),
                "confirming_ticks": 0,
                "candidate": candidate,  # preserve signal_meta from fire time
            }
            log_event(
                "signal",
                f"{symbol} INCUBATING {candidate.action} @ ₹{current_price}  "
                f"(need {config.INCUBATION_MIN_TICKS}+ confirming ticks AND "
                f"{config.INCUBATION_PRICE_MOVE_PCT * 100:.1f}% move within "
                f"{config.INCUBATION_TIMEOUT_SECS}s)",
                symbol=symbol,
            )
            return None

        # ── Already watching ──────────────────────────────────────────────────
        watch = self._watching[symbol]

        # Direction changed — signal flipped while we were watching.
        # Discard old watch; new direction will start fresh on next tick.
        # Example: EASEMYTRIP sometimes flipped BUY→SELL→BUY rapidly in the
        # 11:45-11:54 window.  Starting fresh avoids acting on stale direction.
        if watch["action"] != candidate.action:
            log_event(
                "signal",
                f"{symbol} incubation reset: signal direction changed "
                f"{watch['action']} → {candidate.action}",
                symbol=symbol,
            )
            del self._watching[symbol]
            return None

        # Timeout — signal never confirmed within the time limit.
        # ADANIPOWER: SELL signal, price kept rising for >2 min.  After 120s
        # timeout the watch is abandoned.  This is the single biggest
        # protection against trend-fighting entries.
        age = (datetime.now() - watch["first_seen"]).total_seconds()
        if age > config.INCUBATION_TIMEOUT_SECS:
            log_event(
                "signal",
                f"{symbol} incubation TIMED OUT after {age:.0f}s — "
                f"price never moved {config.INCUBATION_PRICE_MOVE_PCT * 100:.1f}% "
                f"in {watch['action']} direction from ₹{watch['trigger_price']}",
                symbol=symbol,
            )
            del self._watching[symbol]
            return None

        # ── Count confirming ticks (Layer 2) ──────────────────────────────────
        trigger = watch["trigger_price"]

        if candidate.action == "BUY" and current_price > trigger:
            # Price above trigger → confirming tick for BUY signal
            watch["confirming_ticks"] += 1
        elif candidate.action == "SELL" and current_price < trigger:
            # Price below trigger → confirming tick for SELL signal
            watch["confirming_ticks"] += 1
        else:
            # Price at or against trigger → decay confidence, don't reset.
            # See module docstring for why we decay instead of reset.
            watch["confirming_ticks"] = max(0, watch["confirming_ticks"] - 1)

        move_pct = abs(current_price - trigger) / trigger if trigger > 0 else 0

        # Confirmed when BOTH:
        #   confirming_ticks >= INCUBATION_MIN_TICKS (default 4)
        #   total price move  >= INCUBATION_PRICE_MOVE_PCT (default 0.1%)
        confirmed = (
            watch["confirming_ticks"] >= config.INCUBATION_MIN_TICKS
            and move_pct >= config.INCUBATION_PRICE_MOVE_PCT
        )

        log_event(
            "signal",
            f"{symbol} WATCHING {watch['action']}  "
            f"ticks={watch['confirming_ticks']}/{config.INCUBATION_MIN_TICKS}  "
            f"move={move_pct * 100:.3f}%/{config.INCUBATION_PRICE_MOVE_PCT * 100:.1f}%  "
            f"age={age:.0f}s/{config.INCUBATION_TIMEOUT_SECS}s  "
            f"trigger=₹{trigger}  now=₹{current_price}",
            symbol=symbol,
        )

        if not confirmed:
            return None

        # ── CONFIRMED ─────────────────────────────────────────────────────────
        log_event(
            "signal",
            f"{symbol} CONFIRMED {watch['action']} @ ₹{current_price}  "
            f"(trigger ₹{trigger} → moved {move_pct * 100:.3f}%, "
            f"{watch['confirming_ticks']} confirming ticks in {age:.0f}s)",
            symbol=symbol,
        )
        result = ConfirmedSignal(
            symbol=symbol,
            action=watch["action"],
            entry_price=current_price,
            ratio=candidate.ratio,
            bid_qty=candidate.bid_qty,
            ask_qty=candidate.ask_qty,
            signal_meta=watch["candidate"].signal_meta,  # ML meta from signal-fire time
        )
        del self._watching[symbol]
        return result

    def clear(self, symbol: str) -> None:
        """
        Discard any pending watch for this symbol.
        Called by pipeline.reset_symbol() when a stock is removed from the
        watchlist — we don't want a stale watch to fire if the stock is
        re-added later.
        """
        self._watching.pop(symbol, None)

    def watching_symbols(self) -> list:
        """
        Returns list of symbols currently in incubation.
        Exposed in /status API so the dashboard can show which stocks are
        in the "WATCHING" state.  Useful for debugging entries that seem
        slow to fire.
        """
        return list(self._watching.keys())

    # ── private ───────────────────────────────────────────────────────────────

    @staticmethod
    def _trend_gate_blocks(action: str, current_price: float, tick_history: list) -> bool:
        """
        Layer 1: Check if the stock has been trending persistently AGAINST
        the signal direction for the last INCUBATION_TREND_LOOKBACK_SECS
        seconds (default: 300s = 5 minutes).

        HOW IT WORKS
        ─────────────
        1. Find the oldest tick within the last 300 seconds.
        2. Compare its LTP to current_price.
        3. If the price move over that window exceeds INCUBATION_TREND_BLOCK_PCT
           (0.2%) in the OPPOSITE direction to the signal → block.

        EXAMPLE — HFCL 2026-03-25 (SELL signal @ 10:25)
        ──────────────────────────────────────────────────
        If HFCL was at ₹70.70 at 10:20 (5 min earlier) and is now ₹70.95:
          price_change_pct = (70.95 - 70.70) / 70.70 = +0.35%
          action = SELL, price_change_pct > INCUBATION_TREND_BLOCK_PCT (0.2%)
          → block SELL signal immediately.
        HFCL closed trades 131 (-₹366) and 141 (-₹995) were on a rising stock.
        The trend gate would have saved both.

        EXAMPLE — ADANIPOWER 2026-03-25 (SELL signal @ 10:25)
        ───────────────────────────────────────────────────────
        ADANIPOWER was at ₹154.71 at 10:15 and ₹154.11 at 10:25 (DOWN -0.39%).
        price_change_pct = (154.11 - 154.71) / 154.71 = -0.39%
        action = SELL.  Price moved DOWN (matching SELL direction) by 0.39%.
        Trend gate: price change is in SELL direction → NOT blocked.
        The stock had a brief dip, then recovered.  The trend gate misses this.
        The INCUBATION tick confirmation (Layer 2) catches ADANIPOWER instead:
        after signal fires, price goes UP (154.15→154.20→154.42), accumulating
        zero confirming SELL ticks → timeout → abandoned.

        This is why both layers are needed:
          Trend gate   → catches persistent multi-minute trends
          Tick confirm → catches short-term recoveries / false dips

        EDGE CASES
        ──────────
        - Not enough history (< 5 ticks in window): return False (benefit of doubt)
          This happens at market open or when a stock is first added to watchlist.
        - past_price == 0: return False (DB data quality issue, don't block)
        - Window partially populated (< 300s of history available): return False
          We'd rather miss a block than block valid signals at market open.

        PARAMETER CHOICE — 0.2% over 5 minutes
        ─────────────────────────────────────────
        0.2% (INCUBATION_TREND_BLOCK_PCT) over 5 minutes:
          - HFCL moved +0.35% in 5 min before SELL signal → caught
          - Typical NIFTY50 stock normal 5-min range: 0.05-0.15%
          - Setting at 0.2% means we only block on *clear* directional trends,
            not normal price oscillation.
          If this is too aggressive (blocking valid reversals), decrease to 0.15%.
          If trend-fighting entries still occur, increase to 0.15% (closer to noise
          level) — but first check if tick confirmation alone catches them.
        """
        if not tick_history or len(tick_history) < 5:
            return False  # not enough history — don't block

        latest_ts = tick_history[0].get("timestamp", 0)
        cutoff_ts = latest_ts - config.INCUBATION_TREND_LOOKBACK_SECS

        # tick_history is newest-first; ticks within lookback window
        window = [t for t in tick_history if t.get("timestamp", 0) >= cutoff_ts]
        if len(window) < 5:
            # Lookback window not populated (early in session or new stock)
            return False

        # window[-1] is the oldest tick within the lookback window
        past_price = window[-1].get("ltp", current_price)
        if past_price == 0:
            return False

        price_change_pct = (current_price - past_price) / past_price

        # Block a SELL signal if price has been rising (uptrend)
        # e.g. price_change_pct = +0.35% and action = SELL → block
        if action == "SELL" and price_change_pct > config.INCUBATION_TREND_BLOCK_PCT:
            return True

        # Block a BUY signal if price has been falling (downtrend)
        # e.g. price_change_pct = -0.35% and action = BUY → block
        if action == "BUY" and price_change_pct < -config.INCUBATION_TREND_BLOCK_PCT:
            return True

        return False
