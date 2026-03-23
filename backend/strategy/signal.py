from typing import Dict, List
from strategy.bid_ask_ratio import analyze_bid_ask, is_significant
from strategy.momentum import detect_direction, monitor_for_entry
from config import config

def generate_signal(tick_history: List[Dict], can_open_new_trade: bool = True) -> Dict:
    """
    Generate trading signal based on all strategy components

    Args:
        tick_history: List of tick dictionaries
        can_open_new_trade: Whether a new trade can be opened (portfolio constraint)

    Returns:
        Dictionary with signal details
    """
    if not tick_history:
        return {
            'action': 'HOLD',
            'confidence': 0,
            'reason': 'No market data',
            'details': {}
        }

    # Analyze bid-ask ratio
    bid_ask_analysis = analyze_bid_ask(tick_history)

    # Check if imbalance is significant (either BUY or SELL direction)
    if not bid_ask_analysis['is_significant']:
        r = bid_ask_analysis['current_ratio']
        return {
            'action': 'HOLD',
            'confidence': 0,
            'reason': f"Ratio {r:.2f}x — not significant (need >{config.BID_ASK_THRESHOLD_RATIO}x for BUY or <{1/config.BID_ASK_THRESHOLD_RATIO:.2f}x for SELL)",
            'details': bid_ask_analysis
        }

    # Monitor for entry confirmation
    entry_analysis = monitor_for_entry(tick_history)

    if not entry_analysis['can_enter']:
        return {
            'action': 'HOLD',
            'confidence': 0,
            'reason': f"Entry not confirmed: {entry_analysis['reason']}",
            'details': {
                'bid_ask': bid_ask_analysis,
                'entry': entry_analysis
            }
        }

    # Cannot open new trades if portfolio is full
    if not can_open_new_trade:
        return {
            'action': 'HOLD',
            'confidence': entry_analysis['confidence'],
            'reason': 'Portfolio at max capacity (2 active trades)',
            'details': {
                'bid_ask': bid_ask_analysis,
                'entry': entry_analysis
            }
        }

    # Generate final signal
    direction = entry_analysis['direction']
    confidence = entry_analysis['confidence']

    return {
        'action': direction,
        'confidence': confidence,
        'reason': f"{direction} signal with {confidence:.2%} confidence. Ratio: {bid_ask_analysis['current_ratio']:.2f}x",
        'details': {
            'bid_ask': bid_ask_analysis,
            'entry': entry_analysis,
            'direction': direction
        }
    }


def should_place_order(signal: Dict) -> bool:
    """
    Check if we should place an order based on signal

    Args:
        signal: Signal dict from generate_signal

    Returns:
        True if should place order
    """
    return signal['action'] in ['BUY', 'SELL'] and signal['confidence'] >= 0.6
