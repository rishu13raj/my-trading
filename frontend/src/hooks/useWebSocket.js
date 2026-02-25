/**
 * Connects to the backend WebSocket and dispatches messages by type.
 * Auto-reconnects with exponential backoff on disconnect.
 */

import { useEffect, useRef, useCallback } from 'react'

const WS_URL = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws`

export function useWebSocket(handlers) {
  const wsRef = useRef(null)
  const reconnectDelay = useRef(1000)
  const handlersRef = useRef(handlers)
  handlersRef.current = handlers

  const connect = useCallback(() => {
    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.onopen = () => {
      reconnectDelay.current = 1000
      handlersRef.current?.onConnected?.()
      // heartbeat
      const ping = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send('ping')
      }, 20_000)
      ws._pingInterval = ping
    }

    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data)
        if (msg === 'pong') return
        handlersRef.current?.[msg.type]?.(msg.payload)
        handlersRef.current?.onAny?.(msg)
      } catch {
        // ignore malformed
      }
    }

    ws.onclose = () => {
      clearInterval(ws._pingInterval)
      handlersRef.current?.onDisconnected?.()
      // reconnect with backoff
      setTimeout(connect, reconnectDelay.current)
      reconnectDelay.current = Math.min(reconnectDelay.current * 2, 30_000)
    }

    ws.onerror = () => ws.close()
  }, [])

  useEffect(() => {
    connect()
    return () => {
      wsRef.current?.close()
    }
  }, [connect])
}
