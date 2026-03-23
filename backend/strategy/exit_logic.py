from datetime import datetime, timedelta
from typing import Dict, Tuple
from config import config
from strategy.momentum import is_momentum_slowing
from strategy.stop_loss import is_stop_loss_hit
import time

def is_time_exit(entry_time, max_duration_minutes: int = None) -> bool:
    """
    Check if trade should exit based on time

    Args:
        entry_time: Entry timestamp (int/float), ISO string, or datetime
        max_duration_minutes: Maximum hold duration (default from config)

    Returns:
        True if max duration has exceeded
    """
    if max_duration_minutes is None:
        max_duration_minutes = config.TIME_EXIT_MINUTES

    if isinstance(entry_time, (int, float)):
        entry_dt = datetime.fromtimestamp(entry_time)
    elif isinstance(entry_time, str):
        entry_dt = datetime.fromisoformat(entry_time)
    else:
        entry_dt = entry_time  # already a datetime

    now = datetime.now()
    duration = (now - entry_dt).total_seconds() / 60

    return duration >= max_duration_minutes


def is_momentum_reversal(tick_history: list, direction: str) -> bool:
    """
    Check if momentum has reversed from entry direction

    Args:
        tick_history: List of tick dictionaries
        direction: Entry direction ('BUY' or 'SELL')

    Returns:
        True if momentum has reversed
    """
    if not tick_history or len(tick_history) < 3:
        return False

    return is_momentum_slowing(tick_history)


def should_exit(position: Dict, tick_history: list, current_price: float) -> Tuple[bool, str]:
    """
    Determine if a position should be exited

    Args:
        position: Trade position dict (id, symbol, direction, entry_price, entry_time, stop_loss_price)
        tick_history: List of tick dictionaries
        current_price: Current market price

    Returns:
        Tuple (should_exit: bool, reason: str)
    """
    # Check 1: Stop loss (always active)
    if is_stop_loss_hit(current_price, position['stop_loss_price'], position['direction']):
        return True, f"STOP_LOSS (price: {current_price} vs stop: {position['stop_loss_price']})"

    # Calculate hold duration
    et = position['entry_time']
    if isinstance(et, (int, float)):
        entry_dt = datetime.fromtimestamp(et)
    elif isinstance(et, str):
        entry_dt = datetime.fromisoformat(et)
    else:
        entry_dt = et
    held_seconds = (datetime.now() - entry_dt).total_seconds()

    # Check 2: Time-based exit
    if is_time_exit(position['entry_time']):
        return True, f"TIME_EXIT ({config.TIME_EXIT_MINUTES} minutes exceeded)"

    # Check 3: Momentum reversal — only after minimum hold time to prevent flip-flop exits
    MIN_HOLD_SECONDS = 60
    if held_seconds >= MIN_HOLD_SECONDS and is_momentum_reversal(tick_history, position['direction']):
        return True, "MOMENTUM_REVERSAL"

    return False, "HOLD"


def get_exit_analysis(position: Dict, tick_history: list, current_price: float) -> Dict:
    """
    Get detailed exit analysis for a position

    Args:
        position: Trade position dict
        tick_history: List of tick dictionaries
        current_price: Current market price

    Returns:
        Dictionary with exit analysis
    """
    should_exit_flag, reason = should_exit(position, tick_history, current_price)

    # Calculate entry time duration
    et = position['entry_time']
    if isinstance(et, (int, float)):
        entry_dt = datetime.fromtimestamp(et)
    elif isinstance(et, str):
        entry_dt = datetime.fromisoformat(et)
    else:
        entry_dt = et

    duration_minutes = (datetime.now() - entry_dt).total_seconds() / 60

    return {
        'should_exit': should_exit_flag,
        'exit_reason': reason,
        'duration_minutes': round(duration_minutes, 2),
        'current_price': current_price,
        'entry_price': position['entry_price'],
        'stop_loss_price': position['stop_loss_price'],
        'momentum_slowing': is_momentum_reversal(tick_history, position['direction']),
        'time_exceeded': duration_minutes >= config.TIME_EXIT_MINUTES
    }
