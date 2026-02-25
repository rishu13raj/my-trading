"""
History routes — past trades and daily stats.
"""

from fastapi import APIRouter, Query

from models import TradeOut
from trade_logger import get_recent_trades, get_daily_stats, get_today_stats

router = APIRouter(prefix="/history", tags=["history"])


@router.get("/trades", response_model=list[TradeOut])
async def list_trades(limit: int = Query(50, ge=1, le=500)):
    trades = await get_recent_trades(limit=limit)
    return trades


@router.get("/daily")
async def daily_stats(days: int = Query(30, ge=1, le=365)):
    stats = await get_daily_stats(days=days)
    return [
        {
            "date": s.date,
            "total_pnl": s.total_pnl,
            "trade_count": s.trade_count,
            "winning_trades": s.winning_trades,
            "losing_trades": s.losing_trades,
            "win_rate": (
                round(s.winning_trades / s.trade_count * 100, 1)
                if s.trade_count else 0.0
            ),
        }
        for s in stats
    ]


@router.get("/today")
async def today_summary():
    stats = await get_today_stats()
    if not stats:
        return {"date": None, "total_pnl": 0.0, "trade_count": 0,
                "winning_trades": 0, "losing_trades": 0, "win_rate": 0.0}
    return {
        "date": stats.date,
        "total_pnl": stats.total_pnl,
        "trade_count": stats.trade_count,
        "winning_trades": stats.winning_trades,
        "losing_trades": stats.losing_trades,
        "win_rate": (
            round(stats.winning_trades / stats.trade_count * 100, 1)
            if stats.trade_count else 0.0
        ),
    }
