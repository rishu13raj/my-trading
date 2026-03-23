from typing import List, Dict, Tuple
from config import config
from strategy.bid_ask_ratio import calculate_ratio
import time

def detect_direction(tick_history: List[Dict]) -> str:
    """
    Detect momentum direction from tick history

    Args:
        tick_history: List of tick dictionaries with bid_qty, ask_qty

    Returns:
        'BUY' if uptrend (more buyers), 'SELL' if downtrend (more sellers), 'NEUTRAL'
    """
    if not tick_history:
        return 'NEUTRAL'

    # Look at recent ticks to determine trend
    if len(tick_history) < 3:
        latest = tick_history[0]  # newest first
        bid_qty = latest.get('bid_qty', 0)
        ask_qty = latest.get('ask_qty', 0)
        ratio = calculate_ratio(bid_qty, ask_qty)

        if ratio > config.BID_ASK_THRESHOLD_RATIO:
            return 'BUY'
        elif ratio < (1 / config.BID_ASK_THRESHOLD_RATIO):
            return 'SELL'
        return 'NEUTRAL'

    # Analyze most recent 3 ticks (index 0,1,2 = newest)
    ratios = [calculate_ratio(tick.get('bid_qty', 0), tick.get('ask_qty', 0))
              for tick in tick_history[:3]]

    avg_ratio = sum(ratios) / len(ratios)

    if avg_ratio > config.BID_ASK_THRESHOLD_RATIO:
        return 'BUY'
    elif avg_ratio < (1 / config.BID_ASK_THRESHOLD_RATIO):
        return 'SELL'
    else:
        return 'NEUTRAL'


def calculate_rate_of_change(tick_history: List[Dict], window_seconds: int = None) -> float:
    """
    Calculate rate of change of bid-ask ratio over time window

    Args:
        tick_history: List of tick dictionaries
        window_seconds: Time window to analyze (default from config)

    Returns:
        Rate of change (positive = increasing buyers, negative = decreasing buyers)
    """
    if window_seconds is None:
        window_seconds = config.MOMENTUM_WINDOW_SECONDS

    if not tick_history or len(tick_history) < 2:
        return 0

    # Get ticks within the window
    latest_timestamp = tick_history[0].get('timestamp', 0)  # newest first
    window_start = latest_timestamp - window_seconds

    window_ticks = [tick for tick in tick_history
                    if tick.get('timestamp', 0) >= window_start]

    if len(window_ticks) < 2:
        return 0

    # window_ticks[0] = newest, window_ticks[-1] = oldest in window
    first_ratio = calculate_ratio(
        window_ticks[-1].get('bid_qty', 0),  # oldest in window
        window_ticks[-1].get('ask_qty', 0)
    )
    last_ratio = calculate_ratio(
        window_ticks[0].get('bid_qty', 0),   # newest in window
        window_ticks[0].get('ask_qty', 0)
    )

    if first_ratio == 0:
        return 0

    rate = (last_ratio - first_ratio) / first_ratio
    return rate


def is_momentum_slowing(tick_history: List[Dict], window_seconds: int = None) -> bool:
    """
    Check if momentum is slowing down (rate of change decreasing)

    Args:
        tick_history: List of tick dictionaries
        window_seconds: Time window to analyze

    Returns:
        True if momentum is slowing
    """
    if window_seconds is None:
        window_seconds = config.MOMENTUM_WINDOW_SECONDS

    if len(tick_history) < 4:
        return False

    # Split history into two halves
    mid = len(tick_history) // 2
    first_half = tick_history[:mid]
    second_half = tick_history[mid:]

    rate_first = calculate_rate_of_change(first_half, window_seconds)
    rate_second = calculate_rate_of_change(second_half, window_seconds)

    # Momentum is slowing if the recent rate has dropped significantly below the older rate.
    # rate_first = recent window, rate_second = older window.
    # Only fire if deceleration exceeds threshold — filters out tick-to-tick noise.
    deceleration = rate_second - rate_first
    return deceleration > config.MOMENTUM_REVERSAL_THRESHOLD


def monitor_for_entry(tick_history: List[Dict], duration_seconds: int = None) -> Dict:
    """
    Monitor ticks for entry confirmation over specified duration

    Args:
        tick_history: List of tick dictionaries
        duration_seconds: Duration to monitor (default from config)

    Returns:
        Dictionary with entry confirmation details
    """
    if duration_seconds is None:
        duration_seconds = config.ENTRY_WAIT_SECONDS

    if not tick_history:
        return {
            'can_enter': False,
            'direction': 'NEUTRAL',
            'confidence': 0,
            'reason': 'No tick history'
        }

    # Get ticks within the monitoring window
    latest_timestamp = tick_history[0].get('timestamp', 0)  # newest first
    window_start = latest_timestamp - duration_seconds

    window_ticks = [tick for tick in tick_history
                    if tick.get('timestamp', 0) >= window_start]

    if len(window_ticks) < 3:
        return {
            'can_enter': False,
            'direction': 'NEUTRAL',
            'confidence': 0,
            'reason': 'Not enough ticks in window'
        }

    # Check consistency of signal
    direction_signals = []
    for tick in window_ticks:
        ratio = calculate_ratio(tick.get('bid_qty', 0), tick.get('ask_qty', 0))
        if ratio > config.BID_ASK_THRESHOLD_RATIO:
            direction_signals.append('BUY')
        elif ratio < (1 / config.BID_ASK_THRESHOLD_RATIO):
            direction_signals.append('SELL')
        else:
            direction_signals.append('NEUTRAL')

    # Determine dominant signal
    buy_count = direction_signals.count('BUY')
    sell_count = direction_signals.count('SELL')
    neutral_count = direction_signals.count('NEUTRAL')

    if buy_count > sell_count and buy_count > neutral_count:
        direction = 'BUY'
        confidence = buy_count / len(direction_signals)
        can_enter = True
    elif sell_count > buy_count and sell_count > neutral_count:
        direction = 'SELL'
        confidence = sell_count / len(direction_signals)
        can_enter = True
    else:
        direction = 'NEUTRAL'
        confidence = 0
        can_enter = False

    return {
        'can_enter': can_enter,
        'direction': direction,
        'confidence': confidence,
        'reason': f'{direction} with {confidence:.2%} confidence'
    }
