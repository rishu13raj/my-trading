"""
strategy/gates/volume_spike.py — Volume Spike Confirmation Gate

PURPOSE
───────
Filters signals that lack volume backing. High-conviction price moves should be
accompanied by elevated volume, confirming institutional participation.

RESEARCH BACKING
────────────────
Academic findings:
  - Breakouts with 2-3x normal volume have significantly higher success rates
  - Low-volume breakouts are often false breakouts (lack of conviction)
  - Volume spikes indicate institutional order absorption
  - Multi-timeframe volume confirmation strengthens signal reliability

IMPLEMENTATION
───────────────
Requires current tick volume to exceed the average of the past VOLUME_SPIKE_WINDOW
ticks by at least VOLUME_SPIKE_MULTIPLIER.

Example:
  Average volume (last 20 ticks): 50,000 shares
  Current tick volume: 150,000 shares
  Ratio: 150,000 / 50,000 = 3.0x
  If VOLUME_SPIKE_MULTIPLIER = 2.5: PASS (3.0 >= 2.5)
"""

from config import config


def check_volume_spike(tick_data: dict, tick_history: list) -> dict:
    """
    Check if current tick volume represents a spike relative to recent average.

    Parameters
    ──────────
    tick_data    : Current tick {volume, ...}
    tick_history : Recent ticks newest-first from DB

    Returns dict with:
      'passed'     : bool — True if volume spike detected
      'reason'     : str — If failed, reason why
      'multiplier' : float — Actual volume multiplier (current / avg)
    """
    current_volume = tick_data.get("volume", 0)

    # Need at least one prior tick to calculate average
    if not tick_history or len(tick_history) < 2:
        # Not enough history — assume spike (benefit of doubt at market open)
        return {
            "passed": True,
            "reason": "insufficient history",
            "multiplier": float("inf"),
        }

    # Calculate average volume from recent window
    window_size = min(config.VOLUME_SPIKE_WINDOW, len(tick_history) - 1)
    recent_ticks = tick_history[:window_size]
    avg_volume = sum(t.get("volume", 0) for t in recent_ticks) / len(recent_ticks)

    # Avoid division by zero (shouldn't happen but defensive)
    if avg_volume <= 0:
        return {
            "passed": True,
            "reason": "zero average volume",
            "multiplier": float("inf"),
        }

    multiplier = current_volume / avg_volume

    if multiplier >= config.VOLUME_SPIKE_MULTIPLIER:
        return {
            "passed": True,
            "reason": None,
            "multiplier": round(multiplier, 2),
        }
    else:
        return {
            "passed": False,
            "reason": f"volume spike: {multiplier:.2f}x (need {config.VOLUME_SPIKE_MULTIPLIER}x)",
            "multiplier": round(multiplier, 2),
        }
