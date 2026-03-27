// Configuration
const API_URL = 'http://localhost:8000';

function calcBrokerage(tradeValue, openOnly) {
    // Actual Zerodha intraday charges (verified from zerodha.com/charges)
    const brokerage = Math.min(0.0003 * tradeValue, 20) * (openOnly ? 1 : 2);
    const stt = openOnly ? 0 : 0.00025 * tradeValue;          // 0.025% sell side only (on close)
    const nse = 0.0000307 * tradeValue * (openOnly ? 1 : 2);   // 0.00307% both sides
    const stamp = 0.00003 * tradeValue;                         // 0.003% buy side only
    const gst = 0.18 * (brokerage + nse);
    return brokerage + stt + nse + stamp + gst;
}
let stocks = [];
let pausedStocks = new Set();
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
        }
        if (data.paused_stocks) {
            pausedStocks = new Set(data.paused_stocks);
        }
        renderStocks();
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

async function runScan() {
    const btn = document.getElementById('scanBtn');
    const panel = document.getElementById('scanResults');
    btn.disabled = true;
    btn.textContent = '⏳ Scanning...';
    panel.style.display = 'none';

    try {
        const res = await fetch(`${API_URL}/scan`);
        const data = await res.json();
        if (data.error) { alert(data.error); return; }
        renderScanResults(data.results, data.scanned);
    } catch (e) {
        alert('Scan failed: ' + e.message);
    } finally {
        btn.disabled = false;
        btn.textContent = '📡 Scan';
    }
}

function renderScanResults(results, scanned) {
    const panel = document.getElementById('scanResults');
    const activeTrades = Array.from(document.querySelectorAll('.stock-tag'))
        .map(t => t.textContent.trim().replace('×', '').trim());

    const rows = results.map((r, i) => {
        const alreadyAdded = stocks.includes(r.symbol);
        const barWidth = Math.round(r.imbalance * 100);
        const biasColor = r.bias === 'BUY' ? '#22c55e' : '#ef4444';
        return `
        <div class="scan-row">
            <span class="scan-rank">${i + 1}</span>
            <span class="scan-symbol">${r.symbol}</span>
            <span class="scan-ratio">${r.ratio}x</span>
            <div class="scan-bar"><div class="scan-bar-fill" style="width:${barWidth}%;background:${biasColor}"></div></div>
            <span class="scan-bias" style="color:${biasColor}">${r.bias}</span>
            <button class="scan-add-btn" onclick="addFromScan('${r.symbol}')" ${alreadyAdded ? 'disabled' : ''}>
                ${alreadyAdded ? '✓' : '+'}
            </button>
        </div>`;
    }).join('');

    const newSymbols = results.map(r => r.symbol).filter(s => !stocks.includes(s));
    panel.innerHTML = `
        <div class="scan-header">
            <span>📡 Top ${results.length} by imbalance — ${scanned} scanned</span>
            <button class="scan-add-all-btn" onclick="addAllFromScan(${JSON.stringify(newSymbols)})" ${newSymbols.length === 0 ? 'disabled' : ''}>
                + Add All
            </button>
        </div>
        ${rows}`;
    panel.style.display = 'block';
}

function addFromScan(symbol) {
    if (stocks.includes(symbol)) return;
    if (stocks.length >= maxWatchedStocks) { alert(`Max ${maxWatchedStocks} stocks`); return; }
    stocks.push(symbol);
    renderStocks();
    updateStocksBackend();
    // Refresh scan panel to update button states
    const panel = document.getElementById('scanResults');
    if (panel.style.display !== 'none') {
        panel.querySelectorAll('.scan-add-btn').forEach(btn => {
            const sym = btn.closest('.scan-row').querySelector('.scan-symbol').textContent;
            if (sym === symbol) { btn.disabled = true; btn.textContent = '✓'; }
        });
        const newSymbols = Array.from(panel.querySelectorAll('.scan-add-btn'))
            .filter(b => !b.disabled)
            .map(b => b.closest('.scan-row').querySelector('.scan-symbol').textContent);
        const addAllBtn = panel.querySelector('.scan-add-all-btn');
        if (addAllBtn) { addAllBtn.disabled = newSymbols.length === 0; addAllBtn.onclick = () => addAllFromScan(newSymbols); }
    }
}

function addAllFromScan(symbols) {
    symbols.forEach(sym => {
        if (!stocks.includes(sym) && stocks.length < maxWatchedStocks) stocks.push(sym);
    });
    renderStocks();
    updateStocksBackend();
    // Re-render scan panel with updated states
    const panel = document.getElementById('scanResults');
    if (panel.style.display !== 'none') {
        panel.querySelectorAll('.scan-add-btn').forEach(btn => { btn.disabled = true; btn.textContent = '✓'; });
        const addAllBtn = panel.querySelector('.scan-add-all-btn');
        if (addAllBtn) addAllBtn.disabled = true;
    }
}

function renderStocks() {
    const container = document.getElementById('selectedStocks');
    if (stocks.length === 0) {
        container.innerHTML = '<div class="empty">No stocks added</div>';
        renderTabs();
        return;
    }

    // Get symbols with open trades from the positions table
    const openTradeSymbols = new Set(
        Array.from(document.querySelectorAll('#positionsTable tr'))
            .map(r => r.querySelector('td')?.textContent?.trim())
            .filter(Boolean)
    );

    container.innerHTML = stocks.map(symbol => {
        const paused = pausedStocks.has(symbol);
        const hasOpenTrade = openTradeSymbols.has(symbol);
        let badge = '';
        if (paused)         badge = '<span class="stock-open-badge stopped">Stopped</span>';
        else if (hasOpenTrade) badge = '<span class="stock-open-badge">OPEN</span>';
        return `
        <div class="stock-row">
            <span class="stock-row-name" onclick="switchTab('${symbol}')">${symbol}</span>
            ${badge}
            <button class="stock-toggle ${paused ? 'paused' : 'active'}" onclick="togglePause('${symbol}')" title="${paused ? 'Resume monitoring' : 'Pause new entries'}">
                ${paused ? '⏸ Paused' : '● Active'}
            </button>
            <button class="stock-remove" onclick="removeStock('${symbol}')" ${hasOpenTrade ? 'disabled title="Close trade first"' : 'title="Remove"'}>×</button>
        </div>`;
    }).join('');

    renderTabs();
}

async function togglePause(symbol) {
    const isPaused = pausedStocks.has(symbol);
    const endpoint = isPaused ? 'resume' : 'pause';
    try {
        await fetch(`${API_URL}/stocks/${symbol}/${endpoint}`, { method: 'POST' });
        if (isPaused) pausedStocks.delete(symbol);
        else pausedStocks.add(symbol);
        renderStocks();
    } catch (e) {
        showStatus('error', `Failed to ${endpoint} ${symbol}`);
    }
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
            <h3>Latest Activity</h3>
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
        const entryVal = trade.entry_price * trade.entry_qty;
        const pnlPct = entryVal > 0 ? (pnl / entryVal * 100) : 0;
        tbody.innerHTML = `
            <tr>
                <td>${trade.direction}</td>
                <td>₹${trade.entry_price.toFixed(2)}</td>
                <td>₹${cur.toFixed(2)}</td>
                <td class="${pnl >= 0 ? 'positive' : 'negative'}">₹${pnl.toFixed(2)} <span style="font-size:0.85em">(${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%)</span></td>
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

async function reconnectWebSocket() {
    try {
        const res = await fetch(`${API_URL}/ws/reconnect`, { method: 'POST' });
        const data = await res.json();
        if (res.ok) showStatus('success', data.message);
        else showStatus('error', data.error || 'Reconnect failed');
    } catch (e) {
        showStatus('error', `Error: ${e.message}`);
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
    try {
        const res = await fetch(`${API_URL}/exit/${tradeId}`, { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
            showStatus('success', data.message);
            await updateStatus();
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

        // Zerodha ticker (KiteTicker) streaming badge — clickable to reconnect
        const wb = document.getElementById('wsBadge');
        if (wb) {
            wb.style.display = '';
            if (data.ticker_connected) {
                wb.textContent = 'WebSocket Connected';
                wb.className = 'status-badge connected';
                wb.title = 'Connected';
            } else {
                wb.textContent = '⟳ WebSocket Not Connected';
                wb.className = 'status-badge error';
                wb.title = 'Click to reconnect';
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

        // Sync paused stocks
        if (data.paused_stocks) {
            pausedStocks = new Set(data.paused_stocks);
            renderStocks();
        }

        // Update portfolio summary
        if (data.portfolio) {
            const p = data.portfolio;

            // Row 1
            document.getElementById('dailyPNL').textContent = `₹${p.today_pnl.toFixed(2)}`;
            document.getElementById('dailyPNLPct').textContent = `${p.today_pnl_pct > 0 ? '+' : ''}${p.today_pnl_pct}% on deployed`;
            const closed = p.today_closed_count || 0;
            const active = p.active_trades || 0;
            const capital = (data.config?.capital_per_trade) || 100000;
            const brokerage = Math.round(closed * calcBrokerage(capital, false) + active * calcBrokerage(capital, true));
            document.getElementById('brokerageCost').textContent = `-₹${brokerage}`;
            document.getElementById('brokerageDetail').textContent = `${closed} closed · ${active} open`;
            document.getElementById('activeTrades').textContent = `${p.active_trades}/${p.max_trades}`;
            document.getElementById('tradesCount').textContent = `${closed} closed today`;

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

            const entryValue = pos.entry_price * pos.entry_qty;
            const pnlPct = entryValue > 0 ? (pnl / entryValue * 100) : 0;
            const pnlClass = pnl > 0 ? 'positive' : 'negative';
            const status = pos.exit_analysis?.should_exit ? '⚠️ Exit' : '✓ Hold';

            return `
                <tr>
                    <td><strong>${pos.symbol}</strong></td>
                    <td>${pos.direction}</td>
                    <td>₹${pos.entry_price.toFixed(2)}</td>
                    <td>₹${pos.current_price.toFixed(2)}</td>
                    <td class="${pnlClass}">₹${pnl.toFixed(2)} <span style="font-size:0.85em">(${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%)</span></td>
                    <td>${status}</td>
                    <td><button class="btn btn-danger btn-sm" onclick="exitTrade(${pos.trade_id})">Exit</button></td>
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
            const isOpen = !trade.exit_price;
            const pnl = trade.pnl || 0;
            const pnlClass = pnl > 0 ? 'positive' : 'negative';
            const className = isOpen ? 'open' : (pnl > 0 ? 'win' : 'loss');
            const qty = trade.entry_qty || 0;
            const capital = trade.entry_price * qty;
            const roiPct = capital > 0 ? (pnl / capital * 100) : 0;
            const exitPrice = trade.exit_price?.toFixed(2) || 'open';
            const pnlDisplay = isOpen ? '— / —' : `${pnl >= 0 ? '+' : ''}₹${pnl.toFixed(2)} (${roiPct >= 0 ? '+' : ''}${roiPct.toFixed(3)}%)`;
            const entryTime = trade.entry_time
                ? new Date(trade.entry_time).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false })
                : '';

            return `
                <div class="trade-item ${className}" style="display:flex;gap:12px;align-items:flex-start">
                    <div style="min-width:52px;text-align:center;padding-top:2px">
                        <span style="display:inline-block;font-weight:800;font-size:13px;color:#2563eb;letter-spacing:1px;border:2px solid #2563eb;border-radius:6px;padding:3px 6px;line-height:1">${entryTime}</span>
                    </div>
                    <div style="flex:1">
                        <div>
                            <span class="trade-symbol">${trade.symbol} ${trade.direction}</span>
                            <span class="trade-pnl ${isOpen ? '' : pnlClass}">${pnlDisplay}</span>
                        </div>
                        <div class="trade-reason">
                            ${qty} qty @ ₹${trade.entry_price.toFixed(2)} → ₹${exitPrice} &nbsp;|&nbsp; Capital: ₹${capital.toFixed(0)}
                            <br><small>Exit: ${trade.exit_reason || 'open'}</small>
                        </div>
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

    if (monitoring) {
        startBtn.disabled = true;
        stopBtn.disabled = false;
    } else {
        startBtn.disabled = false;
        stopBtn.disabled = true;
    }
}

let _configSynced = false;
let _serverConfig = {};  // last known backend values

function syncConfigSliders(cfg) {
    if (_configSynced) return;  // only init once from backend, then user controls
    _configSynced = true;
    _serverConfig = { capital_per_trade: cfg.capital_per_trade, stop_loss_pct: cfg.stop_loss_pct, max_active_trades: cfg.max_active_trades };
    const cap = document.getElementById('capital');
    const sl  = document.getElementById('stopLoss');
    const mt  = document.getElementById('maxTrades');
    if (cap) { cap.value = cfg.capital_per_trade; document.getElementById('capitalDisplay').textContent = cfg.capital_per_trade; }
    if (sl)  { sl.value  = cfg.stop_loss_pct * 100; document.getElementById('slDisplay').textContent = (cfg.stop_loss_pct * 100).toFixed(1); }
    if (mt)  { mt.value  = cfg.max_active_trades; document.getElementById('maxTradesDisplay').textContent = cfg.max_active_trades; }
}

function onSettingChange() {
    const capVal = parseInt(document.getElementById('capital').value);
    const slVal  = parseFloat(document.getElementById('stopLoss').value);
    const mtVal  = parseInt(document.getElementById('maxTrades').value);
    document.getElementById('capitalDisplay').textContent = capVal;
    document.getElementById('slDisplay').textContent = slVal.toFixed(1);
    document.getElementById('maxTradesDisplay').textContent = mtVal;

    const dirty = capVal !== _serverConfig.capital_per_trade ||
                  slVal  !== parseFloat((_serverConfig.stop_loss_pct * 100).toFixed(1)) ||
                  mtVal  !== _serverConfig.max_active_trades;
    document.getElementById('pushBtn').disabled = !dirty;
    document.getElementById('resetBtn').disabled = !dirty;
}

async function pushSettings() {
    const capVal = parseInt(document.getElementById('capital').value);
    const slVal  = parseFloat(document.getElementById('stopLoss').value);
    const mtVal  = parseInt(document.getElementById('maxTrades').value);
    await patchConfig({ capital_per_trade: capVal, stop_loss_pct: slVal / 100, max_active_trades: mtVal });
    _serverConfig = { capital_per_trade: capVal, stop_loss_pct: slVal / 100, max_active_trades: mtVal };
    document.getElementById('pushBtn').disabled = true;
    document.getElementById('resetBtn').disabled = true;
}

function resetSettings() {
    const cap = document.getElementById('capital');
    const sl  = document.getElementById('stopLoss');
    const mt  = document.getElementById('maxTrades');
    cap.value = _serverConfig.capital_per_trade;
    sl.value  = (_serverConfig.stop_loss_pct * 100).toFixed(1);
    mt.value  = _serverConfig.max_active_trades;
    document.getElementById('capitalDisplay').textContent = _serverConfig.capital_per_trade;
    document.getElementById('slDisplay').textContent = (_serverConfig.stop_loss_pct * 100).toFixed(1);
    document.getElementById('maxTradesDisplay').textContent = _serverConfig.max_active_trades;
    document.getElementById('pushBtn').disabled = true;
    document.getElementById('resetBtn').disabled = true;
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



function appendActivityLog(entries) {
    if (!entries.length) return;

    // Overview: show the single latest entry across all stocks
    const overviewEl = document.getElementById('overviewActivityLog');
    if (overviewEl) {
        const latest = entries[entries.length - 1];
        overviewEl.innerHTML = `<div class="log-line ${latest.level}"><span class="log-time">${latest.time}</span><span class="log-msg">${latest.symbol ? '['+latest.symbol+'] ' : ''}${latest.msg}</span></div>`;
    }

    // Per-stock tabs: show the single latest entry for that stock
    // Find the latest entry per symbol from this batch
    const latestPerSymbol = {};
    entries.forEach(e => {
        if (e.symbol) latestPerSymbol[e.symbol] = e;
    });
    Object.entries(latestPerSymbol).forEach(([symbol, e]) => {
        const el = document.getElementById('log-' + symbol);
        if (el) el.innerHTML = `<div class="log-line ${e.level}"><span class="log-time">${e.time}</span><span class="log-msg">${e.msg}</span></div>`;
    });
}

// Cleanup on page unload
window.addEventListener('beforeunload', () => {
    if (ws) ws.close();
});
