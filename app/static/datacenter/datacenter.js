let apiKey = '';
let metricsTimer = null;
let logsTimer = null;

let hourlyChart = null;
let dailyChart = null;
let modelsChart = null;

function $(id) {
  return document.getElementById(id);
}

function setText(id, v) {
  const el = $(id);
  if (!el) return;
  el.textContent = v;
}

function safeNum(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

function formatPercent(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '-';
  return `${n.toFixed(1)}%`;
}

function isAutoRefresh() {
  const el = $('auto-refresh');
  return !!(el && el.checked);
}

function buildCharts() {
  const hourlyEl = $('chart-hourly');
  const dailyEl = $('chart-daily');
  const modelsEl = $('chart-models');
  if (!hourlyEl || !dailyEl || !modelsEl) return;

  const baseOpts = {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    plugins: { legend: { display: true } },
    scales: {
      x: { grid: { color: 'rgba(0,0,0,0.06)' } },
      y: { beginAtZero: true, grid: { color: 'rgba(0,0,0,0.06)' } }
    }
  };

  hourlyChart = new Chart(hourlyEl, {
    type: 'line',
    data: { labels: [], datasets: [
      { label: '成功', data: [], borderColor: '#16a34a', backgroundColor: 'rgba(22,163,74,0.12)', tension: 0.35, fill: true },
      { label: '失败', data: [], borderColor: '#ef4444', backgroundColor: 'rgba(239,68,68,0.10)', tension: 0.35, fill: true },
    ]},
    options: baseOpts
  });

  dailyChart = new Chart(dailyEl, {
    type: 'bar',
    data: { labels: [], datasets: [
      { label: '成功', data: [], backgroundColor: '#16a34a' },
      { label: '失败', data: [], backgroundColor: '#ef4444' },
    ]},
    options: { ...baseOpts, scales: { x: baseOpts.scales.x, y: baseOpts.scales.y } }
  });

  modelsChart = new Chart(modelsEl, {
    type: 'doughnut',
    data: { labels: [], datasets: [{ data: [], backgroundColor: [
      '#2563eb', '#16a34a', '#f59e0b', '#ef4444', '#7c3aed',
      '#06b6d4', '#84cc16', '#f97316', '#64748b', '#ec4899'
    ]}]},
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: { legend: { position: 'right' } }
    }
  });
}

async function fetchMetrics() {
  const res = await fetch('/api/v1/admin/metrics', { headers: buildAuthHeaders(apiKey) });
  if (res.status === 401) {
    logout();
    return null;
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

function updateMetricsUI(data) {
  const t = data.tokens || {};
  const invalid = safeNum(t.expired) + safeNum(t.disabled);

  setText('m-token-total', safeNum(t.total).toLocaleString());
  setText('m-token-active', safeNum(t.active).toLocaleString());
  setText('m-token-cooling', safeNum(t.cooling).toLocaleString());
  setText('m-token-invalid', invalid.toLocaleString());
  setText('m-total-calls', safeNum(t.total_calls).toLocaleString());

  const rs = data.request_stats || {};
  const sum = rs.summary || {};
  setText('m-req-total', safeNum(sum.total).toLocaleString());
  setText('m-req-success', safeNum(sum.success).toLocaleString());
  setText('m-req-failed', safeNum(sum.failed).toLocaleString());
  setText('m-success-rate', formatPercent(safeNum(sum.success_rate)));

  const cache = data.cache || {};
  const li = cache.local_image || { count: 0, size_mb: 0 };
  const lv = cache.local_video || { count: 0, size_mb: 0 };
  setText('m-local-image', `${safeNum(li.count)} / ${safeNum(li.size_mb)} MB`);
  setText('m-local-video', `${safeNum(lv.count)} / ${safeNum(lv.size_mb)} MB`);

  // Charts
  const hourly = Array.isArray(rs.hourly) ? rs.hourly : [];
  const daily = Array.isArray(rs.daily) ? rs.daily : [];
  const models = Array.isArray(rs.models) ? rs.models : [];

  if (hourlyChart) {
    hourlyChart.data.labels = hourly.map((x) => x.hour || '');
    hourlyChart.data.datasets[0].data = hourly.map((x) => safeNum(x.success));
    hourlyChart.data.datasets[1].data = hourly.map((x) => safeNum(x.failed));
    hourlyChart.update();
  }
  if (dailyChart) {
    dailyChart.data.labels = daily.map((x) => x.date || '');
    dailyChart.data.datasets[0].data = daily.map((x) => safeNum(x.success));
    dailyChart.data.datasets[1].data = daily.map((x) => safeNum(x.failed));
    dailyChart.update();
  }
  if (modelsChart) {
    modelsChart.data.labels = models.map((x) => x.model || '');
    modelsChart.data.datasets[0].data = models.map((x) => safeNum(x.count));
    modelsChart.update();
  }
}

async function refreshMetricsOnce(silent = false) {
  try {
    const data = await fetchMetrics();
    if (!data) return;
    updateMetricsUI(data);
  } catch (e) {
    if (!silent) showToast(`刷新失败: ${e.message || e}`, 'error');
  }
}

async function fetchLogFiles() {
  const res = await fetch('/api/v1/admin/logs/files', { headers: buildAuthHeaders(apiKey) });
  if (res.status === 401) {
    logout();
    return null;
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

async function loadLogFiles() {
  const sel = $('log-file');
  if (!sel) return;
  try {
    const data = await fetchLogFiles();
    if (!data) return;
    const files = Array.isArray(data.files) ? data.files : [];
    sel.innerHTML = '';
    files.forEach((f) => {
      const opt = document.createElement('option');
      opt.value = f.name;
      opt.textContent = f.name;
      sel.appendChild(opt);
    });
  } catch (e) {
    showToast(`获取日志文件失败: ${e.message || e}`, 'error');
  }
}

async function fetchTail(file, lines) {
  const params = new URLSearchParams();
  if (file) params.set('file', file);
  params.set('lines', String(lines || 500));
  const url = `/api/v1/admin/logs/tail?${params.toString()}`;
  const res = await fetch(url, { headers: buildAuthHeaders(apiKey) });
  if (res.status === 401) {
    logout();
    return null;
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

function applyLogFilter(rawLines) {
  const filter = ($('log-filter')?.value || '').trim();
  if (!filter) return rawLines;
  const lower = filter.toLowerCase();
  return rawLines.filter((l) => String(l).toLowerCase().includes(lower));
}

async function refreshLogsOnce(silent = false) {
  const sel = $('log-file');
  const linesEl = $('log-lines');
  const content = $('log-content');
  if (!content) return;
  try {
    const wasAtBottom = content.scrollTop + content.clientHeight >= content.scrollHeight - 10;
    const file = sel ? sel.value : '';
    const n = linesEl ? Math.max(50, Math.min(5000, Number(linesEl.value || 500))) : 500;
    const data = await fetchTail(file, n);
    if (!data) return;
    const rawLines = Array.isArray(data.lines) ? data.lines : [];
    const lines = applyLogFilter(rawLines);
    content.textContent = lines.length ? lines.join('\n') : '(空)';
    if (wasAtBottom) content.scrollTop = content.scrollHeight;
  } catch (e) {
    if (!silent) showToast(`日志刷新失败: ${e.message || e}`, 'error');
  }
}

function setupEvents() {
  const btn = $('btn-refresh');
  if (btn) {
    btn.addEventListener('click', async () => {
      await refreshMetricsOnce(false);
      await refreshLogsOnce(true);
    });
  }

  const logRefresh = $('log-refresh');
  if (logRefresh) {
    logRefresh.addEventListener('click', async () => refreshLogsOnce(false));
  }

  const logFile = $('log-file');
  if (logFile) {
    logFile.addEventListener('change', async () => refreshLogsOnce(true));
  }
  const logLines = $('log-lines');
  if (logLines) {
    logLines.addEventListener('change', async () => refreshLogsOnce(true));
  }
  const logFilter = $('log-filter');
  if (logFilter) {
    logFilter.addEventListener('input', () => refreshLogsOnce(true));
  }
}

function startTimers() {
  stopTimers();
  metricsTimer = setInterval(() => {
    if (!isAutoRefresh()) return;
    refreshMetricsOnce(true);
  }, 5000);
  logsTimer = setInterval(() => {
    if (!isAutoRefresh()) return;
    refreshLogsOnce(true);
  }, 3000);
}

function stopTimers() {
  if (metricsTimer) clearInterval(metricsTimer);
  if (logsTimer) clearInterval(logsTimer);
  metricsTimer = null;
  logsTimer = null;
}

async function init() {
  apiKey = await ensureApiKey();
  if (apiKey === null) return;
  buildCharts();
  setupEvents();
  await refreshMetricsOnce(true);
  await loadLogFiles();
  await refreshLogsOnce(true);
  startTimers();
}

window.onload = init;
