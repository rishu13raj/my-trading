"""
strategy/gates/time_of_day.py — Time-of-Day Trading Gate

PURPOSE
───────
Blocks or softens signals during low-conviction periods:
  - Completely blocks entries 09:15-09:30 (opening chaos, high noise)
  - Raises BID_ASK threshold by 50% during 12:00-13:30 (lunch, lower liquidity)
  - Favors mid-morning (10:00-12:00) and afternoon (13:30+) windows

RESEARCH BACKING
────────────────
Academic findings show:
  1. Opening spreads much higher than rest of day (Stoll & Whaley)
  2. Volume-driven volatility patterns: U-shaped intraday curve
  3. Early-day momentum (09:15-11:00) carries signals; late-day dissipates
  4. Lunch period shows lowest volume and widest spreads
"""

from datetime import datetime
from config import config


def get_time_of_day_window(now: datetime = None) -> str:
    """
    Return the current trading window based on market hours.

    Parameters
    ──────────
    now : datetime
      The time to evaluate. If None, uses current time (or simulated time in backtest).

    Returns one of:
      'OPENING'    : 09:15-09:30 (blocked)
      'MORNING'    : 09:30-12:00 (full signal strength)
      'LUNCH'      : 12:00-13:30 (raised threshold)
      'AFTERNOON'  : 13:30-15:30 (full signal strength)
    """
    if now is None:
        now = datetime.now()
    hour = now.hour
    minute = now.minute
    hhmm = hour * 100 + minute

    opening_end = int(config.TRADING_BLOCK_END)
    lunch_start = int(config.LUNCH_BLOCK_START)
    lunch_end = int(config.LUNCH_BLOCK_END)

    if hhmm < opening_end:  # before 09:30
        return 'OPENING'
    elif hhmm < lunch_start:  # before 12:00
        return 'MORNING'
    elif hhmm < lunch_end:  # before 13:30
        return 'LUNCH'
    else:
        return 'AFTERNOON'


def is_trading_blocked(now: datetime = None) -> bool:
    """
    Check if trading is blocked due to time-of-day (opening period).

    Parameters
    ──────────
    now : datetime
      The time to evaluate. If None, uses current time (or simulated time in backtest).

    Returns True if within TRADING_BLOCK_START to TRADING_BLOCK_END.
    """
    if now is None:
        now = datetime.now()
    hour = now.hour
    minute = now.minute
    hhmm = hour * 100 + minute

    block_start = int(config.TRADING_BLOCK_START)
    block_end = int(config.TRADING_BLOCK_END)

    return block_start <= hhmm < block_end


def get_threshold_multiplier(now: datetime = None) -> float:
    """
    Get the BID_ASK_THRESHOLD_RATIO multiplier for current time window.

    Parameters
    ──────────
    now : datetime
      The time to evaluate. If None, uses current time (or simulated time in backtest).

    During lunch (12:00-13:30): raises threshold by 50% (require stronger signal)
    Other times: multiplier = 1.0 (normal threshold)

    Example:
      Normal BID_ASK=1.6x
      During lunch: effective threshold = 1.6 * 1.5 = 2.4x
    """
    window = get_time_of_day_window(now)

    if window == 'LUNCH':
        return config.LUNCH_THRESHOLD_RATIO_MULTIPLIER
    else:
        return 1.0
