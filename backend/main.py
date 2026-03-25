"""
main.py — infrastructure layer only.

Responsibilities:
  - FastAPI application, routes, CORS middleware
  - WebSocket feed to frontend
  - EOD square-off watchdog
  - Startup / shutdown lifecycle

All trading business logic lives in strategy/pipeline.py and its phases.
This file should never contain trading rules.
"""

import asyncio
import json
import os
import re
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, WebSocket, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from auth.zerodha_auth import auth
from config import config
from core.logger import activity_log, log_event
from data.database import db
from data.websocket_client import WebSocketClient
from strategy.pipeline import TradePipeline
from trading.order_manager import order_manager

# ── Runtime state ─────────────────────────────────────────────────────────────
ws_client: WebSocketClient = None
monitoring: bool = False
selected_stocks: list = []
paused_stocks: set = set()

# Single pipeline instance — owns all per-phase state
pipeline = TradePipeline()


# ── Startup helpers ───────────────────────────────────────────────────────────

def _check_stale_open_trades(trigger: str = "RESTART") -> None:
    """
    On (re)start, evaluate any still-open trades against current exit criteria
    using live prices from the Zerodha REST API, falling back to the last
    stored tick.  Peak prices are not available after a restart (see TODO:
    persist peak prices to DB), so PROFIT_TRAIL / TRAIL_STOP may not fire
    correctly until a new peak is established.
    """
    import time as _time
    from strategy.exit_logic import should_exit
    from trading.paper_trader import paper_trader

    open_trades = db.get_active_trades()
    if not open_trades:
        print("✓ No open trades to check on restart")
        return

    live_prices = {}
    for attempt in range(1, 11):
        try:
            kite = auth.get_kite_client()
            symbols = list({f"NSE:{t['symbol']}" for t in open_trades})
            ltp_data = kite.ltp(symbols)
            for key, val in ltp_data.items():
                live_prices[key.replace("NSE:", "")] = val["last_price"]
            print(f"  ✓ Live prices fetched for {list(live_prices.keys())} (attempt {attempt})")
            break
        except Exception as e:
            print(f"  ⚠  Live price fetch failed (attempt {attempt}/10): {e}")
            if attempt < 10:
                _time.sleep(3)
            else:
                print("  ⚠  All attempts failed — falling back to last stored tick")

    print(f"⚠  {len(open_trades)} open trade(s) — checking exit criteria ({trigger}) ...")
    for trade in open_trades:
        symbol = trade["symbol"]
        ticks = db.get_recent_ticks(symbol, limit=100)
        if symbol in live_prices:
            current_price = live_prices[symbol]
        elif ticks:
            current_price = ticks[0]["ltp"]
            print(f"  ⚠  {symbol}: using stale tick price ₹{current_price}")
        else:
            print(f"  {symbol}: no price data — skipping")
            continue

        exit_flag, reason = should_exit(trade, ticks, current_price)  # peak=None on restart
        if exit_flag:
            paper_trader.close_order(trade["id"], current_price, f"{trigger}_EXIT:{reason}")
            log_event("trade", f"{trigger} EXIT {symbol} @ ₹{current_price} | {reason}", symbol=symbol)
        else:
            print(
                f"  → {symbol} {trade['direction']} @ ₹{trade['entry_price']} "
                f"— live ₹{current_price} — holding"
            )


async def _eod_squareoff_watchdog() -> None:
    """Close all positions at 15:20 IST on weekdays."""
    from datetime import datetime, time as dtime
    from trading.paper_trader import paper_trader

    while True:
        await asyncio.sleep(30)
        now = datetime.now()
        if now.weekday() >= 5:
            continue
        if dtime(15, 20) <= now.time() <= dtime(15, 30):
            active = db.get_active_trades()
            if active:
                log_event("trade", f"EOD SQUARE-OFF: closing {len(active)} position(s) at 15:20")
                for trade in active:
                    ticks = db.get_recent_ticks(trade["symbol"], limit=1)
                    price = ticks[0]["ltp"] if ticks else trade["entry_price"]
                    paper_trader.close_order(trade["id"], price, "EOD_SQUAREOFF")
                    log_event(
                        "trade", f"EOD CLOSED {trade['symbol']} @ ₹{price}", symbol=trade["symbol"]
                    )
                global monitoring, ws_client
                monitoring = False
                if ws_client:
                    ws_client.disconnect()
                    ws_client = None


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global selected_stocks, monitoring, ws_client
    print("\n" + "=" * 50)
    print("MY TRADING BOT - STARTING UP")
    print("=" * 50)
    config.validate()
    print(f"✓ Paper trading: {config.PAPER_TRADING}")

    monitoring = False
    ws_client = None
    print("✓ Monitoring: OFF (clean start)")

    selected_stocks = db.get_selected_stocks()
    if selected_stocks:
        print(f"✓ Restored stocks: {selected_stocks}")

    _check_stale_open_trades()
    print()

    eod_task = asyncio.create_task(_eod_squareoff_watchdog())
    yield
    eod_task.cancel()
    if ws_client:
        ws_client.disconnect()
    print("✓ Shutdown complete")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="My Trading Bot", version="2.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Tick callback — single line of trading logic in main.py ──────────────────

def on_tick_received(tick_data: dict) -> None:
    if not monitoring:
        return
    pipeline.on_tick(tick_data, set(selected_stocks), paused_stocks)


# ── API routes ────────────────────────────────────────────────────────────────

@app.get("/status")
async def get_status():
    return JSONResponse({
        "status": "running",
        "paper_trading": config.PAPER_TRADING,
        "monitoring": monitoring,
        "trading_paused": db.get_trading_paused(),
        "max_watched_stocks": config.MAX_WATCHED_STOCKS,
        "ticker_connected": ws_client.is_connected if ws_client else False,
        "ticker_lag_seconds": (
            ws_client.get_connection_status()["seconds_since_last_tick"] if ws_client else None
        ),
        "selected_stocks": selected_stocks,
        "paused_stocks": list(paused_stocks),
        "incubating_symbols": pipeline.incubator.watching_symbols(),
        "zerodha_authenticated": auth.is_authenticated(),
        "portfolio": order_manager.get_portfolio_status(),
        "config": {
            "capital_per_trade": config.CAPITAL_PER_TRADE,
            "stop_loss_pct": config.STOP_LOSS_PCT,
            "max_active_trades": config.MAX_ACTIVE_TRADES,
            "time_exit_minutes": config.TIME_EXIT_MINUTES,
        },
    })


@app.post("/trading/pause")
async def pause_trading():
    db.set_trading_paused(True)
    log_event("info", "Trading PAUSED for today — no new entries will be opened")
    return JSONResponse({"trading_paused": True})


@app.post("/trading/resume")
async def resume_trading():
    db.set_trading_paused(False)
    log_event("info", "Trading RESUMED — new entries allowed")
    return JSONResponse({"trading_paused": False})


@app.patch("/config")
async def update_config(body: dict = Body(...)):
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    allowed = {
        "capital_per_trade": ("CAPITAL_PER_TRADE", int),
        "stop_loss_pct":     ("STOP_LOSS_PCT",     float),
        "max_active_trades": ("MAX_ACTIVE_TRADES",  int),
        "time_exit_minutes": ("TIME_EXIT_MINUTES",  int),
    }
    updated = {}
    for key, value in body.items():
        if key not in allowed:
            continue
        env_key, cast = allowed[key]
        value = cast(value)
        setattr(config, env_key, value)
        with open(env_path, "r") as f:
            content = f.read()
        pattern = rf"^{env_key}=.*$"
        replacement = f"{env_key}={value}"
        if re.search(pattern, content, re.MULTILINE):
            content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
        else:
            content += f"\n{replacement}\n"
        with open(env_path, "w") as f:
            f.write(content)
        updated[key] = value
        log_event("info", f"Config updated: {env_key}={value}")

    from trading.portfolio import portfolio
    portfolio.capital_per_trade = config.CAPITAL_PER_TRADE
    portfolio.max_active_trades = config.MAX_ACTIVE_TRADES
    return JSONResponse({"updated": updated})


@app.get("/auth/login-url")
async def get_login_url():
    if not auth.api_key or not auth.api_secret:
        return JSONResponse({"error": "API credentials not configured"}, status_code=400)
    return JSONResponse({"login_url": auth.generate_login_url()})


@app.get("/callback")
async def zerodha_callback(request: Request):
    request_token = request.query_params.get("request_token")
    status = request.query_params.get("status")
    if status != "success" or not request_token:
        return HTMLResponse(
            "<script>window.close();</script><p>Auth failed.</p>", status_code=400
        )
    token = auth.generate_access_token(request_token)
    if not token:
        return HTMLResponse(
            "<script>window.close();</script><p>Failed to generate token.</p>", status_code=500
        )
    return HTMLResponse("""
        <html><body><p>Authentication successful! Closing...</p>
        <script>
            if (window.opener) {
                window.opener.postMessage({ type: 'zerodha_auth_success' }, 'http://localhost:3001');
            }
            setTimeout(() => window.close(), 1000);
        </script></body></html>
    """)


@app.post("/auth/token")
async def set_access_token(request_token: str):
    token = auth.generate_access_token(request_token)
    if not token:
        return JSONResponse({"error": "Failed to generate access token"}, status_code=400)
    return JSONResponse({"access_token": token})


@app.get("/instruments/search")
async def search_instruments(q: str = ""):
    if not q or len(q) < 2 or not auth.kite:
        return JSONResponse([])
    try:
        instruments = auth.kite.instruments("NSE")
        q_upper = q.upper()
        matches = [
            {"symbol": i["tradingsymbol"], "name": i.get("name", "")}
            for i in instruments
            if q_upper in i["tradingsymbol"] or q_upper in i.get("name", "").upper()
        ]
        return JSONResponse(matches[:20])
    except Exception:
        return JSONResponse([])


@app.post("/stocks")
async def set_stock_basket(stocks: list = Body(...)):
    global selected_stocks
    if not stocks or len(stocks) > config.MAX_WATCHED_STOCKS:
        return JSONResponse(
            {"error": f"Please select 1–{config.MAX_WATCHED_STOCKS} stocks"}, status_code=400
        )
    removed = set(selected_stocks) - set(stocks)
    for sym in removed:
        pipeline.reset_symbol(sym)
    selected_stocks = stocks
    db.save_selected_stocks(stocks)
    if monitoring and ws_client:
        log_event("info", f"Stock list changed — restarting ticker for {selected_stocks}")
        ws_client.connect(selected_stocks)
    return JSONResponse({"message": f"Stock basket updated: {stocks}", "stocks": selected_stocks})


@app.post("/stocks/{symbol}/pause")
async def pause_stock(symbol: str):
    symbol = symbol.upper()
    paused_stocks.add(symbol)
    log_event("info", f"Paused new entries for {symbol}", symbol=symbol)
    return JSONResponse({"symbol": symbol, "paused": True})


@app.post("/stocks/{symbol}/resume")
async def resume_stock(symbol: str):
    symbol = symbol.upper()
    paused_stocks.discard(symbol)
    log_event("info", f"Resumed monitoring for {symbol}", symbol=symbol)
    return JSONResponse({"symbol": symbol, "paused": False})


@app.post("/start")
async def start_monitoring():
    global monitoring, ws_client
    if not auth.is_authenticated():
        return JSONResponse({"error": "Not authenticated with Zerodha"}, status_code=400)
    if not selected_stocks:
        return JSONResponse({"error": "Please select stocks first"}, status_code=400)
    if monitoring and ws_client:
        ws_client.disconnect()
        ws_client = None

    monitoring = True
    log_event("info", f"Monitoring started for {selected_stocks}")
    _check_stale_open_trades(trigger="MONITORING_ON")

    ws_client = WebSocketClient(on_tick_callback=on_tick_received)
    success = ws_client.connect(selected_stocks)
    if not success:
        monitoring = False
        return JSONResponse({"error": "Failed to connect WebSocket"}, status_code=500)
    return JSONResponse({"message": "Monitoring started", "stocks": selected_stocks})


@app.post("/stop")
async def stop_monitoring():
    global monitoring, ws_client
    from trading.paper_trader import paper_trader

    monitoring = False
    active_trades = db.get_active_trades()
    closed_count = 0
    if active_trades:
        log_event("trade", f"Monitoring stopped — closing {len(active_trades)} open position(s)")
        for trade in active_trades:
            ticks = db.get_recent_ticks(trade["symbol"], limit=1)
            price = ticks[0]["ltp"] if ticks else trade["entry_price"]
            paper_trader.close_order(trade["id"], price, "MONITORING_STOPPED")
            log_event(
                "trade",
                f"CLOSED {trade['symbol']} @ ₹{price} — monitoring stopped",
                symbol=trade["symbol"],
            )
            closed_count += 1
    if ws_client:
        ws_client.disconnect()
        ws_client = None
    log_event("info", "Monitoring stopped")
    return JSONResponse({"message": "Monitoring stopped", "closed_positions": closed_count})


@app.post("/ws/reconnect")
async def reconnect_websocket():
    global ws_client
    if not auth.is_authenticated():
        return JSONResponse({"error": "Not authenticated"}, status_code=400)
    if not selected_stocks:
        return JSONResponse({"error": "No stocks selected"}, status_code=400)
    log_event("info", "Manual WebSocket reconnect triggered")
    if ws_client:
        ws_client.connect(selected_stocks)
    else:
        ws_client = WebSocketClient(on_tick_callback=on_tick_received)
        ws_client.connect(selected_stocks)
    return JSONResponse({"message": f"Reconnecting to {selected_stocks}"})


@app.post("/exit/{trade_id}")
async def exit_position(trade_id: int):
    from trading.paper_trader import paper_trader

    trade = db.get_trade_by_id(trade_id)
    if not trade:
        return JSONResponse({"error": f"Trade {trade_id} not found"}, status_code=404)
    if trade.get("exit_price") is not None:
        return JSONResponse({"error": "Trade already closed"}, status_code=400)
    ticks = db.get_recent_ticks(trade["symbol"], limit=1)
    price = ticks[0]["ltp"] if ticks else trade["entry_price"]
    paper_trader.close_order(trade_id, price, "MANUAL_EXIT")
    log_event("trade", f"MANUAL EXIT {trade['symbol']} @ ₹{price}", symbol=trade["symbol"])
    paused_stocks.add(trade["symbol"])
    return JSONResponse({"message": f"Closed trade {trade_id} @ ₹{price}"})


@app.post("/exit-all")
async def exit_all_positions():
    active_trades = order_manager.get_active_trades()
    if not active_trades:
        return JSONResponse({"message": "No active trades to close"})
    current_prices = {}
    for trade in active_trades:
        ticks = db.get_recent_ticks(trade["symbol"], limit=1)
        current_prices[trade["symbol"]] = (
            ticks[0]["ltp"] if ticks else trade["entry_price"]
        )
    results = order_manager.close_all_positions(current_prices)
    return JSONResponse({"message": f"Closed {len(results)} positions", "results": results})


@app.get("/positions")
async def get_active_positions():
    trades = order_manager.get_active_trades()
    return JSONResponse({"count": len(trades), "positions": trades})


@app.get("/trades")
async def get_trade_history():
    trades = db.get_today_trades()
    return JSONResponse({"count": len(trades), "trades": [dict(t) for t in trades]})


@app.get("/debug/tick")
async def debug_raw_tick():
    if ws_client and ws_client.last_raw_tick:
        return JSONResponse({"raw": str(ws_client.last_raw_tick)})
    return JSONResponse({"error": "No tick received yet"})


@app.get("/logs")
async def list_logs():
    import glob
    log_dir = os.path.join(os.path.dirname(__file__), "..", "logs")
    files = sorted(glob.glob(os.path.join(log_dir, "*.log")), reverse=True)
    return JSONResponse([os.path.basename(f) for f in files])


@app.get("/logs/{date}")
async def get_log(date: str, symbol: str = None):
    log_dir = os.path.join(os.path.dirname(__file__), "..", "logs")
    log_file = os.path.join(log_dir, f"{date}.log")
    if not os.path.exists(log_file):
        return JSONResponse({"error": "Log not found"}, status_code=404)
    with open(log_file) as f:
        lines = f.readlines()
    if symbol:
        lines = [l for l in lines if f"[{symbol}]" in l]
    return JSONResponse({"lines": lines[-500:]})


@app.get("/logs/activity/recent")
async def get_recent_activity(symbol: str = None):
    entries = (
        activity_log
        if not symbol
        else [e for e in activity_log if e.get("symbol") == symbol or e.get("symbol") is None]
    )
    return JSONResponse(entries[-200:])


@app.get("/stocks/{symbol}/trades")
async def get_symbol_trades(symbol: str):
    all_trades = db.get_today_trades()
    symbol_trades = [t for t in all_trades if t["symbol"] == symbol.upper()]
    return JSONResponse({"count": len(symbol_trades), "trades": symbol_trades})


@app.get("/stocks/{symbol}/ticks/latest")
async def get_symbol_latest_tick(symbol: str):
    ticks = db.get_recent_ticks(symbol.upper(), limit=1)
    if not ticks:
        return JSONResponse({"error": "No tick data"}, status_code=404)
    return JSONResponse(ticks[0])


@app.get("/pnl")
async def get_pnl():
    return JSONResponse({
        "daily_pnl": round(db.get_today_pnl(), 2),
        "portfolio": order_manager.get_portfolio_status(),
    })


@app.get("/scan")
async def scan_stocks():
    if not auth.is_authenticated():
        return JSONResponse({"error": "Not authenticated with Zerodha"}, status_code=400)
    import math
    from data.stock_universe import get_batches
    from strategy.signal_quality import check_signal_quality

    try:
        kite = auth.get_kite_client()
    except Exception as e:
        return JSONResponse({"error": f"Kite client unavailable: {e}"}, status_code=500)

    all_quotes = {}
    for i, batch in enumerate(get_batches()):
        try:
            all_quotes.update(kite.quote([f"NSE:{sym}" for sym in batch]))
        except Exception as e:
            print(f"  ⚠  Scan batch {i + 1} failed: {e}")

    if not all_quotes:
        return JSONResponse({"error": "Could not fetch any quotes"}, status_code=500)

    scored = []
    filtered_out = 0
    for key, q in all_quotes.items():
        symbol = key.replace("NSE:", "")
        buy_qty = q.get("buy_quantity", 0)
        sell_qty = q.get("sell_quantity", 0)
        volume = q.get("volume", 0)
        total = buy_qty + sell_qty
        if total == 0 or volume < 10000:
            continue
        sq = check_signal_quality(buy_qty, sell_qty)
        if not sq["passed"]:
            filtered_out += 1
            continue
        imbalance = abs(buy_qty - sell_qty) / total
        bias = "BUY" if buy_qty > sell_qty else "SELL"
        ratio = (
            round(buy_qty / sell_qty, 2)
            if bias == "BUY" and sell_qty > 0
            else round(sell_qty / buy_qty, 2) if buy_qty > 0 else 99.0
        )
        score = imbalance * math.log1p(volume) * sq["quality_score"]
        scored.append({
            "symbol": symbol,
            "bias": bias,
            "ratio": ratio,
            "imbalance": round(imbalance, 3),
            "volume": volume,
            "last_price": q.get("last_price", 0),
            "score": score,
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    top5 = scored[:5]
    for r in top5:
        del r["score"]
    print(
        f"[scan] {len(all_quotes)} stocks → {filtered_out} filtered → "
        f"top 5: {[s['symbol'] for s in top5]}"
    )
    return JSONResponse({"results": top5, "scanned": len(all_quotes)})


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        last_seq = 0
        while True:
            ticks = {}
            for sym in selected_stocks:
                recent = db.get_recent_ticks(sym, limit=1)
                if recent:
                    ticks[sym] = recent[0]
            try:
                active_trades = order_manager.get_active_trades()
            except Exception:
                active_trades = []
            new_entries = [e for e in activity_log if e.get("seq", 0) > last_seq]
            if new_entries:
                last_seq = new_entries[-1]["seq"]
            await websocket.send_json({
                "monitoring": monitoring,
                "portfolio": order_manager.get_portfolio_status(),
                "active_trades": active_trades,
                "log": new_entries,
                "ticks": ticks,
            })
            await asyncio.sleep(1)
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        await websocket.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=config.SERVER_HOST, port=config.SERVER_PORT, log_level="info")
