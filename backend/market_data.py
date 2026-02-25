"""
Market data loop: connects to Zerodha (WebSocket preferred, REST poll as fallback),
converts raw ticks into TickData, feeds them to the strategy engine, and
broadcasts updates to connected frontend WebSocket clients.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from kite_client import kite_client
from models import TickData
from strategy import strategy
from order_manager import order_manager
from config import settings

logger = logging.getLogger(__name__)

# Resolved once when session starts: "NSE:INFY" → instrument_token (int)
_token_to_symbol: dict[int, str] = {}
_symbol_to_exchange: dict[str, str] = {}

# Queue from the KiteTicker thread into the asyncio event loop
_tick_queue: asyncio.Queue[list[dict]] = asyncio.Queue(maxsize=500)

# Broadcast sink — set by main.py
_broadcast_cb = None
_poll_task: asyncio.Task | None = None
_using_websocket: bool = False


def set_broadcast_callback(cb) -> None:
    global _broadcast_cb
    _broadcast_cb = cb


async def _broadcast(msg_type: str, payload: dict) -> None:
    if _broadcast_cb:
        await _broadcast_cb({"type": msg_type, "payload": payload})


# ── Raw tick → TickData ───────────────────────────────────────────────────────

def _parse_tick(raw: dict) -> TickData | None:
    token = raw.get("instrument_token")
    symbol = _token_to_symbol.get(token)
    if symbol is None:
        return None

    depth = raw.get("depth", {})
    buy_orders = depth.get("buy", [{}])
    sell_orders = depth.get("sell", [{}])

    bid_price = buy_orders[0].get("price", 0.0) if buy_orders else 0.0
    bid_qty   = buy_orders[0].get("quantity", 0) if buy_orders else 0
    ask_price = sell_orders[0].get("price", 0.0) if sell_orders else 0.0
    ask_qty   = sell_orders[0].get("quantity", 0) if sell_orders else 0

    total_buy_qty  = raw.get("total_buy_quantity", bid_qty)
    total_sell_qty = raw.get("total_sell_quantity", ask_qty)

    total = total_buy_qty + total_sell_qty
    obi = (total_buy_qty - total_sell_qty) / total if total else 0.0

    return TickData(
        symbol=symbol,
        ltp=raw.get("last_price", 0.0),
        bid_price=bid_price,
        bid_qty=bid_qty,
        ask_price=ask_price,
        ask_qty=ask_qty,
        total_buy_qty=total_buy_qty,
        total_sell_qty=total_sell_qty,
        obi=obi,
        obi_rate=0.0,   # filled by strategy after history update
        volume=raw.get("volume_traded", 0),
        timestamp=datetime.utcnow(),
    )


# ── Parse quote API response (REST fallback) ──────────────────────────────────

def _parse_quote(symbol: str, q: dict) -> TickData | None:
    depth = q.get("depth", {})
    buy_orders  = depth.get("buy",  [{}])
    sell_orders = depth.get("sell", [{}])

    bid_price = buy_orders[0].get("price", 0.0) if buy_orders else 0.0
    bid_qty   = buy_orders[0].get("quantity", 0) if buy_orders else 0
    ask_price = sell_orders[0].get("price", 0.0) if sell_orders else 0.0
    ask_qty   = sell_orders[0].get("quantity", 0) if sell_orders else 0

    total_buy_qty  = q.get("total_buy_quantity", bid_qty)
    total_sell_qty = q.get("total_sell_quantity", ask_qty)
    total = total_buy_qty + total_sell_qty
    obi = (total_buy_qty - total_sell_qty) / total if total else 0.0

    return TickData(
        symbol=symbol,
        ltp=q.get("last_price", 0.0),
        bid_price=bid_price,
        bid_qty=bid_qty,
        ask_price=ask_price,
        ask_qty=ask_qty,
        total_buy_qty=total_buy_qty,
        total_sell_qty=total_sell_qty,
        obi=obi,
        obi_rate=0.0,
        volume=q.get("volume_traded", 0),
        timestamp=datetime.utcnow(),
    )


# ── Process a single TickData ─────────────────────────────────────────────────

async def _process_tick(tick: TickData) -> None:
    # Stop-loss check runs on every tick regardless of strategy state
    await order_manager.check_stop_losses(tick.symbol, tick.ltp)

    # Strategy signal
    signal = strategy.on_tick(tick)

    # Broadcast tick to frontend
    await _broadcast("tick", tick.model_dump(mode="json"))

    # Broadcast strategy snapshot
    await _broadcast("strategy", strategy.snapshot())

    if signal:
        exchange = _symbol_to_exchange.get(tick.symbol, "NSE")
        await order_manager.handle_signal(signal, exchange=exchange)
        await _broadcast("signal", signal.model_dump(mode="json"))


# ── WebSocket consumer loop ───────────────────────────────────────────────────

async def _ws_consumer_loop() -> None:
    """Drain the queue that KiteTicker's background thread pushes into."""
    logger.info("WebSocket consumer loop started.")
    while True:
        try:
            ticks: list[dict] = await asyncio.wait_for(_tick_queue.get(), timeout=5.0)
            for raw in ticks:
                tick = _parse_tick(raw)
                if tick:
                    await _process_tick(tick)
        except asyncio.TimeoutError:
            pass
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("Consumer loop error: %s", exc, exc_info=True)


def _enqueue_ticks(ticks: list[dict]) -> None:
    """Called from KiteTicker thread; puts ticks onto the asyncio queue."""
    try:
        _tick_queue.put_nowait(ticks)
    except asyncio.QueueFull:
        pass  # drop if overwhelmed


# ── REST polling fallback ─────────────────────────────────────────────────────

async def _poll_loop(instruments: list[str]) -> None:
    logger.info("REST poll loop started (interval=%.1fs)", settings.POLL_INTERVAL_SECONDS)
    while True:
        try:
            quotes = await kite_client.get_quote(instruments)
            for inst_key, q in quotes.items():
                # inst_key is "NSE:INFY"
                tick = _parse_quote(inst_key, q)
                if tick:
                    await _process_tick(tick)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("Poll loop error: %s", exc)
        await asyncio.sleep(settings.POLL_INTERVAL_SECONDS)


# ── Session lifecycle ─────────────────────────────────────────────────────────

async def start_market_data(basket: list[dict]) -> None:
    """
    basket: list of {"symbol": "INFY", "exchange": "NSE"}
    Tries WebSocket first; falls back to REST polling if unavailable.
    """
    global _poll_task, _using_websocket, _token_to_symbol, _symbol_to_exchange

    instruments = [f"{item['exchange']}:{item['symbol']}" for item in basket]
    _symbol_to_exchange = {item["symbol"]: item["exchange"] for item in basket}

    # Try to resolve instrument tokens for WebSocket
    tokens = []
    try:
        for item in basket:
            token = await kite_client.instrument_token(item["exchange"], item["symbol"])
            tokens.append(token)
            _token_to_symbol[token] = item["symbol"]
        logger.info("Resolved tokens: %s", _token_to_symbol)
    except Exception as exc:
        logger.warning("Could not resolve instrument tokens (%s); falling back to REST.", exc)

    if tokens:
        try:
            loop = asyncio.get_event_loop()
            kite_client.start_ticker(tokens, on_tick=_enqueue_ticks, loop=loop)
            asyncio.create_task(_ws_consumer_loop())
            _using_websocket = True
            logger.info("Market data: using WebSocket.")
            return
        except Exception as exc:
            logger.warning("WebSocket startup failed (%s); falling back to REST.", exc)

    # REST fallback
    _poll_task = asyncio.create_task(_poll_loop(instruments))
    _using_websocket = False
    logger.info("Market data: using REST polling.")


async def stop_market_data() -> None:
    global _poll_task, _using_websocket
    kite_client.stop_ticker()
    if _poll_task:
        _poll_task.cancel()
        _poll_task = None
    _using_websocket = False
    logger.info("Market data stopped.")
