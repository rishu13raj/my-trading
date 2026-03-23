# My Trading Bot - Setup Guide

## Prerequisites
- Python 3.8+
- Zerodha account with API access
- pip/conda for package management

## Installation & Setup

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Zerodha Credentials
Edit `.env` file and add your credentials:
```
API_KEY=your_api_key_here
API_SECRET=your_api_secret_here
ACCESS_TOKEN=your_access_token_here
```

**Getting Credentials:**
- API_KEY: Available in your Zerodha console
- API_SECRET: Provided when you set up API access
- ACCESS_TOKEN: Generated via authentication (see below)

### 3. Authenticate with Zerodha

**Option A: Via Web UI (Recommended)**
1. Start the server: `python backend/main.py`
2. Open http://localhost:8000 in browser
3. Click "Authenticate Zerodha" button
4. Follow the login flow
5. Copy request token and paste it back in the app

**Option B: Via Command Line**
```python
from backend.auth.zerodha_auth import auth

# Get login URL
login_url = auth.generate_login_url()
print(login_url)

# After logging in, exchange request token
request_token = "your_request_token_from_login"
access_token = auth.generate_access_token(request_token)
```

### 4. Start the Application

**Terminal 1 - Backend:**
```bash
cd backend
python main.py
```

The server will start at `http://localhost:8000`

**Terminal 2 - Frontend:**
```bash
cd frontend
# If you have Python 3.10+
python -m http.server 3000

# Or use any simple HTTP server
# For Node.js users: npx http-server -p 3000
```

Open browser: `http://localhost:3000`

## Usage

1. **Authenticate**: Click the Zerodha authentication button
2. **Select Stocks**: Add 2-3 stock symbols (e.g., RELIANCE, INFY, TCS)
3. **Configure Settings**: Set capital per trade and stop loss percentage
4. **Start Monitoring**: Click "Start Monitoring" to watch market for signals
5. **Monitor Trades**: Watch active positions and P&L in real-time
6. **Exit**: Click "Exit All" to close all positions manually

## Configuration

Edit `.env` file to customize:

```
# Trading Parameters
CAPITAL_PER_TRADE=50000        # Capital allocated per trade
MAX_ACTIVE_TRADES=2            # Maximum simultaneous open trades
STOP_LOSS_PCT=0.05             # Stop loss percentage (5%)
TIME_EXIT_MINUTES=60           # Exit after 60 minutes if no profit

# Strategy Parameters
BID_ASK_THRESHOLD_RATIO=2.0    # Entry threshold (2.0x = 200% more buyers)
MOMENTUM_WINDOW_SECONDS=60     # Monitor for 60 seconds before entry
ENTRY_WAIT_SECONDS=60          # Confirmation window

# Server
SERVER_HOST=127.0.0.1
SERVER_PORT=8000
```

## File Structure

```
backend/
├── main.py                 # FastAPI server entry point
├── config.py               # Configuration loader
├── auth/
│   └── zerodha_auth.py    # Zerodha authentication
├── data/
│   ├── database.py         # SQLite database
│   └── websocket_client.py # Real-time data connection
├── strategy/
│   ├── bid_ask_ratio.py    # Bid-ask analysis
│   ├── momentum.py         # Momentum detection
│   ├── stop_loss.py        # Stop loss logic
│   ├── exit_logic.py       # Exit conditions
│   └── signal.py           # Signal generation
└── trading/
    ├── portfolio.py        # Portfolio management
    ├── paper_trader.py     # Paper trading simulator
    └── order_manager.py    # Order execution

frontend/
├── index.html              # Web UI
├── app.js                  # JavaScript logic
└── style.css               # Styling
```

## Troubleshooting

### "API Key not configured"
- Add API_KEY to .env file
- Restart the server

### "WebSocket connection failed"
- Check Zerodha API subscription
- Ensure access token is valid
- Check internet connection

### "Port 8000 already in use"
- Change SERVER_PORT in .env
- Or kill the process: `lsof -ti:8000 | xargs kill -9`

### Database errors
- Delete `trading.db` and restart
- The database will be recreated automatically

## Features

✅ Paper trading (simulated)
✅ Real-time market data via WebSocket
✅ Momentum-based trading signals
✅ Bid-ask imbalance analysis
✅ Automatic stop loss management
✅ Time-based exit logic
✅ Position tracking & P&L calculation
✅ Trade history logging
✅ Web-based dashboard

## Next Steps

1. **Backtest the strategy** on historical data
2. **Paper trade** for 1-2 weeks to validate signals
3. **Analyze performance** and tweak parameters
4. **Switch to live trading** (modify PAPER_TRADING=false in .env)
5. **Monitor trades** and continuously improve

## Support

For issues or questions:
1. Check logs in terminal
2. Review trade logs on dashboard
3. Verify Zerodha connectivity
4. Check .env configuration

Happy trading! 📈
