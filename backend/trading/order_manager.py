from typing import Dict, Optional, List
from trading.paper_trader import paper_trader
from trading.portfolio import portfolio
from strategy.signal import should_place_order
from strategy.exit_logic import should_exit, get_exit_analysis
from data.database import db

class OrderManager:
    """Manages order placement and execution"""

    def __init__(self):
        self.pending_entries = {}  # Track pending entry monitors per symbol

    def process_signal(self, symbol: str, signal: Dict, current_price: float) -> Optional[int]:
        """
        Process trading signal and place order if applicable

        Args:
            symbol: Stock symbol
            signal: Signal dict from strategy
            current_price: Current market price

        Returns:
            Trade ID if order placed, None otherwise
        """
        if not should_place_order(signal):
            return None

        if not portfolio.can_open_trade():
            print(f"⚠️  Cannot open new trade for {symbol}: portfolio full")
            return None

        # Calculate quantity
        quantity = portfolio.calculate_quantity(current_price)

        # Place order
        direction = signal['action']
        trade_id = paper_trader.place_order(
            symbol=symbol,
            direction=direction,
            quantity=quantity,
            entry_price=current_price
        )

        return trade_id

    def check_exits(self, tick_data: Dict) -> List[Dict]:
        """
        Check if any active positions should be exited

        Args:
            tick_data: Current tick data {symbol, ltp, bid_qty, ask_qty, ...}

        Returns:
            List of exit execution results
        """
        symbol = tick_data.get('symbol')
        current_price = tick_data.get('ltp', 0)

        if not symbol or current_price <= 0:
            return []

        results = []

        # Get active positions
        active_trades = db.get_active_trades()

        for trade in active_trades:
            if trade['symbol'] != symbol:
                continue

            # Get recent ticks for this symbol
            tick_history = db.get_recent_ticks(symbol, limit=100)

            # Check if should exit
            should_exit_flag, reason = should_exit(trade, tick_history, current_price)

            if should_exit_flag:
                # Close the trade
                success = paper_trader.close_order(
                    trade_id=trade['id'],
                    exit_price=current_price,
                    exit_reason=reason
                )

                results.append({
                    'trade_id': trade['id'],
                    'symbol': symbol,
                    'action': 'CLOSED',
                    'reason': reason,
                    'success': success
                })

        return results

    def get_portfolio_status(self) -> Dict:
        """Get current portfolio status"""
        return portfolio.get_portfolio_summary()

    def get_active_trades(self) -> List[Dict]:
        """Get active trades with analysis"""
        trades = db.get_active_trades()
        results = []

        for trade in trades:
            tick_history = db.get_recent_ticks(trade['symbol'], limit=100)
            current_price = tick_history[0].get('ltp') if tick_history else trade['entry_price']

            exit_analysis = get_exit_analysis(trade, tick_history, current_price)

            results.append({
                'trade_id': trade['id'],
                'symbol': trade['symbol'],
                'direction': trade['direction'],
                'entry_price': trade['entry_price'],
                'current_price': current_price,
                'entry_qty': trade['entry_qty'],
                'entry_time': str(trade['entry_time']),
                'stop_loss_price': trade['stop_loss_price'],
                'exit_analysis': exit_analysis
            })

        return results

    def close_all_positions(self, current_prices: Dict[str, float]) -> List[Dict]:
        """
        Close all active positions immediately

        Args:
            current_prices: Dict of {symbol: price}

        Returns:
            List of close results
        """
        results = []
        active_trades = db.get_active_trades()

        for trade in active_trades:
            price = current_prices.get(trade['symbol'], trade['entry_price'])
            success = paper_trader.close_order(
                trade_id=trade['id'],
                exit_price=price,
                exit_reason='MANUAL_EXIT_ALL'
            )
            results.append({
                'trade_id': trade['id'],
                'symbol': trade['symbol'],
                'success': success
            })

        return results

# Global order manager instance
order_manager = OrderManager()
