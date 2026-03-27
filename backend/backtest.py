#!/usr/bin/env python3
"""
backtest.py — Replay today's post-09:30 ticks through the new pipeline.

Feeds real tick data from trading.db through the refactored pipeline
(Scanner → Incubator → Entry → Monitor) in simulation mode.

No writes to trading.db.  All trade state is in-memory.

Run from backend/ directory:
    cd /home/rishu/code/personal/my-trading/backend
    python3 backtest.py
"""

import os
import sys
import sqlite3
from collections import defaultdict
from datetime import datetime

# ── Simulated clock ────────────────────────────────────────────────────────────
# All datetime.now() calls in strategy code will return this value.
# It is advanced to match each tick's Unix timestamp as the replay progresses.
import datetime as _dt_module

_SIM_NOW = [_dt_module.datetime(2026, 3, 25, 9, 30, 0)]


class _SimDatetime(_dt_module.datetime):
    """Subclass of datetime that overrides .now() to return the sim clock."""

    @classmethod
    def now(cls, tz=None):
        return _SIM_NOW[0]


# ── In-memory trade state ──────────────────────────────────────────────────────
_sim_active = {}   # trade_id (int) → trade dict
_sim_closed = []   # list of closed trade dicts
_sim_id     = [0]  # auto-increment trade_id counter (wrapped in list for mutation)

# Per-symbol tick buffer: newest tick first, capped at 200 entries.
# Populated before replay starts (from 09:15 ticks) so the trend gate has
# 5 minutes of history at the very first 09:30 tick.
_tick_bufs = defaultdict(list)  # symbol → [newest, ..., oldest]


# ── Patch data.database.db ────────────────────────────────────────────────────
# MUST be done before importing ANY strategy module that does `from data.database import db`.
# When those imports run, Python reads the current value of data.database.db,
# which will be our BacktestDB.

import data.database as _db_mod


class _BacktestDB:
    """
    Replacement for data.database.Database.
    Reads tick history from the in-memory buffer; manages trade state in memory.
    Never touches trading.db.
    """

    def get_recent_ticks(self, symbol: str, limit: int = 100) -> list:
        return _tick_bufs[symbol][:limit]

    def get_active_trades(self) -> list:
        return list(_sim_active.values())

    def get_trading_paused(self) -> bool:
        return False  # backtest always runs

    def get_selected_stocks(self) -> list:
        return []  # not needed in backtest

    def insert_trade(self, symbol, direction, entry_price, entry_qty,
                     stop_loss_price, signal_meta=None, is_flip=False):
        _sim_id[0] += 1
        tid = _sim_id[0]
        _sim_active[tid] = {
            'id':              tid,
            'symbol':          symbol,
            'direction':       direction,
            'entry_price':     entry_price,
            'entry_qty':       entry_qty,
            'stop_loss_price': stop_loss_price,
            'entry_time':      _SIM_NOW[0],   # simulated entry time
            'exit_price':      None,
            'exit_time':       None,
            'exit_reason':     None,
            'pnl':             None,
        }
        return tid

    def get_trade_by_id(self, trade_id: int) -> dict:
        if trade_id in _sim_active:
            return _sim_active[trade_id]
        return next((t for t in _sim_closed if t['id'] == trade_id), None)

    def get_today_trades(self) -> list:
        return list(_sim_active.values()) + _sim_closed

    def get_today_pnl(self) -> float:
        return sum(t['pnl'] for t in _sim_closed if t['pnl'] is not None)

    def update_trade_exit(self, trade_id: int, exit_price: float, exit_reason: str):
        trade = _sim_active.pop(trade_id, None)
        if not trade:
            return
        if trade['direction'] == 'BUY':
            pnl = (exit_price - trade['entry_price']) * trade['entry_qty']
        else:
            pnl = (trade['entry_price'] - exit_price) * trade['entry_qty']
        trade['exit_price']  = exit_price
        trade['exit_reason'] = exit_reason
        trade['pnl']         = pnl
        trade['exit_time']   = _SIM_NOW[0]
        _sim_closed.append(trade)


_db_mod.db = _BacktestDB()


# ── Patch trading.paper_trader ────────────────────────────────────────────────
# Import AFTER patching db so that paper_trader module gets BacktestDB when
# it runs `from data.database import db`.

import trading.paper_trader as _pt_mod
from strategy.stop_loss import calculate_stop_loss


class _BacktestPaperTrader:
    def place_order(self, symbol, direction, quantity, entry_price,
                    signal_meta=None, is_flip=False):
        sl = calculate_stop_loss(entry_price, direction)
        return _db_mod.db.insert_trade(
            symbol, direction, entry_price, quantity, sl, signal_meta, is_flip
        )

    def close_order(self, trade_id, exit_price, exit_reason):
        _db_mod.db.update_trade_exit(trade_id, exit_price, exit_reason)
        return True

    def get_trade_by_id(self, trade_id):
        return _db_mod.db.get_trade_by_id(trade_id)


_pt_mod.paper_trader = _BacktestPaperTrader()


# ── Patch trading.portfolio and trading.order_manager ─────────────────────────
# Import AFTER patching db and paper_trader so their `from X import Y` bindings
# point to our backtest implementations.

import trading.portfolio as _port_mod
import trading.order_manager as _om_mod
# Re-instantiate so the instances are created with the already-patched db.
_port_mod.portfolio      = _port_mod.Portfolio()
_om_mod.order_manager    = _om_mod.OrderManager()


# ── Suppress log output ───────────────────────────────────────────────────────
# Replace log_event with a no-op so the backtest doesn't flood stdout or
# write a 50,000-line log file.

import core.logger as _log_mod
_log_mod.log_event = lambda *args, **kwargs: None


# ── Patch datetime.now in strategy modules ────────────────────────────────────
# Import each module first (creates it in sys.modules), THEN replace its
# `datetime` name so all subsequent calls to datetime.now() return _SIM_NOW[0].
# This must happen BEFORE strategy.pipeline is imported.

import strategy.exit_logic      as _el_mod
import strategy.phases.entry    as _entry_mod
import strategy.phases.incubator as _inc_mod
import strategy.phases.monitor  as _mon_mod

_el_mod.datetime    = _SimDatetime
_entry_mod.datetime = _SimDatetime
_inc_mod.datetime   = _SimDatetime
_mon_mod.datetime   = _SimDatetime


# ── Import pipeline (picks up all patches above) ──────────────────────────────
from strategy.pipeline import TradePipeline
from config import config


# ── Brokerage calculation (Zerodha intraday NSE equity) ───────────────────────
def calc_brokerage(entry_price: float, exit_price: float, qty: int, direction: str) -> float:
    """
    Zerodha intraday equity charges for one round-trip trade (entry + exit).

    Components:
      Brokerage    : min(₹20, 0.03% of trade value) per leg — both entry and exit
      STT          : 0.025% on sell-side turnover only
                     BUY trade → sell is the exit leg
                     SELL trade → sell is the entry leg
      Exchange     : NSE = 0.00325% of total turnover (entry + exit)
      GST          : 18% on (brokerage + exchange charges)
      SEBI         : ₹10 per crore = 0.000001 of total turnover
      Stamp duty   : 0.003% on buy-side turnover only (the entry for BUY, exit for SELL)
    """
    entry_val = entry_price * qty
    exit_val  = exit_price  * qty
    turnover  = entry_val + exit_val

    brokerage = min(20.0, 0.0003 * entry_val) + min(20.0, 0.0003 * exit_val)

    # STT: charged only on the sell leg
    stt = 0.00025 * (exit_val if direction == 'BUY' else entry_val)

    exchange  = 0.0000325 * turnover
    gst       = 0.18 * (brokerage + exchange)
    sebi      = 0.000001 * turnover   # ₹10 per crore
    stamp     = 0.00003 * (entry_val if direction == 'BUY' else exit_val)  # buy side

    return round(brokerage + stt + exchange + gst + sebi + stamp, 2)


# ── Main ───────────────────────────────────────────────────────────────────────

def run(date_str: str = None):
    """
    Run a backtest for a given date (YYYY-MM-DD).  Defaults to today.

    The watchlist is auto-detected: any symbol that has ticks on that date
    in trading.db is included.  The 09:15–09:30 window pre-populates the
    tick buffers so the incubator trend gate has 5 minutes of history at
    the very first 09:30 tick.
    """
    # Connect to the real DB for reading ticks only — no writes happen here.
    real_conn = sqlite3.connect('trading.db')
    real_conn.row_factory = sqlite3.Row

    # Determine the date to replay.
    if date_str:
        year, month, day = map(int, date_str.split('-'))
    else:
        year, month, day = 2026, 3, 25

    # Time boundaries (Unix seconds).
    t_915 = int(_dt_module.datetime(year, month, day,  9, 15, 0).timestamp())
    t_930 = int(_dt_module.datetime(year, month, day,  9, 30, 0).timestamp())
    t_end = int(_dt_module.datetime(year, month, day, 15, 30, 0).timestamp())

    # Auto-detect watchlist: all symbols that have post-09:30 ticks that day.
    sym_rows = real_conn.execute("""
        SELECT DISTINCT symbol FROM ticks
        WHERE timestamp >= ? AND timestamp <= ?
    """, [t_930, t_end]).fetchall()
    watchlist = {r['symbol'] for r in sym_rows}

    rows = real_conn.execute(f"""
        SELECT symbol, timestamp, ltp, bid_qty, ask_qty, volume
        FROM ticks
        WHERE timestamp >= ? AND timestamp <= ?
          AND symbol IN ({','.join('?' * len(watchlist))})
        ORDER BY timestamp ASC, rowid ASC
    """, [t_915, t_end] + list(watchlist)).fetchall()
    real_conn.close()

    all_ticks  = [dict(r) for r in rows]
    pre_ticks  = [t for t in all_ticks if t['timestamp'] <  t_930]
    post_ticks = [t for t in all_ticks if t['timestamp'] >= t_930]

    date_label = f"{year:04d}-{month:02d}-{day:02d}"
    print(f"\nDate      : {date_label}")
    print(f"Symbols   : {sorted(watchlist)}")
    print(f"Config    : CAPITAL_PER_TRADE=₹{config.CAPITAL_PER_TRADE:,}  "
          f"MAX_ACTIVE_TRADES={config.MAX_ACTIVE_TRADES}  "
          f"STOP_LOSS={config.STOP_LOSS_PCT*100:.1f}%  "
          f"TIME_EXIT={config.TIME_EXIT_MINUTES}m  "
          f"BID_ASK={config.BID_ASK_THRESHOLD_RATIO}x  "
          f"INCUB_TICKS={config.INCUBATION_MIN_TICKS}")
    print(f"Pre-ticks : {len(pre_ticks):,}  (buffer for trend gate)")
    print(f"Replay    : {len(post_ticks):,}  ticks after 09:30")
    print("Starting replay...\n")

    # Set simulation clock to the start of the replay day.
    _SIM_NOW[0] = _dt_module.datetime(year, month, day, 9, 30, 0)

    # Pre-populate tick buffers so the incubator trend gate has history
    # at the very first post-09:30 tick.
    for tick in pre_ticks:
        sym = tick['symbol']
        buf = _tick_bufs[sym]
        buf.insert(0, tick)
        if len(buf) > 200:
            buf.pop()

    pipeline = TradePipeline()
    paused   = set()  # no per-symbol pauses in backtest

    # Replay every post-09:30 tick in chronological order
    for tick in post_ticks:
        sym = tick['symbol']
        ts  = tick['timestamp']

        # Advance the simulated clock to this tick's time
        _SIM_NOW[0] = _dt_module.datetime.fromtimestamp(ts)

        # Add tick to buffer BEFORE calling on_tick, matching the live system's
        # behaviour (tick is saved to DB before on_tick_received is called).
        buf = _tick_bufs[sym]
        buf.insert(0, tick)
        if len(buf) > 200:
            buf.pop()

        pipeline.on_tick(tick, watchlist, paused)

    # ── Summary ────────────────────────────────────────────────────────────────
    print(f"\nReplay complete.")
    print(f"  Closed : {len(_sim_closed)}  |  Still open (ignored): {len(_sim_active)}\n")

    if not _sim_closed:
        print("No trades executed in simulation.")
        return

    closed   = sorted(_sim_closed, key=lambda t: t['entry_time'])
    pnls     = [t['pnl'] for t in closed]
    wins     = [t for t in closed if t['pnl'] > 0]
    losses   = [t for t in closed if t['pnl'] <= 0]
    win_rate = len(wins) / len(closed) * 100

    total_pnl  = sum(pnls)
    total_brok = sum(
        calc_brokerage(t['entry_price'], t['exit_price'], t['entry_qty'], t['direction'])
        for t in closed
    )
    net_pnl = total_pnl - total_brok

    hold_mins = [
        (t['exit_time'] - t['entry_time']).total_seconds() / 60
        for t in closed
    ]
    avg_hold = sum(hold_mins) / len(hold_mins)

    reasons = defaultdict(int)
    for t in closed:
        key = (t['exit_reason'] or 'UNKNOWN').split('(')[0].strip()
        reasons[key] += 1

    sym_pnl   = defaultdict(float)
    sym_count = defaultdict(int)
    for t in closed:
        sym_pnl[t['symbol']]   += t['pnl']
        sym_count[t['symbol']] += 1

    # ── Print results ──────────────────────────────────────────────────────────
    W = 65
    print("=" * W)
    print("  BACKTEST RESULTS — New Pipeline with Incubation Logic")
    print(f"  {date_label} | Post 09:30 | Paper | {len(watchlist)} symbols")
    print("=" * W)
    print(f"\n  Trades          : {len(closed)}")
    print(f"  Wins / Losses   : {len(wins)} W / {len(losses)} L")
    print(f"  Win rate        : {win_rate:.1f}%")
    print(f"\n  Gross P&L       : ₹{total_pnl:+,.2f}")
    print(f"  Brokerage + tax : ₹{total_brok:,.2f}")
    print(f"  Net P&L         : ₹{net_pnl:+,.2f}")
    print(f"\n  Best trade      : ₹{max(pnls):+,.2f}")
    print(f"  Worst trade     : ₹{min(pnls):+,.2f}")
    print(f"  Avg P&L/trade   : ₹{total_pnl / len(closed):+,.2f}")
    print(f"  Avg hold        : {avg_hold:.1f} min")

    print(f"\n  Exit reasons:")
    for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"    {reason:<35} {count:>3}")

    print(f"\n  By symbol (gross P&L):")
    for sym in sorted(sym_pnl, key=lambda s: -sym_pnl[s]):
        w = len([t for t in closed if t['symbol'] == sym and t['pnl'] > 0])
        l = sym_count[sym] - w
        print(f"    {sym:<14}  {sym_count[sym]:>3} trades  "
              f"{w}W/{l}L  ₹{sym_pnl[sym]:+,.2f}")

    print(f"\n  TRADE LOG:")
    header = f"  {'#':<4} {'Time':<6} {'Symbol':<12} {'Dir':<5} {'Entry':>7} " \
             f"{'Exit':>7} {'Qty':>5} {'P&L':>10}  Reason"
    print(header)
    print("  " + "-" * (W + 14))
    for i, t in enumerate(closed, 1):
        et_str = t['entry_time'].strftime('%H:%M')
        reason = (t['exit_reason'] or '')[:38]
        print(f"  {i:<4} {et_str:<6} {t['symbol']:<12} {t['direction']:<5} "
              f"{t['entry_price']:>7.2f} {t['exit_price']:>7.2f} {t['entry_qty']:>5} "
              f"₹{t['pnl']:>+9,.2f}  {reason}")

    print("=" * W)


if __name__ == '__main__':
    run(sys.argv[1] if len(sys.argv) > 1 else None)
