"""
strategy/phases/scanner.py — Phase 1: Signal Detection

PURPOSE
───────
The scanner evaluates ONE tick for ONE symbol and answers: "Is there a
meaningful OFI (Order Flow Imbalance) signal right now?"

It is STATELESS — every tick is evaluated independently, with no memory of
previous ticks.  Persistence of a signal across ticks is handled by Phase 2
(Incubator), not here.

TWO CHECKS APPLIED (in order)
──────────────────────────────
1. OFI signal — generate_signal() from strategy/signal.py:
   Uses bid_ask_ratio.py to check if bid/ask imbalance crosses the threshold
   (BID_ASK_THRESHOLD_RATIO = 2.0x by default).  Also requires monitor_for_entry()
   confirmation: the ratio must have been above threshold for >= ENTRY_WAIT_SECONDS
   seconds in the recent tick window.

2. Signal quality filter — check_signal_quality() from strategy/signal_quality.py:
   Five sub-checks that filter out technically valid but unreliable signals:
     a) Minimum total queue depth (SQ_MIN_TOTAL_QUEUE = 10,000)
     b) Minimum weaker-side depth (SQ_MIN_SIDE_QUEUE = 2,000)
     c) Extreme ratio (>8x) on shallow book (< SQ_EXTREME_RATIO_MIN_DEPTH = 25,000)
     d) Imbalance buildup — ratio must appear in >= SQ_BUILDUP_TICKS of last
        SQ_BUILDUP_WINDOW ticks (filters single-tick spikes)
     e) Iceberg suspect — extreme ratio but ask doesn't shrink and price is flat
        (hidden seller refreshing the ask to absorb aggressive buyers)

WHAT THE SCANNER DOES NOT DO
──────────────────────────────
- It does NOT check portfolio capacity, cooldown, or trading-paused state.
  Those are Entry (Phase 3) concerns.
- It does NOT confirm the signal over time. That is Incubator (Phase 2).
- It does NOT track whether it previously fired a signal for this symbol.
  Each call is a fresh, independent judgment.

OUTPUT FORMAT
─────────────
CandidateSignal if both checks pass.
None if either check fails (with a log line explaining which check blocked it).

REAL EXAMPLES THAT SHAPED THE SIGNAL QUALITY FILTER
─────────────────────────────────────────────────────

[A] MORNING CHOPPINESS — 2026-03-25 09:15 (pre-09:30)
  TRIDENT,  09:15:21, -₹1,310 loss
  NBCC,     09:15:21, -₹930  loss
  PNB,      09:15:21, -₹313  loss
  Root cause: user clicked Scan at 09:15 — auction residue creates extreme
  bid/ask ratios that look like OFI signals but are just opening chaos.
  Fix: the buildup check (sub-check d) requires the ratio to be present in
  3 of the last 5 ticks.  At 09:15:21 there ARE no previous ticks → filter
  correctly blocks all these entries.
  NOTE: These were scan-driven manual entries, not automatic monitoring entries.
  The monitoring auto-entry logic has ENTRY_WAIT_SECONDS (60s) which also helps.

[B] LODHA ICEBERG — 2026-03-25 09:32 (Trade 123)
  Observed: 2,143,500 bid vs 122,325 ask = 17.5x ratio
  Reality: hidden institutional SELLER was refreshing the ask queue.
  Evidence from tick sequence:
    Tick 1:  ask_qty = 106,000  (initial ask)
    Tick 10: ask_qty = 140,000  (ask GREW despite buyers lifting it → replenishment)
    Tick 20: ask_qty = 138,000  (still not shrinking)
    Tick 40: ask_qty = 128,000  (40 ticks of absorption, price fell ₹7)
  The iceberg suspect check (sub-check e) catches this: if ratio > 8x, price
  hasn't moved > 0.1%, and the weaker side (ask) hasn't shrunk by > 10% →
  flag as SUSPECT.  This is the "hidden seller absorbing buyer pressure" pattern.
  Reference: Xu, Gould & Howison (arXiv:1907.06230) on L1 OFI limitations
  with iceberg orders.

  NOTE: The iceberg check helps but is not perfect with L1 data only.  The
  full absorption pattern (how fast the ask is refreshed) requires L2 depth.
  Until we have that, we treat extreme-ratio + flat-price + non-shrinking-side
  as "suspicious, not confirmed" and defer to the Incubator's price confirmation.

[C] WHY SIGNAL_META IS CAPTURED HERE AND NOT IN ENTRY
  The scanner is the FIRST to see the full market context at signal-fire time.
  By the time Entry runs (after incubation, 60-120 seconds later), the
  bid/ask queues, OFI, and imbalance will have changed.
  We want ML training data to reflect the market at the moment a signal
  appeared — not the market at the moment we chose to act on it.
  So signal_meta is frozen in CandidateSignal and carried unchanged through
  to the trades table via ConfirmedSignal → Entry → DB.
"""

from datetime import datetime
from typing import Optional

from config import config
from core.logger import log_event
from strategy.signal import generate_signal
from strategy.signal_quality import check_signal_quality
from strategy.types import CandidateSignal
from strategy.gates.time_of_day import is_trading_blocked, get_threshold_multiplier
from strategy.gates.volume_spike import check_volume_spike
from strategy.gates.vwap_alignment import check_vwap_alignment
from strategy.gates.session_momentum import session_momentum_gate
from strategy.gates import nifty_alignment
from strategy.gates import cvd as cvd_gate


class Scanner:

    def evaluate(
        self,
        symbol: str,
        tick_data: dict,
        tick_history: list,
    ) -> Optional[CandidateSignal]:
        """
        Evaluate one tick for one symbol, applying all four gates.

        Parameters
        ──────────
        symbol      : NSE trading symbol
        tick_data   : Raw tick dict from Zerodha WebSocket
                      {ltp, bid_qty, ask_qty, volume, timestamp, ...}
        tick_history: Recent ticks newest-first from DB, up to 100 rows.
                      Used by generate_signal() for consistency window and
                      by check_signal_quality() for buildup + iceberg checks.

        Returns CandidateSignal or None.
        """
        bid_qty = tick_data.get("bid_qty", 0)
        ask_qty = tick_data.get("ask_qty", 0)
        current_price = tick_data.get("ltp", 0)

        # ── Gate 4a: Time-of-Day Block ───────────────────────────────────────
        # Block all entries during opening chaos (09:15-09:30).
        # Use tick timestamp if available, otherwise use system time
        tick_time = None
        if "timestamp" in tick_data and tick_data["timestamp"]:
            from datetime import datetime as dt_cls
            try:
                tick_time = dt_cls.fromtimestamp(tick_data["timestamp"])
            except (ValueError, OSError):
                pass  # Invalid timestamp, fall back to system time

        if is_trading_blocked(tick_time):
            log_event(
                "signal",
                f"{symbol} entry blocked: trading blocked during opening period (09:15-09:30)",
                symbol=symbol,
            )
            return None

        # ── Check 1: OFI signal ───────────────────────────────────────────────
        # generate_signal() internally calls:
        #   analyze_bid_ask()     → ratio + imbalance from latest tick
        #   monitor_for_entry()   → looks at ENTRY_WAIT_SECONDS window of ticks
        #                           and requires consistent ratio direction
        # Returns action='HOLD' if ratio below threshold or not consistent.
        signal = generate_signal(tick_history)
        if signal["action"] not in ("BUY", "SELL"):
            return None

        # ── Check 2: Signal quality filter ───────────────────────────────────
        # Snapshot mode (no tick_history) is used by the /scan endpoint.
        # Monitoring mode (with tick_history) also runs the buildup + iceberg
        # sub-checks which require history to evaluate.
        sq = check_signal_quality(bid_qty, ask_qty, tick_history=tick_history)
        if not sq["passed"]:
            # Logged so the operator can see which check blocked a signal.
            # In practice the most common block is "thin book" on small-cap
            # stocks or "iceberg suspect" on stocks with extreme ratios + flat
            # price (e.g. LODHA in the 09:32 session).
            log_event(
                "signal",
                f"{symbol} SQ blocked: {sq['reason']}",
                symbol=symbol,
            )
            return None

        signal_action = signal["action"]

        # ── Conviction Gate 1: Session Momentum (action now known) ────────────
        if session_momentum_gate.blocks(symbol, signal_action):
            bias = session_momentum_gate.get_bias(symbol)
            log_event(
                "signal",
                f"{symbol} SESSION_MOMENTUM blocked: {signal_action} against session bias ({bias})",
                symbol=symbol,
            )
            return None

        # ── Conviction Gate 2: Nifty Alignment ───────────────────────────────
        if nifty_alignment.blocks(signal_action):
            nifty_dir = nifty_alignment.get_nifty_direction()
            log_event(
                "signal",
                f"{symbol} NIFTY_ALIGNMENT blocked: {signal_action} against Nifty direction ({nifty_dir})",
                symbol=symbol,
            )
            return None

        # ── Conviction Gate 3: CVD ────────────────────────────────────────────
        if cvd_gate.blocks(signal_action, tick_history):
            cvd_stats = cvd_gate.compute_cvd(tick_history)
            log_event(
                "signal",
                f"{symbol} CVD blocked: {signal_action} but actual prints "
                f"{cvd_stats['up_count']}↑ {cvd_stats['down_count']}↓ "
                f"({cvd_stats['direction']} pressure, opposite to signal)",
                symbol=symbol,
            )
            return None

        # ── Gate 4b: Time-of-Day Threshold Adjustment (lunch gate — BUG FIX) ─
        # During lunch (12:00-13:30), raise threshold by 50%.
        # BUG WAS HERE: signal["details"]["current_ratio"] doesn't exist —
        # the ratio is nested at signal["details"]["bid_ask"]["current_ratio"].
        threshold_multiplier = get_threshold_multiplier(tick_time)
        effective_threshold = config.BID_ASK_THRESHOLD_RATIO * threshold_multiplier

        # FIXED: correct path into the nested details structure
        bid_ask_analysis = signal.get("details", {}).get("bid_ask", {})
        raw_ratio = bid_ask_analysis.get("current_ratio", 0)

        if signal_action == "BUY":
            ratio_passes = raw_ratio >= effective_threshold
        else:  # SELL
            ratio_passes = raw_ratio <= (1 / effective_threshold)

        if not ratio_passes:
            multiplier_label = f" (lunch: {threshold_multiplier}x)" if threshold_multiplier > 1.0 else ""
            log_event(
                "signal",
                f"{symbol} threshold blocked: ratio {raw_ratio:.3f} "
                f"vs effective {effective_threshold:.2f}x{multiplier_label}",
                symbol=symbol,
            )
            return None

        # ── Gate 5: Volume Spike ──────────────────────────────────────────────
        # DISABLED - too restrictive, needs tuning on live data
        # vol_check = check_volume_spike(tick_data, tick_history)
        # if not vol_check["passed"]:
        #     log_event("signal", f"{symbol} volume blocked: {vol_check['reason']}", symbol=symbol)
        #     return None

        # ── Gate 6: VWAP Alignment ────────────────────────────────────────────
        # DISABLED - filtering out profitable trades, needs recalibration
        # vwap_check = check_vwap_alignment(signal["action"], current_price, tick_history)
        # if not vwap_check["passed"]:
        #     log_event("signal", f"{symbol} VWAP blocked: {vwap_check['reason']}", symbol=symbol)
        #     return None

        ratio = self._display_ratio(bid_qty, ask_qty, signal["action"])

        return CandidateSignal(
            symbol=symbol,
            action=signal["action"],
            trigger_price=current_price,
            ratio=ratio,
            bid_qty=bid_qty,
            ask_qty=ask_qty,
            signal_meta=self._build_meta(signal, tick_history, bid_qty, ask_qty),
        )

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _display_ratio(bid_qty: int, ask_qty: int, action: str) -> float:
        """
        Always return the dominant-side ratio >= 1 for readability in logs.

        Raw bid/ask ratio for a SELL signal is < 1 (e.g. 0.26x for ADANIPOWER).
        We flip it so logs show 3.85x instead of 0.26x.  This matches the
        format in the /scan endpoint and the activity log.

        ADANIPOWER 2026-03-25 example:
          bid_qty = 1,150,000, ask_qty = 3,990,000
          raw ratio = 1,150,000 / 3,990,000 = 0.288x (confusing in logs)
          display ratio = 3,990,000 / 1,150,000 = 3.47x (clear: sellers 3.5x)
        """
        if action == "BUY":
            return round(bid_qty / ask_qty, 2) if ask_qty > 0 else 99.0
        else:
            return round(ask_qty / bid_qty, 2) if bid_qty > 0 else 99.0

    @staticmethod
    def _build_meta(signal: dict, tick_history: list, bid_qty: int, ask_qty: int) -> dict:
        """
        Capture market context at signal-fire time for ML training data.
        Written to the trades table and never used for live trading decisions.

        Fields and why each matters for ML:
        ─────────────────────────────────────
        ratio              : raw bid/ask ratio (not display ratio).
                             Hypothesis: extreme ratios (>10x) may predict lower
                             win rate due to iceberg / thin-book artefacts.
                             LODHA Trade 123 had entry_ratio = 12.94x and was
                             immediately stopped — supports this hypothesis.

        ofi                : Order Flow Imbalance delta between last two ticks.
                             = (bid_qty[t] - bid_qty[t-1]) - (ask_qty[t] - ask_qty[t-1])
                             Positive = net buying pressure, negative = net selling.
                             LODHA Trade 123 had entry_ofi = 570 (weakly positive
                             despite 17.5x ratio — iceberg absorbing the flow).

        confidence         : Fraction of ticks in ENTRY_WAIT_SECONDS window that
                             showed the signal direction.  0.0-1.0.
                             Higher = more consistent signal.

        norm_imbalance     : (bid_qty - ask_qty) / (bid_qty + ask_qty)
                             Ranges -1 to +1; normalised so it's comparable
                             across stocks with different absolute queue sizes.

        minutes_since_open : Minutes since 09:15 IST.
                             Key ML feature: we expect win rate to vary by
                             time-of-day segment (see TODO.md time-of-day
                             segmentation analysis task).
                             09:15-09:30 (0-15 min): lowest expected win rate
                             10:00-12:30 (45-195 min): highest expected win rate
                             All three 09:15 losses on 2026-03-25 have
                             minutes_since_open = 0.
        """
        now = datetime.now()
        session_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
        minutes_since_open = max(0, int((now - session_open).total_seconds() / 60))

        total = bid_qty + ask_qty
        norm_imbalance = (bid_qty - ask_qty) / total if total > 0 else 0
        ratio_raw = bid_qty / ask_qty if ask_qty > 0 else 0

        # OFI: change in net order flow between last two ticks.
        # Requires >= 2 ticks in history; 0 if not enough history.
        ofi = 0
        if len(tick_history) >= 2:
            t0, t1 = tick_history[0], tick_history[1]  # newest, second-newest
            ofi = (
                (t0.get("bid_qty", 0) - t1.get("bid_qty", 0))
                - (t0.get("ask_qty", 0) - t1.get("ask_qty", 0))
            )

        return {
            "ratio": round(ratio_raw, 4),
            "ofi": ofi,
            "confidence": round(signal.get("confidence", 0), 4),
            "norm_imbalance": round(norm_imbalance, 4),
            "minutes_since_open": minutes_since_open,
        }
