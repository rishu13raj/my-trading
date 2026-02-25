"""
Thin async-friendly wrapper around the KiteConnect REST client and KiteTicker WebSocket.

KiteConnect's SDK is synchronous; we run blocking calls in a thread-pool via
asyncio.run_in_executor so the FastAPI event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import Any

from kiteconnect import KiteConnect, KiteTicker

from config import settings

logger = logging.getLogger(__name__)


class KiteClient:
    """
    Singleton wrapper around KiteConnect REST API and KiteTicker WebSocket.
    Call `initialise(access_token)` once per session after the Zerodha OAuth flow.
    """

    def __init__(self) -> None:
        self._kite: KiteConnect | None = None
        self._ticker: KiteTicker | None = None
        self._subscribed_tokens: set[int] = set()
        self._ticker_thread: threading.Thread | None = None
        self._on_tick_cb: Callable[[list[dict]], None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # ── Initialisation ────────────────────────────────────────────────────────

    def initialise(self, access_token: str) -> None:
        """Set up the REST client with a fresh access token (call once per day)."""
        self._kite = KiteConnect(api_key=settings.KITE_API_KEY)
        self._kite.set_access_token(access_token)
        logger.info("KiteConnect REST client initialised.")

    @property
    def is_ready(self) -> bool:
        return self._kite is not None

    def _ensure_ready(self) -> None:
        if not self.is_ready:
            raise RuntimeError("KiteClient not initialised. POST /auth/token first.")

    # ── REST helpers (run in executor to avoid blocking the event loop) ───────

    async def _run(self, fn: Callable, *args, **kwargs) -> Any:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))

    async def get_quote(self, instruments: list[str]) -> dict:
        """
        Fetch full market quote (includes depth) for a list of instruments.
        instrument format: "NSE:INFY"
        """
        self._ensure_ready()
        return await self._run(self._kite.quote, instruments)

    async def get_ltp(self, instruments: list[str]) -> dict:
        self._ensure_ready()
        return await self._run(self._kite.ltp, instruments)

    async def instrument_token(self, exchange: str, symbol: str) -> int:
        """Look up the numeric instrument token needed for WebSocket subscription."""
        self._ensure_ready()
        instruments = await self._run(self._kite.instruments, exchange)
        for inst in instruments:
            if inst["tradingsymbol"] == symbol:
                return inst["instrument_token"]
        raise ValueError(f"Symbol {symbol} not found on {exchange}")

    async def place_order(
        self,
        *,
        exchange: str,
        symbol: str,
        transaction_type: str,   # "BUY" or "SELL"
        quantity: int,
        order_type: str = "MARKET",
        product: str = "MIS",    # MIS = intraday
        price: float | None = None,
        tag: str = "momentum_bot",
    ) -> str:
        """Place an order and return the Zerodha order ID."""
        self._ensure_ready()
        params: dict[str, Any] = dict(
            exchange=exchange,
            tradingsymbol=symbol,
            transaction_type=transaction_type,
            quantity=quantity,
            order_type=order_type,
            product=product,
            tag=tag,
        )
        if price is not None:
            params["price"] = price
        order_id = await self._run(self._kite.place_order, variety="regular", **params)
        logger.info("Order placed: %s %s %s qty=%d → order_id=%s",
                    transaction_type, exchange, symbol, quantity, order_id)
        return str(order_id)

    async def cancel_order(self, order_id: str) -> None:
        self._ensure_ready()
        await self._run(self._kite.cancel_order, variety="regular", order_id=order_id)
        logger.info("Order cancelled: %s", order_id)

    async def get_positions(self) -> dict:
        self._ensure_ready()
        return await self._run(self._kite.positions)

    async def get_orders(self) -> list:
        self._ensure_ready()
        return await self._run(self._kite.orders)

    # ── WebSocket (KiteTicker) ────────────────────────────────────────────────

    def start_ticker(
        self,
        tokens: list[int],
        on_tick: Callable[[list[dict]], None],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """
        Start the KiteTicker WebSocket in a background thread.
        `on_tick` is called with the raw Kite tick list on every market update.
        """
        self._ensure_ready()
        self._on_tick_cb = on_tick
        self._loop = loop
        self._subscribed_tokens = set(tokens)

        self._ticker = KiteTicker(
            api_key=settings.KITE_API_KEY,
            access_token=self._kite.access_token,  # type: ignore[union-attr]
        )

        def _on_ticks(ws, ticks):  # noqa: ARG001
            if self._on_tick_cb:
                self._on_tick_cb(ticks)

        def _on_connect(ws, response):  # noqa: ARG001
            logger.info("KiteTicker connected, subscribing to tokens: %s", tokens)
            ws.subscribe(tokens)
            ws.set_mode(ws.MODE_FULL, tokens)

        def _on_close(ws, code, reason):  # noqa: ARG001
            logger.warning("KiteTicker closed: %s %s", code, reason)

        def _on_error(ws, code, reason):  # noqa: ARG001
            logger.error("KiteTicker error: %s %s", code, reason)

        def _on_reconnect(ws, attempts_count):  # noqa: ARG001
            logger.info("KiteTicker reconnecting, attempt %d", attempts_count)

        self._ticker.on_ticks = _on_ticks
        self._ticker.on_connect = _on_connect
        self._ticker.on_close = _on_close
        self._ticker.on_error = _on_error
        self._ticker.on_reconnect = _on_reconnect

        self._ticker_thread = threading.Thread(
            target=self._ticker.connect, kwargs={"threaded": True}, daemon=True
        )
        self._ticker_thread.start()
        logger.info("KiteTicker started in background thread.")

    def stop_ticker(self) -> None:
        if self._ticker:
            try:
                self._ticker.close()
            except Exception:
                pass
            self._ticker = None
        logger.info("KiteTicker stopped.")

    # ── Login URL helper ──────────────────────────────────────────────────────

    def login_url(self) -> str:
        """Return the Zerodha Kite login URL for the OAuth flow."""
        kite = KiteConnect(api_key=settings.KITE_API_KEY)
        return kite.login_url()

    async def generate_session(self, request_token: str) -> str:
        """
        Exchange a request_token for an access_token.
        Returns the access_token string.
        """
        kite = KiteConnect(api_key=settings.KITE_API_KEY)
        data = await self._run(
            kite.generate_session, request_token, api_secret=settings.KITE_API_SECRET
        )
        return data["access_token"]


# ── Global singleton ──────────────────────────────────────────────────────────
kite_client = KiteClient()
