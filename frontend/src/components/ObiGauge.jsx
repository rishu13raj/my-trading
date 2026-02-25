/**
 * Visual OBI (Order Book Imbalance) gauge.
 * Shows a horizontal bar from -1 (full sell) to +1 (full buy),
 * with the current OBI value highlighted.
 */

import clsx from 'clsx'

export default function ObiGauge({ symbol, obi = 0, obiRate = 0, threshold = 0.3 }) {
  const pct = ((obi + 1) / 2) * 100   // map -1..+1 → 0..100%
  const abs = Math.abs(obi)
  const strong = abs >= threshold

  const color = obi > 0
    ? (strong ? 'bg-profit' : 'bg-green-800')
    : (strong ? 'bg-loss'   : 'bg-red-900')

  const rateSymbol = obiRate > 0.001 ? '▲' : obiRate < -0.001 ? '▼' : '—'
  const rateColor  = obiRate > 0.001 ? 'text-profit' : obiRate < -0.001 ? 'text-loss' : 'text-muted'

  return (
    <div className="card space-y-2">
      <div className="flex justify-between items-center">
        <span className="text-sm font-bold">{symbol}</span>
        <span className={clsx('value-sm', obi > 0 ? 'text-profit' : 'text-loss')}>
          OBI {obi >= 0 ? '+' : ''}{obi.toFixed(3)}
        </span>
        <span className={clsx('text-xs font-semibold', rateColor)}>
          {rateSymbol} rate
        </span>
      </div>

      {/* Track */}
      <div className="relative h-4 bg-border rounded-full overflow-hidden">
        {/* Threshold zones */}
        <div
          className="absolute top-0 bottom-0 bg-green-900/30"
          style={{ left: `${((threshold + 1) / 2) * 100}%`, right: 0 }}
        />
        <div
          className="absolute top-0 bottom-0 bg-red-900/30"
          style={{ left: 0, right: `${(1 - ((-threshold + 1) / 2)) * 100}%` }}
        />

        {/* OBI bar */}
        {obi >= 0 ? (
          <div
            className={clsx('absolute top-0 bottom-0 transition-all', color)}
            style={{ left: '50%', width: `${(obi / 2) * 100}%` }}
          />
        ) : (
          <div
            className={clsx('absolute top-0 bottom-0 transition-all', color)}
            style={{ right: '50%', width: `${(-obi / 2) * 100}%` }}
          />
        )}

        {/* Centre line */}
        <div className="absolute top-0 bottom-0 w-px bg-muted left-1/2" />
      </div>

      <div className="flex justify-between text-xs text-muted">
        <span>Sell -1</span>
        <span className={clsx('font-semibold', strong ? 'text-white' : 'text-muted')}>
          {strong ? (obi > 0 ? '✓ BUY ZONE' : '✓ SELL ZONE') : 'below threshold'}
        </span>
        <span>Buy +1</span>
      </div>
    </div>
  )
}
