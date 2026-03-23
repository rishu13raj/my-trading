from typing import Dict, Optional
from data.database import db
from datetime import datetime
from config import config
from strategy.stop_loss import calculate_stop_loss

class PaperTrader:
    """Paper trading simulator"""

    def place_order(self, symbol: str, direction: str, quantity: int, entry_price: float, signal_meta: dict = None) -> Optional[int]:
        """
        Place a paper order (simulated)

        Args:
            symbol: Stock symbol
            direction: 'BUY' or 'SELL'
            quantity: Number of shares
            entry_price: Entry price

        Returns:
            Trade ID if successful, None otherwise
        """
        if not symbol or direction not in ['BUY', 'SELL'] or quantity <= 0 or entry_price <= 0:
            return None

        # Calculate stop loss
        stop_loss_price = calculate_stop_loss(entry_price, direction)

        # Insert trade into database
        trade_id = db.insert_trade(
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            entry_qty=quantity,
            stop_loss_price=stop_loss_price,
            signal_meta=signal_meta
        )

        print(f"✓ Paper {direction} order placed: {symbol} x{quantity} @ ₹{entry_price}")
        print(f"  Stop loss: ₹{stop_loss_price}")

        return trade_id

    def close_order(self, trade_id: int, exit_price: float, exit_reason: str) -> bool:
        """
        Close a paper order

        Args:
            trade_id: Trade ID to close
            exit_price: Exit price
            exit_reason: Reason for exit

        Returns:
            True if successful
        """
        trade = db.get_trade_by_id(trade_id)

        if not trade:
            return False

        # Calculate P&L
        if trade['direction'] == 'BUY':
            pnl = (exit_price - trade['entry_price']) * trade['entry_qty']
        else:  # SELL
            pnl = (trade['entry_price'] - exit_price) * trade['entry_qty']

        # Update trade in database
        db.update_trade_exit(trade_id, exit_price, exit_reason)

        pnl_sign = "+" if pnl >= 0 else ""
        print(f"✓ Paper order closed: {trade['symbol']} (Trade #{trade_id})")
        print(f"  Entry: ₹{trade['entry_price']} | Exit: ₹{exit_price} | P&L: {pnl_sign}₹{pnl}")
        print(f"  Reason: {exit_reason}")

        return True

    def get_trade_status(self, trade_id: int) -> Optional[Dict]:
        """
        Get current status of a trade

        Args:
            trade_id: Trade ID

        Returns:
            Trade dictionary or None
        """
        return db.get_trade_by_id(trade_id)

    def get_active_trades(self):
        """Get all active trades"""
        return db.get_active_trades()

# Global paper trader instance
paper_trader = PaperTrader()
