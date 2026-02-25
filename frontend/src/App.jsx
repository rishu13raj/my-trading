/**
 * Root application component.
 */

import { useEffect } from 'react'
import clsx from 'clsx'
import { Wifi, WifiOff } from 'lucide-react'

import { useTrader } from './hooks/useTrader'
import AuthPanel   from './components/AuthPanel'
import PnlBanner   from './components/PnlBanner'
import ConfigPanel from './components/ConfigPanel'
import ControlBar  from './components/ControlBar'
import TickCard    from './components/TickCard'
import TradeLog    from './components/TradeLog'

export default function App() {
  const trader = useTrader()

  // Check authentication on mount
  useEffect(() => {
    trader.checkAuth()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  if (!trader.authenticated) {
    return (
      <AuthPanel
        accessTokenInput={trader.accessTokenInput}
        setAccessTokenInput={trader.setAccessTokenInput}
        onAuthenticate={trader.authenticate}
        loading={trader.loading}
        error={trader.error}
      />
    )
  }

  const symbols = trader.config.basket.map(b => b.symbol).filter(Boolean)

  return (
    <div className="min-h-screen p-4 space-y-4 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-bold tracking-tight">⚡ Momentum Trader</h1>
        <div className="flex items-center gap-2 text-xs text-muted">
          {trader.connected
            ? <><Wifi size={14} className="text-profit" /> Live</>
            : <><WifiOff size={14} className="text-loss" /> Reconnecting…</>
          }
        </div>
      </div>

      {/* Error banner */}
      {trader.error && (
        <div className="bg-loss/10 border border-loss rounded-xl px-4 py-2 text-loss text-sm">
          ⚠ {trader.error}
        </div>
      )}

      {/* Daily P&L + Strategy State */}
      <PnlBanner
        dailyPnl={trader.dailyPnl}
        dailyTrades={trader.dailyTrades}
        strategyState={trader.strategyState}
        target={trader.config.daily_stop_loss}
      />

      {/* Main grid: Config left, Controls + Ticks right */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Left column: config */}
        <div className="lg:col-span-1">
          <ConfigPanel
            config={trader.config}
            updateBasket={trader.updateBasket}
            addBasketItem={trader.addBasketItem}
            removeBasketItem={trader.removeBasketItem}
            updateConfig={trader.updateConfig}
            onConfigure={trader.configure}
            loading={trader.loading}
            disabled={trader.strategyState === 'TRADING'}
          />
        </div>

        {/* Right columns: controls + live ticks */}
        <div className="lg:col-span-2 space-y-4">
          <ControlBar
            strategyState={trader.strategyState}
            onStart={trader.startMonitoring}
            onStop={trader.stopMonitoring}
            onReset={trader.resetSession}
            loading={trader.loading}
            authenticated={trader.authenticated}
          />

          {/* Live tick cards */}
          {symbols.length > 0 ? (
            <div className={clsx(
              'grid gap-4',
              symbols.length === 1 ? 'grid-cols-1' : 'grid-cols-1 md:grid-cols-2'
            )}>
              {symbols.map(sym => (
                <TickCard
                  key={sym}
                  symbol={sym}
                  tick={trader.ticks[sym]}
                  history={trader.obiHistory[sym] ?? []}
                  threshold={trader.config.obi_threshold}
                  position={trader.positions[sym] ?? null}
                />
              ))}
            </div>
          ) : (
            <div className="card text-center text-muted text-sm py-12">
              Add stocks to your basket and press <em>Apply Configuration</em> to start.
            </div>
          )}
        </div>
      </div>

      {/* Trade log */}
      <TradeLog trades={trader.trades} />
    </div>
  )
}
