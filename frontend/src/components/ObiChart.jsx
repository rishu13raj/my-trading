/**
 * Sparkline chart of OBI over time for one symbol.
 */

import {
  ResponsiveContainer,
  LineChart,
  Line,
  ReferenceLine,
  Tooltip,
  YAxis,
} from 'recharts'

export default function ObiChart({ data = [], threshold = 0.3 }) {
  if (data.length < 2) {
    return (
      <div className="flex items-center justify-center h-16 text-muted text-xs">
        Waiting for data…
      </div>
    )
  }

  return (
    <ResponsiveContainer width="100%" height={80}>
      <LineChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
        <YAxis domain={[-1, 1]} hide />
        <Tooltip
          contentStyle={{ background: '#1e293b', border: '1px solid #334155', fontSize: 11 }}
          formatter={(v) => [v.toFixed(3), 'OBI']}
          labelFormatter={() => ''}
        />
        <ReferenceLine y={threshold}  stroke="#22c55e" strokeDasharray="3 3" strokeOpacity={0.5} />
        <ReferenceLine y={0}          stroke="#94a3b8" strokeDasharray="2 2" strokeOpacity={0.4} />
        <ReferenceLine y={-threshold} stroke="#ef4444" strokeDasharray="3 3" strokeOpacity={0.5} />
        <Line
          type="monotone"
          dataKey="obi"
          stroke="#6366f1"
          dot={false}
          strokeWidth={2}
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
  )
}
