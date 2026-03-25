"""
Phase 1 — Scanner

Evaluates each tick for an actionable OFI signal.

Checks applied (in order):
  1. OFI ratio threshold + entry confirmation  (generate_signal)
  2. Signal quality filter — thin book, extreme ratio, iceberg, buildup
     (check_signal_quality)

Output: CandidateSignal if both checks pass, None otherwise.
The Scanner is stateless — it produces a fresh judgment on every tick.
"""

from datetime import datetime
from typing import Optional

from config import config
from core.logger import log_event
from strategy.signal import generate_signal
from strategy.signal_quality import check_signal_quality
from strategy.types import CandidateSignal


class Scanner:
    def evaluate(
        self,
        symbol: str,
        tick_data: dict,
        tick_history: list,
    ) -> Optional[CandidateSignal]:
        bid_qty = tick_data.get("bid_qty", 0)
        ask_qty = tick_data.get("ask_qty", 0)
        current_price = tick_data.get("ltp", 0)

        # OFI signal from tick history
        signal = generate_signal(tick_history)
        if signal["action"] not in ("BUY", "SELL"):
            return None

        # Signal quality filter
        sq = check_signal_quality(bid_qty, ask_qty, tick_history=tick_history)
        if not sq["passed"]:
            log_event(
                "signal",
                f"{symbol} SQ blocked: {sq['reason']}",
                symbol=symbol,
            )
            return None

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

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _display_ratio(bid_qty: int, ask_qty: int, action: str) -> float:
        """Always return the dominant-side ratio (>= 1) for readability."""
        if action == "BUY":
            return round(bid_qty / ask_qty, 2) if ask_qty > 0 else 99.0
        else:
            return round(ask_qty / bid_qty, 2) if bid_qty > 0 else 99.0

    @staticmethod
    def _build_meta(signal: dict, tick_history: list, bid_qty: int, ask_qty: int) -> dict:
        """Capture market context at signal-fire time for ML training data."""
        now = datetime.now()
        session_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
        minutes_since_open = max(0, int((now - session_open).total_seconds() / 60))

        total = bid_qty + ask_qty
        norm_imbalance = (bid_qty - ask_qty) / total if total > 0 else 0
        ratio_raw = bid_qty / ask_qty if ask_qty > 0 else 0

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
