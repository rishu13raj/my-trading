/**
 * Session configuration panel: basket + risk parameters.
 */

import { Plus, Trash2 } from 'lucide-react'
import clsx from 'clsx'

export default function ConfigPanel({
  config,
  updateBasket,
  addBasketItem,
  removeBasketItem,
  updateConfig,
  onConfigure,
  loading,
  disabled,
}) {
  return (
    <div className="card space-y-4">
      <div className="label">Session Configuration</div>

      {/* Basket */}
      <div className="space-y-2">
        <div className="text-xs text-muted mb-1">Stock Basket (1–5 symbols)</div>
        {config.basket.map((item, idx) => (
          <div key={idx} className="flex gap-2">
            <select
              className="bg-surface border border-border rounded-lg px-2 py-1.5 text-sm w-20"
              value={item.exchange}
              onChange={e => updateBasket(idx, 'exchange', e.target.value)}
              disabled={disabled}
            >
              <option>NSE</option>
              <option>BSE</option>
            </select>
            <input
              type="text"
              placeholder="e.g. INFY"
              className="flex-1 bg-surface border border-border rounded-lg px-3 py-1.5 text-sm uppercase placeholder-muted"
              value={item.symbol}
              onChange={e => updateBasket(idx, 'symbol', e.target.value.toUpperCase())}
              disabled={disabled}
            />
            {config.basket.length > 1 && (
              <button
                className="btn btn-danger px-2"
                onClick={() => removeBasketItem(idx)}
                disabled={disabled}
              >
                <Trash2 size={14} />
              </button>
            )}
          </div>
        ))}
        {config.basket.length < 5 && (
          <button
            className="btn btn-primary flex items-center gap-1 text-xs"
            onClick={addBasketItem}
            disabled={disabled}
          >
            <Plus size={12} /> Add stock
          </button>
        )}
      </div>

      {/* Risk parameters */}
      <div className="grid grid-cols-2 gap-3">
        <NumberField
          label="Capital per trade (₹)"
          value={config.capital_per_trade}
          onChange={v => updateConfig('capital_per_trade', v)}
          disabled={disabled}
          min={1000}
        />
        <NumberField
          label="Per-trade stop-loss (₹)"
          value={config.per_trade_stop_loss}
          onChange={v => updateConfig('per_trade_stop_loss', v)}
          disabled={disabled}
          min={100}
        />
        <NumberField
          label="Daily stop-loss (₹)"
          value={config.daily_stop_loss}
          onChange={v => updateConfig('daily_stop_loss', v)}
          disabled={disabled}
          min={100}
        />
        <NumberField
          label="OBI threshold (0–1)"
          value={config.obi_threshold}
          onChange={v => updateConfig('obi_threshold', parseFloat(v))}
          disabled={disabled}
          min={0.05}
          max={0.95}
          step={0.05}
        />
        <NumberField
          label="Observation window (s)"
          value={config.observation_seconds}
          onChange={v => updateConfig('observation_seconds', v)}
          disabled={disabled}
          min={10}
          max={300}
        />
      </div>

      <button
        className="btn btn-primary w-full"
        onClick={onConfigure}
        disabled={disabled || loading}
      >
        {loading ? 'Applying…' : 'Apply Configuration & Connect'}
      </button>
    </div>
  )
}

function NumberField({ label, value, onChange, disabled, min, max, step = 1 }) {
  return (
    <div>
      <div className="label mb-1">{label}</div>
      <input
        type="number"
        className="w-full bg-surface border border-border rounded-lg px-3 py-1.5 text-sm"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={e => onChange(parseFloat(e.target.value))}
        disabled={disabled}
      />
    </div>
  )
}
