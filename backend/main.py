from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, Request, Body
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from config import config
from auth.zerodha_auth import auth
from data.websocket_client import WebSocketClient
from trading.order_manager import order_manager
from strategy.signal import generate_signal
from strategy.exit_logic import get_exit_analysis
from data.database import db
import asyncio
import json

# Global state
ws_client = None
monitoring = False
selected_stocks = []
paused_stocks = set()  # stocks in watchlist but blocked from new entries
activity_log = []  # ring buffer of last 500 events
_log_seq = 0       # ever-incrementing counter; each entry gets a unique seq id
_position_log_tick = {}  # tick counter per symbol for throttling position logs
_last_exit_time = {}     # symbol -> datetime of last position exit (for cooldown)
ENTRY_COOLDOWN_SECONDS = 60  # don't re-enter a symbol within 60s of last exit
_position_flip_state = {}    # trade_id -> {cumulative_delta, consecutive_flip_ticks}
_position_peak = {}          # trade_id -> best price seen since entry (high for BUY, low for SELL)

async def _eod_squareoff_watchdog():
    """Close all open positions at 3:20 PM IST every trading day"""
    from datetime import datetime, time as dtime
    while True:
        await asyncio.sleep(30)
        now = datetime.now()
        if now.weekday() >= 5:
            continue
        if now.time() >= dtime(15, 20) and now.time() <= dtime(15, 30):
            active = db.get_active_trades()
            if active:
                log_event("trade", f"EOD SQUARE-OFF: closing {len(active)} open position(s) at 15:20")
                for trade in active:
                    ticks = db.get_recent_ticks(trade['symbol'], limit=1)
                    price = ticks[0]['ltp'] if ticks else trade['entry_price']
                    from trading.paper_trader import paper_trader
                    paper_trader.close_order(trade['id'], price, 'EOD_SQUAREOFF')
                    log_event("trade", f"EOD CLOSED {trade['symbol']} @ ₹{price}", symbol=trade['symbol'])
                global monitoring, ws_client
                monitoring = False
                if ws_client:
                    ws_client.disconnect()
                    ws_client = None


def log_event(level: str, msg: str, symbol: str = None):
    """Add event to activity log and write to dated log file"""
    global _log_seq
    from datetime import datetime
    import os
    now = datetime.now()
    _log_seq += 1
    entry = {"seq": _log_seq, "time": now.strftime("%H:%M:%S"), "level": level, "msg": msg, "symbol": symbol}
    activity_log.append(entry)
    if len(activity_log) > 500:
        activity_log.pop(0)

    # Write to dated log file
    log_dir = os.path.join(os.path.dirname(__file__), '..', 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"{now.strftime('%Y-%m-%d')}.log")
    sym_tag = f"[{symbol}]" if symbol else "[system]"
    with open(log_file, 'a') as f:
        f.write(f"{now.strftime('%H:%M:%S')} {sym_tag} [{level.upper()}] {msg}\n")

    print(f"[{entry['time']}] {sym_tag} {msg}")

def _check_stale_open_trades(trigger: str = "RESTART"):
    """
    Check all open trades against exit criteria using live prices from Zerodha REST API.
    Falls back to last stored tick if live price is unavailable.
    Called on restart and when monitoring is turned ON (covers gap while monitoring was off).
    """
    from strategy.exit_logic import should_exit
    from trading.paper_trader import paper_trader
    open_trades = db.get_active_trades()
    if not open_trades:
        print("✓ No open trades to check on restart")
        return

    # Fetch live prices via kite.ltp() REST API — retry up to 10 times before falling back
    import time as _time
    live_prices = {}
    MAX_RETRIES = 10
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            kite = auth.get_kite_client()
            symbols = list({f"NSE:{t['symbol']}" for t in open_trades})
            ltp_data = kite.ltp(symbols)
            for key, val in ltp_data.items():
                symbol = key.replace("NSE:", "")
                live_prices[symbol] = val['last_price']
            print(f"  ✓ Fetched live prices for: {list(live_prices.keys())} (attempt {attempt})")
            break
        except Exception as e:
            print(f"  ⚠  Live price fetch failed (attempt {attempt}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES:
                _time.sleep(3)
            else:
                print(f"  ⚠  All {MAX_RETRIES} attempts failed — falling back to last stored tick")

    print(f"⚠  Found {len(open_trades)} open trade(s) — checking exit criteria ({trigger})...")
    for trade in open_trades:
        symbol = trade['symbol']
        ticks = db.get_recent_ticks(symbol, limit=100)
        # Use live price if available, otherwise fall back to last stored tick
        if symbol in live_prices:
            current_price = live_prices[symbol]
        elif ticks:
            current_price = ticks[0]['ltp']
            print(f"  ⚠  {symbol}: using stale tick price ₹{current_price}")
        else:
            print(f"  {symbol}: no price data available, skipping")
            continue
        exit_flag, reason = should_exit(trade, ticks, current_price)
        if exit_flag:
            paper_trader.close_order(trade['id'], current_price, f"{trigger}_EXIT:{reason}")
            log_event("trade", f"{trigger} EXIT {symbol} @ ₹{current_price} | {reason}", symbol=symbol)
            print(f"  ✓ Closed {symbol} {trade['direction']} @ ₹{current_price} — {reason}")
        else:
            print(f"  → {symbol} {trade['direction']} @ ₹{trade['entry_price']} — live ₹{current_price} — no exit criteria met, keeping open")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize on app startup — always starts with monitoring OFF"""
    global selected_stocks, monitoring, ws_client
    print("\n" + "="*50)
    print("MY TRADING BOT - STARTING UP")
    print("="*50)
    config.validate()
    print(f"✓ Paper trading: {config.PAPER_TRADING}")

    # Guarantee clean state — no zombie monitoring from previous session
    monitoring = False
    ws_client = None
    print("✓ Monitoring: OFF (clean start)")

    selected_stocks = db.get_selected_stocks()
    if selected_stocks:
        print(f"✓ Restored stocks: {selected_stocks}")

    # Check open trades from before shutdown — exit any that now meet exit criteria
    _check_stale_open_trades()
    print()

    # Start EOD square-off watchdog
    eod_task = asyncio.create_task(_eod_squareoff_watchdog())

    yield

    # On shutdown: stop any active monitoring cleanly
    eod_task.cancel()
    if ws_client:
        ws_client.disconnect()
    print("✓ Shutdown complete")

# Initialize FastAPI app
app = FastAPI(title="My Trading Bot", version="1.0.0", lifespan=lifespan)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/status")
async def get_status():
    """Get system status"""
    return JSONResponse({
        "status": "running",
        "paper_trading": config.PAPER_TRADING,
        "monitoring": monitoring,
        "trading_paused": db.get_trading_paused(),
        "max_watched_stocks": config.MAX_WATCHED_STOCKS,
        "ticker_connected": ws_client.is_connected if ws_client else False,
        "ticker_lag_seconds": (ws_client.get_connection_status()['seconds_since_last_tick'] if ws_client else None),
        "selected_stocks": selected_stocks,
        "paused_stocks": list(paused_stocks),
        "zerodha_authenticated": auth.is_authenticated(),
        "portfolio": order_manager.get_portfolio_status(),
        "config": {
            "capital_per_trade": config.CAPITAL_PER_TRADE,
            "stop_loss_pct": config.STOP_LOSS_PCT,
            "max_active_trades": config.MAX_ACTIVE_TRADES,
            "time_exit_minutes": config.TIME_EXIT_MINUTES,
        }
    })

@app.post("/trading/pause")
async def pause_trading():
    """Pause new trade entries for today — persisted in DB, survives restarts"""
    db.set_trading_paused(True)
    log_event("info", "Trading PAUSED for today — no new entries will be opened")
    return JSONResponse({"trading_paused": True})

@app.post("/trading/resume")
async def resume_trading():
    """Resume new trade entries for today"""
    db.set_trading_paused(False)
    log_event("info", "Trading RESUMED — new entries allowed")
    return JSONResponse({"trading_paused": False})

@app.patch("/config")
async def update_config(body: dict = Body(...)):
    """Update trading config at runtime and persist to .env"""
    import re, os
    env_path = os.path.join(os.path.dirname(__file__), '..', '.env')

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

        # Update live config
        setattr(config, env_key, value)

        # Persist to .env
        with open(env_path, 'r') as f:
            content = f.read()
        pattern = rf'^{env_key}=.*$'
        replacement = f'{env_key}={value}'
        if re.search(pattern, content, re.MULTILINE):
            content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
        else:
            content += f'\n{replacement}\n'
        with open(env_path, 'w') as f:
            f.write(content)

        updated[key] = value
        log_event("info", f"Config updated: {env_key}={value}")

    # Sync portfolio with new capital/trade settings
    from trading.portfolio import portfolio
    portfolio.capital_per_trade = config.CAPITAL_PER_TRADE
    portfolio.max_active_trades = config.MAX_ACTIVE_TRADES

    return JSONResponse({"updated": updated})


@app.get("/auth/login-url")
async def get_login_url():
    """Get Zerodha login URL"""
    if not auth.api_key or not auth.api_secret:
        return JSONResponse({
            "error": "API credentials not configured"
        }, status_code=400)

    login_url = auth.generate_login_url()
    return JSONResponse({
        "login_url": login_url,
        "message": "Visit this URL to authenticate with Zerodha"
    })

@app.get("/callback")
async def zerodha_callback(request: Request):
    """Handle Zerodha OAuth callback - auto exchanges request token"""
    request_token = request.query_params.get("request_token")
    status = request.query_params.get("status")

    if status != "success" or not request_token:
        return HTMLResponse("<script>window.close();</script><p>Auth failed. Close this window.</p>", status_code=400)

    token = auth.generate_access_token(request_token)

    if not token:
        return HTMLResponse("<script>window.close();</script><p>Failed to generate access token. Close this window.</p>", status_code=500)

    return HTMLResponse("""
        <html><body>
        <p>Authentication successful! This window will close automatically.</p>
        <script>
            if (window.opener) {
                window.opener.postMessage({ type: 'zerodha_auth_success' }, 'http://localhost:3001');
            }
            setTimeout(() => window.close(), 1000);
        </script>
        </body></html>
    """)

@app.post("/auth/token")
async def set_access_token(request_token: str):
    """Exchange request token for access token"""
    token = auth.generate_access_token(request_token)

    if not token:
        return JSONResponse({
            "error": "Failed to generate access token"
        }, status_code=400)

    return JSONResponse({
        "access_token": token,
        "message": "Access token generated successfully"
    })

@app.get("/instruments/search")
async def search_instruments(q: str = ""):
    """Search NSE instruments by symbol/name"""
    if not q or len(q) < 2:
        return JSONResponse([])
    if not auth.kite:
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
    except Exception as e:
        return JSONResponse([])

@app.post("/stocks")
async def set_stock_basket(stocks: list = Body(...)):
    """
    Set stock basket for trading

    Args:
        stocks: List of stock symbols (2-3 stocks)
    """
    global selected_stocks

    if not stocks or len(stocks) > config.MAX_WATCHED_STOCKS:
        return JSONResponse({
            "error": f"Please select 1–{config.MAX_WATCHED_STOCKS} stocks"
        }, status_code=400)

    selected_stocks = stocks
    db.save_selected_stocks(stocks)
    print(f"✓ Stock basket selected: {selected_stocks}")

    # If monitoring is active, restart ticker so new stock gets subscribed immediately
    if monitoring and ws_client:
        log_event("info", f"Stock list changed — restarting ticker for {selected_stocks}")
        ws_client.connect(selected_stocks)  # connect() hard-stops old ticker first

    return JSONResponse({
        "message": f"Stock basket updated: {stocks}",
        "stocks": selected_stocks
    })

@app.post("/stocks/{symbol}/pause")
async def pause_stock(symbol: str):
    """Pause new entries for a specific stock. Open trades still managed."""
    global paused_stocks
    symbol = symbol.upper()
    paused_stocks.add(symbol)
    log_event("info", f"Paused new entries for {symbol}", symbol=symbol)
    return JSONResponse({"symbol": symbol, "paused": True})

@app.post("/stocks/{symbol}/resume")
async def resume_stock(symbol: str):
    """Resume new entries for a specific stock."""
    global paused_stocks
    symbol = symbol.upper()
    paused_stocks.discard(symbol)
    log_event("info", f"Resumed monitoring for {symbol}", symbol=symbol)
    return JSONResponse({"symbol": symbol, "paused": False})

@app.post("/start")
async def start_monitoring():
    """Start monitoring and trading"""
    global monitoring, ws_client

    if not auth.is_authenticated():
        return JSONResponse({
            "error": "Not authenticated with Zerodha"
        }, status_code=400)

    if not selected_stocks:
        return JSONResponse({
            "error": "Please select stocks first"
        }, status_code=400)

    # If already monitoring, restart WebSocket with updated stock list
    if monitoring and ws_client:
        ws_client.disconnect()
        ws_client = None

    monitoring = True
    log_event("info", f"Monitoring started for {selected_stocks}")

    # Check open trades with live prices before WebSocket starts ticking
    # This catches any exits missed while monitoring was off
    _check_stale_open_trades(trigger="MONITORING_ON")

    # Initialize WebSocket client with callback
    ws_client = WebSocketClient(on_tick_callback=on_tick_received)

    # Connect to WebSocket
    success = ws_client.connect(selected_stocks)

    if not success:
        monitoring = False
        return JSONResponse({
            "error": "Failed to connect WebSocket"
        }, status_code=500)

    print(f"✓ Started monitoring: {selected_stocks}")

    return JSONResponse({
        "message": "Monitoring started",
        "stocks": selected_stocks
    })

@app.post("/stop")
async def stop_monitoring():
    """Stop monitoring and close all open positions"""
    global monitoring, ws_client

    monitoring = False

    active_trades = db.get_active_trades()
    closed_count = 0
    if active_trades:
        log_event("trade", f"Monitoring stopped — closing {len(active_trades)} open position(s)")
        for trade in active_trades:
            ticks = db.get_recent_ticks(trade['symbol'], limit=1)
            close_price = ticks[0]['ltp'] if ticks else trade['entry_price']
            from trading.paper_trader import paper_trader
            paper_trader.close_order(
                trade_id=trade['id'],
                exit_price=close_price,
                exit_reason='MONITORING_STOPPED'
            )
            log_event("trade", f"CLOSED {trade['symbol']} @ ₹{close_price} — monitoring stopped", symbol=trade['symbol'])
            closed_count += 1

    if ws_client:
        ws_client.disconnect()
        ws_client = None

    log_event("info", "Monitoring stopped")
    return JSONResponse({"message": "Monitoring stopped", "closed_positions": closed_count})


@app.post("/ws/reconnect")
async def reconnect_websocket():
    """Reconnect the Zerodha WebSocket ticker — does not affect trades or monitoring state"""
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
    """Close a specific position by trade ID"""
    from trading.paper_trader import paper_trader
    trade = db.get_trade_by_id(trade_id)
    if not trade:
        return JSONResponse({"error": f"Trade {trade_id} not found"}, status_code=404)
    if trade.get('exit_price') is not None:
        return JSONResponse({"error": "Trade already closed"}, status_code=400)
    ticks = db.get_recent_ticks(trade['symbol'], limit=1)
    price = ticks[0]['ltp'] if ticks else trade['entry_price']
    paper_trader.close_order(trade_id, price, 'MANUAL_EXIT')
    log_event("trade", f"MANUAL EXIT {trade['symbol']} @ ₹{price}", symbol=trade['symbol'])
    # Auto-pause this stock after manual exit — user must explicitly resume
    paused_stocks.add(trade['symbol'])
    return JSONResponse({"message": f"Closed trade {trade_id} @ ₹{price}"})


@app.post("/exit-all")
async def exit_all_positions():
    """Close all active positions immediately"""
    active_trades = order_manager.get_active_trades()

    if not active_trades:
        return JSONResponse({
            "message": "No active trades to close"
        })

    # Get current prices for all trades
    current_prices = {}
    for trade in active_trades:
        ticks = db.get_recent_ticks(trade['symbol'], limit=1)
        if ticks:
            current_prices[trade['symbol']] = ticks[0]['ltp']
        else:
            current_prices[trade['symbol']] = trade['entry_price']

    # Close all positions
    results = order_manager.close_all_positions(current_prices)

    return JSONResponse({
        "message": f"Closed {len(results)} positions",
        "results": results
    })

@app.get("/positions")
async def get_active_positions():
    """Get active positions"""
    trades = order_manager.get_active_trades()
    return JSONResponse({
        "count": len(trades),
        "positions": trades
    })

@app.get("/trades")
async def get_trade_history():
    """Get trade history for today"""
    trades = db.get_today_trades()

    return JSONResponse({
        "count": len(trades),
        "trades": [dict(t) for t in trades]
    })

@app.get("/debug/tick")
async def debug_raw_tick():
    """Show raw tick structure from Zerodha — use this to inspect actual fields"""
    if ws_client and ws_client.last_raw_tick:
        return JSONResponse({"raw": str(ws_client.last_raw_tick)})
    return JSONResponse({"error": "No tick received yet"})

@app.get("/logs")
async def list_logs():
    """List available log files"""
    import os, glob
    log_dir = os.path.join(os.path.dirname(__file__), '..', 'logs')
    files = sorted(glob.glob(os.path.join(log_dir, '*.log')), reverse=True)
    return JSONResponse([os.path.basename(f) for f in files])

@app.get("/logs/{date}")
async def get_log(date: str, symbol: str = None):
    """Get log file contents, optionally filtered by symbol"""
    import os
    log_dir = os.path.join(os.path.dirname(__file__), '..', 'logs')
    log_file = os.path.join(log_dir, f"{date}.log")
    if not os.path.exists(log_file):
        return JSONResponse({"error": "Log not found"}, status_code=404)
    with open(log_file) as f:
        lines = f.readlines()
    if symbol:
        lines = [l for l in lines if f"[{symbol}]" in l]
    return JSONResponse({"lines": lines[-500:]})  # last 500 lines

@app.get("/logs/activity/recent")
async def get_recent_activity(symbol: str = None):
    """Get recent in-memory activity, optionally filtered by symbol"""
    entries = activity_log if not symbol else [e for e in activity_log if e.get('symbol') == symbol or e.get('symbol') is None]
    return JSONResponse(entries[-200:])

@app.get("/stocks/{symbol}/trades")
async def get_symbol_trades(symbol: str):
    all_trades = db.get_today_trades()
    symbol_trades = [t for t in all_trades if t['symbol'] == symbol.upper()]
    return JSONResponse({"count": len(symbol_trades), "trades": symbol_trades})

@app.get("/stocks/{symbol}/ticks/latest")
async def get_symbol_latest_tick(symbol: str):
    ticks = db.get_recent_ticks(symbol.upper(), limit=1)
    if not ticks:
        return JSONResponse({"error": "No tick data"}, status_code=404)
    return JSONResponse(ticks[0])

@app.get("/pnl")
async def get_pnl():
    """Get daily P&L summary"""
    daily_pnl = db.get_today_pnl()
    portfolio_summary = order_manager.get_portfolio_status()

    return JSONResponse({
        "daily_pnl": round(daily_pnl, 2),
        "portfolio": portfolio_summary
    })

@app.get("/scan")
async def scan_stocks():
    """
    Scan ~450 liquid NSE stocks for order imbalance.
    Makes 3 batched kite.quote() calls, scores each stock, returns top 5.
    """
    if not auth.is_authenticated():
        return JSONResponse({"error": "Not authenticated with Zerodha"}, status_code=400)

    from data.stock_universe import get_batches
    import math

    try:
        kite = auth.get_kite_client()
    except Exception as e:
        return JSONResponse({"error": f"Kite client unavailable: {e}"}, status_code=500)

    batches = get_batches()
    all_quotes = {}

    for i, batch in enumerate(batches):
        instruments = [f"NSE:{sym}" for sym in batch]
        try:
            result = kite.quote(instruments)
            all_quotes.update(result)
        except Exception as e:
            print(f"  ⚠  Scan batch {i+1} failed: {e}")

    if not all_quotes:
        return JSONResponse({"error": "Could not fetch any quotes"}, status_code=500)

    scored = []
    for key, q in all_quotes.items():
        symbol = key.replace("NSE:", "")
        buy_qty = q.get("buy_quantity", 0)
        sell_qty = q.get("sell_quantity", 0)
        volume = q.get("volume", 0)
        total = buy_qty + sell_qty
        if total == 0 or volume < 10000:  # skip illiquid / no order book
            continue

        imbalance = abs(buy_qty - sell_qty) / total  # 0-1, higher = stronger
        bias = "BUY" if buy_qty > sell_qty else "SELL"
        ratio = round(buy_qty / sell_qty, 2) if sell_qty > 0 else 99.0
        if bias == "SELL" and buy_qty > 0:
            ratio = round(sell_qty / buy_qty, 2)

        # Weight imbalance by volume activity (log scale so huge volumes don't dominate)
        score = imbalance * math.log1p(volume)

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
        del r["score"]  # internal only

    print(f"[scan] Scanned {len(all_quotes)} stocks → top 5: {[s['symbol'] for s in top5]}")
    return JSONResponse({"results": top5, "scanned": len(all_quotes)})


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates"""
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
            except Exception as e:
                active_trades = []
                print(f"get_active_trades error: {e}")
            new_entries = [e for e in activity_log if e.get('seq', 0) > last_seq]
            if new_entries:
                last_seq = new_entries[-1]['seq']
            status = {
                "monitoring": monitoring,
                "portfolio": order_manager.get_portfolio_status(),
                "active_trades": active_trades,
                "log": new_entries,
                "ticks": ticks
            }
            await websocket.send_json(status)
            await asyncio.sleep(1)

    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        await websocket.close()

def _build_signal_meta(signal: dict, tick_history: list, bid_qty: int, ask_qty: int) -> dict:
    """Capture market context at entry moment for ML training"""
    from datetime import datetime, time as dtime
    now = datetime.now()
    session_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    minutes_since_open = int((now - session_open).total_seconds() / 60)

    total = bid_qty + ask_qty
    norm_imbalance = (bid_qty - ask_qty) / total if total > 0 else 0
    ratio = bid_qty / ask_qty if ask_qty > 0 else 0

    # OFI: delta between last two ticks
    ofi = 0
    if len(tick_history) >= 2:
        t0, t1 = tick_history[0], tick_history[1]  # newest, second-newest
        ofi = (t0.get('bid_qty', 0) - t1.get('bid_qty', 0)) - (t0.get('ask_qty', 0) - t1.get('ask_qty', 0))

    return {
        'ratio': round(ratio, 4),
        'ofi': ofi,
        'confidence': round(signal.get('confidence', 0), 4),
        'norm_imbalance': round(norm_imbalance, 4),
        'minutes_since_open': max(0, minutes_since_open),
    }


def on_tick_received(tick_data):
    """Callback when new tick is received"""
    if not monitoring:
        return

    symbol = tick_data.get('symbol')
    current_price = tick_data.get('ltp', 0)
    bid_qty = tick_data.get('bid_qty', 0)
    ask_qty = tick_data.get('ask_qty', 0)

    ratio = round(bid_qty / ask_qty, 2) if ask_qty else 0
    log_event("info", f"{symbol}  ₹{current_price}  bid={bid_qty}  ask={ask_qty}  ratio={ratio}x", symbol=symbol)

    # Get tick history for strategy analysis
    tick_history = db.get_recent_ticks(symbol, limit=100)

    # Log position status every 10 ticks so user can see ongoing P&L and exit reasoning
    active_positions = [t for t in db.get_active_trades() if t['symbol'] == symbol]
    if active_positions:
        _position_log_tick[symbol] = _position_log_tick.get(symbol, 0) + 1
        if _position_log_tick[symbol] % 10 == 1:  # log on tick 1, 11, 21 ...
            for pos in active_positions:
                qty = pos['entry_qty']
                entry = pos['entry_price']
                pnl = (current_price - entry) * qty if pos['direction'] == 'BUY' else (entry - current_price) * qty
                analysis = get_exit_analysis(pos, tick_history, current_price)
                held_min = analysis['duration_minutes']
                sl = pos['stop_loss_price']
                sl_dist = round(abs(current_price - sl), 2)
                momentum_flag = "⚠️ momentum reversing" if analysis['momentum_slowing'] else "momentum OK"
                time_flag = f"⚠️ time limit hit ({held_min:.0f}m)" if analysis['time_exceeded'] else f"held {held_min:.0f}m/{config.TIME_EXIT_MINUTES}m"
                pnl_str = f"+₹{pnl:.2f}" if pnl >= 0 else f"-₹{abs(pnl):.2f}"
                log_event("signal",
                    f"POSITION {pos['direction']} {symbol}  qty={qty}  entry=₹{entry}  now=₹{current_price}  P&L={pnl_str}  SL=₹{sl}(dist={sl_dist})  {time_flag}  {momentum_flag}  → {'EXIT' if analysis['should_exit'] else 'HOLD'}",
                    symbol=symbol)
    else:
        _position_log_tick.pop(symbol, None)  # reset counter when no position

    # --- Step 1: Update peak prices and check exits ---
    from datetime import datetime as _dt
    # Update peak price for each active position on this symbol
    for pos in [t for t in db.get_active_trades() if t['symbol'] == symbol]:
        tid = pos['id']
        if tid not in _position_peak:
            _position_peak[tid] = current_price  # init on first tick
        elif pos['direction'] == 'BUY':
            _position_peak[tid] = max(_position_peak[tid], current_price)
        else:
            _position_peak[tid] = min(_position_peak[tid], current_price)

    try:
        exit_results = order_manager.check_exits(tick_data, peak_prices=_position_peak)
        for result in exit_results:
            log_event("trade", f"CLOSED {symbol} @ ₹{current_price} | reason: {result['reason']}", symbol=symbol)
            _last_exit_time[symbol] = _dt.now()
            _position_peak.pop(result['trade_id'], None)
            _position_flip_state.pop(result['trade_id'], None)
    except Exception as e:
        log_event("error", f"Exit check error for {symbol}: {e}", symbol=symbol)

    # Refresh position list after exits
    active_positions = [t for t in db.get_active_trades() if t['symbol'] == symbol]

    # --- Step 2: Hard boundary — only enter trades for stocks explicitly in the watchlist ---
    if symbol not in selected_stocks:
        return
    if symbol in paused_stocks:
        # Stock is paused — exits above still ran, but no new entries allowed
        return

    # --- Step 3: Generate signal (needed for entry and reversal detection) ---
    can_open = order_manager.get_portfolio_status()['can_open_new']
    signal = generate_signal(tick_history, can_open_new_trade=True)  # portfolio check done below

    # --- Step 4: Cooldown check — don't re-enter within ENTRY_COOLDOWN_SECONDS of last exit ---
    in_cooldown = False
    cooldown_remaining = 0
    if symbol in _last_exit_time:
        elapsed = (_dt.now() - _last_exit_time[symbol]).total_seconds()
        if elapsed < ENTRY_COOLDOWN_SECONDS:
            in_cooldown = True
            cooldown_remaining = ENTRY_COOLDOWN_SECONDS - elapsed

    if not active_positions:
        # No open position — enter if signal is actionable
        level = "trade" if signal['action'] in ['BUY', 'SELL'] else "signal"
        log_event(level, f"{symbol} → {signal['action']} ({signal['confidence']:.0%})  {signal['reason']}", symbol=symbol)

        if signal['action'] in ['BUY', 'SELL'] and can_open:
            if db.get_trading_paused():
                pass  # trading paused for today — silently skip new entries
            elif in_cooldown:
                log_event("signal", f"{symbol} cooldown: {cooldown_remaining:.0f}s before next entry allowed", symbol=symbol)
            else:
                from trading.portfolio import portfolio as _portfolio
                qty = _portfolio.calculate_quantity(current_price)
                signal_meta = _build_signal_meta(signal, tick_history, bid_qty, ask_qty)
                trade_id = order_manager.process_signal(symbol, signal, current_price, signal_meta=signal_meta)
                log_event("trade", f"OPENED {signal['action']} {symbol} @ ₹{current_price}  qty={qty} (trade #{trade_id})", symbol=symbol)

    else:
        # Already in a position — check for smart momentum flip
        from strategy.reversal_detector import check_flip
        from datetime import datetime as _dt2
        from trading.paper_trader import paper_trader as _pt
        for pos in active_positions:
            trade_id = pos['id']

            # Enforce minimum hold before flip is even evaluated
            et = pos['entry_time']
            if isinstance(et, (int, float)):
                entry_dt = _dt2.fromtimestamp(et)
            elif isinstance(et, str):
                entry_dt = _dt2.fromisoformat(et)
            else:
                entry_dt = et
            held_seconds = (_dt2.now() - entry_dt).total_seconds()

            if held_seconds < config.MIN_HOLD_BEFORE_FLIP:
                log_event("signal", f"{symbol} [holding {pos['direction']}] → flip check skipped ({held_seconds:.0f}s < {config.MIN_HOLD_BEFORE_FLIP}s min hold)", symbol=symbol)
                continue

            # Get or init per-position flip state
            if trade_id not in _position_flip_state:
                _position_flip_state[trade_id] = {'cumulative_delta': 0.0, 'consecutive_flip_ticks': 0}
            flip_state = _position_flip_state[trade_id]

            reverse_direction = check_flip(pos, tick_history, flip_state)

            if reverse_direction and not db.get_trading_paused():
                # Close current position
                _pt.close_order(trade_id, current_price, 'MOMENTUM_FLIP')
                log_event("trade", f"MOMENTUM FLIP: closed {pos['direction']} {symbol} @ ₹{current_price}", symbol=symbol)
                _last_exit_time[symbol] = _dt.now()
                _position_flip_state.pop(trade_id, None)

                # Immediately open reverse trade — bypass cooldown (intentional flip)
                flip_signal = {'action': reverse_direction, 'confidence': 1.0, 'reason': 'MOMENTUM_FLIP'}
                signal_meta = _build_signal_meta(flip_signal, tick_history, bid_qty, ask_qty)
                new_trade_id = order_manager.process_signal(symbol, flip_signal, current_price,
                                                            signal_meta=signal_meta, is_flip=True)
                if new_trade_id:
                    log_event("trade", f"FLIP OPENED {reverse_direction} {symbol} @ ₹{current_price} (trade #{new_trade_id})", symbol=symbol)
            else:
                log_event("signal", f"{symbol} [holding {pos['direction']}] flip_ticks={flip_state.get('consecutive_flip_ticks',0)}  cum_delta={flip_state.get('cumulative_delta',0):.0f}", symbol=symbol)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=config.SERVER_HOST,
        port=config.SERVER_PORT,
        log_level="info"
    )
