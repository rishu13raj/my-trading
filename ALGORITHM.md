# Trading Algorithm — Design & Decisions

## Core Idea

Intraday bidirectional trading on NSE stocks using real-time bid/ask order flow imbalance (OFI) as the primary signal. The system profits from short-term momentum bursts in either direction, then flips to the opposite side when that momentum exhausts.

---

## Entry Signal

**Source**: `backend/strategy/signal.py`

Zerodha WebSocket delivers ticks with `bid_qty` (total buy queue) and `ask_qty` (total sell queue).

```
ratio = bid_qty / ask_qty

BUY  signal → ratio >= BID_ASK_THRESHOLD_RATIO (default 1.6)  → buyers dominant
SELL signal → ratio <= 1 / BID_ASK_THRESHOLD_RATIO            → sellers dominant
```

Entry is blocked if:
- `MAX_ACTIVE_TRADES` already open (default 5)
- Symbol is in 60s cooldown after a non-flip exit
- `trading_paused` flag is set for today

---

## Exit Conditions

**Source**: `backend/strategy/exit_logic.py`

Three exits, checked in priority order on every tick:

| Priority | Exit | Trigger |
|---|---|---|
| 1 | Stop Loss | Price crosses 5% against entry (configurable) |
| 2 | Time Exit | Position held > 60 minutes (configurable) |
| 3 | Momentum Reversal | Rate of OFI deceleration > 0.3 threshold, after 60s min hold |

---

## Momentum Flip (Smart Reversal)

**Source**: `backend/strategy/reversal_detector.py`

When a position is open, on every tick the flip detector runs **3 layers** of confirmation. On confirmation: close current trade → immediately open reverse trade (no cooldown, marked `is_flip=1` in DB).

### Layer 1 — OFI Flip (sustained opposite imbalance)
```
For SELL position: bid_qty > ask_qty * FLIP_RATIO (1.4x) for FLIP_CONFIRM_TICKS (4) consecutive ticks
For BUY  position: ask_qty > bid_qty * FLIP_RATIO (1.4x) for FLIP_CONFIRM_TICKS (4) consecutive ticks
```
Single-tick flips are noise. Must sustain.

### Layer 2 — Delta Exhaustion (pressure has turned)
```
delta_slope = sum(bid_qty - ask_qty) over last FLIP_DELTA_WINDOW (5) ticks

For SELL position: delta_slope > 0  → net buying pressure has emerged
For BUY  position: delta_slope < 0  → net selling pressure has emerged
```

### Layer 3 — Absorption (dominant side failed to move price)
```
Look back FLIP_ABSORPTION_WINDOW (8) ticks:
  For SELL: majority of ticks had ask > bid (sellers pushing) BUT price moved up or flat
  For BUY:  majority of ticks had bid > ask (buyers pushing) BUT price moved down or flat
```
Absorption fires the flip on its own — it is the highest quality signal because it means the opposing side is large enough to absorb all the pressure without yielding price.

### Minimum hold before flip
`MIN_HOLD_BEFORE_FLIP = 30s` — prevents flip-flop on entry noise.

### Flip signal fires when:
- Layer 3 alone (absorption), OR
- Layer 1 AND Layer 2 together

---

## Per-Position State Tracked (in memory)

```python
_position_flip_state[trade_id] = {
    'cumulative_delta': float,      # running sum of (bid_qty - ask_qty) since entry
    'consecutive_flip_ticks': int,  # ticks in a row showing opposite imbalance
}
```

Reset to zero when a flip trade is opened.

---

## Tick Processing Order (on every tick)

```
1. Log tick (price, bid, ask, ratio)
2. Log position P&L status every 10 ticks
3. Check standard exits (stop loss, time, momentum reversal)
4. Check momentum flip detector for each active position
   → if flip confirmed: close + open reverse
5. If no active position: generate entry signal → check cooldown → enter
```

---

## Config Parameters

| Parameter | Default | Purpose |
|---|---|---|
| `BID_ASK_THRESHOLD_RATIO` | 1.6 | Entry imbalance threshold |
| `FLIP_RATIO` | 1.4 | Reversal imbalance threshold (lower than entry) |
| `FLIP_CONFIRM_TICKS` | 4 | Consecutive ticks to confirm OFI flip |
| `FLIP_DELTA_WINDOW` | 5 | Ticks for delta slope calculation |
| `FLIP_ABSORPTION_WINDOW` | 8 | Ticks for absorption check |
| `MIN_HOLD_BEFORE_FLIP` | 30s | Minimum hold before flip evaluates |
| `STOP_LOSS_PCT` | 5% | Stop loss distance from entry |
| `TIME_EXIT_MINUTES` | 60 | Max hold duration |
| `CAPITAL_PER_TRADE` | ₹50,000 | Capital deployed per position |
| `MAX_ACTIVE_TRADES` | 5 | Max concurrent open positions |

All configurable via `.env` and hot-reloadable via the Settings sliders (capital, stop loss) or direct `.env` edit.

---

## Data Stored Per Trade (for ML training)

Every trade in SQLite records at entry:

| Column | Description |
|---|---|
| `entry_ratio` | bid/ask ratio at entry moment |
| `entry_ofi` | order flow imbalance delta between last 2 ticks |
| `entry_signal_confidence` | signal confidence score |
| `entry_norm_imbalance` | (bid-ask)/(bid+ask), bounded [-1, +1] |
| `entry_minutes_since_open` | minutes since 9:15 AM session open |
| `is_flip` | 1 if this trade was a momentum flip, 0 if cold entry |

---

## Known Limitations & Open Questions

1. **No L2 order book** — we only see total bid/ask queue, not depth levels. A large iceberg order at level 1 absorbing all flow is invisible to us.

2. **Flip threshold calibration** — `FLIP_RATIO=1.4` and `FLIP_CONFIRM_TICKS=4` are starting values. Need walkforward validation on historical data to tune.

3. **Absorption check is price-based** — Layer 3 uses `ltp` price direction, which makes it slightly price-dependent. Ideally would use queue depth changes (unavailable without L2).

4. **Cooldown bypass on flip** — flip trades skip the 60s cooldown intentionally. If a flip trade also gets flipped quickly, this could cause rapid cycling. Monitor in production.

5. **Delta accumulation resets on flip** — cumulative delta resets to 0 when a flip opens. The new position starts fresh. This is correct but means early ticks of the flip trade have no delta history.

---

## Planned: ML Layer

Once sufficient `is_flip=1` and `is_flip=0` trade data is collected:

- Walk-forward validation (never random train/test split for time series)
- Features: entry_ratio, entry_ofi, entry_norm_imbalance, entry_minutes_since_open, cumulative_delta at entry, held_seconds, rolling volatility
- Target: trade P&L > 0 (binary classification) or raw P&L (regression)
- Use model confidence to filter entries — only take trades where model P(win) > threshold
- Export training data to Parquet: `scripts/export_training_data.py` (TODO)
