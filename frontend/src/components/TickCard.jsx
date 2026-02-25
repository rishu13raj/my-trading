/**
 * Real-time market data card for a single symbol.
 */

import clsx from 'clsx'
import ObiGauge from './ObiGauge'
import ObiChart from './ObiChart'

export default function TickCard({ symbol, tick, history, threshold, position }) {
  if (!tick) {
    return (
      <div className="card space-y-3">
        <div className="flex justify-between">
          <span className="font-bold">{symbol}</span>
          <span className="badge bg-border text-muted">No data</span>
        </div>
      </div>
    )
  }

  const hasPosition = !!position
  const isLong = position === 'LONG'

  return (
    <div className={clsx('card space-y-3', hasPosition && 'ring-1', isLong ? 'ring-profit' : hasPosition ? 'ring-loss' : '')}>
      {/* Header */}
      <div className="flex justify-between items-center">
        <div className="flex items-center gap-2">
          <span className="font-bold text-base">{symbol}</span>
          {hasPosition && (
            <span className={clsx('badge', isLong ? 'bg-profit/20 text-profit' : 'bg-loss/20 text-loss')}>
              {position}
            </span>
          )}
        </div>
        <span className="value-lg">₹{tick.ltp?.toFixed(2)}</span>
      </div>

      {/* Bid/Ask spread */}
      <div className="grid grid-cols-2 gap-2 text-sm">
        <div className="bg-green-900/20 rounded-lg p-2">
          <div className="label">Bid</div>
          <div className="text-profit font-semibold">
            ₹{tick.bid_price?.toFixed(2)}
            <span className="text-muted ml-1 text-xs">× {tick.bid_qty?.toLocaleString()}</span>
          </div>
          <div className="text-xs text-muted mt-1">
            Total buyers: {tick.total_buy_qty?.toLocaleString()}
          </div>
        </div>
        <div className="bg-red-900/20 rounded-lg p-2">
          <div className="label">Ask</div>
          <div className="text-loss font-semibold">
            ₹{tick.ask_price?.toFixed(2)}
            <span className="text-muted ml-1 text-xs">× {tick.ask_qty?.toLocaleString()}</span>
          </div>
          <div className="text-xs text-muted mt-1">
            Total sellers: {tick.total_sell_qty?.toLocaleString()}
          </div>
        </div>
      </div>

      {/* Volume */}
      <div className="flex justify-between text-xs text-muted">
        <span>Volume: {tick.volume?.toLocaleString()}</span>
        <span className="text-xs text-muted">
          {new Date(tick.timestamp).toLocaleTimeString()}
        </span>
      </div>

      {/* OBI Gauge */}
      <ObiGauge
        symbol={symbol}
        obi={tick.obi}
        obiRate={tick.obi_rate}
        threshold={threshold}
      />

      {/* OBI History Chart */}
      <ObiChart data={history} threshold={threshold} />
    </div>
  )
}
