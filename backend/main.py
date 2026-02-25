"""
FastAPI application entry-point.

Provides:
  - REST endpoints (auth, trading control, history)
  - WebSocket /ws — broadcasts real-time ticks, signals, and strategy state
    to all connected frontend clients
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from db.database import init_db
from market_data import set_broadcast_callback
from order_manager import order_manager
from routers import auth, trading, history
from trade_logger import save_trade

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ── WebSocket connection manager ──────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self._connections: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._connections.append(ws)
        logger.info("WS client connected. Total: %d", len(self._connections))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._connections = [c for c in self._connections if c is not ws]
        logger.info("WS client disconnected. Total: %d", len(self._connections))

    async def broadcast(self, message: dict) -> None:
        if not self._connections:
            return
        data = json.dumps(message, default=str)
        async with self._lock:
            dead: list[WebSocket] = []
            for ws in self._connections:
                try:
                    await ws.send_text(data)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self._connections.remove(ws)


ws_manager = ConnectionManager()


# ── Trade-closed callback (called by OrderManager) ────────────────────────────

async def _on_trade_closed(trade_dict: dict) -> None:
    """Persist to DB and broadcast to all WS clients."""
    await save_trade(trade_dict)
    await ws_manager.broadcast({"type": "trade_closed", "payload": trade_dict})


# ── App lifecycle ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
    set_broadcast_callback(ws_manager.broadcast)
    order_manager.set_trade_closed_callback(_on_trade_closed)
    logger.info("Database initialised. Application ready.")

    # If an access token is already set in env, initialise Kite immediately
    if settings.KITE_ACCESS_TOKEN:
        from kite_client import kite_client
        kite_client.initialise(settings.KITE_ACCESS_TOKEN)
        logger.info("KiteConnect initialised from env token.")

    yield

    # Shutdown
    from market_data import stop_market_data
    await stop_market_data()
    logger.info("Application shutting down.")


# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Zerodha Momentum Trader",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow the Vite dev server on :5173 and any production origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(trading.router)
app.include_router(history.router)


# ── WebSocket endpoint ────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True:
            # Keep connection alive; we mostly push from server → client
            data = await ws.receive_text()
            # Handle ping/pong from client
            if data == "ping":
                await ws.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        await ws_manager.disconnect(ws)


# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
