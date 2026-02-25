/**
 * Authentication panel — shown until the user provides a Zerodha access token.
 */

import { useState } from 'react'
import { ExternalLink } from 'lucide-react'
import * as api from '../lib/api'

export default function AuthPanel({ accessTokenInput, setAccessTokenInput, onAuthenticate, loading, error }) {
  const [loginUrl, setLoginUrl] = useState(null)

  const fetchLoginUrl = async () => {
    try {
      const { login_url } = await api.getLoginUrl()
      setLoginUrl(login_url)
    } catch (e) {
      // ignore
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <div className="card max-w-md w-full space-y-6">
        <div>
          <h1 className="text-xl font-bold">Momentum Trader</h1>
          <p className="text-muted text-sm mt-1">Connect your Zerodha account to begin</p>
        </div>

        {/* Step 1: Get login URL */}
        <div className="space-y-2">
          <div className="label">Step 1 — Zerodha Login</div>
          <button
            className="btn btn-primary flex items-center gap-2"
            onClick={fetchLoginUrl}
          >
            <ExternalLink size={14} />
            Get Zerodha Login URL
          </button>
          {loginUrl && (
            <a
              href={loginUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="block text-accent text-sm underline break-all"
            >
              {loginUrl}
            </a>
          )}
        </div>

        {/* Step 2: Paste access token */}
        <div className="space-y-2">
          <div className="label">Step 2 — Enter Access Token</div>
          <p className="text-xs text-muted">
            After logging in, Zerodha will redirect to a URL containing{' '}
            <code className="text-accent">request_token=…</code>. Exchange it via{' '}
            <code className="text-accent">GET /auth/callback?request_token=…</code> to get your
            access token, then paste it here. Or use the{' '}
            <code className="text-accent">/auth/callback</code> endpoint directly.
          </p>
          <input
            type="password"
            placeholder="Paste access token…"
            className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-sm font-mono"
            value={accessTokenInput}
            onChange={e => setAccessTokenInput(e.target.value)}
          />
          <button
            className="btn btn-success w-full"
            onClick={onAuthenticate}
            disabled={!accessTokenInput.trim() || loading}
          >
            {loading ? 'Authenticating…' : 'Connect'}
          </button>
        </div>

        {error && (
          <div className="bg-loss/10 border border-loss rounded-lg px-3 py-2 text-loss text-sm">
            {error}
          </div>
        )}
      </div>
    </div>
  )
}
