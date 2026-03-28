# 💹 My Trading Bot

A sophisticated momentum-based options/stock trading system using Zerodha APIs.

## Quick Start (5 minutes)

### Prerequisites
- ✅ conda installed
- ✅ asdf installed
- ✅ Zerodha account with API access (API_KEY & API_SECRET)

### Step 1: One-Time Setup
```bash
./setup.sh
```

### Step 2: Start Services (every time)

**Terminal 1 — Backend:**
```bash
conda activate my-trading
cd backend
python main.py
```
Backend runs at: `http://localhost:8000`

**Terminal 2 — Frontend:**
```bash
cd frontend
npm start
```
Dashboard runs at: `http://localhost:3001`

### Step 3: Open Dashboard
```
http://localhost:3001
```

### Killing stuck processes
```bash
# Backend
lsof -ti:8000 | xargs kill -9

# Frontend
lsof -ti:3001 | xargs kill -9
```

---

## 📋 Commands Reference

| Command | Purpose |
|---------|---------|
| `./setup.sh` | One-time setup (installs all dependencies) |
| `./start.sh both` | Start both services (run frontend in another terminal) |
| `./start.sh backend` | Start FastAPI server only (port 8000) |
| `./start.sh frontend` | Start web dashboard only (port 3001) |
| `./start.sh check` | Check if services are running |
| `conda activate my-trading` | Activate Python environment manually |
| `npm start` (in frontend/) | Start frontend manually |

---

## 🏗️ Architecture

### Backend (Python)
- **Framework**: FastAPI + Uvicorn
- **Environment**: conda (Python 3.11)
- **Database**: SQLite
- **Real-time Data**: Zerodha WebSocket API
- **Strategy**: Bid-ask imbalance + momentum detection
- **Ports**: Backend 8000, Frontend 3001

### Frontend (JavaScript)
- **Runtime**: Node.js 20.10 (via asdf)
- **Server**: http-server
- **UI**: Pure HTML/CSS/JS (no dependencies)
- **Updates**: WebSocket + REST API polling

---

## 🎯 Trading Strategy

**Core Logic:**
1. Monitor bid-ask quantities for 2-3 selected stocks
2. Detect imbalance (2.0x threshold = buy signal if more buyers)
3. Confirm momentum over 60-second window
4. Enter trade with position sizing (₹50k capital per trade)
5. Exit on: -5% stop loss OR 1-hour time OR momentum reversal

**Capital Management:**
- Max 5 active trades simultaneously
- ₹50,000 per trade (configurable via dashboard)
- ₹100,000 daily capital limit
- Paper trading by default (no real money)

---

## 📁 Project Structure

```
my-trading/
├── backend/
│   ├── main.py                    # FastAPI server
│   ├── config.py                  # Configuration loader
│   ├── auth/
│   │   └── zerodha_auth.py       # Zerodha authentication
│   ├── data/
│   │   ├── database.py            # SQLite models
│   │   └── websocket_client.py   # Real-time data
│   ├── strategy/
│   │   ├── bid_ask_ratio.py      # Imbalance calculation
│   │   ├── momentum.py            # Momentum detection
│   │   ├── stop_loss.py          # Stop loss logic
│   │   ├── exit_logic.py         # Exit conditions
│   │   └── signal.py             # Signal generation
│   └── trading/
│       ├── portfolio.py           # Position tracking
│       ├── paper_trader.py       # Order execution
│       └── order_manager.py      # Trade orchestration
│
├── frontend/
│   ├── index.html                 # Web UI
│   ├── app.js                     # Frontend logic
│   ├── style.css                  # Styling
│   └── package.json               # npm config
│
├── .env                           # Configuration (credentials)
├── environment.yml                # conda dependencies
├── .node-version                  # Node.js version (asdf)
├── setup.sh                       # One-time setup script
└── start.sh                       # Service startup script
```

---

## 🔐 Environment Setup

**Credentials and all config live in `.env` — never hardcode them in code.**

```bash
cp .env.example .env
nano .env   # fill in your values
```

`.env` is gitignored. It will never be committed. `.env.example` is committed and shows every available key with placeholder values — it is the source of truth for what can be configured.

### Required (must fill in)
```
API_KEY=your_zerodha_api_key
API_SECRET=your_zerodha_api_secret
ACCESS_TOKEN=        # generated daily via the Authenticate button in the dashboard
```

`API_KEY` and `API_SECRET` come from https://kite.zerodha.com/account/settings/developers. Each user needs their own — credentials cannot be shared between accounts.

`ACCESS_TOKEN` expires at midnight every day. It is regenerated automatically when you click "Authenticate Zerodha" in the dashboard. The app writes the new token back to `.env` automatically.

### Optional (defaults in `.env.example` are sensible)
All trading parameters — capital per trade, stop loss %, session windows, thresholds — are in `.env`. Change them there, restart the backend, no code changes needed.

### For a new coding agent
- **Never** add credentials to any `.py`, `.js`, `.json`, or `.yaml` file
- **Never** commit `.env` (it is gitignored at root level)
- **Always** add new config values to `config.py` via `os.getenv("KEY", default)` and document them in `.env.example`
- The single source of truth for all env keys is `.env.example`

---

## 🚀 First Trade

1. **Authenticate**: Click "🔐 Authenticate Zerodha" in dashboard
2. **Select Stocks**: Add 2-3 symbols (e.g., RELIANCE, INFY, TCS)
3. **Start Monitoring**: Click "▶️ Start Monitoring"
4. **Watch Signals**: System detects bid-ask imbalances
5. **Auto Trade**: Orders placed when momentum confirmed
6. **Monitor**: Real-time P&L tracking on dashboard
7. **Exit**: Click "🚪 Exit All" to close positions

---

## 📊 Features

- ✅ Real-time WebSocket data streaming
- ✅ Paper trading (simulated orders)
- ✅ Bid-ask imbalance signal generation
- ✅ Momentum confirmation (60-second window)
- ✅ Automatic stop loss (-5%)
- ✅ Time-based exits (1 hour)
- ✅ Position tracking (max 2 simultaneous)
- ✅ Trade history & P&L logging
- ✅ Web dashboard with live updates
- ✅ Portfolio summary & statistics

---

## 🛠️ Troubleshooting

| Issue | Solution |
|-------|----------|
| "conda not found" | Reinstall conda or add to PATH |
| "asdf not found" | Reinstall asdf or add to PATH |
| "Port 8000 in use" | `lsof -ti:8000 \| xargs kill -9` |
| "Port 3000 in use" | `lsof -ti:3000 \| xargs kill -9` |
| "No API credentials" | Update `.env` with Zerodha credentials |
| "WebSocket failed" | Check Zerodha subscription & connectivity |

---

## 📚 Documentation

- **QUICKSTART.md** - Detailed setup instructions
- **SETUP.md** - Full feature documentation
- **ML_RESEARCH.md** - ML/AI roadmap, feature engineering notes, model progression plan
- **TODO.md** - Pending improvements
- **backend/main.py** - API endpoint documentation
- **start.sh** - Service startup script
- **setup.sh** - Automated setup script

---

## ⚠️ Disclaimer

- **Paper Trading**: This system uses simulated orders. No real money is spent.
- **Risk**: Before switching to live trading, thoroughly test the strategy.
- **Losses**: The system uses stop losses (-5%) to limit downside.
- **Volatility**: Market conditions affect signal reliability.

Start with paper trading for 1-2 weeks before going live!

---

## 📈 Next Steps

1. Run `./setup.sh` to install dependencies
2. Update `.env` with your Zerodha API credentials
3. Run `./start.sh backend` in one terminal
4. Run `./start.sh frontend` in another terminal
5. Open http://localhost:3000 and start trading!

Good luck! 🚀
