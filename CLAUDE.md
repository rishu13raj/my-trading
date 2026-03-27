# Rules for Claude

## Backend Process — NEVER restart without permission

**NEVER kill or restart the trading backend (`backend/main.py`, process `python main.py`) without explicit user confirmation.**

Ask first: "This requires a backend restart — is that okay?"

**Why this matters:** Restarting clears in-memory tick history. The trend gate needs 5+ minutes of history to block bad signals. A restart during market hours (09:15–15:30 IST) directly causes losing trades.

For health checks and debugging, use read-only commands only:
- `curl http://localhost:8000/status`
- `tail /tmp/trading-backend.log`
- `ps aux | grep main.py`

Code changes that require a restart → ask the user first, always.
