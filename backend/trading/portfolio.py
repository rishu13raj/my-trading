from typing import List, Dict, Optional
from config import config
from data.database import db
import math

class Portfolio:
    def __init__(self):
        self.max_active_trades = config.MAX_ACTIVE_TRADES
        self.capital_per_trade = config.CAPITAL_PER_TRADE
        self.daily_capital = config.DAILY_CAPITAL

    def can_open_trade(self) -> bool:
        """
        Check if a new trade can be opened

        Returns:
            True if number of active trades < max_active_trades
        """
        active_trades = db.get_active_trades()
        return len(active_trades) < self.max_active_trades

    def get_active_position_count(self) -> int:
        """Get count of active trades"""
        return len(db.get_active_trades())

    def calculate_quantity(self, price: float) -> int:
        """
        Calculate order quantity based on capital allocation

        Args:
            price: Current price of the asset

        Returns:
            Number of shares/units to buy
        """
        if price <= 0:
            return 0

        # Calculate quantity based on capital per trade
        quantity = math.floor(self.capital_per_trade / price)
        return max(quantity, 1)  # Ensure at least 1 unit

    def get_active_positions(self) -> List[Dict]:
        """
        Get all active positions

        Returns:
            List of active trade dictionaries
        """
        return db.get_active_trades()

    def get_daily_pnl(self) -> float:
        """
        Get total P&L for the day

        Returns:
            Daily P&L amount
        """
        return db.get_today_pnl()

    def get_daily_trades(self) -> List[Dict]:
        """
        Get all trades from today

        Returns:
            List of today's trades
        """
        return db.get_today_trades()

    def get_portfolio_summary(self) -> Dict:
        """
        Get portfolio summary

        Returns:
            Dictionary with portfolio statistics
        """
        active_trades = self.get_active_positions()
        today_trades = self.get_daily_trades()
        daily_pnl = self.get_daily_pnl()

        # Calculate active position value
        total_entry_value = sum(
            t.get('entry_price', 0) * t.get('entry_qty', 0)
            for t in active_trades
        )

        closed_trades = [t for t in today_trades if t.get('exit_price') is not None]
        pnls = [t.get('pnl') or 0 for t in closed_trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        # Total capital deployed = sum of (entry_price * qty) for closed trades
        total_deployed = sum(t.get('entry_price', 0) * t.get('entry_qty', 0) for t in closed_trades)
        pnl_pct = round((daily_pnl / total_deployed * 100), 3) if total_deployed > 0 else 0

        # Average hold time in minutes
        from datetime import datetime
        hold_times = []
        for t in closed_trades:
            try:
                entry_dt = datetime.fromisoformat(str(t['entry_time']))
                exit_dt  = datetime.fromisoformat(str(t['exit_time']))
                hold_times.append((exit_dt - entry_dt).total_seconds() / 60)
            except Exception:
                pass
        avg_hold_min = round(sum(hold_times) / len(hold_times), 1) if hold_times else 0

        return {
            'active_trades': len(active_trades),
            'max_trades': self.max_active_trades,
            'can_open_new': self.can_open_trade(),
            'capital_per_trade': self.capital_per_trade,
            'daily_capital': self.daily_capital,
            'today_pnl': round(daily_pnl, 2),
            'today_pnl_pct': pnl_pct,
            'total_entry_value': round(total_entry_value, 2),
            'total_deployed_today': round(total_deployed, 2),
            'today_trades_count': len(today_trades),
            'today_closed_count': len(closed_trades),
            'today_win_count': len(wins),
            'today_loss_count': len(losses),
            'avg_pnl_per_trade': round(sum(pnls) / len(pnls), 2) if pnls else 0,
            'best_trade': round(max(pnls), 2) if pnls else 0,
            'worst_trade': round(min(pnls), 2) if pnls else 0,
            'avg_hold_minutes': avg_hold_min,
        }

# Global portfolio instance
portfolio = Portfolio()
