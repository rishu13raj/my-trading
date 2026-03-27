# Backtesting Guide

## Quick Start

```bash
cd /home/rishu/code/personal/my-trading/backend
python backtest.py 2026-03-25
```

---

## Database Locations

**Historical tick data:** `/home/rishu/code/personal/my-trading/backend/trading.db`
- Contains all historical ticks from Zerodha WebSocket captures
- 275K+ ticks covering Mar 23-27, 2026
- This is the **source database**

**Backtest reads from:** `/home/rishu/code/personal/my-trading/trading.db`
- Empty by default — must be populated before backtesting
- This is the **working database** for backtest.py

---

## Preparing Data for Backtest

### Step 1: Copy Historical Data

```bash
cp /home/rishu/code/personal/my-trading/backend/trading.db \
   /home/rishu/code/personal/my-trading/trading.db
```

This copies 275K ticks from the source database to the working database.

### Step 2: Verify Data Exists

```bash
python << 'EOF'
import sqlite3
conn = sqlite3.connect('trading.db')
c = conn.cursor()
c.execute("""
  SELECT
    DATE(DATETIME(timestamp, 'unixepoch', 'localtime')) as date,
    COUNT(*) as count
  FROM ticks
  GROUP BY date
  ORDER BY date DESC
""")
print("Ticks by date:")
for date, count in c.fetchall():
    print(f"  {date}: {count:,}")
conn.close()
EOF
```

**Expected output:**
```
Ticks by date:
  2026-03-27:    306 ticks
  2026-03-25: 202,993 ticks
  2026-03-24:  59,631 ticks
  2026-03-23:  12,149 ticks
```

---

## Running Backtests

### Basic Usage

```bash
cd /home/rishu/code/personal/my-trading/backend
python backtest.py YYYY-MM-DD
```

**Examples:**

```bash
python backtest.py 2026-03-25  # Single day
python backtest.py 2026-03-24  # Another day
```

### What Happens

1. Backtest reads ticks from `trading.db` for specified date
2. Auto-detects all symbols (stocks) that traded that day
3. Sets simulated time to 09:30 IST (market open)
4. Pre-populates tick buffer with 09:15-09:30 ticks for trend gate history
5. Replays all post-09:30 ticks through the trading pipeline
6. Reports results: trade count, win rate, P&L, exit reasons

### Output Example

```
Date      : 2026-03-25
Symbols   : ['ADANIPOWER', 'EASEMYTRIP', 'FIRSTCRY', ...]
Config    : CAPITAL_PER_TRADE=₹100,000  MAX_ACTIVE_TRADES=7  BID_ASK=1.6x
Pre-ticks : 3,077  (buffer for trend gate)
Replay    : 192,864 ticks after 09:30
Starting replay...

Replay complete.
  Closed : 30  |  Still open (ignored): 5

=================================================================
  BACKTEST RESULTS
  2026-03-25 | Post 09:30 | Paper | 9 symbols
=================================================================

  Trades          : 30
  Wins / Losses   : 20 W / 10 L
  Win rate        : 66.7%

  Gross P&L       : ₹+557.22
  Brokerage + tax : ₹2,491.40
  Net P&L         : ₹-1,934.18

  Best trade      : ₹+704.85
  Worst trade     : ₹-1,133.44
  Avg P&L/trade   : ₹+18.57
  Avg hold        : 31.8 min

  Exit reasons:
    PROFIT_TRAIL                         12
    TRAIL_STOP                            6
    MOMENTUM_REVERSAL                     6
    TIME_EXIT_LOSS                        3
    STOP_LOSS                             3

  By symbol (gross P&L):
    ADANIPOWER       13 trades  10W/3L  ₹+655.16
    HFCL              7 trades  5W/2L  ₹+541.73
    NTPCGREEN        4 trades  3W/1L  ₹+472.64
```

---

## Understanding Results

### Key Metrics

| Metric | Meaning |
|--------|---------|
| **Trades** | Total positions opened |
| **Wins / Losses** | Count of profitable vs losing trades (before brokerage) |
| **Win rate** | % of trades that made profit before costs |
| **Gross P&L** | Raw profit/loss from trades (before brokerage) |
| **Brokerage + tax** | Total trading costs (fees, taxes, etc) |
| **Net P&L** | **Actual profit/loss = Gross - Brokerage** |
| **Avg P&L/trade** | Average profit per trade |
| **Avg hold** | Average time each trade stayed open |

### Exit Reasons

- **PROFIT_TRAIL**: Exited when price retraced 25% of peak gain (trailing stop)
- **TRAIL_STOP**: Similar to profit trail, stopped at trailing level
- **MOMENTUM_REVERSAL**: Signal direction flipped (opposite OFI signal fired)
- **TIME_EXIT_LOSS**: 60 minutes elapsed and trade was still losing
- **STOP_LOSS**: Hit 1% stop loss threshold

---

## Comparing Across Days

### Run Multiple Days

```bash
python backtest.py 2026-03-24
python backtest.py 2026-03-25
```

### Typical Results (Historical)

**2026-03-24:** 19 trades, 52.6% win rate, ₹-2,987 net
**2026-03-25:** 30 trades, 66.7% win rate, ₹-1,934 net

---

## Common Issues

### Issue: "No trades executed in simulation"

**Cause 1: Empty database**
```bash
# Check if data exists
python << 'EOF'
import sqlite3
conn = sqlite3.connect('trading.db')
c = conn.cursor()
c.execute("SELECT COUNT(*) FROM ticks")
print("Total ticks:", c.fetchone()[0])
conn.close()
EOF
```

If count is 0, copy data from source:
```bash
cp /home/rishu/code/personal/my-trading/backend/trading.db trading.db
```

**Cause 2: Gates too restrictive**
Check `backend/strategy/gates/` for any disabled gates. If volume or VWAP gates are enabled, they may be filtering all signals. Look for commented-out code in `backend/strategy/phases/scanner.py`.

### Issue: "timestamp" not found errors

The backtest patches `datetime` globally. If gates are using `datetime.now()` without passing timestamp parameter, they'll use real time instead of simulated time.

**Solution:** Ensure time-of-day gate calls include `tick_time` parameter:
```python
if is_trading_blocked(tick_time):  # ← pass tick_time
    return None
```

---

## Modifying Config for Backtest

Edit `/home/rishu/code/personal/my-trading/.env`:

```ini
# Trading Configuration
CAPITAL_PER_TRADE=100000          # Rupees per trade
MAX_ACTIVE_TRADES=7               # Max simultaneous open positions
STOP_LOSS_PCT=0.01                # 1% stop loss
TIME_EXIT_MINUTES=60              # Exit losing trades after 60 min

# Bid-Ask Strategy
BID_ASK_THRESHOLD_RATIO=1.6       # Minimum OFI ratio to trigger signal
ENTRY_WAIT_SECONDS=60             # Consistency window for OFI

# Incubation Gate
INCUBATION_MIN_TICKS=4            # Min confirming ticks to enter
INCUBATION_PRICE_MOVE_PCT=0.001   # 0.1% price move required

# Time-of-Day Gate
TRADING_BLOCK_START=0915          # Block entries 09:15-09:30
TRADING_BLOCK_END=0930
LUNCH_BLOCK_START=1200            # Lunch period 12:00-13:30
LUNCH_BLOCK_END=1330
LUNCH_THRESHOLD_RATIO_MULTIPLIER=1.5  # Raise threshold 50% during lunch
```

Then run backtest (it auto-reloads config):
```bash
python backtest.py 2026-03-25
```

---

## Backtest Architecture

### Data Flow

1. **Load ticks from DB** → All ticks for specified date grouped by symbol
2. **Pre-populate buffers** → 09:15-09:30 ticks loaded for trend gate history
3. **Simulate time** → `SimDatetime` overrides `datetime.now()` to match each tick's timestamp
4. **Replay ticks** → For each post-09:30 tick:
   - Scanner evaluates OFI + gates
   - Incubator confirms signal if CandidateSignal returned
   - Entry places order if ConfirmedSignal returned
   - Monitor manages exits (stop loss, time exit, profit trail, momentum reversal)
5. **Report results** → Summary stats, trade log, exit breakdown

### Key Files

- `backtest.py` — Main replay engine with SimDatetime patch
- `backend/strategy/pipeline.py` — Core trading logic (Scanner → Incubator → Entry → Monitor)
- `backend/strategy/phases/scanner.py` — Signal generation + gates
- `backend/strategy/gates/time_of_day.py` — Time-of-day gate (only active gate currently)
- `.env` — Config parameters (tuned during backtest)
- `trading.db` — Working database with tick data

---

## Next Steps After Backtest

1. **Compare metrics** across dates to identify patterns
2. **Adjust config** (thresholds, timeouts) based on win rate
3. **Enable/test gates** by modifying scanner.py comments
4. **Run live session** — Backend collects real ticks, applies same logic
5. **Compare backtest vs actual** — Real results should match closely if clock/config aligned

---

## Script: Backtest All Available Dates

```bash
cd /home/rishu/code/personal/my-trading/backend

# Copy data once
cp /home/rishu/code/personal/my-trading/backend/trading.db \
   /home/rishu/code/personal/my-trading/trading.db

# Run all dates
for date in 2026-03-24 2026-03-25; do
    echo "========== BACKTEST: $date =========="
    python backtest.py $date | grep -A 50 "Replay complete"
    echo ""
done
```

---

## Troubleshooting

### Q: Why is the database 275MB but backtest says "No trades"?

**A:** The database might have the wrong path or you're not in the backend directory. Always run from:
```bash
cd /home/rishu/code/personal/my-trading/backend
```

And ensure `trading.db` in the project root is populated:
```bash
# From project root
cp backend/trading.db trading.db
```

### Q: How do I test a single symbol or time range?

**A:** Backtest always runs the entire day post-09:30. To test specific times:

1. Modify backtest.py to add a `start_time` parameter, or
2. Write a custom test script that calls Scanner/Incubator/Entry directly

### Q: Gates are disabled — how do I enable them?

**A:** In `backend/strategy/phases/scanner.py`, uncomment the gate check:

```python
# Change this:
# vol_check = check_volume_spike(...)  # DISABLED
# if not vol_check["passed"]:
#     return None

# To this:
vol_check = check_volume_spike(...)
if not vol_check["passed"]:
    return None
```

---

## Summary

- **Copy data:** `cp backend/trading.db trading.db`
- **Verify data:** Run SQL count query
- **Run backtest:** `python backtest.py YYYY-MM-DD`
- **Adjust config:** Edit `.env` and re-run
- **Compare results:** Track win rate, net P&L, trades per day

This one-page reference should eliminate the need to explore the codebase to understand backtesting.
