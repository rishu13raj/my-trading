"""
TradePipeline — the orchestrator.

Wires the four phases together and is the single entry point called from
main.py for every tick received from Zerodha's WebSocket.

Flow per tick:
                                         ┌──────────────────────────────────┐
  WebSocket tick ──→  pipeline.on_tick() │                                  │
                                         │  [Monitor]  always runs first    │
                                         │    ↓ ExitDecision?               │
                                         │      yes → _execute_exit()       │
                                         │            record_exit, return   │
                                         │                                  │
                                         │  Gates (watchlist, paused,       │
                                         │         active position,         │
                                         │         portfolio capacity)      │
                                         │                                  │
                                         │  [Scanner]  → CandidateSignal?   │
                                         │  [Incubator] → ConfirmedSignal?  │
                                         │  [Entry]    → OpenPosition?      │
                                         └──────────────────────────────────┘

Special case — MOMENTUM_FLIP:
  Monitor returns ExitDecision(reason='MOMENTUM_FLIP').
  _execute_exit() closes the position, then calls entry.open_flip() to
  immediately open the reverse trade (bypasses incubation and cooldown).

main.py responsibilities:
  - Create one TradePipeline instance at startup.
  - Pass `selected_stocks` and `paused_stocks` on each call (they change at
    runtime via API endpoints).
  - Handle infrastructure (FastAPI routes, WebSocket, EOD square-off, logging).
  - Pipeline never touches HTTP or WebSocket concerns.
"""

from typing import Set

from core.logger import log_event
from data.database import db
from strategy.phases.entry import EntryPhase
from strategy.phases.incubator import Incubator
from strategy.phases.monitor import Monitor
from strategy.phases.scanner import Scanner
from strategy.types import ConfirmedSignal, ExitDecision
from trading.order_manager import order_manager
from trading.paper_trader import paper_trader


class TradePipeline:
    def __init__(self):
        self.scanner = Scanner()
        self.incubator = Incubator()
        self.entry = EntryPhase()
        self.monitor = Monitor()

    # ── main entry point ─────────────────────────────────────────────────────

    def on_tick(
        self,
        tick_data: dict,
        selected_stocks: Set[str],
        paused_stocks: Set[str],
    ) -> None:
        symbol = tick_data.get("symbol")
        current_price = tick_data.get("ltp", 0)
        bid_qty = tick_data.get("bid_qty", 0)
        ask_qty = tick_data.get("ask_qty", 0)

        if not symbol or not current_price:
            return

        ratio = round(bid_qty / ask_qty, 2) if ask_qty else 0
        log_event(
            "info",
            f"{symbol}  ₹{current_price}  bid={bid_qty}  ask={ask_qty}  ratio={ratio}x",
            symbol=symbol,
        )

        # Fetch tick history once — all phases share the same snapshot this tick
        tick_history = db.get_recent_ticks(symbol, limit=100)

        # ── Phase 4: Monitor — ALWAYS runs (exits fire even if stock is paused) ──
        exit_decision = self.monitor.evaluate(symbol, current_price, tick_history)
        if exit_decision:
            self._execute_exit(exit_decision, tick_data, tick_history)
            return  # don't evaluate entry on the same tick as an exit

        # ── Gate 1: watchlist ─────────────────────────────────────────────────
        if symbol not in selected_stocks:
            return

        # ── Gate 2: paused (exits ran above, new entries blocked) ─────────────
        if symbol in paused_stocks:
            return

        # ── Gate 3: already holding this symbol ──────────────────────────────
        active = [t for t in db.get_active_trades() if t["symbol"] == symbol]
        if active:
            return

        # ── Gate 4: portfolio capacity ────────────────────────────────────────
        if not order_manager.get_portfolio_status()["can_open_new"]:
            return

        # ── Phase 1: Scanner ──────────────────────────────────────────────────
        candidate = self.scanner.evaluate(symbol, tick_data, tick_history)
        if candidate:
            log_event(
                "signal",
                f"{symbol} → {candidate.action} signal  ratio={candidate.ratio:.2f}x",
                symbol=symbol,
            )

        # ── Phase 2: Incubator ────────────────────────────────────────────────
        confirmed = self.incubator.update(symbol, candidate, current_price, tick_history)
        if confirmed is None:
            return

        # ── Phase 3: Entry ────────────────────────────────────────────────────
        self.entry.open(confirmed, current_price)

    # ── exit execution ────────────────────────────────────────────────────────

    def _execute_exit(
        self,
        decision: ExitDecision,
        tick_data: dict,
        tick_history: list,
    ) -> None:
        """Close the position and handle any follow-on logic (flip reverse entry)."""
        symbol = decision.symbol
        current_price = decision.exit_price
        trade_id = decision.trade_id

        if decision.reason == "MOMENTUM_FLIP":
            # Close current position
            paper_trader.close_order(trade_id, current_price, "MOMENTUM_FLIP")
            log_event(
                "trade",
                f"MOMENTUM FLIP: closed {decision.direction} {symbol} @ ₹{current_price}",
                symbol=symbol,
            )
            self.monitor.clear_position(trade_id)
            self.entry.record_exit(symbol)

            # Immediately open reverse trade — bypasses incubation and cooldown
            reverse_action = "BUY" if decision.direction == "SELL" else "SELL"
            bid_qty = tick_data.get("bid_qty", 0)
            ask_qty = tick_data.get("ask_qty", 0)
            flip_confirmed = ConfirmedSignal(
                symbol=symbol,
                action=reverse_action,
                entry_price=current_price,
                ratio=Scanner._display_ratio(bid_qty, ask_qty, reverse_action),
                bid_qty=bid_qty,
                ask_qty=ask_qty,
                signal_meta=Scanner._build_meta(
                    {"action": reverse_action, "confidence": 1.0},
                    tick_history,
                    bid_qty,
                    ask_qty,
                ),
            )
            self.entry.open_flip(flip_confirmed, current_price)

        else:
            paper_trader.close_order(trade_id, current_price, decision.reason)
            log_event(
                "trade",
                f"CLOSED {decision.direction} {symbol} @ ₹{current_price} | {decision.reason}",
                symbol=symbol,
            )
            self.monitor.clear_position(trade_id)
            self.entry.record_exit(symbol)

    # ── housekeeping ──────────────────────────────────────────────────────────

    def reset_symbol(self, symbol: str) -> None:
        """Clear incubation state when a stock is removed from the watchlist."""
        self.incubator.clear(symbol)
