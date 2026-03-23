# My Trading Bot - Quick Start

## Prerequisites
- conda (already installed ✓)
- asdf (already installed ✓)
- Zerodha account with API access

---

## 🚀 One-Time Setup

### 1. Configure Environment Variables

Edit `.env` file:
```bash
nano .env
```

Update with your credentials:
```
API_KEY=us25z34qmhdjuf3v
API_SECRET=your_zerodha_api_secret_here
ACCESS_TOKEN=leave_empty_for_now
```

Save and exit (Ctrl+X → Y → Enter for nano).

### 2. Setup Backend (Python/Conda)

```bash
# Create conda environment from environment.yml
conda env create -f environment.yml

# Activate environment
conda activate my-trading

# Verify installation
python -c "import kiteconnect; print('✓ Backend ready')"
```

### 3. Setup Frontend (Node.js/asdf)

```bash
# Install Node.js version specified in .node-version
asdf install nodejs

# Install npm dependencies
cd frontend
npm install
cd ..

# Verify installation
node --version
npm --version
```

---

## ▶️ Start Services (Every Time)

### Terminal 1: Start Backend

```bash
# Activate conda environment
conda activate my-trading

# Start FastAPI server
cd backend
python main.py
```

Server runs at: `http://localhost:8000`

### Terminal 2: Start Frontend

```bash
# Frontend already uses asdf Node version from .node-version
cd frontend
npm start
```

Dashboard runs at: `http://localhost:3000`

---

## 📋 Quick Commands Reference

| Task | Command |
|------|---------|
| Create conda env | `conda env create -f environment.yml` |
| Activate conda env | `conda activate my-trading` |
| Deactivate conda env | `conda deactivate` |
| Install Node | `asdf install nodejs` |
| Check Node version | `node --version` |
| Install frontend deps | `cd frontend && npm install` |
| Start backend | `conda activate my-trading && cd backend && python main.py` |
| Start frontend | `cd frontend && npm start` |
| View .env | `cat .env` |
| Edit .env | `nano .env` (or your editor) |

---

## 🔄 Full Fresh Start (After First Setup)

If you need to restart everything from scratch:

```bash
# Terminal 1 - Backend
conda activate my-trading
cd backend
python main.py

# Terminal 2 - Frontend (in new terminal)
cd frontend
npm start
```

Done! Open http://localhost:3000

---

## ✅ Health Check

Once both services are running:

```bash
# Backend health
curl http://localhost:8000/status

# Frontend
Open http://localhost:3000 in browser
```

Should see:
- ✓ Backend: JSON response with status
- ✓ Frontend: Web dashboard loads

---

## 🔐 First Authentication

1. Frontend loads at http://localhost:3000
2. Click "🔐 Authenticate Zerodha"
3. You'll be redirected to Zerodha login
4. Login with your Zerodha credentials
5. Copy the **request_token** from the URL
6. Paste it into the app prompt
7. System auto-saves **access_token** to `.env`

After this, you're ready to trade!

---

## 📝 Common Issues

**"command not found: conda"**
- Conda not in PATH. Reinstall or add to PATH.

**"command not found: asdf"**
- asdf not in PATH. Add to shell profile or reinstall.

**"Port 8000 already in use"**
- Kill existing process: `lsof -ti:8000 | xargs kill -9`
- Or change `SERVER_PORT` in `.env`

**"Port 3000 already in use"**
- Kill existing process: `lsof -ti:3000 | xargs kill -9`
- Or run: `npm start -- --port 3001`

**"ModuleNotFoundError: No module named 'kiteconnect'"**
- Make sure conda env is activated: `conda activate my-trading`

**"npm: command not found"**
- asdf Node.js not activated. Check: `cd frontend && asdf current nodejs`

---

## 🎯 Ready to Trade?

1. ✅ Backend running on port 8000
2. ✅ Frontend running on port 3000
3. ✅ .env configured with API credentials
4. ✅ Zerodha authenticated

Go to http://localhost:3000 and start trading! 📈
