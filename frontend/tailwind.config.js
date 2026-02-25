/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        surface: '#0f172a',
        panel:   '#1e293b',
        border:  '#334155',
        muted:   '#94a3b8',
        profit:  '#22c55e',
        loss:    '#ef4444',
        warn:    '#f59e0b',
        accent:  '#6366f1',
      },
    },
  },
  plugins: [],
}
