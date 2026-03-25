# Trading Algorithm — Design & Decisions

_Last updated: 2026-03-25_

---

## Core Idea

Intraday bidirectional trading on NSE equity stocks using real-time bid/ask order flow imbalance (OFI) as the primary signal. The system profits from short-term momentum bursts in either direction, exits on reversal or time limit, and can flip to the opposite side when momentum exhausts.

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
- Symbol is **paused** (user paused it manually or auto-paused after manual exit)
- Symbol is not in `selected_stocks` watchlist — hard boundary, no exceptions
- `trading_paused` flag is set for today (Stop Trading Today)
- Signal quality filter rejects the tick (see Signal Quality Filter section)

---

## Signal Quality Filter

**Source**: `backend/strategy/signal_quality.py`

Shared filter called by both the scanner and tick-level entry logic. Prevents entries on noisy signals caused by thin order books, extreme ratios on shallow books, single-tick spikes, and iceberg absorption.

### Five checks (in order):

**Check 1 — Minimum total queue depth**
```
if (bid_qty + ask_qty) < SQ_MIN_TOTAL_QUEUE (10,000):
    BLOCK — ratio is meaningless when total queue is this small
```

**Check 2 — Thin side gate**
```
if min(bid_qty, ask_qty) < SQ_MIN_SIDE_QUEUE (2,000):
    BLOCK — near-zero denominator artefact (produces false 12x–17x ratios)
```

**Check 3 — Extreme ratio requires deep book**
```
if ratio > SQ_EXTREME_RATIO_THRESHOLD (8x):
    if total_queue < SQ_EXTREME_RATIO_MIN_DEPTH (25,000):
        BLOCK — extreme ratio on shallow book is noise, not signal
```

**Check 4 — Imbalance buildup** *(monitoring only — requires tick history)*
```
Look back SQ_BUILDUP_WINDOW (5) ticks:
    require at least SQ_BUILDUP_TICKS (3) ticks confirming same direction
    → prevents single-tick ratio spikes (fleeting orders / manipulation) from firing
```

**Check 5 — Iceberg absorption suspect** *(monitoring only)*
```
if ratio > 8x AND (over last 5 ticks):
    price move < 0.1%  AND  weak side NOT shrinking
    → BLOCK — large passive order absorbing flow without price moving
    → entering here leads to immediate reversal (confirmed in LODHA 2026-03-25)
```

### Scanner scoring with quality
Scanner score is now: `imbalance × log(volume) × quality_score`

`quality_score` (0–1) rewards deep books and penalises extreme ratios that barely passed.
This prevents a 17x thin-book stock from always outranking a 3x liquid stock.

### Why extreme ratios (>8x) are often false signals
When `ask_qty` is very small (e.g., 200 shares), any normal bid queue (3,000 shares) produces
15x. It is not a signal — it is the denominator being near zero. In liquid markets a 1.6x ratio
is meaningful. In thin books a 15x ratio means nothing.
*(Academic basis: Xu, Gould & Howison 2019, arXiv:1907.06230)*

---

## Exit Conditions

**Source**: `backend/strategy/exit_logic.py`

Four exits, checked in priority order on every tick:

| Priority | Exit | Trigger |
|---|---|---|
| 1 | Stop Loss | Price crosses `STOP_LOSS_PCT` (default 2%) against entry — always active |
| 2 | Profit Trail | Once profit ≥ `PROFIT_TRAIL_ACTIVATION_PCT` (0.5%), exit if price retraces 50% of peak gain |
| 3 | Smart Time Exit | After `TIME_EXIT_MINUTES` (120 min): losers exit immediately, winners get trailing stop |
| 4 | Momentum Reversal | OFI deceleration > 0.3 threshold, after 60s minimum hold |

### Profit Protection Trailing Stop (Early Trail)

Activates the moment profit crosses the activation threshold — no time gate required.
Protects against giving back a meaningful gain while still letting small fluctuations ride.

```
activation_threshold = entry_price * PROFIT_TRAIL_ACTIVATION_PCT  (default 0.5%)

For BUY:
    peak_gain = peak_price - entry_price
    if peak_gain >= activation_threshold:
        trail_stop = entry_price + peak_gain * (1 - TRAIL_STOP_RETRACEMENT)
        if current_price <= trail_stop:
            EXIT  →  reason: PROFIT_TRAIL

For SELL:
    peak_gain = entry_price - peak_price
    if peak_gain >= activation_threshold:
        trail_stop = entry_price - peak_gain * (1 - TRAIL_STOP_RETRACEMENT)
        if current_price >= trail_stop:
            EXIT  →  reason: PROFIT_TRAIL
```

Rationale: OFI-based momentum reversal looks at order flow, not price. A stock can show strong
OFI while the price is giving back gains (iceberg absorption, sector rotation). The price-based
trail stop catches this case independently of OFI signal state.

Real example (LODHA 2026-03-25): entered ₹749.30, peaked at ₹758.85 (+₹1,270), fell to ₹747.55
before recovering. PROFIT_TRAIL would have exited at ₹754 (+₹633). Momentum reversal eventually
exited at ₹752.90 (+₹478). Without either, trade went to -₹160 at worst.

### Smart Time Exit (replaces flat time limit)

After 120 minutes, the system checks P&L before deciding:

```
if held >= 120 min:
    if pnl <= 0:
        EXIT immediately  →  reason: TIME_EXIT_LOSS
    if pnl > 0:
        activate trailing stop based on peak price seen since entry:
            BUY:  trail_stop = entry + peak_gain * (1 - TRAIL_STOP_RETRACEMENT)
            SELL: trail_stop = entry - peak_gain * (1 - TRAIL_STOP_RETRACEMENT)
        if price retraces TRAIL_STOP_RETRACEMENT (50%) of peak gain:
            EXIT  →  reason: TRAIL_STOP
```

Rationale: don't kill a winning trade just because time is up. Let it run with a trailing stop. Only force-exit losers.

### Peak Price Tracking

`_position_peak[trade_id]` in memory tracks the best price seen since entry:
- BUY: highest `ltp` seen
- SELL: lowest `ltp` seen

Updated on every tick, cleaned up on trade close.

**Known limitation**: peak is in-memory only — lost on backend restart. On restart, profit trail
and trail stop cannot fire until a new peak is established from live ticks. Fix tracked in TODO.md.

---

## Momentum Flip (Smart Reversal)

**Source**: `backend/strategy/reversal_detector.py`

When a position is open, on every tick the flip detector runs **3 layers** of confirmation. All 3 must confirm simultaneously. On confirmation: close current trade → immediately open reverse trade (no cooldown, marked `is_flip=1` in DB).

### Gate: Minimum adverse move before flip evaluates
```
adverse_pct = abs(current_price - entry_price) / entry_price
if adverse_pct < FLIP_MIN_ADVERSE_PCT (0.3%):
    skip flip check entirely — price hasn't moved enough to confirm reversal
```
This prevents flip-flop in flat/choppy markets.

### Layer 1 — OFI Flip (sustained opposite imbalance)
```
For SELL position: bid_qty > ask_qty * FLIP_RATIO (1.4x) for FLIP_CONFIRM_TICKS (4) consecutive ticks
For BUY  position: ask_qty > bid_qty * FLIP_RATIO (1.4x) for FLIP_CONFIRM_TICKS (4) consecutive ticks
```
Single-tick flips are noise. Must sustain.

### Layer 2 — Delta Exhaustion (cumulative pressure has turned)
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

### Flip fires when ALL THREE layers confirm
Previously Layer 3 alone could trigger — this caused a flip explosion in choppy markets (20 trades in 5 minutes). Now all 3 must confirm simultaneously.

### Minimum hold before flip
`MIN_HOLD_BEFORE_FLIP = 60s` — prevents flip-flop on entry noise.

---

## Per-Position State Tracked (in memory)

```python
_position_flip_state[trade_id] = {
    'cumulative_delta': float,       # running sum of (bid_qty - ask_qty) since entry
    'consecutive_flip_ticks': int,   # ticks in a row showing opposite imbalance
}
_position_peak[trade_id] = float     # best price seen since entry (high for BUY, low for SELL)
```

Both dicts are cleaned up immediately on trade close.

---

## Tick Processing Order (on every tick)

```
1. Guard: if symbol not in selected_stocks → return (hard boundary)
2. Guard: if symbol in paused_stocks → return after exits (no new entries)
3. Log tick (price, bid, ask, ratio)
4. Log position P&L status every 10 ticks
5. Update _position_peak for all active positions on this symbol
6. Check standard exits (stop loss, profit trail, smart time exit, momentum reversal)
7. Check momentum flip detector for each active position
   → if flip confirmed: close + open reverse trade
8. If no active position: generate entry signal → signal quality filter → check cooldown → enter
```

---

## Per-Stock Monitoring Control

Each stock in the watchlist has an **Active** or **Paused** state:

| State | New entries | Exit checks | Remove |
|---|---|---|---|
| Active | ✓ allowed | ✓ running | ✓ if no open trade |
| Paused | ✗ blocked | ✓ running | ✓ if no open trade |
| Active + open trade | ✓ allowed | ✓ running | ✗ blocked |
| Paused + open trade | ✗ blocked | ✓ running | ✗ blocked |

**Auto-pause on manual exit**: when the user clicks Exit on a trade, that stock is automatically paused so the system cannot re-enter until the user explicitly resumes it.

---

## Stock Scanner

**Source**: `backend/data/stock_universe.py`, `/scan` endpoint in `main.py`

On demand (Scan button), the system:
1. Fetches `kite.quote()` for ~323 liquid NSE stocks in 3 batches of ~108
2. Runs signal quality filter (snapshot mode) on each stock — removes thin books and extreme ratios on shallow books
3. Scores each passing stock by: `imbalance × log(volume) × quality_score`
4. Filters out stocks with volume < 10,000 (illiquid)
5. Returns top 5 ranked by score with bias (BUY/SELL), ratio, and imbalance bar

Scan results are additive only — they never remove existing stocks or affect open trades.

### Why the old score (imbalance × log(volume)) was flawed
A thin-book stock with 17x ratio has imbalance ≈ 0.87 — near maximum. Even with low volume it
always ranked #1, pushing out more reliable 2–3x signals on deep liquid books. The quality_score
multiplier corrects this by penalising extreme ratios and rewarding depth.

---

## Database

**Source**: `backend/data/database.py`

### Indexes
`idx_ticks_symbol_ts` on `ticks(symbol, timestamp DESC)` — created 2026-03-25.
Required for `get_recent_ticks()` performance. Without it, every tick processing call does a
full table scan. At 40k ticks/day the table grows to ~10M rows in ~9 months — the index keeps
queries fast at any size.

### Growth estimate
~40k ticks/day → ~1M rows/month → ~10M rows/year at current monitoring intensity.
DB size: ~60 bytes/tick → ~600 MB at 10M rows. Manageable for SQLite with index.

### Tick pruning (pending — see TODO.md)
Raw ticks older than 90 days serve no purpose — ML features are extracted per trade at entry.
A pruning job is planned but not yet implemented.

---

## Startup / Reconnect Safety

On every backend **restart** and every time **monitoring is turned ON**:
1. Fetch live prices via `kite.ltp()` REST API (retries up to 10 times, 3s apart)
2. Run exit checks on all open trades using live prices
3. Close any trades that now meet stop loss, time exit, or momentum exit criteria
4. Fall back to last stored tick price only after all 10 retries fail

This handles crashes, restarts, and gaps where monitoring was off.

---

## Brokerage Cost Model

Actual Zerodha intraday charges per trade (at ₹1,00,000 position size):

| Charge | Rate | Amount |
|---|---|---|
| Brokerage | min(0.03%, ₹20) per leg × 2 | ₹40 |
| STT | 0.025% sell side | ₹25 |
| NSE transaction | 0.00307% both sides | ₹6 |
| Stamp duty | 0.003% buy side | ₹3 |
| GST | 18% on brokerage + fees | ₹8 |
| **Total round trip** | | **~₹82** |
| Break-even price move | | **0.082%** |

Open trades (entry leg only): ~₹27 until closed.

At current 50% win rate, need average win > 2× average loss to be net profitable after charges.

---

## Config Parameters

| Parameter | Default | Purpose |
|---|---|---|
| `BID_ASK_THRESHOLD_RATIO` | 1.6 | Entry imbalance threshold |
| `FLIP_RATIO` | 1.4 | Reversal imbalance threshold |
| `FLIP_CONFIRM_TICKS` | 4 | Consecutive ticks to confirm OFI flip |
| `FLIP_DELTA_WINDOW` | 5 | Ticks for delta slope calculation |
| `FLIP_ABSORPTION_WINDOW` | 8 | Ticks for absorption check |
| `MIN_HOLD_BEFORE_FLIP` | 60s | Minimum hold before flip evaluates |
| `FLIP_MIN_ADVERSE_PCT` | 0.3% | Minimum adverse move before flip considers |
| `STOP_LOSS_PCT` | 2% | Stop loss distance from entry |
| `TIME_EXIT_MINUTES` | 120 | Minutes before time-based exit logic activates |
| `TRAIL_STOP_RETRACEMENT` | 0.5 | Fraction of peak gain to retrace before trailing stop fires |
| `PROFIT_TRAIL_ACTIVATION_PCT` | 0.5% | Profit threshold to activate early trailing stop |
| `CAPITAL_PER_TRADE` | ₹1,00,000 | Capital deployed per position |
| `MAX_ACTIVE_TRADES` | 5 | Max concurrent open positions |
| `ENTRY_COOLDOWN_SECONDS` | 60 | Cooldown after auto-exit before re-entry |
| `SQ_MIN_TOTAL_QUEUE` | 10,000 | Min total bid+ask queue for valid signal |
| `SQ_MIN_SIDE_QUEUE` | 2,000 | Min weak-side queue for valid signal |
| `SQ_EXTREME_RATIO_THRESHOLD` | 8.0 | Ratios above this require deep book |
| `SQ_EXTREME_RATIO_MIN_DEPTH` | 25,000 | Required total queue when ratio is extreme |
| `SQ_BUILDUP_TICKS` | 3 | Min confirming ticks in buildup window |
| `SQ_BUILDUP_WINDOW` | 5 | Window size for buildup check |

All configurable via `.env`. Capital, stop loss, and max active trades are hot-reloadable via Settings sliders in the dashboard.

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

### ML notes
- `entry_minutes_since_open = 0` trades (first minute of open) are consistently losers — morning
  auction residue creates false OFI. ML will learn this from the feature directly.
- Negative `entry_ofi` on a high-ratio BUY entry is a warning sign (ask growing at entry despite
  strong bid — iceberg pattern). LODHA 2026-03-25 Trade 1: entry_ratio=17.5, entry_ofi=-22751 → Loss.
- Do not split train/test randomly — always use walk-forward validation (train on older data,
  test on newer) to avoid look-ahead bias.
- Do not add per-stock rules after single bad days. Accumulate 1+ week of data, then let ML
  identify structurally bad stocks by their win rate and signal quality patterns.

---

## Going Live Criteria

Current status: **paper trading only**

Minimum bar before risking real money:
1. Win rate ≥ 60% consistently over 2-3 weeks of paper trading
2. Average win > 2× average loss (so net positive after ₹82 charges per trade)
3. No algorithm bugs causing uncontrolled trade bursts (flip explosion was fixed)
4. At least 2-3 profitable weeks on the correct algorithm

F&O note: Futures have slightly lower charges (~₹65 vs ₹82) but introduce expiry risk, lot size constraints, and margin calls. Options buying has capped loss but theta decay conflicts with 20min-2hr hold time. **Stay on equity intraday until algorithm is proven.**

---

## Known Limitations & Open Questions

1. **No L2 order book** — only total bid/ask queue, not depth levels. Large iceberg orders are
   partially detectable via L1 heuristics (replenishment fingerprint) but not fully.

2. **Flip threshold calibration** — `FLIP_RATIO=1.4`, `FLIP_CONFIRM_TICKS=4` are working values
   but need walk-forward validation on historical data.

3. **Absorption check is price-based** — Layer 3 uses `ltp` direction, which is slightly
   price-dependent. Ideally would use queue depth changes (unavailable without L2).

4. **paused_stocks resets on restart** — pause state is in-memory only, not persisted to DB.
   After restart, all stocks resume Active.

5. **peak_price resets on restart** — `_position_peak` is in-memory only. PROFIT_TRAIL and
   TRAIL_STOP cannot fire correctly after restart until a new peak is established. Fix in TODO.md.

6. **Scanner universe is static** — 323 hardcoded stocks. NSE adds/removes listings over time.
   Review universe quarterly.

7. **Signal quality filter is snapshot-only in scanner** — iceberg check (Check 5) and buildup
   check (Check 4) require tick history and only run during monitoring. Scanner cannot detect
   icebergs — it surfaces candidates, monitoring guards the actual entry.

---

## Planned

- Walk-forward ML validation on trade data (features: entry_ratio, entry_ofi, entry_norm_imbalance, entry_minutes_since_open, held_seconds, rolling volatility)
- EOD Parquet export for training data (`scripts/export_training_data.py`)
- Dashboard date selector + live/paper filter
- Auto-scan at market open (9:20 AM) to pre-populate watchlist
- Persist peak_price to DB so profit trail survives backend restarts
