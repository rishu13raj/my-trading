"""
strategy/gates/cvd.py — Cumulative Volume Delta Conviction Gate

THE PROBLEM THIS SOLVES
────────────────────────
OFI (bid/ask queue imbalance) tells you who is QUEUING. CVD tells you who is
actually TRADING — who wants in badly enough to lift the ask or hit the bid.

A stock can show heavy ask-side OFI (SELL signal) while actual trades keep
printing on the bid (buyers absorbing the sell queue). In that case, the OFI
is defensive — market makers refreshing the ask to sell into demand — not a
genuine reversal signal. CVD divergence from OFI is the warning sign.

Chordia, Roll, Subrahmanyam (JFE 2002): trade (order) imbalance, i.e., actual
execution direction, is more predictive of subsequent returns than quote
imbalance at intraday horizons.

HOW IT WORKS
────────────
Uses the TICK RULE to classify each trade without needing bid/ask prices:
  - LTP went UP from previous tick → buyer-initiated (paid the ask)
  - LTP went DOWN → seller-initiated (hit the bid)
  - LTP unchanged → neutral (carry forward, not counted)

Computes over the last CVD_WINDOW_TICKS ticks:
  up_count   = ticks where ltp went up
  down_count = ticks where ltp went down

Block condition (CVD_BLOCK_THRESHOLD default 0.65):
  For SELL entry: block if up_count / total_moves >= threshold
    (65%+ of actual trades are buying → OFI signal is a fake-out)
  For BUY entry: block if down_count / total_moves >= threshold
    (65%+ of actual trades are selling → same)

If total_moves < 5 (price barely moving): NEUTRAL — don't block.
This avoids blocking in flat markets where tick rule is noisy.
"""

from config import config


def compute_cvd(tick_history: list) -> dict:
    """
    Compute CVD stats from tick_history (newest-first list of tick dicts).

    Returns dict with:
      up_count    : ticks where ltp went up
      down_count  : ticks where ltp went down
      total_moves : up_count + down_count
      direction   : 'BUY' (net buying), 'SELL' (net selling), 'NEUTRAL'
    """
    window = tick_history[:config.CVD_WINDOW_TICKS]
    if len(window) < 3:
        return {"up_count": 0, "down_count": 0, "total_moves": 0, "direction": "NEUTRAL"}

    up_count = 0
    down_count = 0

    # window is newest-first; compare consecutive pairs
    for i in range(len(window) - 1):
        curr_ltp = window[i].get("ltp", 0)
        prev_ltp = window[i + 1].get("ltp", 0)
        if curr_ltp > prev_ltp:
            up_count += 1
        elif curr_ltp < prev_ltp:
            down_count += 1

    total_moves = up_count + down_count

    if total_moves < 5:
        direction = "NEUTRAL"
    elif up_count / total_moves >= config.CVD_BLOCK_THRESHOLD:
        direction = "BUY"
    elif down_count / total_moves >= config.CVD_BLOCK_THRESHOLD:
        direction = "SELL"
    else:
        direction = "NEUTRAL"

    return {
        "up_count": up_count,
        "down_count": down_count,
        "total_moves": total_moves,
        "direction": direction,
    }


def blocks(action: str, tick_history: list) -> bool:
    """
    Returns True if CVD is clearly OPPOSITE to the intended action.

    Only blocks when CVD is strongly against — neutral CVD is allowed through.
    We want to block "OFI says SELL but 65%+ of actual prints are buyers."
    We don't block on ambiguous/flat markets.
    """
    cvd = compute_cvd(tick_history)
    direction = cvd["direction"]

    if direction == "NEUTRAL":
        return False
    if action == "SELL" and direction == "BUY":
        return True
    if action == "BUY" and direction == "SELL":
        return True
    return False
