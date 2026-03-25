"""
strategy/types.py — Pipeline data types ("envelopes" passed between phases).

WHY THIS FILE EXISTS
────────────────────
Before this refactor (branch: main, commit 42091ea), all trade lifecycle state
lived as scattered dicts and globals in main.py:
    _position_peak        = {}   # trade_id → float
    _position_flip_state  = {}   # trade_id → dict
    _last_exit_time       = {}   # symbol   → datetime
    ENTRY_COOLDOWN_SECONDS = 60

There were no contracts between phases. A signal dict looked like:
    {'action': 'BUY', 'confidence': 0.83, 'reason': '...', 'details': {...}}
...but which keys were guaranteed vs optional was undocumented, and downstream
code did dict.get() defensively everywhere.

The lifecycle was also implicit — you could only infer "we evaluated signal →
we entered" by reading 200 lines of on_tick_received().

These dataclasses make the lifecycle explicit and checkable:

    Scanner     produces  CandidateSignal
    Incubator   produces  ConfirmedSignal   (or None if rejected)
    Entry       produces  OpenPosition      (or None if blocked)
    Monitor     produces  ExitDecision      (or None if holding)

Each class is exactly the information the *next* phase needs — nothing more.
If you ever wonder "does phase X know about Y?", the answer is in this file.

REAL EXAMPLE THAT MOTIVATED THIS (2026-03-25)
──────────────────────────────────────────────
ADANIPOWER had 3 consecutive SELL trades all losing because the stock was in
an uptrend all morning:
  Trade 130: SELL @ 154.11 → TIME_EXIT_LOSS @ 154.44  (-₹213)
  Trade 139: SELL @ 154.44 → MOMENTUM_REVERSAL @ 155.59 (-₹744)
  Trade 157: SELL @ 155.2  → STOP_LOSS @ 157.0         (-₹1,159)
  Total loss: -₹2,116

Root cause: OFI ratio showed ask-heavy (3.8x) → SELL signal fired → entry was
immediate.  The ratio was ask-heavy because passive sellers keep refreshing the
ask in a trending bullish stock — it's NOT a reversal signal, just market
mechanics.  No intermediate type enforced any confirmation before the order.

With the new types: Scanner produces a CandidateSignal, which must survive the
Incubator (4+ confirming ticks of price actually falling) before becoming a
ConfirmedSignal that can reach Entry.  All 3 ADANIPOWER trades would have been
abandoned in the Incubator because price moved UP during the confirmation window.
"""

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class CandidateSignal:
    """
    Raw signal that passed the OFI threshold AND the signal-quality filter.

    Lives in the Incubator until either confirmed (→ ConfirmedSignal) or
    abandoned (timeout / wrong price direction / trend gate blocked it).

    Fields
    ──────
    symbol        : NSE trading symbol, e.g. 'HCLTECH'
    action        : 'BUY' or 'SELL'
    trigger_price : LTP at the exact moment the signal fired.
                    The Incubator tracks price movement RELATIVE to this value
                    to count confirming ticks.
    ratio         : Dominant-side ratio, always >= 1 for readability.
                    BUY signal → bid_qty / ask_qty  (e.g. 12.94x for LODHA)
                    SELL signal → ask_qty / bid_qty (e.g. 3.8x for ADANIPOWER)
                    NOTE: the raw bid/ask ratio for a SELL signal is < 1
                    (e.g. 0.26x), but we flip it for display purposes.
    bid_qty       : Level-1 bid queue depth at signal time (absolute shares)
    ask_qty       : Level-1 ask queue depth at signal time
    signal_meta   : ML training features captured at signal-fire time.
                    Keys: ratio, ofi, confidence, norm_imbalance,
                          minutes_since_open
                    These are NOT used for trading decisions — they are only
                    written to the trades table for future ML analysis.
                    See ALGORITHM.md §ML Notes for the full feature list.

    WHY trigger_price IS SEPARATE FROM entry_price
    ───────────────────────────────────────────────
    trigger_price = what the scanner saw when ratio crossed threshold.
    entry_price   = what we actually paid (in ConfirmedSignal) after the
                    Incubator waited for N confirming ticks.
    These differ because incubation takes 60-120 seconds, during which price
    moves.  For a BUY signal, entry_price >= trigger_price (we entered after
    the stock already moved up a bit).  This is the cost of confirmation —
    we accept slightly worse fill in exchange for avoiding false entries.

    REAL EXAMPLE — HCLTECH (2026-03-25, Trade 126)
    ────────────────────────────────────────────────
    09:40:32  BUY signal fires at ₹1371.5 (ratio 1.61x, entry_ofi = 0)
    After confirmation, entry at ₹1371.5 (confirmed quickly, minimal drift)
    Peak reached ₹1406.5+ by 12:30 → PROFIT_TRAIL protecting ₹1397.75+
    This was the cleanest trade of the day.
    """

    symbol: str
    action: str
    trigger_price: float
    ratio: float
    bid_qty: int
    ask_qty: int
    signal_meta: Dict = field(default_factory=dict)


@dataclass
class ConfirmedSignal:
    """
    A signal that survived the full Incubator confirmation process.

    To reach here, the stock must have:
      1. Passed the trend gate — price wasn't already moving against signal
         for the last INCUBATION_TREND_LOOKBACK_SECS (300s / 5 min).
      2. Shown >= INCUBATION_MIN_TICKS (4) ticks where price moved in signal
         direction from trigger_price.
      3. Accumulated a total price move >= INCUBATION_PRICE_MOVE_PCT (0.1%)
         from trigger_price in the signal direction.
      4. Done all of the above within INCUBATION_TIMEOUT_SECS (120s).

    entry_price is the LTP at the moment criteria 2+3 were satisfied — it
    differs from CandidateSignal.trigger_price by the drift during confirmation.

    WHY 4 TICKS AND 0.1%?
    ──────────────────────
    Motivated by two specific failure patterns on 2026-03-25:

    EASEMYTRIP Trade 145 (11:45, BUY @ 6.98, -₹1,002):
      Incubation would have shown price STUCK at 6.98 for 10+ ticks (₹0.01
      tick size, no upward movement).  The 0.1% threshold = ₹0.007 ≈ 1 tick.
      With price flat at trigger, confirming_ticks would never accumulate.
      → BLOCKED: saves -₹1,002

    EASEMYTRIP Trade 149 (11:52, BUY @ 6.91, -₹1,012, 30-SECOND HOLD):
      Tick sequence during incubation: 6.93→6.93→6.93→6.90→6.90 (falling).
      Price was falling from the start — confirming_ticks decays to 0.
      → BLOCKED: saves -₹1,012

    LODHA Trade 123 (09:32, BUY @ 758.75, -₹995):
      Tick sequence during incubation: 757.7→757.55→757.85→...→750.25 by 09:35.
      Price fell 1.1% against the BUY signal during the observation window.
      → BLOCKED: saves -₹995

    The 4-tick minimum prevents entering on a single lucky uptick.
    The 0.1% minimum prevents entering on noise within the tick spread.
    Combined they require the stock to actually BE moving, not just flickering.

    COST OF CONFIRMATION (accepted trade-off)
    ──────────────────────────────────────────
    EASEMYTRIP Trade 140 (11:28, BUY +₹441, 4-min hold):
      This was a very fast trade. Incubation would have consumed ~60s,
      leaving only ~3 min of profitable move.  Estimated reduced profit: ~₹200.
      This is the cost of incubation — we accept smaller wins on fast trades
      in exchange for blocking the ADANIPOWER/HFCL/EASEMYTRIP loss patterns.
      Net impact 2026-03-25: ~+₹6,400 improvement (blocked losses) vs ~-₹800
      in reduced profits on fast confirmed trades.  See analysis in TODO.md.
    """

    symbol: str
    action: str
    entry_price: float
    ratio: float
    bid_qty: int
    ask_qty: int
    signal_meta: Dict = field(default_factory=dict)


@dataclass
class OpenPosition:
    """
    Returned by Entry after successfully opening a trade and recording it in DB.

    Currently used mainly as a confirmation return value — the pipeline logs
    trade_id and the monitor picks up the position from DB on the next tick.

    stop_loss is re-calculated from config.STOP_LOSS_PCT at entry time and
    stored in the trades table.  It does NOT change after entry (no trailing
    stop on the downside — only PROFIT_TRAIL for winners).

    WHY STOP_LOSS IS FIXED AT ENTRY
    ────────────────────────────────
    We experimented briefly with tightening stop losses as the trade aged, but
    the HCLTECH trade (2026-03-25, Trade 126) showed the risk: stock spent 75
    minutes in a ₹13 consolidation range after peaking at ₹1399.5.  A tighter
    time-based stop would have exited early.  PROFIT_TRAIL (which locks 75% of
    peak gain) handles the "don't give it all back" problem without requiring
    a dynamic stop on the downside.
    """

    trade_id: int
    symbol: str
    direction: str
    entry_price: float
    qty: int
    stop_loss: float


@dataclass
class ExitDecision:
    """
    Returned by Monitor when a position should be closed.

    reason='MOMENTUM_FLIP' is special: the pipeline will also immediately
    open a reverse trade after executing the close (bypassing incubation and
    cooldown, since a flip is an intentional direction reversal).

    For all other reasons the pipeline just closes and records the exit time
    (which starts the per-symbol cooldown clock in EntryPhase).

    KNOWN EXIT REASONS AND THEIR SOURCES
    ──────────────────────────────────────
    'STOP_LOSS (...)'          : exit_logic.py — price hit stop_loss_price
    'PROFIT_TRAIL (...)'       : exit_logic.py — retraced 25% of peak gain
                                 after profit >= 0.5% of entry activated trail
    'TIME_EXIT_LOSS (...)'     : exit_logic.py — held > TIME_EXIT_MINUTES and
                                 still losing
    'TRAIL_STOP (...)'         : exit_logic.py — time limit hit but profitable;
                                 uses same trail logic as PROFIT_TRAIL
    'MOMENTUM_REVERSAL'        : exit_logic.py — momentum slowing indicator
    'MOMENTUM_FLIP'            : monitor.py — 3-layer flip detector confirmed
    'MANUAL_EXIT'              : main.py /exit/{trade_id} endpoint
    'MONITORING_STOPPED'       : main.py /stop endpoint
    'EOD_SQUAREOFF'            : main.py EOD watchdog at 15:20 IST
    'MONITORING_ON_EXIT:...'   : main.py restart stale-trade check

    direction field is the ORIGINAL trade direction (before the exit), used
    for logging and for flip logic (flip opens the OPPOSITE direction).

    REAL EXAMPLE — PROFIT_TRAIL (2026-03-25, HCLTECH Trade 126)
    ─────────────────────────────────────────────────────────────
    Entry: BUY @ 1371.5 (09:40)
    Peak seen by monitor: ₹1406.5 (as of ~12:30)
    peak_gain = 1406.5 - 1371.5 = ₹35.0
    activation_threshold = 1371.5 * 0.005 = ₹6.86  (0.5% of entry) ← already hit
    trail_stop = 1371.5 + 35.0 * 0.75 = ₹1397.75
    (keeps 75% of peak gain = ₹26.25; only gives back 25% = ₹8.75)

    Previous value before 2026-03-25 session:
    TRAIL_STOP_RETRACEMENT was 0.5 (exit if retraced 50% of peak).
    User wanted to KEEP 75%, not allow 75% retracement.
    Correct value: 0.25 (exit if gives back 25%, which means we kept 75%).
    Corrected during 2026-03-25 session — see commit 42091ea.
    """

    trade_id: int
    symbol: str
    exit_price: float
    reason: str
    direction: str  # original trade direction before exit
