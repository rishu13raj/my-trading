"""
strategy/gates/nifty_alignment.py — Nifty Index Alignment Gate

THE PROBLEM THIS SOLVES
────────────────────────
Individual stock OFI signals have materially lower follow-through when the
broad market (Nifty) is moving in the opposite direction. If Nifty is trending
up, SELL signals on constituent stocks are fighting institutional rebalancing,
ETF inflows, and index arbitrage — all of which push stocks up regardless of
momentary order book imbalance.

Academic basis:
- Lead-lag research on NSE (Journal of Prediction Markets, 2017): index futures
  movements lead individual stock movements for macro-driven sessions.
- Beta transmission: high-beta PSU/infra stocks amplify index direction within
  15-30 minute windows. A SELL signal on NHPC during a Nifty rally is a low
  conviction trade by construction.

HOW IT WORKS
────────────
Reads the last NIFTY_ALIGNMENT_WINDOW_MINS minutes of NIFTY 50 ticks from the
DB (stored as symbol NIFTY_SYMBOL by the WebSocket client).

Computes: (current_nifty - nifty_N_min_ago) / nifty_N_min_ago

If Nifty is UP > NIFTY_ALIGNMENT_THRESHOLD_PCT (0.15%): block SELL entries.
If Nifty is DOWN > threshold: block BUY entries.
If within threshold OR no data yet: pass (benefit of the doubt).

No data = pass because:
- Market open: Nifty ticks not yet in DB
- New session: first few minutes of data
- Don't block valid early signals just because Nifty subscription isn't warmed up.
"""

from config import config
from data.database import db


def get_nifty_direction(as_of_ts: int = None) -> str:
    """
    Returns 'UP', 'DOWN', or 'NEUTRAL' based on Nifty's recent trend.
    'NEUTRAL' also covers the no-data case (benefit of the doubt).

    as_of_ts: Unix timestamp upper bound. When provided (backtest mode),
              only ticks up to this time are considered. None = live mode
              (uses most recent ticks in DB).
    """
    ticks = db.get_recent_ticks(
        config.NIFTY_SYMBOL,
        limit=config.NIFTY_ALIGNMENT_WINDOW_MINS * 10,  # ~10 ticks/min
        as_of_ts=as_of_ts,
    )
    if len(ticks) < 5:
        return "NEUTRAL"  # not enough data — don't block

    current = ticks[0]["ltp"]

    # Find the tick closest to N minutes ago
    if not ticks[0].get("timestamp"):
        return "NEUTRAL"
    cutoff_ts = ticks[0]["timestamp"] - (config.NIFTY_ALIGNMENT_WINDOW_MINS * 60)
    old_ticks = [t for t in ticks if t["timestamp"] <= cutoff_ts]
    if not old_ticks:
        return "NEUTRAL"  # window not yet populated

    past = old_ticks[0]["ltp"]
    if past == 0:
        return "NEUTRAL"

    change_pct = (current - past) / past

    if change_pct > config.NIFTY_ALIGNMENT_THRESHOLD_PCT:
        return "UP"
    elif change_pct < -config.NIFTY_ALIGNMENT_THRESHOLD_PCT:
        return "DOWN"
    return "NEUTRAL"


def blocks(action: str, as_of_ts: int = None) -> bool:
    """
    Returns True if the current Nifty direction blocks the given action.
    SELL blocked when Nifty is rising. BUY blocked when Nifty is falling.

    as_of_ts: passed through to get_nifty_direction for backtest mode.
    """
    direction = get_nifty_direction(as_of_ts=as_of_ts)
    if direction == "UP" and action == "SELL":
        return True
    if direction == "DOWN" and action == "BUY":
        return True
    return False
