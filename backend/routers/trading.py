"""
Trading control routes:
  POST /trading/configure   — set basket + risk params for the session
  POST /trading/start       — start the observation window
  POST /trading/stop        — halt monitoring (won't close open positions)
  POST /trading/reset       — reset daily counters
  GET  /trading/status      — current strategy state + P&L
  GET  /trading/positions   — live open positions
"""

from fastapi import APIRouter, HTTPException

from models import SessionConfig, AppStatus
from strategy import strategy, StrategyState
from order_manager import order_manager
from market_data import start_market_data, stop_market_data
from kite_client import kite_client

router = APIRouter(prefix="/trading", tags=["trading"])


@router.post("/configure")
async def configure_session(config: SessionConfig):
    """Configure the basket and risk parameters before starting."""
    if not kite_client.is_ready:
        raise HTTPException(status_code=401, detail="Not authenticated. POST /auth/token first.")

    basket = [{"symbol": item.symbol, "exchange": item.exchange} for item in config.basket]
    symbols = [item.symbol for item in config.basket]

    strategy.configure(
        basket=symbols,
        obi_threshold=config.obi_threshold,
        observation_seconds=config.observation_seconds,
        daily_stop_loss=config.daily_stop_loss,
        per_trade_stop_loss=config.per_trade_stop_loss,
    )
    order_manager.configure(
        capital_per_trade=config.capital_per_trade,
        per_trade_stop_loss=config.per_trade_stop_loss,
    )

    # Start the market data feed (WebSocket or REST)
    await stop_market_data()   # stop any previous feed
    await start_market_data(basket)

    return {"status": "configured", "basket": symbols}


@router.post("/start")
async def start_monitoring():
    """
    Press the 'Start' button — enters the observation window.
    The strategy will watch the OBI for `observation_seconds` before committing to a trade.
    """
    if not kite_client.is_ready:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    if strategy.state == StrategyState.IDLE and not strategy._basket:
        raise HTTPException(status_code=400, detail="Configure basket first via POST /trading/configure.")
    if strategy.state == StrategyState.HALTED:
        raise HTTPException(status_code=400, detail="Trading halted (daily stop-loss). Reset first.")

    strategy.start_observing()
    return {"status": "observing", "observation_seconds": strategy.observation_seconds}


@router.post("/stop")
async def stop_monitoring():
    """Manually stop monitoring. Does NOT close open positions."""
    strategy.stop()
    return {"status": "stopped"}


@router.post("/reset")
async def reset_session():
    """Reset daily P&L counters and strategy state. Use at the start of each day."""
    strategy.reset_session()
    return {"status": "reset"}


@router.get("/status", response_model=AppStatus)
async def get_status():
    snap = strategy.snapshot()
    return AppStatus(
        trading_halted=strategy.state == StrategyState.HALTED,
        daily_pnl=strategy.daily_pnl,
        daily_trades=strategy.daily_trades,
        active_positions=len(strategy.open_positions),
        message=f"Strategy state: {strategy.state.name}",
    )


@router.get("/positions")
async def get_positions():
    positions = []
    for sym, pos in order_manager.positions.items():
        positions.append({
            "symbol": sym,
            "direction": pos.direction.value,
            "quantity": pos.quantity,
            "entry_price": pos.entry_price,
            "entry_time": pos.entry_time.isoformat(),
            "stop_loss_price": pos.stop_loss_price,
        })
    return {"positions": positions}
