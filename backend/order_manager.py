"""
OrderManager: bridges TradeSignals from the strategy engine to actual Zerodha orders.

Responsibilities:
  - Translate signals into BUY / SELL (or short SELL / buy-to-cover) orders
  - Calculate position size from configured capital
  - Track open orders & filled quantities
  - Compute P&L when a position is closed
  - Enforce per-trade stop-loss
  - Notify the strategy engine of realised P&L (for daily stop-loss tracking)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Awaitable

from models import Direction, TradeSignal, TradeStatus
from kite_client import kite_client
from strategy import strategy

logger = logging.getLogger(__name__)


@dataclass
class OpenPosition:
    symbol: str
    exchange: str
    direction: Direction
    quantity: int
    entry_price: float
    entry_time: datetime
    entry_obi: float
    order_id: str
    stop_loss_price: float     # absolute price, not %


class OrderManager:
    """
    Manages order lifecycle for the session.
    `on_trade_closed` callback is used to persist the record to the DB
    and broadcast via WebSocket.
    """

    def __init__(self, capital_per_trade: float = 10_000.0, per_trade_stop_loss: float = 400.0) -> None:
        self.capital_per_trade = capital_per_trade
        self.per_trade_stop_loss = per_trade_stop_loss
        self._positions: dict[str, OpenPosition] = {}
        self._on_trade_closed: Callable[[dict], Awaitable[None]] | None = None

    def configure(self, capital_per_trade: float, per_trade_stop_loss: float) -> None:
        self.capital_per_trade = capital_per_trade
        self.per_trade_stop_loss = per_trade_stop_loss

    def set_trade_closed_callback(self, cb: Callable[[dict], Awaitable[None]]) -> None:
        self._on_trade_closed = cb

    # ── Signal handler ────────────────────────────────────────────────────────

    async def handle_signal(
        self,
        signal: TradeSignal,
        exchange: str = "NSE",
    ) -> None:
        """
        Called by the market data loop whenever the strategy fires a signal.
        Determines whether this is an entry or exit and acts accordingly.
        """
        sym = signal.symbol

        if sym in self._positions:
            # We have an open position → this is an exit signal
            await self._close_position(sym, signal)
        else:
            # No open position → entry signal
            await self._open_position(signal, exchange)

    # ── Entry ─────────────────────────────────────────────────────────────────

    async def _open_position(self, signal: TradeSignal, exchange: str) -> None:
        sym = signal.symbol
        ltp = signal.ltp

        if ltp <= 0:
            logger.warning("Cannot open position: invalid LTP %.2f for %s", ltp, sym)
            return

        quantity = max(1, int(self.capital_per_trade // ltp))

        # For a SHORT we need to sell first (SELL transaction_type)
        transaction_type = "BUY" if signal.direction == Direction.LONG else "SELL"

        # Stop-loss price
        if signal.direction == Direction.LONG:
            stop_loss_price = ltp - (self.per_trade_stop_loss / quantity)
        else:
            stop_loss_price = ltp + (self.per_trade_stop_loss / quantity)

        try:
            order_id = await kite_client.place_order(
                exchange=exchange,
                symbol=sym,
                transaction_type=transaction_type,
                quantity=quantity,
                order_type="MARKET",
                product="MIS",
            )
        except Exception as exc:
            logger.error("Failed to place %s order for %s: %s", signal.direction, sym, exc)
            return

        self._positions[sym] = OpenPosition(
            symbol=sym,
            exchange=exchange,
            direction=signal.direction,
            quantity=quantity,
            entry_price=ltp,
            entry_time=datetime.utcnow(),
            entry_obi=signal.obi,
            order_id=order_id,
            stop_loss_price=stop_loss_price,
        )

        logger.info(
            "POSITION OPENED: %s %s qty=%d entry=%.2f SL=%.2f order=%s",
            signal.direction, sym, quantity, ltp, stop_loss_price, order_id,
        )

    # ── Exit ──────────────────────────────────────────────────────────────────

    async def _close_position(self, sym: str, signal: TradeSignal) -> None:
        pos = self._positions.pop(sym)
        exit_price = signal.ltp

        # Reverse transaction to close
        transaction_type = "SELL" if pos.direction == Direction.LONG else "BUY"

        try:
            await kite_client.place_order(
                exchange=pos.exchange,
                symbol=sym,
                transaction_type=transaction_type,
                quantity=pos.quantity,
                order_type="MARKET",
                product="MIS",
            )
        except Exception as exc:
            logger.error("Failed to close position for %s: %s", sym, exc)
            # Re-insert position so we can retry
            self._positions[sym] = pos
            return

        pnl = self._compute_pnl(pos, exit_price)
        halted = strategy.record_trade_pnl(pnl)

        trade_record = {
            "symbol": sym,
            "direction": pos.direction.value,
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "quantity": pos.quantity,
            "entry_obi": pos.entry_obi,
            "exit_obi": signal.obi,
            "pnl": pnl,
            "status": TradeStatus.STOPPED.value if halted else TradeStatus.CLOSED.value,
            "entry_time": pos.entry_time.isoformat(),
            "exit_time": datetime.utcnow().isoformat(),
            "notes": signal.reason,
        }

        logger.info(
            "POSITION CLOSED: %s %s exit=%.2f pnl=%.2f | %s",
            pos.direction, sym, exit_price, pnl, signal.reason,
        )

        if self._on_trade_closed:
            await self._on_trade_closed(trade_record)

    # ── Stop-loss check (called on every tick) ────────────────────────────────

    async def check_stop_losses(self, symbol: str, ltp: float) -> None:
        """
        Called on every price tick to check if the stop-loss level has been hit.
        If hit, generates a synthetic exit signal.
        """
        if symbol not in self._positions:
            return
        pos = self._positions[symbol]

        hit = False
        if pos.direction == Direction.LONG and ltp <= pos.stop_loss_price:
            hit = True
        elif pos.direction == Direction.SHORT and ltp >= pos.stop_loss_price:
            hit = True

        if hit:
            logger.warning(
                "STOP-LOSS HIT for %s %s: ltp=%.2f SL=%.2f",
                pos.direction, symbol, ltp, pos.stop_loss_price,
            )
            from models import TradeSignal, Direction as D
            synthetic_signal = TradeSignal(
                symbol=symbol,
                direction=D.SHORT if pos.direction == D.LONG else D.LONG,
                obi=0.0,
                ltp=ltp,
                quantity=pos.quantity,
                reason=f"Stop-loss triggered at {ltp:.2f} (SL={pos.stop_loss_price:.2f})",
            )
            await self._close_position(symbol, synthetic_signal)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _compute_pnl(pos: OpenPosition, exit_price: float) -> float:
        if pos.direction == Direction.LONG:
            return (exit_price - pos.entry_price) * pos.quantity
        else:  # SHORT
            return (pos.entry_price - exit_price) * pos.quantity

    @property
    def positions(self) -> dict[str, OpenPosition]:
        return dict(self._positions)


# ── Global singleton ──────────────────────────────────────────────────────────
order_manager = OrderManager()
