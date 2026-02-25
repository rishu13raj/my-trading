/**
 * Thin wrapper around the backend REST API.
 * All functions throw on HTTP errors.
 */

const BASE = '/api'

async function request(method, path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

// ── Auth ──────────────────────────────────────────────────────────────────────
export const getLoginUrl   = () => request('GET',  '/auth/login-url')
export const setToken      = (token) => request('POST', '/auth/token', { access_token: token })
export const getAuthStatus = () => request('GET',  '/auth/status')

// ── Trading ───────────────────────────────────────────────────────────────────
export const configureSession = (config) => request('POST', '/trading/configure', config)
export const startMonitoring  = () => request('POST', '/trading/start')
export const stopMonitoring   = () => request('POST', '/trading/stop')
export const resetSession     = () => request('POST', '/trading/reset')
export const getTradingStatus = () => request('GET',  '/trading/status')
export const getPositions     = () => request('GET',  '/trading/positions')

// ── History ───────────────────────────────────────────────────────────────────
export const getRecentTrades = (limit = 50) => request('GET', `/history/trades?limit=${limit}`)
export const getDailyStats   = (days = 30)  => request('GET', `/history/daily?days=${days}`)
export const getTodaySummary = ()           => request('GET', '/history/today')
