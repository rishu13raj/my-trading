from config import config
from typing import Dict, Tuple

def calculate_ratio(bid_qty: int, ask_qty: int) -> float:
    """
    Calculate bid-ask ratio (buyers to sellers)

    Args:
        bid_qty: Number of buyers
        ask_qty: Number of sellers

    Returns:
        Ratio of bid to ask (e.g., 3.0 means 3x more buyers than sellers)
    """
    if ask_qty == 0:
        return float('inf') if bid_qty > 0 else 0
    return bid_qty / ask_qty


def calculate_imbalance_pct(bid_qty: int, ask_qty: int) -> float:
    """
    Calculate bid-ask imbalance as percentage

    Args:
        bid_qty: Number of buyers
        ask_qty: Number of sellers

    Returns:
        Imbalance percentage (positive = more buyers, negative = more sellers)
    """
    total = bid_qty + ask_qty
    if total == 0:
        return 0

    imbalance = (bid_qty - ask_qty) / total * 100
    return imbalance


def is_significant(ratio: float, threshold: float = None) -> bool:
    """
    Check if bid-ask ratio is significant enough for trading — either direction.
    BUY: ratio >= threshold (e.g. 2.0x — buyers dominate)
    SELL: ratio <= 1/threshold (e.g. 0.5x — sellers dominate)
    """
    if threshold is None:
        threshold = config.BID_ASK_THRESHOLD_RATIO

    return ratio >= threshold or ratio <= (1 / threshold)


def get_signal_from_ratio(ratio: float, current_imbalance: float) -> str:
    """
    Determine trading signal from bid-ask ratio

    Args:
        ratio: Bid-ask ratio
        current_imbalance: Current imbalance percentage

    Returns:
        'BUY' if more buyers, 'SELL' if more sellers, 'NEUTRAL' if balanced
    """
    if ratio > config.BID_ASK_THRESHOLD_RATIO:
        return 'BUY'
    elif ratio < (1 / config.BID_ASK_THRESHOLD_RATIO):
        return 'SELL'
    else:
        return 'NEUTRAL'


def analyze_bid_ask(tick_history: list) -> Dict:
    """
    Analyze bid-ask data from tick history

    Args:
        tick_history: List of tick dictionaries with bid_qty, ask_qty

    Returns:
        Dictionary with analysis results
    """
    if not tick_history:
        return {
            'current_ratio': 0,
            'current_imbalance': 0,
            'signal': 'NEUTRAL',
            'is_significant': False
        }

    latest_tick = tick_history[0]  # DB returns newest first (ORDER BY timestamp DESC)
    bid_qty = latest_tick.get('bid_qty', 0)
    ask_qty = latest_tick.get('ask_qty', 0)

    ratio = calculate_ratio(bid_qty, ask_qty)
    imbalance = calculate_imbalance_pct(bid_qty, ask_qty)
    signal = get_signal_from_ratio(ratio, imbalance)
    is_sig = is_significant(ratio)

    return {
        'current_ratio': ratio,
        'current_imbalance': imbalance,
        'signal': signal,
        'is_significant': is_sig,
        'bid_qty': bid_qty,
        'ask_qty': ask_qty
    }
