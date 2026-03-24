"""
Smart momentum flip detector — 3-layer confirmation before reversing a trade.

Layer 1 (OFI Flip): Opposite bid/ask imbalance holds for FLIP_CONFIRM_TICKS consecutive ticks.
Layer 2 (Delta Exhaustion): Cumulative delta slope has turned against the trade direction.
Layer 3 (Absorption): Dominant side was pushing price but price didn't move → passive side absorbed.

Signal fires when ALL THREE layers confirm together.
Layer 3 alone is NOT sufficient — absorption in a choppy market fires constantly.
Returns ("BUY" | "SELL" | None) — the direction of the REVERSE trade to open.
"""
from config import config


def check_flip(position: dict, tick_history: list, position_state: dict) -> str | None:
    """
    Check whether the momentum for `position` has flipped and we should reverse.

    Args:
        position:       Active trade dict (direction, entry_price, entry_time, ...)
        tick_history:   Recent ticks newest-first [{ltp, bid_qty, ask_qty, ...}, ...]
        position_state: Mutable per-position dict we maintain in main.py
                        Must contain: cumulative_delta (float), consecutive_flip_ticks (int)

    Returns:
        'BUY' or 'SELL' (the reverse direction) if flip confirmed, else None
    """
    if not tick_history or len(tick_history) < max(config.FLIP_CONFIRM_TICKS, config.FLIP_ABSORPTION_WINDOW):
        return None

    direction   = position['direction']
    reverse     = 'BUY' if direction == 'SELL' else 'SELL'
    entry_price = position['entry_price']
    current_ltp = tick_history[0].get('ltp', entry_price)

    # ── Gate: minimum adverse price move before flip is even considered ───────
    # In a choppy/flat market the flip detector fires constantly on noise.
    # Only evaluate if price has moved at least FLIP_MIN_ADVERSE_PCT against us.
    adverse_move = (
        (current_ltp - entry_price) / entry_price if direction == 'SELL'
        else (entry_price - current_ltp) / entry_price
    )
    if adverse_move < config.FLIP_MIN_ADVERSE_PCT:
        position_state['consecutive_flip_ticks'] = 0  # reset — too early to flip
        return None

    current_tick = tick_history[0]             # newest tick
    bid_qty = current_tick.get('bid_qty', 0)
    ask_qty = current_tick.get('ask_qty', 0)

    # ── Update cumulative delta ──────────────────────────────────────────────
    tick_delta = bid_qty - ask_qty             # positive = net buy pressure
    position_state['cumulative_delta'] = position_state.get('cumulative_delta', 0.0) + tick_delta

    # ── Layer 1: OFI Flip — opposite imbalance for N consecutive ticks ───────
    if direction == 'SELL':
        # We're short, flip signal = buyers dominant
        ofi_flipped = ask_qty > 0 and bid_qty > ask_qty * config.FLIP_RATIO
    else:
        # We're long, flip signal = sellers dominant
        ofi_flipped = bid_qty > 0 and ask_qty > bid_qty * config.FLIP_RATIO

    if ofi_flipped:
        position_state['consecutive_flip_ticks'] = position_state.get('consecutive_flip_ticks', 0) + 1
    else:
        position_state['consecutive_flip_ticks'] = 0

    layer1 = position_state['consecutive_flip_ticks'] >= config.FLIP_CONFIRM_TICKS

    # ── Layer 2: Delta Exhaustion — slope of cumulative delta has turned ──────
    window = config.FLIP_DELTA_WINDOW
    if len(tick_history) >= window + 1:
        # Reconstruct cumulative delta N ticks ago from the window of ticks
        recent_deltas = [t.get('bid_qty', 0) - t.get('ask_qty', 0) for t in tick_history[:window]]
        delta_slope = sum(recent_deltas)  # net delta over last N ticks (positive = buying)

        if direction == 'SELL':
            # For a short: delta should have been negative; reversal if now net positive
            layer2 = delta_slope > 0
        else:
            # For a long: delta should have been positive; reversal if now net negative
            layer2 = delta_slope < 0
    else:
        layer2 = False

    # ── Layer 3: Absorption — dominant side pushed but price didn't move ─────
    window_abs = config.FLIP_ABSORPTION_WINDOW
    recent = tick_history[:window_abs]
    if len(recent) >= 3:
        price_move = recent[0].get('ltp', 0) - recent[-1].get('ltp', 0)  # newest minus oldest

        if direction == 'SELL':
            # Sellers were dominant (ask > bid), but price didn't fall → buyers absorbed
            sell_dominant_ticks = sum(
                1 for t in recent
                if t.get('ask_qty', 0) > t.get('bid_qty', 0) * config.FLIP_RATIO
            )
            # Absorption: most ticks showed sell pressure but price rose or stayed flat
            layer3 = sell_dominant_ticks >= (window_abs // 2) and price_move >= 0
        else:
            # Buyers were dominant (bid > ask), but price didn't rise → sellers absorbed
            buy_dominant_ticks = sum(
                1 for t in recent
                if t.get('bid_qty', 0) > t.get('ask_qty', 0) * config.FLIP_RATIO
            )
            layer3 = buy_dominant_ticks >= (window_abs // 2) and price_move <= 0
    else:
        layer3 = False

    # ── Decision ─────────────────────────────────────────────────────────────
    # All three layers must confirm — Layer 3 alone is too noisy in choppy markets.
    if layer1 and layer2 and layer3:
        position_state['consecutive_flip_ticks'] = 0
        position_state['cumulative_delta'] = 0.0
        return reverse

    return None
