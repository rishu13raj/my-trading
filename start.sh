#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# start.sh — one-shot dev launcher for Zerodha Momentum Trader
# Usage: ./start.sh
# ─────────────────────────────────────────────────────────────────────────────
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"

# ── 1. Backend setup ──────────────────────────────────────────────────────────
echo "==> Setting up backend…"
cd "$BACKEND"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
  echo "    Python venv created."
fi

source .venv/bin/activate
pip install -q -r requirements.txt

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo ""
  echo "⚠  IMPORTANT: backend/.env was created from .env.example."
  echo "   Please edit it and fill in your KITE_API_KEY and KITE_API_SECRET."
  echo "   Then re-run this script."
  echo ""
  exit 1
fi

# ── 2. Frontend setup ─────────────────────────────────────────────────────────
echo "==> Setting up frontend…"
cd "$FRONTEND"

if [ ! -d "node_modules" ]; then
  npm install
fi

# ── 3. Launch both in parallel ────────────────────────────────────────────────
echo ""
echo "==> Starting backend  on http://localhost:8000"
echo "==> Starting frontend on http://localhost:5173"
echo "==> Press Ctrl+C to stop both."
echo ""

cd "$BACKEND"
uvicorn main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

cd "$FRONTEND"
npm run dev &
FRONTEND_PID=$!

# Trap Ctrl+C and kill both
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" INT TERM

wait $BACKEND_PID $FRONTEND_PID
