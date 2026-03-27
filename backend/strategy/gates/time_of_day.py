"""
strategy/gates/time_of_day.py — Time-of-Day Trading Gate

TWO ALLOWED SESSIONS ONLY
──────────────────────────
Based on 3-day analysis (Mar 24, 25, 27) of actual trade P&L by 30-min bucket:

  Morning session   09:45–10:45  → positive all 3 days, most consistent window
  Afternoon session 13:15–15:30  → strong on Mar 24 (+₹4,492) and Mar 27 (+₹4,372)

Dead zone 10:45–13:15 (-₹4,871 gross across all 3 days):
  10:45–11:15  too few trades to confirm
  11:15–11:45  negative all 3 days (worst bucket: -₹3,880)
  11:45–12:45  looks positive but driven by EASEMYTRIP outlier + single JUBLFOOD trade
  12:45–13:15  12.5% win rate across 8 trades

Everything outside the two sessions is hard-blocked.
No threshold multiplier needed — we just don't trade outside the windows.
"""

from datetime import datetime
from config import config


def _hhmm(now: datetime) -> int:
    return now.hour * 100 + now.minute


def is_trading_blocked(now: datetime = None) -> bool:
    """
    Returns True if the current time is outside both allowed trading sessions.

    Allowed windows (from config):
      Morning   : MORNING_SESSION_START   – MORNING_SESSION_END
      Afternoon : AFTERNOON_SESSION_START – AFTERNOON_SESSION_END

    Everything else is blocked.
    """
    if now is None:
        now = datetime.now()

    t = _hhmm(now)
    morning_start   = int(config.MORNING_SESSION_START)
    morning_end     = int(config.MORNING_SESSION_END)
    afternoon_start = int(config.AFTERNOON_SESSION_START)
    afternoon_end   = int(config.AFTERNOON_SESSION_END)

    in_morning   = morning_start   <= t < morning_end
    in_afternoon = afternoon_start <= t < afternoon_end

    return not (in_morning or in_afternoon)


def get_time_of_day_window(now: datetime = None) -> str:
    """
    Returns the current session name for logging.

    'MORNING'   : 09:45–10:45 (allowed)
    'AFTERNOON' : 13:15–15:30 (allowed)
    'BLOCKED'   : everything else
    """
    if now is None:
        now = datetime.now()

    t = _hhmm(now)
    morning_start   = int(config.MORNING_SESSION_START)
    morning_end     = int(config.MORNING_SESSION_END)
    afternoon_start = int(config.AFTERNOON_SESSION_START)
    afternoon_end   = int(config.AFTERNOON_SESSION_END)

    if morning_start <= t < morning_end:
        return 'MORNING'
    elif afternoon_start <= t < afternoon_end:
        return 'AFTERNOON'
    else:
        return 'BLOCKED'


def get_threshold_multiplier(now: datetime = None) -> float:
    """
    Returns 1.0 always — threshold multiplier removed.
    We no longer need a soft raise during lunch because 10:45–13:15 is hard-blocked.
    Kept for API compatibility with scanner.py.
    """
    return 1.0
