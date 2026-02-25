/**
 * Central state hook for the entire trading dashboard.
 * Manages WebSocket updates, API calls, and derived state.
 */

import { useState, useCallback, useRef } from 'react'
import { useWebSocket } from './useWebSocket'
import * as api from '../lib/api'

const INITIAL_CONFIG = {
  basket: [{ symbol: '', exchange: 'NSE' }],
  capital_per_trade: 10000,
  per_trade_stop_loss: 400,
  daily_stop_loss: 1000,
  obi_threshold: 0.30,
  observation_seconds: 60,
}

export function useTrader() {
  const [connected, setConnected]         = useState(false)
  const [authenticated, setAuthenticated] = useState(false)
  const [strategyState, setStrategyState] = useState('IDLE')
  const [dailyPnl, setDailyPnl]           = useState(0)
  const [dailyTrades, setDailyTrades]     = useState(0)
  const [positions, setPositions]         = useState({})
  const [ticks, setTicks]                 = useState({})     // symbol → TickData
  const [obiHistory, setObiHistory]       = useState({})     // symbol → [{t, obi}]
  const [trades, setTrades]               = useState([])     // recent closed trades
  const [signals, setSignals]             = useState([])     // recent signals
  const [config, setConfig]               = useState(INITIAL_CONFIG)
  const [error, setError]                 = useState(null)
  const [loading, setLoading]             = useState(false)
  const [accessTokenInput, setAccessTokenInput] = useState('')

  const MAX_HISTORY = 120   // keep last 120 OBI data-points per symbol

  // ── WebSocket handlers ───────────────────────────────────────────────────

  useWebSocket({
    onConnected:    () => setConnected(true),
    onDisconnected: () => setConnected(false),

    tick: (payload) => {
      setTicks(prev => ({ ...prev, [payload.symbol]: payload }))
      setObiHistory(prev => {
        const arr = prev[payload.symbol] ?? []
        const next = [...arr, { t: payload.timestamp, obi: payload.obi }]
        return { ...prev, [payload.symbol]: next.slice(-MAX_HISTORY) }
      })
    },

    strategy: (payload) => {
      setStrategyState(payload.state)
      setDailyPnl(payload.daily_pnl)
      setDailyTrades(payload.daily_trades)
      setPositions(payload.open_positions ?? {})
    },

    trade_closed: (payload) => {
      setTrades(prev => [payload, ...prev].slice(0, 100))
    },

    signal: (payload) => {
      setSignals(prev => [payload, ...prev].slice(0, 50))
    },
  })

  // ── API actions ───────────────────────────────────────────────────────────

  const withLoading = useCallback(async (fn) => {
    setLoading(true)
    setError(null)
    try {
      const result = await fn()
      return result
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  const authenticate = useCallback(() => withLoading(async () => {
    await api.setToken(accessTokenInput.trim())
    setAuthenticated(true)
  }), [accessTokenInput, withLoading])

  const configure = useCallback(() => withLoading(async () => {
    const filteredBasket = config.basket.filter(b => b.symbol.trim())
    if (!filteredBasket.length) throw new Error('Add at least one stock to the basket.')
    await api.configureSession({ ...config, basket: filteredBasket })
    // Reload recent trades after config
    const recent = await api.getRecentTrades(50)
    setTrades(recent)
  }), [config, withLoading])

  const startMonitoring = useCallback(() => withLoading(async () => {
    await api.startMonitoring()
  }), [withLoading])

  const stopMonitoring = useCallback(() => withLoading(async () => {
    await api.stopMonitoring()
  }), [withLoading])

  const resetSession = useCallback(() => withLoading(async () => {
    await api.resetSession()
    setDailyPnl(0)
    setDailyTrades(0)
    setPositions({})
    setTrades([])
    setSignals([])
    setTicks({})
    setObiHistory({})
    setStrategyState('IDLE')
  }), [withLoading])

  const checkAuth = useCallback(() => withLoading(async () => {
    const { authenticated: a } = await api.getAuthStatus()
    setAuthenticated(a)
  }), [withLoading])

  // ── Config helpers ────────────────────────────────────────────────────────

  const updateBasket = useCallback((idx, field, value) => {
    setConfig(prev => {
      const basket = [...prev.basket]
      basket[idx] = { ...basket[idx], [field]: value }
      return { ...prev, basket }
    })
  }, [])

  const addBasketItem = useCallback(() => {
    setConfig(prev => ({
      ...prev,
      basket: [...prev.basket, { symbol: '', exchange: 'NSE' }],
    }))
  }, [])

  const removeBasketItem = useCallback((idx) => {
    setConfig(prev => ({
      ...prev,
      basket: prev.basket.filter((_, i) => i !== idx),
    }))
  }, [])

  const updateConfig = useCallback((key, value) => {
    setConfig(prev => ({ ...prev, [key]: value }))
  }, [])

  return {
    // state
    connected, authenticated, strategyState,
    dailyPnl, dailyTrades, positions,
    ticks, obiHistory, trades, signals,
    config, error, loading, accessTokenInput,
    // actions
    setAccessTokenInput,
    authenticate, configure,
    startMonitoring, stopMonitoring, resetSession,
    checkAuth,
    // config helpers
    updateBasket, addBasketItem, removeBasketItem, updateConfig,
  }
}
