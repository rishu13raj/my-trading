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


def should_exit(position: Dict, tick_history: list, current_price: float, peak_price: float = None) -> Tuple[bool, str]:
    """
    Determine if a position should be exited.

    peak_price: Best price seen since entry (highest for BUY, lowest for SELL).
                Passed in from main.py which tracks it per position in memory.
    """
    direction = position['direction']
    entry_price = position['entry_price']

    # Check 1: Stop loss (always active)
    if is_stop_loss_hit(current_price, position['stop_loss_price'], direction):
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
    held_minutes = held_seconds / 60

    # Check 2a: Early profit-protection trailing stop
    # Activates the moment profit crosses PROFIT_TRAIL_ACTIVATION_PCT — no time gate.
    # Protects against giving back a meaningful gain while still letting small fluctuations ride.
    if peak_price is not None:
        if direction == 'BUY':
            peak_gain = peak_price - entry_price
            activation_threshold = entry_price * config.PROFIT_TRAIL_ACTIVATION_PCT
            if peak_gain >= activation_threshold:
                trail_stop = entry_price + peak_gain * (1 - config.TRAIL_STOP_RETRACEMENT)
                if current_price <= trail_stop:
                    return True, f"PROFIT_TRAIL (retraced {config.TRAIL_STOP_RETRACEMENT*100:.0f}% of peak gain ₹{peak_gain:.2f})"
        else:  # SELL
            peak_gain = entry_price - peak_price
            activation_threshold = entry_price * config.PROFIT_TRAIL_ACTIVATION_PCT
            if peak_gain >= activation_threshold:
                trail_stop = entry_price - peak_gain * (1 - config.TRAIL_STOP_RETRACEMENT)
                if current_price >= trail_stop:
                    return True, f"PROFIT_TRAIL (retraced {config.TRAIL_STOP_RETRACEMENT*100:.0f}% of peak gain ₹{peak_gain:.2f})"

    # Check 2b: Smart time exit — split on profit/loss
    if held_minutes >= config.TIME_EXIT_MINUTES:
        # Calculate current pnl
        if direction == 'BUY':
            pnl = (current_price - entry_price) * position.get('entry_qty', 1)
        else:
            pnl = (entry_price - current_price) * position.get('entry_qty', 1)

        if pnl <= 0:
            # Losing trade — exit immediately at time limit
            return True, f"TIME_EXIT_LOSS ({config.TIME_EXIT_MINUTES}m, still losing)"

        # Profitable trade — activate trailing stop instead of hard exit
        if peak_price is not None:
            if direction == 'BUY':
                peak_gain = peak_price - entry_price
                if peak_gain > 0:
                    trail_stop = entry_price + peak_gain * (1 - config.TRAIL_STOP_RETRACEMENT)
                    if current_price <= trail_stop:
                        return True, f"TRAIL_STOP (retraced {config.TRAIL_STOP_RETRACEMENT*100:.0f}% of peak gain ₹{peak_gain:.2f})"
            else:  # SELL
                peak_gain = entry_price - peak_price
                if peak_gain > 0:
                    trail_stop = entry_price - peak_gain * (1 - config.TRAIL_STOP_RETRACEMENT)
                    if current_price >= trail_stop:
                        return True, f"TRAIL_STOP (retraced {config.TRAIL_STOP_RETRACEMENT*100:.0f}% of peak gain ₹{peak_gain:.2f})"

    # Check 3: Momentum reversal — only after minimum hold time
    MIN_HOLD_SECONDS = 60
    if held_seconds >= MIN_HOLD_SECONDS and is_momentum_reversal(tick_history, direction):
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
