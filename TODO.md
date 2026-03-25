# TODO

## Dashboard
- [ ] Date selector on Overview page — default to today, but allow switching to any past date
- [ ] Filter trades by mode: All / Live / Paper
- [ ] When viewing a past date, all P&L, trade log, and stats should reflect that date's data

## Algorithm Enhancements
- [ ] **Time-of-day segmentation analysis + adaptive parameters** — divide the trading day into
  segments, analyse performance per segment, and tune algorithm parameters per segment.

  Proposed segments:
  | Segment | Time | Character |
  |---|---|---|
  | Opening chaos | 09:15–09:30 | Auction residue, fake signals, avoid entries |
  | Early volatile | 09:30–10:00 | Settling down, still choppy, tight stops needed |
  | Mid-morning stable | 10:00–12:30 | Most reliable OFI signals, best win rate expected |
  | Lunch lull | 12:30–13:30 | Low volume, wide spreads, avoid new entries |
  | Afternoon active | 13:30–14:45 | Institutional rebalancing, good momentum |
  | EOD squareoff | 14:45–15:20 | Forced squareoffs create noise, reduce entries |

  Parameters to tune per segment (after data confirms):
  - `TRAIL_STOP_RETRACEMENT`: tighter (e.g. 0.3–0.4) in stable mid-morning so profits are
    locked faster on saturation; looser (0.6) in volatile morning to avoid premature exits
  - `PROFIT_TRAIL_ACTIVATION_PCT`: lower activation threshold in stable hours (0.3%) vs
    volatile hours (0.7%) — stable stocks saturate at smaller moves
  - `BID_ASK_THRESHOLD_RATIO`: higher required ratio (2.0x) in opening/EOD noise periods
  - Entry blocking: hard block 09:15–09:30, soft block (raise threshold) 09:30–10:00

  How to do the analysis (EOD, after 1 week of data):
  1. Tag each trade with its segment based on entry_minutes_since_open
  2. Compute win rate, avg P&L, avg hold time per segment
  3. For long-held winning trades: find at what minute the ratio dropped + range tightened
     → compare locked profit at that moment vs actual exit P&L per segment
  4. Only parameterise segments where data shows a clear difference (don't tune on noise)

  Real motivation (HCLTECH 2026-03-25): entered 09:40 (early volatile), peaked at +₹1,570,
  then consolidated for 75 min in a ₹13 range with ratio dropping to 1.66x. In mid-morning
  stable segment, a tighter trail (0.3 retracement) would have locked ~₹1,400 vs waiting.


- [ ] **Consolidation / momentum saturation exit** — exit a winning trade when momentum has
  clearly exhausted even if price hasn't retraced 50% yet.
  - Concept: if unrealised profit >= X% AND (price range in last N ticks < Y% AND ratio has
    dropped below Z threshold), the move is over — take the profit now rather than waiting
    for PROFIT_TRAIL to fire on the way down.
  - Different from PROFIT_TRAIL: PROFIT_TRAIL exits on price DROP. Consolidation exit fires
    on price STAGNATION — the profit isn't going away yet but momentum signal says it will.
  - Real example (HCLTECH 2026-03-25): peaked at +₹1,570, held for 75 min, last 20 min range
    only ₹12.9 (0.93%), avg ratio dropped from ~1.9x at peak to 1.66x (near entry threshold).
    Clear saturation — consolidation exit would have locked near peak instead of waiting.
  - **Do EOD analysis first**: review all today's trades held >30 min, find at what point
    ratio dropped + range narrowed, compare locked profit at that moment vs actual exit P&L.
    Only implement if data confirms the pattern consistently.
  - Only applies to WINNING trades. Losing trades: let stop loss handle it.

## Persistence / Reliability
- [ ] **Persist peak prices to DB** — `_position_peak` (best price seen since entry, used by
  PROFIT_TRAIL and TRAIL_STOP) lives only in memory. On backend restart, peak is lost and
  profit protection trailing stops cannot fire correctly until a new peak is established.
  Fix: write peak to a `trade_peaks` table or add a `peak_price` column to `trades` on each
  tick update, so it survives restarts.

## Database Maintenance
- [ ] **Tick pruning job** — delete ticks older than 90 days on a daily/weekly schedule
  - Why: ticks table grows ~40k rows/day. Raw tick data older than 90 days has no practical
    use — ML features are already extracted per trade at entry time (entry_ratio, entry_ofi,
    entry_norm_imbalance etc. are stored in the trades table). Old ticks only serve live
    analysis like "why did this trade behave this way" which is only relevant within days of
    the trade. At current growth rate the table hits ~10M rows in ~9 months; with the index
    queries stay fast but disk usage grows unnecessarily.
  - Implementation: a background task in main.py (or a cron script) running once daily at
    EOD that executes:
    `DELETE FROM ticks WHERE timestamp < unixepoch('now', '-90 days')`
  - Do NOT prune the trades table — that is permanent ML training data.

## Performance / UX
- [ ] Virtualize activity log DOM — store entries in JS array, render only last ~50 as DOM nodes to fix browser lag with high tick volume
