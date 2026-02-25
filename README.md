# Zerodha Momentum Trader

An intraday **Order Book Imbalance (OBI) momentum strategy** built on the Zerodha KiteConnect API.

---

## Strategy at a Glance

```
OBI = (Total Buy Qty − Total Sell Qty) / (Total Buy Qty + Total Sell Qty)

OBI range: −1.0 (all sellers) → +1.0 (all buyers)
```

| Condition | Action |
|---|---|
| OBI > threshold, rising, sustained N ticks after observation window | **BUY** (LONG) |
| OBI < −threshold, falling, sustained N ticks after observation window | **SHORT SELL** |
| OBI flips sign, or rate of change reverses | **Exit position** |
| Per-trade loss ≥ stop-loss | **Auto-exit** |
| Daily loss ≥ daily stop-loss | **Halt all trading** |

---

## Architecture

```
┌──────────────┐   WebSocket /ws    ┌──────────────────────────────┐
│  React UI    │ ◄───────────────── │  FastAPI Backend             │
│  (Vite :5173)│ ──── REST /api ──► │  main.py                     │
└──────────────┘                    │                              │
                                    │  ┌─────────────────────┐    │
                                    │  │  KiteConnect        │    │
                                    │  │  REST + KiteTicker  │    │
                                    │  │  WebSocket          │    │
                                    │  └────────┬────────────┘    │
                                    │           │ ticks           │
                                    │  ┌────────▼────────────┐    │
                                    │  │  Strategy Engine    │    │
                                    │  │  (OBI + momentum)   │    │
                                    │  └────────┬────────────┘    │
                                    │           │ signals         │
                                    │  ┌────────▼────────────┐    │
                                    │  │  Order Manager      │    │
                                    │  │  (Zerodha orders)   │    │
                                    │  └────────┬────────────┘    │
                                    │           │ trades          │
                                    │  ┌────────▼────────────┐    │
                                    │  │  Trade Logger       │    │
                                    │  │  (SQLite)           │    │
                                    │  └─────────────────────┘    │
                                    └──────────────────────────────┘
```

---

## Setup

### Prerequisites

- Python 3.11+
- Node.js 18+
- A [Zerodha Developer account](https://developers.kite.trade/) with an API key

### Quick Start

```bash
git clone <repo>
cd my-trading
./start.sh
```

The script will:
1. Create a Python venv and install backend dependencies
2. Copy `.env.example` → `.env` on first run (fill in your keys)
3. Install frontend npm packages
4. Start both servers

| Service  | URL |
|---|---|
| Frontend | http://localhost:5173 |
| Backend API | http://localhost:8000 |
| API Docs | http://localhost:8000/docs |

### Manual Setup

```bash
# Backend
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # edit with your keys
uvicorn main:app --reload

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

---

## Authentication (Every Session)

Zerodha access tokens expire **daily**. Each morning:

1. Open the app → click **Get Zerodha Login URL**
2. Log in via Kite → copy the `request_token` from the redirect URL
3. Exchange it: `GET http://localhost:8000/auth/callback?request_token=<token>`
4. Copy the returned `access_token` and paste it into the app, **or** set
   `KITE_ACCESS_TOKEN=...` in `.env` for auto-init on startup

---

## Daily Trading Workflow

1. **Morning**: Authenticate (fresh access token)
2. **Configure basket**: Add 2–3 high-volume stocks (e.g., INFY, TCS, RELIANCE)
3. **Set risk parameters**: capital per trade, stop-losses
4. **Press "Apply Configuration & Connect"** — market data feed starts
5. **Observe** the OBI gauges and charts
6. **Press "Start Monitoring"** — begins the 60-second observation window; entry fires automatically when signal conditions are met
7. **Monitor the trade** — the app auto-exits on momentum reversal or stop-loss
8. **End of day**: Press "Stop" to halt

---

## Key Files

```
backend/
  main.py           # FastAPI app, WebSocket hub
  strategy.py       # OBI calculation + state machine (IDLE→OBSERVING→TRADING→HALTED)
  order_manager.py  # Order placement, position tracking, stop-loss enforcement
  kite_client.py    # KiteConnect wrapper (REST + KiteTicker WebSocket)
  market_data.py    # Market data loop (WebSocket preferred, REST fallback)
  trade_logger.py   # SQLite persistence
  config.py         # All settings via .env
  models.py         # Pydantic + SQLAlchemy models

frontend/src/
  App.jsx                       # Root component
  hooks/useTrader.js            # Central state + WebSocket + API
  hooks/useWebSocket.js         # WS connection with auto-reconnect
  components/ObiGauge.jsx       # Visual OBI bar
  components/ObiChart.jsx       # OBI sparkline (Recharts)
  components/TickCard.jsx       # Per-symbol live data card
  components/PnlBanner.jsx      # Daily P&L + strategy state
  components/TradeLog.jsx       # Closed trades table
  components/ConfigPanel.jsx    # Basket + risk configuration
  components/ControlBar.jsx     # Start / Stop / Reset buttons
  components/AuthPanel.jsx      # Authentication screen
```

---

## REST API Reference

| Method | Path | Description |
|---|---|---|
| GET | `/auth/login-url` | Get Kite OAuth URL |
| GET | `/auth/callback?request_token=` | Exchange token |
| POST | `/auth/token` | Set access token directly |
| POST | `/trading/configure` | Set basket + risk params, start data feed |
| POST | `/trading/start` | Enter observation window |
| POST | `/trading/stop` | Stop monitoring |
| POST | `/trading/reset` | Reset daily counters |
| GET | `/trading/status` | Current state + P&L |
| GET | `/trading/positions` | Open positions |
| GET | `/history/trades` | Recent closed trades |
| GET | `/history/daily` | Daily P&L history |
| GET | `/history/today` | Today's summary |
| WS | `/ws` | Real-time feed (ticks, signals, trade events) |

Interactive docs: **http://localhost:8000/docs**

---

## Tuning the Strategy

| Parameter | Default | Effect |
|---|---|---|
| `obi_threshold` | 0.30 | Higher = more selective entries, fewer trades |
| `obi_sustain_ticks` | 6 | Higher = longer sustained imbalance required |
| `observation_seconds` | 60 | Longer = more conservative; wait longer before entry |
| `capital_per_trade` | ₹10,000 | Position size (quantity = floor(capital / LTP)) |
| `per_trade_stop_loss` | ₹400 | Max loss per trade |
| `daily_stop_loss` | ₹1,000 | Max total daily loss before halting |

---

## Roadmap (Future Iterations)

- [ ] News sentiment scan via external API (pre-market)
- [ ] Global market context (US markets, ADR performance)
- [ ] Automated stock screening (high-volume candidates from news)
- [ ] Paper trading mode (no real orders, for backtesting live data)
- [ ] Historical performance analytics dashboard
- [ ] Multi-timeframe OBI confirmation
- [ ] Webhook / Telegram alerts for entries and exits

---

## Disclaimer

This software is for **educational and personal use only**. Trading in financial
markets involves significant risk. Always test with the **smallest possible
position sizes** before increasing capital. Past performance is no guarantee of
future results.
