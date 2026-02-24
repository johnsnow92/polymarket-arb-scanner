"""Dashboard HTML template — single-page trading dashboard.

The HTML is served by dashboard.py at GET /. It fetches data from the
JSON API endpoints every REFRESH_SECONDS and renders charts + tables.
"""


def get_dashboard_html(refresh_seconds: int = 15) -> str:
    """Return the complete dashboard HTML as a string.

    Args:
        refresh_seconds: Auto-refresh interval for data polling.

    Returns:
        Full HTML document string with embedded CSS and JS.
    """
    return _TEMPLATE.replace("__REFRESH_SECONDS__", str(refresh_seconds))


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Polymarket Arb Scanner</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
/* ---------------------------------------------------------------------------
   Reset & base
   --------------------------------------------------------------------------- */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg: #0f1117;
  --surface: #1a1d27;
  --surface2: #232735;
  --border: #2d3148;
  --text: #e4e6f0;
  --text-muted: #8b8fa3;
  --accent: #6366f1;
  --accent-dim: #4f46e5;
  --green: #22c55e;
  --green-dim: rgba(34,197,94,0.15);
  --red: #ef4444;
  --red-dim: rgba(239,68,68,0.15);
  --yellow: #eab308;
  --yellow-dim: rgba(234,179,8,0.15);
  --blue: #3b82f6;
  --blue-dim: rgba(59,130,246,0.15);
  --font: 'Segoe UI', system-ui, -apple-system, sans-serif;
  --mono: 'Cascadia Code', 'Fira Code', 'Consolas', monospace;
  --radius: 8px;
}
html { font-size: 14px; }
body {
  font-family: var(--font);
  background: var(--bg);
  color: var(--text);
  line-height: 1.5;
  min-height: 100vh;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

/* ---------------------------------------------------------------------------
   Layout
   --------------------------------------------------------------------------- */
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 24px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  z-index: 100;
}
.header h1 { font-size: 1.15rem; font-weight: 600; letter-spacing: -0.01em; }
.header-right { display: flex; align-items: center; gap: 16px; font-size: 0.85rem; }
.badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 10px;
  border-radius: 12px;
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.badge-live { background: var(--green-dim); color: var(--green); }
.badge-dry { background: var(--yellow-dim); color: var(--yellow); }
.badge-error { background: var(--red-dim); color: var(--red); }
.dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
.dot-green { background: var(--green); }
.dot-yellow { background: var(--yellow); }
.dot-red { background: var(--red); }
.dot-pulse { animation: pulse 2s ease-in-out infinite; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }

.main { padding: 20px 24px; max-width: 1440px; margin: 0 auto; }

/* ---------------------------------------------------------------------------
   Cards grid
   --------------------------------------------------------------------------- */
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 14px;
  margin-bottom: 20px;
}
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px 18px;
}
.card-label { font-size: 0.78rem; color: var(--text-muted); margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.05em; }
.card-value { font-size: 1.65rem; font-weight: 700; font-family: var(--mono); }
.card-sub { font-size: 0.78rem; color: var(--text-muted); margin-top: 4px; }
.positive { color: var(--green); }
.negative { color: var(--red); }

/* ---------------------------------------------------------------------------
   Sections
   --------------------------------------------------------------------------- */
.section {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  margin-bottom: 20px;
  overflow: hidden;
}
.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 18px;
  border-bottom: 1px solid var(--border);
  font-weight: 600;
  font-size: 0.92rem;
}
.section-body { padding: 16px 18px; }
.section-body.no-pad { padding: 0; }

/* ---------------------------------------------------------------------------
   Tables
   --------------------------------------------------------------------------- */
.tbl { width: 100%; border-collapse: collapse; font-size: 0.84rem; }
.tbl th {
  text-align: left;
  padding: 10px 14px;
  color: var(--text-muted);
  font-weight: 500;
  font-size: 0.76rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
.tbl td {
  padding: 9px 14px;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
.tbl tr:last-child td { border-bottom: none; }
.tbl tr:hover td { background: var(--surface2); }
.tbl .mono { font-family: var(--mono); font-size: 0.82rem; }
.tbl .right { text-align: right; }

.status-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 0.72rem;
  font-weight: 600;
  text-transform: uppercase;
}
.status-filled { background: var(--green-dim); color: var(--green); }
.status-pending { background: var(--yellow-dim); color: var(--yellow); }
.status-failed { background: var(--red-dim); color: var(--red); }
.status-open { background: var(--blue-dim); color: var(--blue); }
.status-dry_run { background: var(--yellow-dim); color: var(--yellow); }

/* ---------------------------------------------------------------------------
   Two-column layout
   --------------------------------------------------------------------------- */
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
@media (max-width: 900px) { .grid-2 { grid-template-columns: 1fr; } }

/* ---------------------------------------------------------------------------
   Chart
   --------------------------------------------------------------------------- */
.chart-container { position: relative; height: 220px; width: 100%; }

/* ---------------------------------------------------------------------------
   Alerts
   --------------------------------------------------------------------------- */
.alert-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 0;
  border-bottom: 1px solid var(--border);
  font-size: 0.84rem;
}
.alert-item:last-child { border-bottom: none; }
.alert-sev {
  display: inline-block;
  padding: 2px 7px;
  border-radius: 4px;
  font-size: 0.7rem;
  font-weight: 700;
  text-transform: uppercase;
  flex-shrink: 0;
}
.sev-INFO { background: var(--blue-dim); color: var(--blue); }
.sev-WARNING { background: var(--yellow-dim); color: var(--yellow); }
.sev-CRITICAL { background: var(--red-dim); color: var(--red); }
.alert-time { color: var(--text-muted); font-size: 0.76rem; flex-shrink: 0; min-width: 70px; }

/* ---------------------------------------------------------------------------
   Empty states
   --------------------------------------------------------------------------- */
.empty { color: var(--text-muted); text-align: center; padding: 32px 16px; font-size: 0.88rem; }

/* ---------------------------------------------------------------------------
   Spinner
   --------------------------------------------------------------------------- */
.spinner { display: inline-block; width: 16px; height: 16px; border: 2px solid var(--border); border-top-color: var(--accent); border-radius: 50%; animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* Footer */
.footer { text-align: center; padding: 16px; color: var(--text-muted); font-size: 0.76rem; }
</style>
</head>
<body>

<!-- ====================================================================== -->
<!-- Header                                                                  -->
<!-- ====================================================================== -->
<div class="header">
  <h1>Polymarket Arb Scanner</h1>
  <div class="header-right">
    <span id="mode-badge" class="badge badge-dry"><span class="dot dot-yellow dot-pulse"></span> DRY RUN</span>
    <span id="uptime" style="color:var(--text-muted)"></span>
    <span id="last-scan" style="color:var(--text-muted)"></span>
    <span class="spinner" id="refresh-spinner" style="display:none"></span>
  </div>
</div>

<!-- ====================================================================== -->
<!-- Main content                                                            -->
<!-- ====================================================================== -->
<div class="main">

  <!-- KPI Cards -->
  <div class="cards" id="kpi-cards">
    <div class="card">
      <div class="card-label">Daily P&L</div>
      <div class="card-value" id="kpi-daily-pnl">$0.00</div>
      <div class="card-sub" id="kpi-cumulative-pnl">Cumulative: $0.00</div>
    </div>
    <div class="card">
      <div class="card-label">Open Positions</div>
      <div class="card-value" id="kpi-positions">0</div>
      <div class="card-sub" id="kpi-positions-sub">&nbsp;</div>
    </div>
    <div class="card">
      <div class="card-label">Opportunities Found</div>
      <div class="card-value" id="kpi-opps">0</div>
      <div class="card-sub" id="kpi-scans">Scans: 0</div>
    </div>
    <div class="card">
      <div class="card-label">Trades Executed</div>
      <div class="card-value" id="kpi-trades">0</div>
      <div class="card-sub" id="kpi-trades-failed">Failed: 0</div>
    </div>
    <div class="card">
      <div class="card-label">Avg Slippage</div>
      <div class="card-value" id="kpi-slippage">--</div>
      <div class="card-sub" id="kpi-scan-latency">Scan latency: --</div>
    </div>
    <div class="card">
      <div class="card-label">WebSocket</div>
      <div class="card-value" id="kpi-ws">--</div>
      <div class="card-sub" id="kpi-ws-msgs">Messages: 0</div>
    </div>
  </div>

  <!-- P&L Chart + Strategy Breakdown -->
  <div class="grid-2">
    <div class="section">
      <div class="section-header">P&L History (30 days)</div>
      <div class="section-body">
        <div class="chart-container"><canvas id="pnl-chart"></canvas></div>
      </div>
    </div>
    <div class="section">
      <div class="section-header">Strategy Breakdown</div>
      <div class="section-body no-pad">
        <table class="tbl" id="strategy-table">
          <thead><tr><th>Strategy</th><th class="right">Count</th><th class="right">Avg ROI</th><th class="right">Avg Profit</th><th class="right">Total Profit</th></tr></thead>
          <tbody id="strategy-tbody"></tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- Open Positions -->
  <div class="section">
    <div class="section-header">
      <span>Open Positions</span>
      <span id="positions-count" style="color:var(--text-muted);font-size:0.82rem"></span>
    </div>
    <div class="section-body no-pad">
      <table class="tbl" id="positions-table">
        <thead><tr><th>Market</th><th>Platform</th><th class="right">Expected P&L</th><th>Entry Time</th><th>Status</th></tr></thead>
        <tbody id="positions-tbody"></tbody>
      </table>
    </div>
  </div>

  <!-- Positions by Platform chart + Recent Trades -->
  <div class="grid-2">
    <div class="section">
      <div class="section-header">Positions by Platform</div>
      <div class="section-body">
        <div class="chart-container"><canvas id="platform-chart"></canvas></div>
      </div>
    </div>
    <div class="section">
      <div class="section-header">
        <span>Recent Trades</span>
        <span id="trades-count" style="color:var(--text-muted);font-size:0.82rem"></span>
      </div>
      <div class="section-body no-pad" style="max-height:320px;overflow-y:auto">
        <table class="tbl" id="trades-table">
          <thead><tr><th>Time</th><th>Platform</th><th>Side</th><th class="right">Price</th><th class="right">Fill</th><th class="right">Slippage</th><th>Status</th></tr></thead>
          <tbody id="trades-tbody"></tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- Opportunity Feed -->
  <div class="section">
    <div class="section-header">
      <span>Recent Opportunities</span>
      <span id="opps-count" style="color:var(--text-muted);font-size:0.82rem"></span>
    </div>
    <div class="section-body no-pad" style="max-height:400px;overflow-y:auto">
      <table class="tbl" id="opps-table">
        <thead><tr><th>Time</th><th>Type</th><th>Market</th><th>Prices</th><th class="right">Cost</th><th class="right">Profit</th><th class="right">ROI</th><th class="right">Depth</th><th>Action</th></tr></thead>
        <tbody id="opps-tbody"></tbody>
      </table>
    </div>
  </div>

  <!-- Alerts -->
  <div class="section">
    <div class="section-header">
      <span>Alerts</span>
      <span id="alerts-count" style="color:var(--text-muted);font-size:0.82rem"></span>
    </div>
    <div class="section-body" id="alerts-body">
      <div class="empty">No alerts</div>
    </div>
  </div>

</div>

<div class="footer">
  Auto-refreshing every <span id="refresh-interval">__REFRESH_SECONDS__</span>s
  &middot; Last updated: <span id="last-updated">--</span>
</div>

<!-- ====================================================================== -->
<!-- JavaScript                                                              -->
<!-- ====================================================================== -->
<script>
const REFRESH = __REFRESH_SECONDS__ * 1000;
let pnlChart = null;
let platformChart = null;

// ---------------------------------------------------------------------------
// Fetch helpers
// ---------------------------------------------------------------------------
async function api(path) {
  try {
    const r = await fetch(path);
    if (!r.ok) return null;
    return await r.json();
  } catch { return null; }
}

function $(id) { return document.getElementById(id); }

function fmtUSD(v) {
  if (v == null) return '--';
  const n = parseFloat(v);
  const sign = n >= 0 ? '' : '-';
  return sign + '$' + Math.abs(n).toFixed(4);
}
function fmtPct(v) {
  if (v == null) return '--';
  return (parseFloat(v) * 100).toFixed(2) + '%';
}
function fmtTime(iso) {
  if (!iso) return '--';
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
function fmtDate(iso) {
  if (!iso) return '--';
  return iso.substring(0, 10);
}
function fmtDuration(sec) {
  if (!sec && sec !== 0) return '--';
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (h > 0) return h + 'h ' + m + 'm';
  if (m > 0) return m + 'm';
  return Math.floor(sec) + 's';
}
function pnlClass(v) { return parseFloat(v) >= 0 ? 'positive' : 'negative'; }
function statusClass(s) {
  s = (s || '').toLowerCase();
  if (s === 'filled') return 'status-filled';
  if (s === 'pending') return 'status-pending';
  if (s === 'failed' || s === 'orphaned') return 'status-failed';
  if (s === 'open') return 'status-open';
  if (s === 'dry_run') return 'status-dry_run';
  return '';
}
function truncate(s, n) { return s && s.length > n ? s.substring(0, n) + '...' : s; }

// ---------------------------------------------------------------------------
// P&L chart
// ---------------------------------------------------------------------------
function initPnlChart() {
  const ctx = $('pnl-chart').getContext('2d');
  pnlChart = new Chart(ctx, {
    type: 'bar',
    data: { labels: [], datasets: [{
      label: 'Daily P&L',
      data: [],
      backgroundColor: [],
      borderRadius: 3,
      barPercentage: 0.7,
    }]},
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: '#8b8fa3', font: { size: 11 } }, grid: { display: false } },
        y: {
          ticks: { color: '#8b8fa3', font: { size: 11 }, callback: v => '$' + v.toFixed(2) },
          grid: { color: '#2d3148' },
        },
      },
    },
  });
}

function updatePnlChart(history) {
  if (!pnlChart || !history) return;
  pnlChart.data.labels = history.map(d => d.date.substring(5)); // MM-DD
  pnlChart.data.datasets[0].data = history.map(d => d.pnl);
  pnlChart.data.datasets[0].backgroundColor = history.map(d => d.pnl >= 0 ? '#22c55e' : '#ef4444');
  pnlChart.update('none');
}

// ---------------------------------------------------------------------------
// Platform chart
// ---------------------------------------------------------------------------
function initPlatformChart() {
  const ctx = $('platform-chart').getContext('2d');
  const colors = ['#6366f1','#3b82f6','#22c55e','#eab308','#ef4444','#ec4899','#f97316','#14b8a6'];
  platformChart = new Chart(ctx, {
    type: 'doughnut',
    data: { labels: [], datasets: [{ data: [], backgroundColor: colors, borderWidth: 0 }] },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: '65%',
      plugins: {
        legend: { position: 'right', labels: { color: '#e4e6f0', font: { size: 12 }, padding: 12 } },
      },
    },
  });
}

function updatePlatformChart(platforms) {
  if (!platformChart || !platforms || platforms.length === 0) {
    if (platformChart) {
      platformChart.data.labels = ['No positions'];
      platformChart.data.datasets[0].data = [1];
      platformChart.data.datasets[0].backgroundColor = ['#2d3148'];
      platformChart.update('none');
    }
    return;
  }
  platformChart.data.labels = platforms.map(p => p.platform);
  platformChart.data.datasets[0].data = platforms.map(p => p.count);
  platformChart.update('none');
}

// ---------------------------------------------------------------------------
// Render functions
// ---------------------------------------------------------------------------
function renderStatus(data) {
  if (!data) return;
  $('kpi-daily-pnl').textContent = fmtUSD(data.daily_pnl);
  $('kpi-daily-pnl').className = 'card-value ' + pnlClass(data.daily_pnl);
  $('kpi-positions').textContent = data.open_positions;
  $('kpi-opps').textContent = data.opportunities_found;
  $('kpi-scans').textContent = 'Scans: ' + data.scan_count;
  $('last-scan').textContent = data.last_scan_time ? 'Last scan: ' + fmtTime(data.last_scan_time) : '';
  const ws = data.ws_connections;
  $('kpi-ws').innerHTML = ws > 0
    ? '<span class="positive">Connected</span>'
    : '<span class="negative">Disconnected</span>';
}

function renderHealth(data) {
  if (!data) return;
  // Mode badge
  const badge = $('mode-badge');
  if (data.dry_run) {
    badge.className = 'badge badge-dry';
    badge.innerHTML = '<span class="dot dot-yellow dot-pulse"></span> DRY RUN';
  } else {
    badge.className = 'badge badge-live';
    badge.innerHTML = '<span class="dot dot-green dot-pulse"></span> LIVE';
  }
  // Uptime
  $('uptime').textContent = 'Up ' + fmtDuration(data.uptime_seconds);
  // Metrics
  const m = data.metrics || {};
  const counters = m.counters || {};
  const gauges = m.gauges || {};
  $('kpi-trades').textContent = counters.trades_executed || 0;
  $('kpi-trades-failed').textContent = 'Failed: ' + (counters.trades_failed || 0);
  $('kpi-ws-msgs').textContent = 'Messages: ' + (counters.ws_messages_received || 0);
  const latency = gauges.scan_cycle_duration_seconds;
  $('kpi-scan-latency').textContent = 'Scan latency: ' + (latency != null ? latency.toFixed(2) + 's' : '--');
}

function renderSlippage(data) {
  if (!data) return;
  const avg = data.avg_slippage;
  $('kpi-slippage').textContent = avg != null && avg !== 0 ? (avg * 100).toFixed(3) + '%' : '--';
}

function renderCumulative(data) {
  if (data == null) return;
  const v = data.cumulative_pnl;
  $('kpi-cumulative-pnl').innerHTML = 'Cumulative: <span class="' + pnlClass(v) + '">' + fmtUSD(v) + '</span>';
}

function renderStrategies(data) {
  const tbody = $('strategy-tbody');
  if (!data || data.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty">No data yet</td></tr>';
    return;
  }
  tbody.innerHTML = data.map(s => `
    <tr>
      <td>${s.type}</td>
      <td class="mono right">${s.count}</td>
      <td class="mono right">${fmtPct(s.avg_roi)}</td>
      <td class="mono right">${fmtUSD(s.avg_profit)}</td>
      <td class="mono right ${pnlClass(s.total_profit)}">${fmtUSD(s.total_profit)}</td>
    </tr>`).join('');
}

function renderPositions(data) {
  const tbody = $('positions-tbody');
  if (!data || data.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty">No open positions</td></tr>';
    $('positions-count').textContent = '';
    return;
  }
  $('positions-count').textContent = data.length + ' position' + (data.length !== 1 ? 's' : '');
  tbody.innerHTML = data.map(p => `
    <tr>
      <td title="${p.market_identifier || ''}">${truncate(p.market_identifier || '--', 60)}</td>
      <td>${p.platform || '--'}</td>
      <td class="mono right ${pnlClass(p.expected_pnl)}">${fmtUSD(p.expected_pnl)}</td>
      <td>${fmtTime(p.entry_timestamp)}</td>
      <td><span class="status-badge ${statusClass(p.status)}">${p.status || '--'}</span></td>
    </tr>`).join('');
}

function renderTrades(data) {
  const tbody = $('trades-tbody');
  if (!data || data.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty">No trades yet</td></tr>';
    $('trades-count').textContent = '';
    return;
  }
  $('trades-count').textContent = data.length + ' recent';
  tbody.innerHTML = data.slice(0, 50).map(t => {
    const slip = t.slippage != null ? (t.slippage * 100).toFixed(3) + '%' : '--';
    return `
    <tr>
      <td>${fmtTime(t.timestamp)}</td>
      <td>${t.platform}</td>
      <td>${t.side}</td>
      <td class="mono right">${t.price != null ? t.price.toFixed(4) : '--'}</td>
      <td class="mono right">${t.fill_price != null ? t.fill_price.toFixed(4) : '--'}</td>
      <td class="mono right">${slip}</td>
      <td><span class="status-badge ${statusClass(t.status)}">${t.status}</span></td>
    </tr>`;
  }).join('');
}

function renderOpportunities(data) {
  const tbody = $('opps-tbody');
  if (!data || data.length === 0) {
    tbody.innerHTML = '<tr><td colspan="9" class="empty">No opportunities detected yet</td></tr>';
    $('opps-count').textContent = '';
    return;
  }
  $('opps-count').textContent = data.length + ' recent';
  tbody.innerHTML = data.slice(0, 100).map(o => `
    <tr>
      <td>${fmtTime(o.timestamp)}</td>
      <td>${o.type || '--'}</td>
      <td title="${o.market || ''}">${truncate(o.market || '--', 45)}</td>
      <td class="mono" title="${o.prices || ''}">${truncate(o.prices || '--', 30)}</td>
      <td class="mono right">${fmtUSD(o.total_cost)}</td>
      <td class="mono right ${pnlClass(o.net_profit)}">${fmtUSD(o.net_profit)}</td>
      <td class="mono right">${fmtPct(o.net_roi)}</td>
      <td class="mono right">${o.depth != null ? '$' + parseFloat(o.depth).toFixed(2) : '--'}</td>
      <td><span class="status-badge ${statusClass(o.action)}">${o.action || '--'}</span></td>
    </tr>`).join('');
}

function renderAlerts(data) {
  const body = $('alerts-body');
  if (!data || data.length === 0) {
    body.innerHTML = '<div class="empty">No alerts</div>';
    $('alerts-count').textContent = '';
    return;
  }
  $('alerts-count').textContent = data.length + ' alert' + (data.length !== 1 ? 's' : '');
  body.innerHTML = data.slice(0, 30).map(a => `
    <div class="alert-item">
      <span class="alert-sev sev-${a.severity}">${a.severity}</span>
      <span class="alert-time">${fmtTime(a.timestamp)}</span>
      <span>${a.message || a.type}</span>
    </div>`).join('');
}

// ---------------------------------------------------------------------------
// Main refresh loop
// ---------------------------------------------------------------------------
async function refresh() {
  const spinner = $('refresh-spinner');
  spinner.style.display = 'inline-block';

  const [status, health, slippage, history, strategies, positions,
         platforms, trades, opportunities, alerts] = await Promise.all([
    api('/status'),
    api('/api/health'),
    api('/api/slippage'),
    api('/api/history'),
    api('/api/strategies'),
    api('/api/positions'),
    api('/api/platforms'),
    api('/api/trades'),
    api('/api/opportunities'),
    api('/alerts'),
  ]);

  renderStatus(status);
  renderHealth(health);
  renderSlippage(slippage);
  renderCumulative(health);
  renderStrategies(strategies);
  renderPositions(positions);
  updatePlatformChart(platforms);
  renderTrades(trades);
  renderOpportunities(opportunities);
  renderAlerts(alerts);
  updatePnlChart(history);

  // Positions subtitle
  if (platforms && platforms.length > 0) {
    $('kpi-positions-sub').textContent = platforms.map(p => p.platform + ': ' + p.count).join(', ');
  }

  $('last-updated').textContent = new Date().toLocaleTimeString();
  spinner.style.display = 'none';
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
  initPnlChart();
  initPlatformChart();
  refresh();
  setInterval(refresh, REFRESH);
});
</script>
</body>
</html>"""
