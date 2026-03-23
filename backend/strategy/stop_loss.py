from config import config
from typing import Dict

def calculate_stop_loss(entry_price: float, direction: str, pct: float = None) -> float:
    """
    Calculate stop loss price for a trade

    Args:
        entry_price: Price at entry
        direction: 'BUY' or 'SELL'
        pct: Stop loss percentage (default from config)

    Returns:
        Stop loss price
    """
    if pct is None:
        pct = config.STOP_LOSS_PCT

    if direction == 'BUY':
        # For buy, stop loss is below entry price
        stop_price = entry_price * (1 - pct)
    else:  # SELL
        # For sell, stop loss is above entry price
        stop_price = entry_price * (1 + pct)

    return round(stop_price, 2)


def is_stop_loss_hit(current_price: float, stop_loss_price: float, direction: str) -> bool:
    """
    Check if stop loss has been triggered

    Args:
        current_price: Current market price
        stop_loss_price: Stop loss price
        direction: 'BUY' or 'SELL'

    Returns:
        True if stop loss is hit
    """
    if direction == 'BUY':
        # For buy, stop loss hit when price goes below stop price
        return current_price <= stop_loss_price
    else:  # SELL
        # For sell, stop loss hit when price goes above stop price
        return current_price >= stop_loss_price


def calculate_loss_pct(entry_price: float, current_price: float, direction: str) -> float:
    """
    Calculate current loss percentage

    Args:
        entry_price: Entry price
        current_price: Current price
        direction: 'BUY' or 'SELL'

    Returns:
        Loss percentage (negative = loss, positive = profit)
    """
    if entry_price == 0:
        return 0

    if direction == 'BUY':
        pct = (current_price - entry_price) / entry_price
    else:  # SELL
        pct = (entry_price - current_price) / entry_price

    return round(pct * 100, 2)


def get_stop_loss_status(current_price: float, stop_loss_price: float, entry_price: float, direction: str) -> Dict:
    """
    Get comprehensive stop loss status

    Args:
        current_price: Current market price
        stop_loss_price: Stop loss price
        entry_price: Entry price
        direction: 'BUY' or 'SELL'

    Returns:
        Dictionary with stop loss status
    """
    is_hit = is_stop_loss_hit(current_price, stop_loss_price, direction)
    loss_pct = calculate_loss_pct(entry_price, current_price, direction)

    if direction == 'BUY':
        distance_to_stop = current_price - stop_loss_price
    else:
        distance_to_stop = stop_loss_price - current_price

    return {
        'current_price': current_price,
        'stop_loss_price': stop_loss_price,
        'is_hit': is_hit,
        'loss_pct': loss_pct,
        'distance_to_stop': round(distance_to_stop, 2),
        'status': 'STOP_LOSS_HIT' if is_hit else 'OK'
    }
