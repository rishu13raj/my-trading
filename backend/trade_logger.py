"""
Async trade persistence layer.
Writes completed trade records and daily stats to the SQLite database.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import AsyncSessionLocal
from models import TradeRecord, DailyStats, Direction, TradeStatus

logger = logging.getLogger(__name__)


async def save_trade(trade_dict: dict) -> TradeRecord:
    """Persist a closed trade and update today's DailyStats row."""
    async with AsyncSessionLocal() as session:
        record = TradeRecord(
            symbol=trade_dict["symbol"],
            direction=Direction(trade_dict["direction"]),
            entry_price=trade_dict["entry_price"],
            exit_price=trade_dict.get("exit_price"),
            quantity=trade_dict["quantity"],
            entry_obi=trade_dict["entry_obi"],
            exit_obi=trade_dict.get("exit_obi"),
            pnl=trade_dict.get("pnl"),
            status=TradeStatus(trade_dict.get("status", "CLOSED")),
            entry_time=datetime.fromisoformat(trade_dict["entry_time"]),
            exit_time=datetime.fromisoformat(trade_dict["exit_time"]) if trade_dict.get("exit_time") else None,
            notes=trade_dict.get("notes"),
        )
        session.add(record)
        await session.flush()

        await _update_daily_stats(session, trade_dict.get("pnl", 0.0))
        await session.commit()
        await session.refresh(record)
        logger.info("Trade saved: id=%d pnl=%.2f", record.id, record.pnl or 0.0)
        return record


async def _update_daily_stats(session: AsyncSession, pnl: float) -> None:
    today = date.today().isoformat()
    result = await session.execute(
        select(DailyStats).where(DailyStats.date == today)
    )
    stats = result.scalar_one_or_none()
    if stats is None:
        stats = DailyStats(date=today, total_pnl=0.0, trade_count=0,
                           winning_trades=0, losing_trades=0)
        session.add(stats)
        await session.flush()

    stats.total_pnl = (stats.total_pnl or 0.0) + pnl
    stats.trade_count = (stats.trade_count or 0) + 1
    if pnl >= 0:
        stats.winning_trades = (stats.winning_trades or 0) + 1
    else:
        stats.losing_trades = (stats.losing_trades or 0) + 1


async def get_recent_trades(limit: int = 50) -> list[TradeRecord]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(TradeRecord).order_by(TradeRecord.id.desc()).limit(limit)
        )
        return list(result.scalars().all())


async def get_daily_stats(days: int = 30) -> list[DailyStats]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DailyStats).order_by(DailyStats.date.desc()).limit(days)
        )
        return list(result.scalars().all())


async def get_today_stats() -> DailyStats | None:
    today = date.today().isoformat()
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DailyStats).where(DailyStats.date == today)
        )
        return result.scalar_one_or_none()
