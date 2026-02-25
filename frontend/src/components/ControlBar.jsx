/**
 * Start / Stop / Reset control bar.
 */

import clsx from 'clsx'
import { Play, Square, RotateCcw } from 'lucide-react'

export default function ControlBar({
  strategyState,
  onStart,
  onStop,
  onReset,
  loading,
  authenticated,
}) {
  const isObserving = strategyState === 'OBSERVING'
  const isTrading   = strategyState === 'TRADING'
  const isHalted    = strategyState === 'HALTED'
  const isActive    = isObserving || isTrading
  const canStart    = authenticated && !isActive && !isHalted

  return (
    <div className="card flex flex-wrap items-center gap-3">
      <button
        className="btn btn-success flex items-center gap-2 flex-1"
        onClick={onStart}
        disabled={!canStart || loading}
      >
        <Play size={14} />
        Start Monitoring
      </button>

      <button
        className="btn btn-danger flex items-center gap-2 flex-1"
        onClick={onStop}
        disabled={!isActive || loading}
      >
        <Square size={14} />
        Stop
      </button>

      <button
        className="btn btn-warn flex items-center gap-2"
        onClick={onReset}
        disabled={loading}
        title="Reset daily counters — use at start of each session"
      >
        <RotateCcw size={14} />
        Reset Day
      </button>

      {isHalted && (
        <div className="w-full text-center text-loss text-xs font-semibold animate-pulse">
          ⛔ Daily stop-loss triggered — press Reset Day to re-enable trading
        </div>
      )}
    </div>
  )
}
