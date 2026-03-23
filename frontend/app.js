// Configuration
const API_URL = 'http://localhost:8000';
let stocks = [];
let monitoring = false;
let tradingPaused = false;
let ws = null;
let activeTab = 'overview';
let maxWatchedStocks = 10;  // updated from backend /status on load

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    console.log('App initialized');
    updateStatus();
    restoreStocks();
    startWebSocket();
    setInterval(updateStatus, 3000);
});

async function restoreStocks() {
    try {
        const response = await fetch(`${API_URL}/status`);
        const data = await response.json();
        if (data.selected_stocks && data.selected_stocks.length > 0 && stocks.length === 0) {
            stocks = data.selected_stocks;
            renderStocks(); // also calls renderTabs()
        }
    } catch (e) {}
}

// ============ API Calls ============

async function authenticate() {
    const btn = document.getElementById('authBtn');
    const status = document.getElementById('authStatus');

    try {
        btn.textContent = '⏳ Authenticating...';
        btn.disabled = true;

        const response = await fetch(`${API_URL}/auth/login-url`);
        const data = await response.json();

        if (data.login_url) {
            // Open Zerodha login in new window
            const popup = window.open(data.login_url, 'zerodha_auth', 'width=800,height=600');

            // Listen for auth success message from the callback page
            const onMessage = (event) => {
                if (event.data?.type === 'zerodha_auth_success') {
                    window.removeEventListener('message', onMessage);
                    showStatus('success', 'Authentication successful!');
                    btn.textContent = '✓ Authenticated';
                    btn.disabled = true;
                    document.getElementById('startBtn').disabled = false;
                    updateStatus();
                }
            };
            window.addEventListener('message', onMessage);

            // Fallback: clean up listener if popup is closed manually
            const checkClosed = setInterval(() => {
                if (popup?.closed) {
                    clearInterval(checkClosed);
                    window.removeEventListener('message', onMessage);
                    btn.textContent = '🔐 Authenticate Zerodha';
                    btn.disabled = false;
                    updateStatus();
                }
            }, 1000);
        } else {
            showStatus('error', data.error || 'Failed to get login URL');
        }
    } catch (error) {
        showStatus('error', `Error: ${error.message}`);
    } finally {
        btn.textContent = '🔐 Authenticate Zerodha';
        btn.disabled = false;
    }
}

let searchTimeout = null;
let activeIndex = -1;
let currentResults = [];

async function onStockInput() {
    const q = document.getElementById('stockInput').value.trim();
    activeIndex = -1;
    if (q.length < 2) { closeDropdown(); return; }

    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(async () => {
        try {
            const res = await fetch(`${API_URL}/instruments/search?q=${encodeURIComponent(q)}`);
            currentResults = await res.json();
            renderDropdown(currentResults);
        } catch (e) { closeDropdown(); }
    }, 250);
}

function renderDropdown(results) {
    const dd = document.getElementById('stockDropdown');
    if (!results.length) { closeDropdown(); return; }
    dd.innerHTML = results.map((r, i) => `
        <div class="autocomplete-item" data-symbol="${r.symbol}" onmousedown="selectStock('${r.symbol}')">
            <span class="symbol">${r.symbol}</span>
            <span class="name">${r.name}</span>
        </div>
    `).join('');
    dd.classList.add('open');
}

function closeDropdown() {
    const dd = document.getElementById('stockDropdown');
    dd.classList.remove('open');
    dd.innerHTML = '';
    activeIndex = -1;
}

function onStockKeydown(e) {
    const dd = document.getElementById('stockDropdown');
    const items = dd.querySelectorAll('.autocomplete-item');
    if (e.key === 'ArrowDown') {
        e.preventDefault();
        activeIndex = Math.min(activeIndex + 1, items.length - 1);
    } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        activeIndex = Math.max(activeIndex - 1, 0);
    } else if (e.key === 'Enter') {
        e.preventDefault();
        if (activeIndex >= 0 && currentResults[activeIndex]) {
            selectStock(currentResults[activeIndex].symbol);
        } else {
            addStock();
        }
        return;
    } else if (e.key === 'Escape') {
        closeDropdown(); return;
    }
    items.forEach((el, i) => el.classList.toggle('active', i === activeIndex));
}

function selectStock(symbol) {
    document.getElementById('stockInput').value = symbol;
    closeDropdown();
    addStock();
}

// Close dropdown when clicking outside
document.addEventListener('click', (e) => {
    if (!e.target.closest('.autocomplete-wrap')) closeDropdown();
});

async function addStock() {
    const input = document.getElementById('stockInput');
    const symbol = input.value.toUpperCase().trim();

    if (!symbol) {
        alert('Please enter a stock symbol');
        return;
    }

    if (stocks.includes(symbol)) {
        alert('Stock already added');
        return;
    }

    if (stocks.length >= maxWatchedStocks) {
        alert(`Maximum ${maxWatchedStocks} stocks allowed`);
        return;
    }

    stocks.push(symbol);
    input.value = '';
    closeDropdown();
    renderStocks();
    switchTab(symbol);

    // Update backend
    await updateStocksBackend();
}

function removeStock(symbol) {
    stocks = stocks.filter(s => s !== symbol);
    renderStocks();
    updateStocksBackend();
}

function renderStocks() {
    const container = document.getElementById('selectedStocks');
    if (stocks.length === 0) {
        container.innerHTML = '<div class="empty">No stocks selected</div>';
    } else {
        container.innerHTML = stocks.map(stock => `
            <div class="stock-tag" onclick="switchTab('${stock}')" style="cursor:pointer">
                ${stock}
                <span class="remove" onclick="event.stopPropagation();removeStock('${stock}')">×</span>
            </div>
        `).join('');
    }
    renderTabs();
}

function renderTabs() {
    const tabBar = document.getElementById('tabBar');
    const dashboard = document.querySelector('.dashboard-panel');

    // Remove tabs for stocks no longer selected
    tabBar.querySelectorAll('.tab-btn[data-symbol]').forEach(btn => {
        if (!stocks.includes(btn.dataset.symbol)) {
            btn.remove();
            const panel = document.getElementById('tab-' + btn.dataset.symbol);
            if (panel) panel.remove();
        }
    });

    // Add tabs for new stocks
    stocks.forEach(symbol => {
        if (!document.getElementById('tab-btn-' + symbol)) {
            const btn = document.createElement('button');
            btn.className = 'tab-btn';
            btn.id = 'tab-btn-' + symbol;
            btn.dataset.tab = symbol;
            btn.dataset.symbol = symbol;
            btn.textContent = symbol;
            btn.onclick = () => switchTab(symbol);
            tabBar.appendChild(btn);

            const panel = document.createElement('div');
            panel.className = 'tab-content hidden';
            panel.id = 'tab-' + symbol;
            panel.innerHTML = buildStockTabHTML(symbol);
            dashboard.appendChild(panel);
        }
    });
}

function buildStockTabHTML(symbol) {
    return `
        <div class="stock-price-bar">
            <span class="price-ltp" id="ltp-${symbol}">--</span>
            <span class="price-bid">Bid: <strong id="bid-${symbol}">-</strong></span>
            <span class="price-ask">Ask: <strong id="ask-${symbol}">-</strong></span>
        </div>
        <div class="section">
            <h3>Active Position</h3>
            <table class="table">
                <thead><tr><th>Direction</th><th>Entry</th><th>Current</th><th>P&L</th><th>Status</th></tr></thead>
                <tbody id="pos-${symbol}"><tr><td colspan="5" class="empty">No active position</td></tr></tbody>
            </table>
        </div>
        <div class="section">
            <h3>Trade History (Today)</h3>
            <div id="trades-${symbol}" class="trade-log"><div class="empty">No trades today</div></div>
        </div>
        <div class="section">
            <div class="log-header">
                <h3>Live Activity</h3>
                <button class="btn-refresh" onclick="refreshStockLog('${symbol}')">↻ Refresh</button>
            </div>
            <div id="log-${symbol}" class="activity-log"><div class="empty">No activity yet</div></div>
        </div>
    `;
}

function switchTab(tabId) {
    activeTab = tabId;
    document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
    document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
    const panel = document.getElementById('tab-' + tabId);
    if (panel) panel.classList.remove('hidden');
    const btn = document.getElementById(tabId === 'overview' ? null : 'tab-btn-' + tabId) ||
                document.querySelector(`[data-tab="${tabId}"]`);
    if (btn) btn.classList.add('active');
    if (tabId !== 'overview') fetchStockTrades(tabId);
}

async function fetchStockTrades(symbol) {
    try {
        const res = await fetch(`${API_URL}/stocks/${symbol}/trades`);
        const data = await res.json();
        const container = document.getElementById('trades-' + symbol);
        if (!container) return;
        if (!data.trades || data.trades.length === 0) {
            container.innerHTML = '<div class="empty">No trades today</div>';
            return;
        }
        container.innerHTML = data.trades.map(trade => {
            const pnl = trade.pnl || 0;
            const qty = trade.entry_qty || 0;
            const capital = trade.entry_price * qty;
            const roiPct = capital > 0 ? (pnl / capital * 100) : 0;
            const exitPrice = trade.exit_price?.toFixed(2) || 'open';
            const className = trade.exit_price ? (pnl >= 0 ? 'win' : 'loss') : 'open';
            return `
                <div class="trade-item ${className}">
                    <div>
                        <span class="trade-symbol">${trade.direction} ${qty} qty</span>
                        <span class="trade-pnl ${pnl >= 0 ? 'positive' : 'negative'}">${pnl >= 0 ? '+' : ''}₹${pnl.toFixed(2)} (${roiPct >= 0 ? '+' : ''}${roiPct.toFixed(3)}%)</span>
                    </div>
                    <div class="trade-reason">
                        @ ₹${trade.entry_price.toFixed(2)} → ₹${exitPrice} &nbsp;|&nbsp; Capital: ₹${capital.toFixed(0)}
                        <br><small>Exit: ${trade.exit_reason || 'open'}</small>
                    </div>
                </div>`;
        }).join('');
    } catch (e) {}
}

function updateStockPriceBars(ticks) {
    Object.entries(ticks).forEach(([symbol, tick]) => {
        const ltpEl = document.getElementById('ltp-' + symbol);
        const bidEl = document.getElementById('bid-' + symbol);
        const askEl = document.getElementById('ask-' + symbol);
        if (ltpEl) ltpEl.textContent = `₹${tick.ltp.toFixed(2)}`;
        if (bidEl) bidEl.textContent = tick.bid_qty;
        if (askEl) askEl.textContent = tick.ask_qty;
    });
}

function updateStockPositions(activeTrades) {
    stocks.forEach(symbol => {
        const tbody = document.getElementById('pos-' + symbol);
        if (!tbody) return;
        const trade = activeTrades.find(t => t.symbol === symbol);
        if (!trade) {
            tbody.innerHTML = '<tr><td colspan="5" class="empty">No active position</td></tr>';
            return;
        }
        const cur = trade.current_price || trade.entry_price;
        const pnl = trade.direction === 'BUY'
            ? (cur - trade.entry_price) * trade.entry_qty
            : (trade.entry_price - cur) * trade.entry_qty;
        tbody.innerHTML = `
            <tr>
                <td>${trade.direction}</td>
                <td>₹${trade.entry_price.toFixed(2)}</td>
                <td>₹${cur.toFixed(2)}</td>
                <td class="${pnl >= 0 ? 'positive' : 'negative'}">₹${pnl.toFixed(2)}</td>
                <td>${trade.exit_analysis?.should_exit ? 'Exit soon' : 'Holding'}</td>
            </tr>`;
    });
}

async function updateStocksBackend() {
    try {
        await fetch(`${API_URL}/stocks`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(stocks)
        });
    } catch (error) {
        console.error('Error updating stocks:', error);
    }
}

async function startMonitoring() {
    if (stocks.length === 0) {
        alert('Please select at least one stock');
        return;
    }

    // Re-sync stocks to backend in case it restarted
    await updateStocksBackend();

    try {
        const response = await fetch(`${API_URL}/start`, {
            method: 'POST'
        });
        const data = await response.json();

        if (response.ok) {
            monitoring = true;
            updateButtons();
            showStatus('success', 'Monitoring started');
        } else {
            showStatus('error', data.error || 'Failed to start monitoring');
        }
    } catch (error) {
        showStatus('error', `Error: ${error.message}`);
    }
}

async function stopMonitoring() {
    try {
        const response = await fetch(`${API_URL}/stop`, {
            method: 'POST'
        });

        if (response.ok) {
            monitoring = false;
            updateButtons();
            showStatus('success', 'Monitoring stopped');
        }
    } catch (error) {
        showStatus('error', `Error: ${error.message}`);
    }
}

function updateTradingPauseBtn() {
    const btn = document.getElementById('pauseTradingBtn');
    if (!btn) return;
    if (tradingPaused) {
        btn.textContent = '▶ Resume Trading Today';
        btn.className = 'btn btn-success';
    } else {
        btn.textContent = '⏸ Stop Trading Today';
        btn.className = 'btn btn-secondary';
    }
}

async function toggleTradingPause() {
    const endpoint = tradingPaused ? '/trading/resume' : '/trading/pause';
    const confirmMsg = tradingPaused
        ? 'Resume trading? New positions will be opened again.'
        : 'Stop trading for today? No new positions will be opened (existing positions still managed).';
    if (!confirm(confirmMsg)) return;
    try {
        const res = await fetch(`${API_URL}${endpoint}`, { method: 'POST' });
        const data = await res.json();
        tradingPaused = data.trading_paused;
        updateTradingPauseBtn();
        showStatus('success', tradingPaused ? 'Trading paused for today' : 'Trading resumed');
    } catch (e) {
        showStatus('error', `Error: ${e.message}`);
    }
}

async function exitTrade(tradeId) {
    if (!confirm('Exit this trade now?')) return;
    try {
        const res = await fetch(`${API_URL}/exit/${tradeId}`, { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
            showStatus('success', data.message);
            await updatePositions();
            await updateTrades();
        } else {
            showStatus('error', data.error || 'Failed to exit trade');
        }
    } catch (e) {
        showStatus('error', `Error: ${e.message}`);
    }
}

async function exitAllPositions() {
    if (!confirm('Are you sure you want to close all positions?')) {
        return;
    }

    try {
        const response = await fetch(`${API_URL}/exit-all`, {
            method: 'POST'
        });
        const data = await response.json();

        if (response.ok) {
            showStatus('success', data.message);
        } else {
            showStatus('error', data.error || 'Failed to close positions');
        }
    } catch (error) {
        showStatus('error', `Error: ${error.message}`);
    }
}

async function updateStatus() {
    try {
        const response = await fetch(`${API_URL}/status`);
        const data = response.json ? await response.json() : {};

        // Zerodha auth badge
        const badge = document.getElementById('statusBadge');
        if (data.zerodha_authenticated) {
            badge.textContent = 'Connected';
            badge.className = 'status-badge connected';
            document.getElementById('startBtn').disabled = false;
        } else {
            badge.textContent = 'Not Connected';
            badge.className = 'status-badge error';
        }

        // Zerodha ticker (KiteTicker) streaming badge — the one that actually matters
        const wb = document.getElementById('wsBadge');
        if (wb) {
            wb.style.display = '';
            if (data.ticker_connected) {
                wb.textContent = 'WebSocket Connected';
                wb.className = 'status-badge connected';
            } else {
                wb.textContent = 'WebSocket Not Connected';
                wb.className = 'status-badge error';
            }
        }

        // Sync config from backend — initialize sliders to real values
        if (data.max_watched_stocks) maxWatchedStocks = data.max_watched_stocks;
        if (data.config) syncConfigSliders(data.config);

        // Always sync monitoring state from backend — handles restarts and page reloads
        if (data.monitoring !== undefined) {
            monitoring = data.monitoring;
            updateButtons();
        }

        // Sync trading paused state
        if (data.trading_paused !== undefined) {
            tradingPaused = data.trading_paused;
            updateTradingPauseBtn();
        }

        // Update portfolio summary
        if (data.portfolio) {
            const p = data.portfolio;

            // Row 1
            document.getElementById('dailyPNL').textContent = `₹${p.today_pnl.toFixed(2)}`;
            document.getElementById('dailyPNLPct').textContent = `${p.today_pnl_pct > 0 ? '+' : ''}${p.today_pnl_pct}% on deployed`;
            document.getElementById('activeTrades').textContent = `${p.active_trades}/${p.max_trades}`;
            document.getElementById('tradesCount').textContent = `${p.today_closed_count} closed today`;

            const closed = p.today_closed_count || 0;
            const winRate = closed > 0
                ? ((p.today_win_count / closed) * 100).toFixed(1) + '%'
                : '-';
            document.getElementById('winRate').textContent = winRate;
            document.getElementById('winLoss').textContent = `${p.today_win_count}W / ${p.today_loss_count}L`;

            // Color P&L box
            const pnlEl = document.getElementById('dailyPNL');
            pnlEl.parentElement.classList.toggle('positive', p.today_pnl > 0);
            pnlEl.parentElement.classList.toggle('negative', p.today_pnl < 0);
        }

        // Fetch additional data
        await updatePositions();
        await updateTrades();

    } catch (error) {
        console.error('Error updating status:', error);
    }
}

async function updatePositions() {
    try {
        const response = await fetch(`${API_URL}/positions`);
        const data = await response.json();
        const tbody = document.getElementById('positionsTable');

        if (!data.positions || data.positions.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="empty">No active positions</td></tr>';
            return;
        }

        tbody.innerHTML = data.positions.map(pos => {
            const pnl = pos.current_price ? (
                pos.direction === 'BUY'
                    ? (pos.current_price - pos.entry_price) * pos.entry_qty
                    : (pos.entry_price - pos.current_price) * pos.entry_qty
            ) : 0;

            const pnlClass = pnl > 0 ? 'positive' : 'negative';
            const status = pos.exit_analysis?.should_exit ? '⚠️ Exit' : '✓ Hold';

            return `
                <tr>
                    <td><strong>${pos.symbol}</strong></td>
                    <td>${pos.direction}</td>
                    <td>₹${pos.entry_price.toFixed(2)}</td>
                    <td>₹${pos.current_price.toFixed(2)}</td>
                    <td class="${pnlClass}">₹${pnl.toFixed(2)}</td>
                    <td>${status}</td>
                    <td><button class="btn btn-danger btn-sm" onclick="exitTrade(${pos.id})">Exit</button></td>
                </tr>
            `;
        }).join('');

    } catch (error) {
        console.error('Error updating positions:', error);
    }
}

async function updateTrades() {
    try {
        const response = await fetch(`${API_URL}/trades`);
        const data = await response.json();
        const log = document.getElementById('tradeLog');

        if (!data.trades || data.trades.length === 0) {
            log.innerHTML = '<div class="empty">No trades today</div>';
            return;
        }

        log.innerHTML = data.trades.map(trade => {
            const pnl = trade.pnl || 0;
            const pnlClass = pnl > 0 ? 'positive' : 'negative';
            const className = trade.exit_price ? (pnl > 0 ? 'win' : 'loss') : 'open';
            const qty = trade.entry_qty || 0;
            const capital = trade.entry_price * qty;
            const roiPct = capital > 0 ? (pnl / capital * 100) : 0;
            const exitPrice = trade.exit_price?.toFixed(2) || 'open';

            return `
                <div class="trade-item ${className}">
                    <div>
                        <span class="trade-symbol">${trade.symbol} ${trade.direction}</span>
                        <span class="trade-pnl ${pnlClass}">${pnl >= 0 ? '+' : ''}₹${pnl.toFixed(2)} (${roiPct >= 0 ? '+' : ''}${roiPct.toFixed(3)}%)</span>
                    </div>
                    <div class="trade-reason">
                        ${qty} qty @ ₹${trade.entry_price.toFixed(2)} → ₹${exitPrice} &nbsp;|&nbsp; Capital: ₹${capital.toFixed(0)}
                        <br><small>Exit: ${trade.exit_reason || 'open'}</small>
                    </div>
                </div>
            `;
        }).join('');

    } catch (error) {
        console.error('Error updating trades:', error);
    }
}

function updateButtons() {
    const startBtn = document.getElementById('startBtn');
    const stopBtn = document.getElementById('stopBtn');
    const exitBtn = document.getElementById('exitBtn');

    if (monitoring) {
        startBtn.disabled = true;
        stopBtn.disabled = false;
        exitBtn.disabled = false;
    } else {
        startBtn.disabled = false;
        stopBtn.disabled = true;
        exitBtn.disabled = true;
    }
}

let _configSynced = false;
function syncConfigSliders(cfg) {
    if (_configSynced) return;  // only init once from backend, then user controls
    _configSynced = true;
    const cap = document.getElementById('capital');
    const sl  = document.getElementById('stopLoss');
    if (cap) { cap.value = cfg.capital_per_trade; document.getElementById('capitalDisplay').textContent = cfg.capital_per_trade; }
    if (sl)  { sl.value  = cfg.stop_loss_pct * 100; document.getElementById('slDisplay').textContent = Math.round(cfg.stop_loss_pct * 100); }
}

let _configTimer = null;
function updateCapital() {
    const value = parseInt(document.getElementById('capital').value);
    document.getElementById('capitalDisplay').textContent = value;
    clearTimeout(_configTimer);
    _configTimer = setTimeout(() => patchConfig({ capital_per_trade: value }), 600);
}

function updateStopLoss() {
    const value = parseInt(document.getElementById('stopLoss').value);
    document.getElementById('slDisplay').textContent = value;
    clearTimeout(_configTimer);
    _configTimer = setTimeout(() => patchConfig({ stop_loss_pct: value / 100 }), 600);
}

async function patchConfig(changes) {
    try {
        const res = await fetch(`${API_URL}/config`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(changes)
        });
        const data = await res.json();
        if (data.updated) showStatus('success', `Saved: ${JSON.stringify(data.updated)}`);
    } catch (e) {
        showStatus('error', 'Failed to save config');
    }
}

function showStatus(type, message) {
    const status = document.getElementById('authStatus');
    status.textContent = message;
    status.className = `status-text ${type}`;
    setTimeout(() => {
        status.className = 'status-text';
        status.textContent = '';
    }, 5000);
}

function startWebSocket() {
    try {
        ws = new WebSocket(`ws://localhost:8000/ws`);

        ws.onopen = () => {
            console.log('WebSocket connected');
        };

        ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            if (data.log && data.log.length > 0) appendActivityLog(data.log);
            if (data.ticks) updateStockPriceBars(data.ticks);
            if (data.active_trades) updateStockPositions(data.active_trades);
            if (activeTab !== 'overview' && data.active_trades) fetchStockTrades(activeTab);
        };

        ws.onerror = (error) => {
            console.error('WebSocket error:', error);
        };

        ws.onclose = () => {
            console.log('WebSocket disconnected');
            // Reconnect after 3 seconds
            setTimeout(startWebSocket, 3000);
        };

    } catch (error) {
        console.error('WebSocket error:', error);
    }
}


async function refreshStockLog(symbol) {
    try {
        const today = new Date().toISOString().split('T')[0];
        const res = await fetch(`${API_URL}/logs/${today}?symbol=${symbol}`);
        const data = await res.json();
        if (!data.lines) return;
        const el = document.getElementById('log-' + symbol);
        if (!el) return;
        el.innerHTML = '';
        [...data.lines].reverse().forEach(line => {
            const div = document.createElement('div');
            div.className = 'log-line info';
            div.innerHTML = `<span class="log-msg">${line.trim()}</span>`;
            el.appendChild(div);
        });
    } catch (e) {}
}

function appendActivityLog(entries) {
    // Overview: show last 10 entries across ALL stocks (zombie detector)
    const overviewEl = document.getElementById('overviewActivityLog');
    if (overviewEl && entries.length > 0) {
        if (overviewEl.querySelector('.empty')) overviewEl.innerHTML = '';
        const reversed = [...entries].reverse();
        reversed.forEach(e => {
            const line = document.createElement('div');
            line.className = `log-line ${e.level}`;
            line.innerHTML = `<span class="log-time">${e.time}</span><span class="log-msg">${e.symbol ? '['+e.symbol+'] ' : ''}${e.msg}</span>`;
            overviewEl.insertBefore(line, overviewEl.firstChild);
        });
        // Keep only last 10 in overview
        while (overviewEl.children.length > 10) overviewEl.removeChild(overviewEl.lastChild);
    }

    // Fan out to per-stock log tabs
    entries.forEach(e => {
        if (e.symbol) {
            const stockLogEl = document.getElementById('log-' + e.symbol);
            if (stockLogEl) appendToLog(stockLogEl, [e]);
        }
    });
}

const LOG_MAX_ENTRIES = 1500;   // hard cap per tab
const LOG_MAX_AGE_MS  = 10 * 60 * 1000;  // 10 minutes

function appendToLog(el, entries) {
    if (!el) return;
    if (el.querySelector('.empty')) el.innerHTML = '';
    const now = Date.now();
    // Prepend newest entries at top (reverse so latest appears first)
    const reversed = [...entries].reverse();
    reversed.forEach(e => {
        const line = document.createElement('div');
        line.className = `log-line ${e.level}`;
        line.dataset.ts = now;  // epoch ms when added — used for age-based pruning
        line.innerHTML = `<span class="log-time">${e.time}</span><span class="log-msg">${e.msg}</span>`;
        el.insertBefore(line, el.firstChild);
    });
    // Hard cap safety net
    while (el.children.length > LOG_MAX_ENTRIES) el.removeChild(el.lastChild);
}

// Purge log entries older than 10 minutes from all stock tab log panels
function purgeOldLogs() {
    const cutoff = Date.now() - LOG_MAX_AGE_MS;
    document.querySelectorAll('[id^="log-"]').forEach(el => {
        Array.from(el.children).forEach(child => {
            if (child.dataset.ts && Number(child.dataset.ts) < cutoff) child.remove();
        });
        if (el.children.length === 0) {
            el.innerHTML = '<div class="empty">No activity yet</div>';
        }
    });
}
setInterval(purgeOldLogs, 60_000);  // run every minute

// Cleanup on page unload
window.addEventListener('beforeunload', () => {
    if (ws) ws.close();
});
