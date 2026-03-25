"""
core/logger.py — Centralised logging for the trading system.

PURPOSE
───────
All log output in the pipeline flows through a single function: log_event().
There is one place to change log formatting, one place to change the ring
buffer size, and one place to add new output destinations (e.g. a database
table, a Slack webhook).

Before this module existed (pre-refactor, branch main, commit def2445):
  - activity_log and _log_seq were globals in main.py
  - log_event() was also defined in main.py
  - Every new file that needed to log had to import from main, which created
    circular imports (e.g. pipeline.py → main.py → pipeline.py)

Extracting logging to core/logger.py breaks the circular dependency: all files
can import from core.logger, and main.py also imports from core.logger.

TWO OUTPUTS
────────────
1. In-memory ring buffer (activity_log):
   - Holds the last 500 log entries in memory.
   - Read by main.py's /ws WebSocket endpoint to stream live activity to the
     frontend dashboard.
   - The frontend polls this via WebSocket and renders it in the activity log
     panel (the scrolling event list on the right of the dashboard).
   - Ring buffer size = 500: at ~5 ticks/symbol × 5 symbols = 25 ticks/minute,
     500 entries ≈ 20 minutes of history.  Enough for the current session
     without growing unboundedly.
   - NOT persisted to disk — on restart, the in-memory log is empty.
     The dated log FILE (see below) is the durable record.

2. Dated log file (logs/YYYY-MM-DD.log):
   - One file per trading day.
   - Appended to on every log_event() call.
   - Survives restarts — you can tail -f the file to monitor live, or grep
     post-session to reconstruct what happened.
   - Location: logs/ directory at the project root (two levels up from this
     file: core/logger.py → core/ → backend/ → project root).
   - FORMAT: HH:MM:SS [SYMBOL] [LEVEL] message

REAL EXAMPLE — WHY A SINGLE log_event() MATTERS
─────────────────────────────────────────────────
2026-03-25: During the ADANIPOWER incident (3 SELL trades on an uptrending
stock, -₹2,116 total), the log showed:

  09:45:23 [ADANIPOWER] [SIGNAL] ADANIPOWER → SELL signal  ratio=3.85x
  09:45:23 [ADANIPOWER] [TRADE]  OPENED SELL ADANIPOWER @ ₹154.11  qty=323  SL=₹156.69
  09:52:18 [ADANIPOWER] [TRADE]  CLOSED SELL ADANIPOWER @ ₹154.44 | TIME_EXIT_LOSS (9m, -₹107)
  09:53:44 [ADANIPOWER] [SIGNAL] ADANIPOWER → SELL signal  ratio=3.72x
  09:53:44 [ADANIPOWER] [TRADE]  OPENED SELL ADANIPOWER @ ₹154.44  qty=323  SL=₹157.02

Without structured, timestamped, per-symbol logs it would have been impossible
to reconstruct the sequence of events after the session.  The log is the first
tool for post-session analysis and the primary way to build intuition for what
the system is doing.

LOG LEVELS (informal — no filtering is applied, all levels go everywhere):
  'info'   — every tick heartbeat (symbol, price, bid/ask, ratio)
  'signal' — scanner fired, SQ filter blocked, incubator update, entry blocked
  'trade'  — opened, closed, flip executed
  'error'  — unexpected exceptions caught at API or WebSocket layer

THE seq FIELD
──────────────
Every entry in activity_log has a monotonically increasing seq number.
The frontend uses this to detect if it missed events while the WebSocket
was disconnected (e.g. page refresh, reconnect).  On reconnect, the
frontend requests entries with seq > last_seen_seq to backfill the gap.
This is simpler than timestamps (which can have clock drift) and more
reliable than indices (which reset when the ring buffer wraps).
"""

import os
from datetime import datetime
from typing import Optional

# In-memory ring buffer — read by main.py's /ws WebSocket endpoint to
# stream the activity log to the frontend dashboard.
# Shared state: all modules import this list directly by reference.
activity_log: list = []

# Monotonically increasing sequence number for the ring buffer.
# Incremented on every log_event() call.  Used by the frontend to
# detect and backfill missed events after a WebSocket reconnection.
_log_seq: int = 0


def log_event(level: str, msg: str, symbol: Optional[str] = None) -> None:
    """
    Emit one log event to all three destinations: ring buffer, file, stdout.

    Parameters
    ──────────
    level   : Informal log level string.  One of: 'info', 'signal', 'trade',
              'error'.  No filtering is applied — all levels go everywhere.

    msg     : The log message.  Free-form string.  Convention:
                signal events: "{symbol} → {action} signal  ratio={ratio}x"
                trade events:  "OPENED/CLOSED {action} {symbol} @ ₹{price}"
                info events:   "{symbol}  ₹{price}  bid={bid}  ask={ask}  ratio={ratio}x"

    symbol  : NSE trading symbol if the event is symbol-specific.
              Used for the [symbol] tag in the log file and for frontend
              filtering (the dashboard can filter the activity log by symbol).
              None for system-level events (startup, shutdown, WebSocket errors).

    THREAD SAFETY NOTE
    ──────────────────
    activity_log.append() and activity_log.pop(0) are not atomic.  In theory,
    if two threads called log_event() simultaneously, the list could be
    corrupted.  In practice, Zerodha's WebSocket callback is single-threaded
    and the FastAPI endpoints rarely call log_event() concurrently with ticks.
    If we ever see corruption, add a threading.Lock around the list operations.

    FILE APPEND — WHY open() ON EVERY CALL
    ────────────────────────────────────────
    We open and close the log file on every call rather than holding a file
    handle open.  Reasons:
      1. The date changes at midnight — we need the new date's filename.
         With a persistent handle we'd write to yesterday's file until restart.
      2. It's simpler: no file handle to manage in module state, no cleanup
         on shutdown.
      3. Performance: at ~25 log events/minute (5 symbols × 5 ticks), the
         overhead of open+close per call is negligible vs. the WebSocket I/O.
    """
    global _log_seq, activity_log
    now = datetime.now()
    _log_seq += 1

    entry = {
        "seq": _log_seq,
        "time": now.strftime("%H:%M:%S"),
        "level": level,
        "msg": msg,
        "symbol": symbol,
    }

    # ── Ring buffer ───────────────────────────────────────────────────────────
    activity_log.append(entry)
    # Keep the buffer bounded at 500 entries.  pop(0) on a list is O(n) but at
    # ~25 events/min and 500 entries the buffer only wraps every ~20 minutes.
    # If logging frequency increases significantly, switch to collections.deque
    # with maxlen=500 (O(1) pop from either end).
    if len(activity_log) > 500:
        activity_log.pop(0)

    # ── Dated log file ────────────────────────────────────────────────────────
    # Path: <project_root>/logs/YYYY-MM-DD.log
    # __file__ is core/logger.py
    # os.path.dirname(__file__) → core/
    # ../.. → project root
    log_dir = os.path.join(os.path.dirname(__file__), "..", "..", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"{now.strftime('%Y-%m-%d')}.log")
    sym_tag = f"[{symbol}]" if symbol else "[system]"
    with open(log_file, "a") as f:
        f.write(f"{now.strftime('%H:%M:%S')} {sym_tag} [{level.upper()}] {msg}\n")

    # ── stdout ────────────────────────────────────────────────────────────────
    # Visible when running `uvicorn main:app` in a terminal.
    # Useful for watching the session live without opening the frontend.
    print(f"[{now.strftime('%H:%M:%S')}] {sym_tag} {msg}")
