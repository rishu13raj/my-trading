"""
Centralised logging for the trading system.

activity_log  — in-memory ring buffer of last 500 events, streamed to the
                frontend via the /ws WebSocket endpoint.
log_event()   — single function used by all phases and main.py; writes to
                the ring buffer, a dated file under logs/, and stdout.
"""

import os
from datetime import datetime
from typing import Optional

# In-memory ring buffer — shared reference imported by main.py for the WS feed
activity_log: list = []
_log_seq: int = 0


def log_event(level: str, msg: str, symbol: Optional[str] = None) -> None:
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
    activity_log.append(entry)
    if len(activity_log) > 500:
        activity_log.pop(0)

    # Dated log file — one per trading day, lives next to backend/
    log_dir = os.path.join(os.path.dirname(__file__), "..", "..", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"{now.strftime('%Y-%m-%d')}.log")
    sym_tag = f"[{symbol}]" if symbol else "[system]"
    with open(log_file, "a") as f:
        f.write(f"{now.strftime('%H:%M:%S')} {sym_tag} [{level.upper()}] {msg}\n")

    print(f"[{now.strftime('%H:%M:%S')}] {sym_tag} {msg}")
