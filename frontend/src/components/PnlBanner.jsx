/**
 * Top banner showing daily P&L, trade count, and strategy state.
 */

import clsx from 'clsx'

const STATE_COLORS = {
  IDLE:      'bg-border text-muted',
  OBSERVING: 'bg-warn/20 text-warn',
  TRADING:   'bg-accent/20 text-accent',
  HALTED:    'bg-loss/20 text-loss',
}

const STATE_LABELS = {
  IDLE:      '● Idle',
  OBSERVING: '◎ Observing',
  TRADING:   '▶ Trading',
  HALTED:    '⛔ Halted',
}

export default function PnlBanner({ dailyPnl, dailyTrades, strategyState, target = 1000 }) {
  const pct = Math.min(100, Math.max(0, (dailyPnl / target) * 100))
  const positive = dailyPnl >= 0

  return (
    <div className="card space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <div className="label">Daily P&amp;L</div>
          <div className={clsx('value-lg', positive ? 'text-profit' : 'text-loss')}>
            {positive ? '+' : ''}₹{dailyPnl.toFixed(2)}
          </div>
        </div>

        <div className="text-center">
          <div className="label">Trades today</div>
          <div className="value-lg text-white">{dailyTrades}</div>
        </div>

        <div className="text-right">
          <div className="label">Strategy</div>
          <span className={clsx('badge text-sm px-3 py-1', STATE_COLORS[strategyState] ?? 'bg-border text-muted')}>
            {STATE_LABELS[strategyState] ?? strategyState}
          </span>
        </div>
      </div>

      {/* Progress toward daily target */}
      <div>
        <div className="flex justify-between text-xs text-muted mb-1">
          <span>Daily target progress</span>
          <span>₹{dailyPnl.toFixed(0)} / ₹{target.toLocaleString()}</span>
        </div>
        <div className="h-2 bg-border rounded-full overflow-hidden">
          <div
            className={clsx('h-full rounded-full transition-all', positive ? 'bg-profit' : 'bg-loss')}
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>
    </div>
  )
}
