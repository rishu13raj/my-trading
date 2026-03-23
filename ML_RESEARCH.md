# ML Research Notes — Trading Bot

## Current State (as of March 2026)

### What we log today
- `bid_qty`, `ask_qty`, `LTP`, `volume`, `timestamp` — per tick, per symbol
- Trades: entry/exit price, qty, direction, P&L, mode (paper/live), exit reason
- Signal metadata (added): entry ratio, OFI, signal confidence, normalized imbalance, minutes since open

### What's missing
- NIFTY 50 live price context (not stored)
- India VIX (not stored)
- Level 2 depth (3–5 price levels) — Zerodha provides this, not yet consumed
- VWAP per symbol (not computed/stored)
- Trade direction classification (tick rule)

---

## Feature Engineering Priority List

### Tier 1 — Derive from existing tick data (zero cost)

| Feature | Formula | Why |
|---|---|---|
| Normalized imbalance | `(bid_qty - ask_qty) / (bid_qty + ask_qty)` | Bounded [-1, +1], more stable than raw ratio |
| OFI (Order Flow Imbalance) | `delta_bid_qty - delta_ask_qty` between consecutive ticks | Most studied HFT microstructure signal. Near-linear relationship with mid-price change (Cont, Kukanov & Stoikov 2014) |
| Trade direction | Up tick = buyer initiated, down tick = seller initiated | Classifies aggressor side |
| Rolling realized volatility | Std of log returns over last 20 and 60 ticks | Is signal happening in volatile or calm regime? |
| Log ratio | `ln(bid_qty / ask_qty)` | Handles outliers better than raw ratio |
| Minutes since open | From timestamp, session starts 9:15 AM IST | One of strongest intraday features — volume/volatility is U-shaped |
| Minutes to close | From timestamp, session ends 3:30 PM IST | EOD squaring-off pressure is a real, repeatable pattern |
| VWAP deviation | `(LTP - VWAP) / VWAP`, compute rolling VWAP from tick data | Stocks mean-revert to VWAP intraday — strong exit signal |

### Tier 2 — Requires additional data feeds

| Feature | Source | Why |
|---|---|---|
| NIFTY 50 return since open | Zerodha API (subscribe token 256265) | Is stock moving with or diverging from market? |
| India VIX | NSE website / Zerodha | High VIX = unreliable bid-ask signals, wider spreads. Regime filter |
| Level 2 depth (3–5 levels) | Zerodha KiteTicker full mode | Best bid alone is misleading; queue depth behind it matters |
| Sector index return | Zerodha API | Relative strength: stock vs. sector |
| Prior-day OHLCV | Zerodha historical API | Anchors: prev close, 20-day SMA, ATR, avg volume |

### Tier 3 — Advanced (needs 3–6 months of data first)

| Feature | Notes |
|---|---|
| VPIN (Volume-Synchronized Probability of Informed Trading) | Measures "flow toxicity" — are informed traders active? High VPIN = adverse selection risk |
| Hawkes process arrival rate | Recent clustering of trades is itself predictive of future clustering |
| Multi-level OFI | OFI computed across 3–5 price levels, not just best bid/ask |
| Options PCR / IV skew | Put-call ratio and implied vol skew for directional sentiment |

---

## Training Data Strategy

### When to start training
- Minimum: 20 trading days of data across different market conditions
- Recommended: 60+ days (trending days, choppy days, event days)
- Today's data (3 trades, 30 min session) = not enough for anything

### Validation approach
**Walk-forward validation only** — NOT random train/test split.
- Train on past N days → test on next day → roll forward
- Retrain weekly or monthly
- Random splits leak future information in time-series data

### Key warning from 2024 benchmark study
All LOB-based deep learning models tested showed significant performance degradation on out-of-sample data. The bid-ask imbalance signal degrades quickly on smaller/less liquid stocks. Liquid large-cap stocks (NIFTY 50 components) retain signal quality longer.

### Training labels
For each trade entry, label it:
- `1` = profitable trade (pnl > 0)
- `0` = unprofitable trade (pnl <= 0)
- Or regression: actual pnl as % of capital

For signal quality, the ratio at entry + OFI at entry + time of day are the most important features to capture at the moment of the trading decision.

---

## Model Progression Plan

### Phase 1: Signal Filter (after 20+ trading days)
**Goal:** Given current market conditions, what is the probability this signal is profitable?
**Model:** Logistic regression or XGBoost (fast, interpretable, works on small datasets)
**Features:** entry ratio, OFI, normalized imbalance, time of day, rolling volatility
**Output:** Probability score — only enter if score > threshold (e.g. 0.65)

### Phase 2: Dynamic Threshold (after 60+ trading days)
**Goal:** Instead of fixed 1.6x ratio, learn optimal threshold per stock per time-of-day
**Model:** Gradient boosting with feature importance analysis
**Output:** Adjusted entry threshold per symbol/time combination

### Phase 3: Exit Timing (after 3+ months)
**Goal:** Learn optimal hold duration given entry conditions
**Model:** Survival analysis or reinforcement learning
**Output:** "Hold" / "Exit now" recommendation at each tick after entry

### Phase 4: Market Regime Detection (after 3+ months)
**Goal:** Classify each day at 9:15 AM as trending / choppy / event-driven
**Model:** Clustering (k-means) or classification on overnight futures + first 5 min price action
**Output:** Trading mode for the day — aggressive / cautious / sit out

---

## Data Storage Plan

### Live trading (current): SQLite
- `ticks` table: raw tick data per symbol
- `trades` table: every entry/exit with signal metadata
- Fast enough for real-time queries, simple, local

### ML training (EOD export): Parquet files
- One file per day per symbol: `ml_data/2026-03-23_NTPCGREEN.parquet`
- Contains all ticks + derived features computed at EOD
- Reads into pandas DataFrame in milliseconds
- Industry standard format for backtesting and ML

### EOD export script
Run automatically at 3:35 PM after square-off.
Computes all Tier 1 derived features from SQLite ticks and writes Parquet.

---

## Key Papers & Resources

| Resource | Link | What it covers |
|---|---|---|
| Cont, Kukanov & Stoikov (2014) | arXiv:1011.6402 | OFI → mid-price relationship (foundational) |
| DeepLOB (Zhang et al., 2019) | arXiv:1808.03668 | CNN+LSTM on raw LOB data |
| Ntakaris et al. (2018) | Wiley Journal of Forecasting | Benchmark 10-level LOB feature representation |
| Deep LOB Forecasting Guide (2025) | arXiv:2403.09267 | Comprehensive microstructure feature guide |
| LOB-based DL Benchmark (2024) | Springer | Tests 15 models — overfitting warning |
| VPIN Paper (Easley et al.) | Stern NYU PDF | Flow toxicity, informed trading detection |
| ML for Algo Trading (Stefan Jansen) | GitHub: stefan-jansen/machine-learning-for-trading | Practical Python implementation |
| OFI as HFT Signal (Markwick) | dm13450.github.io | Practical OFI implementation |
| hftbacktest LOB tutorial | hftbacktest.readthedocs.io | Order book imbalance market making |

---

## Daily Review Checklist (Post-Market)

After each trading session, note:
1. How many signals fired? How many were entered?
2. Were entries during first 30 min (9:15–9:45) or last 30 min (3:00–3:30)?
3. Was it a trending day or choppy day? (subjective judgment — log it)
4. Did momentum reversal exit fire correctly or too early?
5. What was NIFTY doing on the day? (up/down/flat, % move)
6. India VIX level (high/medium/low)

This qualitative log becomes your regime labels for Phase 4.
