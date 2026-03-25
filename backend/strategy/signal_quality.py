"""
Signal Quality Filter
---------------------
Shared filter called by both the scanner and tick-level entry logic.

Filters out noise signals caused by:
  - Thin order books (small total queue → extreme ratios are meaningless)
  - Extreme ratios on thin books (iceberg / single passive order artefact)
  - Imbalance appearing in a single tick with no prior buildup (monitoring only)
  - Iceberg absorption: price not moving despite extreme imbalance (monitoring only)

Returns a result dict:
  {
    'passed': bool,
    'reason': str,          # 'OK' or the first failed check
    'quality_score': float  # 0.0–1.0 — used to re-rank scan results
  }

Usage:
  Scanner  → check_signal_quality(bid_qty, ask_qty)
  Monitoring → check_signal_quality(bid_qty, ask_qty, tick_history=recent_ticks)
"""

from config import config
from typing import List, Dict, Optional


def check_signal_quality(
    bid_qty: int,
    ask_qty: int,
    tick_history: Optional[List[Dict]] = None,
) -> Dict:
    """
    Validate signal quality before entry.

    Args:
        bid_qty:      Current best-bid total queue
        ask_qty:      Current best-ask total queue
        tick_history: Recent ticks newest-first (from db.get_recent_ticks).
                      If None or empty, only snapshot checks run (scanner mode).

    Returns:
        {'passed': bool, 'reason': str, 'quality_score': float}
    """
    total_queue = bid_qty + ask_qty
    min_side    = min(bid_qty, ask_qty)
    ratio       = (bid_qty / ask_qty) if ask_qty > 0 else float('inf')
    if ratio < 1 and bid_qty > 0:
        ratio = ask_qty / bid_qty   # always express as dominant/weaker

    norm_imbalance = abs(bid_qty - ask_qty) / total_queue if total_queue > 0 else 0

    # ── Check 1: Minimum total queue depth ────────────────────────────────────
    # When total queue is thin, ratio is an artefact of the denominator being tiny.
    if total_queue < config.SQ_MIN_TOTAL_QUEUE:
        return {
            'passed': False,
            'reason': f"thin book: total queue {total_queue:,} < {config.SQ_MIN_TOTAL_QUEUE:,}",
            'quality_score': 0.0
        }

    # ── Check 2: Thin side gate ────────────────────────────────────────────────
    # Even if total queue is OK, if the weaker side has almost nothing,
    # the ratio is dominated by a near-zero denominator.
    if min_side < config.SQ_MIN_SIDE_QUEUE:
        return {
            'passed': False,
            'reason': f"thin side: weak side {min_side:,} < {config.SQ_MIN_SIDE_QUEUE:,}",
            'quality_score': 0.0
        }

    # ── Check 3: Extreme ratio requires proportionally deep book ──────────────
    # A 12x ratio on a 500k-share book is a real signal.
    # A 12x ratio on a 12k-share book means one side has ~1,100 shares — noise.
    if ratio > config.SQ_EXTREME_RATIO_THRESHOLD:
        if total_queue < config.SQ_EXTREME_RATIO_MIN_DEPTH:
            return {
                'passed': False,
                'reason': (
                    f"extreme ratio {ratio:.1f}x on shallow book "
                    f"({total_queue:,} < {config.SQ_EXTREME_RATIO_MIN_DEPTH:,} required)"
                ),
                'quality_score': 0.0
            }

    # ── Monitoring-only checks (need tick history) ─────────────────────────────
    if tick_history and len(tick_history) >= config.SQ_BUILDUP_TICKS + 1:

        # ── Check 4: Imbalance buildup — signal must have been building ────────
        # A ratio that jumps from neutral to 15x in one tick is a fleeting order,
        # not real momentum. Require imbalance present in at least N of last M ticks.
        direction = 'BUY' if bid_qty > ask_qty else 'SELL'
        buildup_threshold = 1.0 / config.BID_ASK_THRESHOLD_RATIO  # same as entry threshold expressed as norm
        # Convert BID_ASK_THRESHOLD_RATIO to norm_imbalance equivalent
        # ratio R → norm = (R-1)/(R+1)
        R = config.BID_ASK_THRESHOLD_RATIO
        min_norm = (R - 1) / (R + 1)

        window = tick_history[:config.SQ_BUILDUP_WINDOW]   # last N ticks newest-first
        confirming_ticks = 0
        for t in window:
            b, a = t.get('bid_qty', 0), t.get('ask_qty', 0)
            tot = b + a
            if tot == 0:
                continue
            ni = (b - a) / tot
            if direction == 'BUY' and ni >= min_norm:
                confirming_ticks += 1
            elif direction == 'SELL' and ni <= -min_norm:
                confirming_ticks += 1

        if confirming_ticks < config.SQ_BUILDUP_TICKS:
            return {
                'passed': False,
                'reason': (
                    f"no buildup: only {confirming_ticks}/{config.SQ_BUILDUP_WINDOW} "
                    f"ticks confirmed {direction} imbalance (need {config.SQ_BUILDUP_TICKS})"
                ),
                'quality_score': 0.0
            }

        # ── Check 5: Iceberg absorption suspect ───────────────────────────────
        # Signature: extreme ratio + price flat + weak side NOT shrinking.
        # Means a large passive order on the weak side is absorbing all flow
        # without the price moving — entering here leads to immediate reversal.
        if ratio > config.SQ_EXTREME_RATIO_THRESHOLD and len(tick_history) >= 5:
            recent = tick_history[:5]   # last 5 ticks newest-first
            prices = [t.get('ltp', 0) for t in recent]
            price_move_pct = abs(prices[0] - prices[-1]) / prices[-1] if prices[-1] else 0

            # Weak side = ask for BUY signal, bid for SELL signal
            weak_side_qty = [t.get('ask_qty', 0) if direction == 'BUY'
                             else t.get('bid_qty', 0) for t in recent]
            weak_side_shrinking = weak_side_qty[0] < weak_side_qty[-1]  # newest < oldest

            if price_move_pct < 0.001 and not weak_side_shrinking:
                return {
                    'passed': False,
                    'reason': (
                        f"iceberg suspect: ratio {ratio:.1f}x but price flat "
                        f"({price_move_pct*100:.3f}%) and weak side not shrinking"
                    ),
                    'quality_score': 0.0
                }

    # ── All checks passed — compute quality score ──────────────────────────────
    # Score rewards: deep books, moderate (not extreme) ratios, high norm imbalance
    # Penalises: extreme ratios (less reliable), thin books that barely passed
    depth_score     = min(1.0, total_queue / 100_000)          # full score at 100k+
    ratio_score     = 1.0 if ratio <= config.SQ_EXTREME_RATIO_THRESHOLD else \
                      max(0.3, 1.0 - (ratio - config.SQ_EXTREME_RATIO_THRESHOLD) / 20)
    imbalance_score = norm_imbalance                           # already 0–1

    quality_score = round((depth_score * 0.4 + ratio_score * 0.3 + imbalance_score * 0.3), 3)

    return {
        'passed': True,
        'reason': 'OK',
        'quality_score': quality_score
    }
