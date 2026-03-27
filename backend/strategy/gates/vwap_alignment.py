"""
strategy/gates/vwap_alignment.py — VWAP Alignment Gate

PURPOSE
───────
Filters signals that conflict with the day's price-vs-VWAP flow. VWAP is the
institutional fair-value benchmark; trading AGAINST it often fails.

RESEARCH BACKING
────────────────
VWAP is the standard intraday tool used by:
  - Execution desks measuring trade quality (buy below, sell above VWAP)
  - Institutional investors planning entry/exit around VWAP
  - Momentum strategies confirming direction alignment

Key insight:
  - Price > VWAP: buyers in control, favors BUY signals
  - Price < VWAP: sellers in control, favors SELL signals
  - Trading AGAINST VWAP is swimming upstream (lower win rate)

IMPLEMENTATION
───────────────
VWAP = Σ(price × volume) / Σ(volume) over the past VWAP_WINDOW ticks.

Signal filtering:
  - BUY signal: only take if current_price > VWAP (price above fair value)
  - SELL signal: only take if current_price < VWAP (price below fair value)
"""

from config import config


def calculate_vwap(tick_history: list) -> float:
    """
    Calculate Volume Weighted Average Price over the recent window.

    Parameters
    ──────────
    tick_history : Recent ticks newest-first from DB

    Returns the VWAP value, or 0 if insufficient data.
    """
    if not tick_history:
        return 0

    # Use up to VWAP_WINDOW ticks (newest first)
    window_size = min(config.VWAP_WINDOW, len(tick_history))
    window = tick_history[:window_size]

    total_pv = 0  # price × volume
    total_v = 0  # volume

    for tick in window:
        price = tick.get("ltp", 0)
        volume = tick.get("volume", 0)
        if price > 0 and volume > 0:
            total_pv += price * volume
            total_v += volume

    if total_v == 0:
        return 0

    return total_pv / total_v


def check_vwap_alignment(action: str, current_price: float, tick_history: list) -> dict:
    """
    Check if signal direction aligns with price-vs-VWAP positioning.

    Parameters
    ──────────
    action       : 'BUY' or 'SELL'
    current_price: Current LTP
    tick_history : Recent ticks for VWAP calculation

    Returns dict with:
      'passed'  : bool — True if aligned
      'reason'  : str — If failed, reason why
      'vwap'    : float — Calculated VWAP
      'aligned' : bool — True if price/action alignment is correct
    """
    # If VWAP gate is disabled, always pass
    if not config.VWAP_ALIGNMENT_ENABLED:
        return {
            "passed": True,
            "reason": "vwap gate disabled",
            "vwap": 0,
            "aligned": True,
        }

    # Not enough history for VWAP calculation
    if not tick_history or len(tick_history) < 5:
        return {
            "passed": True,
            "reason": "insufficient history for vwap",
            "vwap": 0,
            "aligned": True,
        }

    vwap = calculate_vwap(tick_history)
    if vwap == 0:
        return {
            "passed": True,
            "reason": "vwap calculation failed",
            "vwap": 0,
            "aligned": True,
        }

    # Check alignment
    if action == "BUY":
        aligned = current_price > vwap
        reason = (
            f"VWAP misalignment: BUY signal but price ₹{current_price:.2f} < VWAP ₹{vwap:.2f}"
            if not aligned
            else None
        )
    else:  # SELL
        aligned = current_price < vwap
        reason = (
            f"VWAP misalignment: SELL signal but price ₹{current_price:.2f} > VWAP ₹{vwap:.2f}"
            if not aligned
            else None
        )

    return {
        "passed": aligned,
        "reason": reason,
        "vwap": round(vwap, 2),
        "aligned": aligned,
    }
