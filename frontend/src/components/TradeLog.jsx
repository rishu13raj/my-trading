/**
 * Table of recently closed trades.
 */

import clsx from 'clsx'

export default function TradeLog({ trades = [] }) {
  if (!trades.length) {
    return (
      <div className="card">
        <div className="label mb-2">Recent Trades</div>
        <div className="text-center text-muted text-sm py-8">No trades yet.</div>
      </div>
    )
  }

  return (
    <div className="card overflow-hidden">
      <div className="label mb-3">Recent Trades</div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-muted text-xs border-b border-border">
              <th className="text-left pb-2 pr-3">Symbol</th>
              <th className="text-left pb-2 pr-3">Dir</th>
              <th className="text-right pb-2 pr-3">Entry</th>
              <th className="text-right pb-2 pr-3">Exit</th>
              <th className="text-right pb-2 pr-3">Qty</th>
              <th className="text-right pb-2 pr-3">P&amp;L</th>
              <th className="text-left pb-2">Note</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((t, i) => {
              const pnl = t.pnl ?? 0
              return (
                <tr key={t.id ?? i} className="border-b border-border/50 hover:bg-border/20">
                  <td className="py-2 pr-3 font-semibold">{t.symbol}</td>
                  <td className="py-2 pr-3">
                    <span className={clsx('badge', t.direction === 'LONG' ? 'bg-profit/20 text-profit' : 'bg-loss/20 text-loss')}>
                      {t.direction}
                    </span>
                  </td>
                  <td className="py-2 pr-3 text-right">₹{t.entry_price?.toFixed(2)}</td>
                  <td className="py-2 pr-3 text-right">₹{t.exit_price?.toFixed(2) ?? '—'}</td>
                  <td className="py-2 pr-3 text-right">{t.quantity}</td>
                  <td className={clsx('py-2 pr-3 text-right font-bold', pnl >= 0 ? 'text-profit' : 'text-loss')}>
                    {pnl >= 0 ? '+' : ''}₹{pnl.toFixed(2)}
                  </td>
                  <td className="py-2 text-muted text-xs truncate max-w-[180px]">{t.notes ?? t.status}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
