from kiteconnect import KiteTicker
from config import config
from auth.zerodha_auth import auth
from data.database import db
from typing import Callable, List
import threading
import time
from datetime import datetime, time as dtime

# Imported lazily to avoid circular import
def _log(level, msg, symbol=None):
    try:
        from main import log_event
        log_event(level, msg, symbol=symbol)
    except Exception:
        print(msg)

def _is_market_hours() -> bool:
    """Return True if current time is within NSE market hours (9:15–15:30 IST Mon–Fri)"""
    now = datetime.now()
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    t = now.time()
    return dtime(9, 15) <= t <= dtime(15, 30)


class WebSocketClient:
    """Handle WebSocket connections for real-time market data"""

    def __init__(self, on_tick_callback: Callable = None):
        self.kite = auth.get_kite_client()
        self.on_tick_callback = on_tick_callback
        self.ticker = None
        self._ticker_thread = None          # track the thread so we can check if it's alive
        self._stop_event = threading.Event()  # signal to stop watchdog
        self.subscribed_symbols = []
        self.subscribed_tokens = []
        self.token_to_symbol = {}
        self.last_raw_tick = {}
        self.last_tick_time = None          # epoch of last tick received — used by watchdog
        self.is_connected = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5

    def connect(self, symbols: List[str]) -> bool:
        """Connect to WebSocket and subscribe to symbols"""
        if not self.kite:
            _log("error", "Zerodha auth failed — cannot connect WebSocket")
            return False

        try:
            # No hard limit — Zerodha allows up to 3000 tokens on one WebSocket connection

            instruments = self.kite.instruments("NSE")
            symbol_to_token = {i['tradingsymbol']: i['instrument_token'] for i in instruments}

            tokens = [symbol_to_token.get(sym) for sym in symbols]
            tokens = [t for t in tokens if t]

            missing = [s for s in symbols if not symbol_to_token.get(s)]
            if missing:
                _log("error", f"No instrument token found for: {missing} — they will not be monitored")

            if not tokens:
                _log("error", f"No valid instrument tokens found for {symbols}")
                return False

            self.token_to_symbol = {symbol_to_token[sym]: sym for sym in symbols if symbol_to_token.get(sym)}

            # Hard-stop any existing ticker before creating a new one
            self._hard_stop_ticker()

            self.ticker = KiteTicker(config.API_KEY, auth.access_token)
            self.subscribed_tokens = tokens
            self.subscribed_symbols = [s for s in symbols if symbol_to_token.get(s)]
            self._stop_event.clear()

            self.ticker.on_ticks = self._on_tick
            self.ticker.on_connect = self._on_connect
            self.ticker.on_close = self._on_close
            self.ticker.on_error = self._on_error
            self.ticker.on_reconnect = self._on_reconnect
            self.ticker.on_noreconnect = self._on_noreconnect

            # threaded=True: KiteTicker runs reactor in its own thread with
            # installSignalHandlers=False, avoiding the signal/main-thread crash
            self.ticker.connect(threaded=True)
            self._ticker_thread = getattr(self.ticker, 'websocket_thread', None)
            _log("info", f"Zerodha WebSocket connecting to {self.subscribed_symbols}")

            # Start watchdog
            watchdog = threading.Thread(target=self._watchdog, daemon=True, name="ticker-watchdog")
            watchdog.start()

            return True

        except Exception as e:
            _log("error", f"WebSocket connection error: {e}")
            return False

    def _hard_stop_ticker(self):
        """Forcefully stop any existing ticker and its thread"""
        self._stop_event.set()  # signal watchdog to exit
        if self.ticker:
            try:
                # Stop reconnect attempts WITHOUT calling ticker.close() —
                # ticker.close() calls reactor.stop() and Twisted reactors cannot
                # be restarted, which permanently breaks all future reconnects.
                if hasattr(self.ticker, 'factory') and self.ticker.factory:
                    self.ticker.factory.stopTrying()
                if hasattr(self.ticker, 'ws') and self.ticker.ws:
                    self.ticker.ws.transport.loseConnection()
            except Exception:
                pass
            self.ticker = None
        self.is_connected = False
        self._ticker_thread = None

    def disconnect(self):
        """Disconnect WebSocket and stop watchdog"""
        _log("info", "Disconnecting Zerodha WebSocket")
        self._hard_stop_ticker()

    def _watchdog(self):
        """
        Background thread: checks every 30s that ticks are flowing during market hours.
        If no tick received in 90s while monitoring is active and market is open,
        triggers a reconnect automatically.
        """
        STALE_THRESHOLD = 90  # seconds without a tick before we consider it dead
        CHECK_INTERVAL = 30

        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=CHECK_INTERVAL)
            if self._stop_event.is_set():
                break

            try:
                from main import monitoring
            except Exception:
                break

            if not monitoring:
                continue

            if not _is_market_hours():
                continue  # don't alert outside market hours

            now = time.time()
            if self.last_tick_time is None:
                # Connected but never got a tick — might still be initializing
                continue

            lag = now - self.last_tick_time
            if lag > STALE_THRESHOLD:
                _log("error",
                     f"WATCHDOG: No tick for {lag:.0f}s during market hours — ticker appears dead. "
                     f"Attempting reconnect...")
                self._attempt_reconnect()

    def _attempt_reconnect(self):
        """Reconnect ticker using existing symbol subscription"""
        if not self.subscribed_symbols:
            _log("error", "Watchdog: no symbols to reconnect to")
            return
        try:
            from main import monitoring
            if not monitoring:
                return
        except Exception:
            return

        _log("info", f"Watchdog reconnecting to {self.subscribed_symbols}")
        self.connect(self.subscribed_symbols)

    def _connect_safe(self):
        """Wrap ticker.connect() to suppress signal-only-main-thread ValueError"""
        try:
            self.ticker.connect()
        except ValueError as e:
            if "signal only works in main thread" in str(e):
                pass  # expected when running in non-main thread — watchdog handles reconnects
            else:
                raise
        except Exception as e:
            _log("error", f"KiteTicker connect error: {e}")

    def _on_tick(self, ws, ticks):
        """Handle incoming ticks"""
        self.last_tick_time = time.time()  # watchdog heartbeat

        for tick in ticks:
            try:
                token = tick.get('instrument_token')
                symbol = self.token_to_symbol.get(token, str(token))
                ltp = tick.get('last_price', 0)
                bid_qty = tick.get('total_buy_quantity', 0)
                ask_qty = tick.get('total_sell_quantity', 0)
                volume = tick.get('volume_traded', tick.get('volume', 0))
                ts = tick.get('exchange_timestamp') or tick.get('last_trade_time')
                timestamp = int(ts.timestamp()) if ts else int(time.time())

                if symbol and ltp > 0:
                    db.insert_tick(
                        symbol=str(symbol),
                        timestamp=timestamp,
                        ltp=ltp,
                        bid_qty=bid_qty,
                        ask_qty=ask_qty,
                        volume=volume
                    )

                self.last_raw_tick = tick

                if self.on_tick_callback:
                    self.on_tick_callback({
                        'symbol': str(symbol),
                        'ltp': ltp,
                        'bid_qty': bid_qty,
                        'ask_qty': ask_qty,
                        'volume': volume,
                        'timestamp': timestamp
                    })

            except Exception as e:
                _log("error", f"Error processing tick: {e}")

    def _on_connect(self, ws, response):
        self.is_connected = True
        self.reconnect_attempts = 0
        self.last_tick_time = time.time()  # reset so watchdog doesn't immediately fire
        self.ticker.subscribe(self.subscribed_tokens)
        self.ticker.set_mode(self.ticker.MODE_FULL, self.subscribed_tokens)
        _log("info", f"Zerodha WebSocket connected — subscribed to {self.subscribed_symbols}")

    def _on_close(self, ws, code, reason):
        self.is_connected = False
        _log("error", f"Zerodha WebSocket closed (code={code}): {reason}")

    def _on_error(self, ws, code, reason):
        _log("error", f"Zerodha WebSocket error (code={code}): {reason}")

    def _on_reconnect(self, ws, response):
        self.reconnect_attempts += 1
        _log("error", f"Zerodha WebSocket reconnecting (attempt {self.reconnect_attempts}/{self.max_reconnect_attempts})")

    def _on_noreconnect(self, ws):
        """All built-in reconnect attempts exhausted — watchdog will handle restart"""
        _log("error",
             f"Zerodha WebSocket gave up built-in reconnects after {self.reconnect_attempts} attempts. "
             f"Watchdog will attempt recovery.")
        self.is_connected = False
        # Don't set monitoring=False here — watchdog will reconnect
        # If watchdog also fails, the Ticker: Dead badge will alert the user

    def get_connection_status(self) -> dict:
        lag = round(time.time() - self.last_tick_time, 1) if self.last_tick_time else None
        return {
            'is_connected': self.is_connected,
            'subscribed_symbols': self.subscribed_symbols,
            'reconnect_attempts': self.reconnect_attempts,
            'seconds_since_last_tick': lag
        }


# Global WebSocket client (will be initialized by main)
ws_client = None
