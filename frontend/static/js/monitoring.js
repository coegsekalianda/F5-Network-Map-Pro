const MONITOR_API = '';
const MONITOR_STORAGE_KEY = 'f5_vs_connection_monitor_targets_v1';
const MONITOR_MAX_POINTS = 300;
const MONITOR_COLORS = ['#55b6ff', '#5dd17a', '#f3b54b', '#ff6868', '#b48cff', '#5ce1d8'];

let monitorDevices = [];
let monitorVirtualServers = [];
let monitorTargets = [];
let monitorPollTimer = null;
let monitorPollInFlight = false;
let monitorPageInitialized = false;

function initMonitoringPage() {
  if (monitorPageInitialized) {
    renderMonitoringDashboard();
    startMonitoringPolling();
    return;
  }

  monitorPageInitialized = true;
  renderMonitoringDashboard();
  loadMonitoringDashboard();
  loadMonitorDevices();
  startMonitoringPolling();
}

function startMonitoringPolling() {
  if (monitorPollTimer) return;
  pollMonitoringTargets();
  monitorPollTimer = setInterval(pollMonitoringTargets, 1000);
}

function stopMonitoringPolling() {
  if (monitorPollTimer) {
    clearInterval(monitorPollTimer);
    monitorPollTimer = null;
  }
}

async function loadMonitorDevices() {
  const list = document.getElementById('mon-device-list');
  const input = document.getElementById('mon-device-input');
  if (input) input.placeholder = 'Loading device...';

  try {
    const r = await fetch(`${MONITOR_API}/devices`);
    const devices = await r.json();
    monitorDevices = devices.filter(d => d.enabled);
    if (list) {
      list.innerHTML = monitorDevices.map(d => {
        const label = d.hostname || d.name || d.management_ip;
        return `<option value="${escAttr(label)}"></option>`;
      }).join('');
    }
    if (input) input.placeholder = 'Device hostname...';
  } catch (e) {
    invToast('Failed to load monitoring devices: ' + e.message, 'err');
  }
}

function selectedMonitorDevice() {
  const input = document.getElementById('mon-device-input');
  const value = input ? input.value.trim().toLowerCase() : '';
  if (!value) return null;

  return monitorDevices.find(d => {
    const candidates = [d.hostname, d.name, d.management_ip].filter(Boolean);
    return candidates.some(item => String(item).trim().toLowerCase() === value);
  }) || null;
}

async function loadMonitorVirtualServers() {
  const device = selectedMonitorDevice();
  const list = document.getElementById('mon-vs-list');
  const input = document.getElementById('mon-vs-input');
  if (!device) {
    invToast('Select a device first', 'err');
    return;
  }

  if (input) {
    input.value = '';
    input.placeholder = 'Loading Virtual Server...';
  }
  if (list) list.innerHTML = '';

  try {
    const r = await fetch(`${MONITOR_API}/api/monitoring/virtual-servers?device_id=${encodeURIComponent(device.id)}`);
    const data = await r.json();
    if (!r.ok || data.status !== 'ok') {
      throw new Error(data.error || data.detail || 'Failed to load Virtual Server');
    }

    monitorVirtualServers = data.items || [];
    if (list) {
      list.innerHTML = monitorVirtualServers.map(vs => {
        const label = `${vs.partition || 'Common'}/${vs.name}`;
        return `<option value="${escAttr(label)}" label="${escAttr(vs.destination || '')}"></option>`;
      }).join('');
    }
    if (input) input.placeholder = 'Virtual Server...';
    invToast(`${monitorVirtualServers.length} Virtual Servers loaded`, 'ok');
  } catch (e) {
    if (input) input.placeholder = 'Virtual Server...';
    invToast('Failed to load Virtual Server: ' + e.message, 'err');
  }
}

function selectedMonitorVirtualServer() {
  const input = document.getElementById('mon-vs-input');
  const value = input ? input.value.trim() : '';
  if (!value) return null;

  return monitorVirtualServers.find(vs => {
    const label = `${vs.partition || 'Common'}/${vs.name}`;
    return label.toLowerCase() === value.toLowerCase() || String(vs.name).toLowerCase() === value.toLowerCase();
  }) || null;
}

function addMonitorTarget() {
  const device = selectedMonitorDevice();
  const vs = selectedMonitorVirtualServer();
  const labelInput = document.getElementById('mon-label-input');

  if (!device) {
    invToast('Select a device first', 'err');
    return;
  }
  if (!vs) {
    invToast('Select a Virtual Server first', 'err');
    return;
  }

  const hostname = device.hostname || device.name || device.management_ip;
  const label = (labelInput && labelInput.value.trim()) || `${hostname} - ${vs.name}`;
  const targetKey = `${device.id}|${vs.partition || 'Common'}|${vs.name}`;

  if (monitorTargets.some(target => target.key === targetKey)) {
    invToast('Target already exists in the dashboard', 'err');
    return;
  }

  monitorTargets.push({
    id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    key: targetKey,
    device_id: device.id,
    device_hostname: hostname,
    partition: vs.partition || 'Common',
    vs_name: vs.name,
    destination: vs.destination || '',
    label,
    points: [],
    last: null,
  });

  if (labelInput) labelInput.value = '';
  saveMonitoringDashboard();
  renderMonitoringDashboard();
  pollMonitoringTargets();
}

function removeMonitorTarget(targetId) {
  monitorTargets = monitorTargets.filter(target => target.id !== targetId);
  saveMonitoringDashboard();
  renderMonitoringDashboard();
}

function renameMonitorTarget(targetId) {
  const target = monitorTargets.find(item => item.id === targetId);
  if (!target) return;
  const next = prompt('Rename label target', target.label);
  if (!next || !next.trim()) return;
  target.label = next.trim();
  saveMonitoringDashboard();
  renderMonitoringDashboard();
}

function saveMonitoringDashboard() {
  const payload = monitorTargets.map(({
    points,
    last,
    simulated,
    sim_profile,
    sim_total,
    sim_started_at,
    ...target
  }) => target);
  localStorage.setItem(MONITOR_STORAGE_KEY, JSON.stringify(payload));
  invToast('Monitoring dashboard saved', 'ok');
}

function loadMonitoringDashboard() {
  try {
    const raw = localStorage.getItem(MONITOR_STORAGE_KEY);
    if (!raw) {
      monitorTargets = monitorTargets || [];
      return;
    }
    const saved = JSON.parse(raw);
    monitorTargets = saved
      .filter(target => !target.simulated)
      .map(target => ({
        ...target,
        points: target.points || [],
        last: target.last || null,
      }));
    renderMonitoringDashboard();
  } catch (e) {
    invToast('Failed to load dashboard: ' + e.message, 'err');
  }
}

function clearMonitoringDashboard() {
  if (!confirm('Clear all monitoring targets?')) return;
  monitorTargets = [];
  localStorage.removeItem(MONITOR_STORAGE_KEY);
  renderMonitoringDashboard();
}

async function pollMonitoringTargets() {
  if (!monitorTargets.length || monitorPollInFlight) return;
  monitorPollInFlight = true;

  const body = {
    targets: monitorTargets.map(target => ({
      device_id: target.device_id,
      partition: target.partition,
      vs_name: target.vs_name,
    })),
  };

  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 5000);
    const r = await fetch(`${MONITOR_API}/api/monitoring/vs-connections/batch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    clearTimeout(timeoutId);
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || 'Monitoring API error');
    applyMonitoringResults(data.results || []);
  } catch (e) {
    const now = new Date().toISOString();
    monitorTargets.forEach(target => {
      target.last = {
        status: e.name === 'AbortError' ? 'timeout' : 'error',
        error: e.name === 'AbortError' ? 'Timeout polling monitoring API' : e.message,
        timestamp: now,
      };
    });
    renderMonitoringDashboard();
  } finally {
    monitorPollInFlight = false;
  }
}

function applyMonitoringResults(results) {
  const byKey = new Map();
  results.forEach(result => {
    byKey.set(`${result.device_id}|${result.partition || 'Common'}|${result.vs_name}`, result);
  });

  monitorTargets.forEach(target => {
    const result = byKey.get(target.key);
    if (!result) return;

    if (result.destination && !target.destination) {
      target.destination = result.destination;
    }
    target.device_hostname = result.hostname || target.device_hostname;
    target.last = result;

    const previousPoint = target.points[target.points.length - 1];
    if (
      (result.connection_rate === null || result.connection_rate === undefined)
      && result.total_connections !== null
      && result.total_connections !== undefined
      && previousPoint
      && previousPoint.total !== null
      && previousPoint.total !== undefined
    ) {
      const deltaSeconds = Math.max((Date.now() - previousPoint.ts) / 1000, 1);
      const deltaTotal = Number(result.total_connections) - Number(previousPoint.total);
      result.connection_rate = Math.max(0, Math.round(deltaTotal / deltaSeconds));
    }

    const nowMs = Date.now();
    const shouldAddPoint = !previousPoint || (nowMs - previousPoint.ts >= 3000);
    if (shouldAddPoint) {
      const point = {
        ts: nowMs,
        current: result.current_connections,
        rate: result.connection_rate,
        total: result.total_connections,
      };
      target.points.push(point);
      if (target.points.length > MONITOR_MAX_POINTS) {
        target.points.splice(0, target.points.length - MONITOR_MAX_POINTS);
      }
    }
  });

  renderMonitoringDashboard();
}

function renderMonitoringDashboard() {
  const grid = document.getElementById('monitoring-grid');
  const empty = document.getElementById('monitoring-empty');
  if (!grid || !empty) return;

  empty.style.display = monitorTargets.length ? 'none' : 'flex';
  grid.innerHTML = monitorTargets.map(renderMonitorCard).join('');

  monitorTargets.forEach(target => {
    const canvas = document.getElementById(`monitor-chart-${target.id}`);
    if (canvas) drawLineChart(canvas, [{ label: target.label, points: target.points, color: MONITOR_COLORS[0] }]);
  });
}

function renderMonitorCard(target) {
  const last = target.last || {};
  const warnings = monitorWarnings(target);

  return `
    <div class="monitor-card">
      <div class="monitor-card-head">
        <div>
          <div class="monitor-card-title">${escHtml(target.label)}</div>
        </div>
        <div class="monitor-actions">
          <button class="btn btn-sm" onclick="renameMonitorTarget('${escAttr(target.id)}')">Rename</button>
          <button class="btn btn-sm btn-danger" onclick="removeMonitorTarget('${escAttr(target.id)}')">Remove</button>
        </div>
      </div>
      <div class="monitor-stats">
        ${monitorStat('Current Connection', formatMetric(last.current_connections))}
        ${monitorStat('Total Connection', formatMetric(last.total_connections))}
      </div>
      ${warnings ? `<div class="monitor-warning">${warnings}</div>` : ''}
      <canvas id="monitor-chart-${escAttr(target.id)}" class="monitor-chart"></canvas>
    </div>
  `;
}

function monitorStat(label, value) {
  return `
    <div class="monitor-stat">
      <div class="monitor-stat-value">${escHtml(value)}</div>
      <div class="monitor-stat-label">${escHtml(label)}</div>
    </div>
  `;
}

function monitorWarnings(target) {
  const warnings = [];
  const last = target.last || {};
  const points = target.points || [];
  const latest = points[points.length - 1];
  const prev = points[points.length - 2];

  if (last.status && last.status !== 'ok') {
    warnings.push('Device is unreachable or timed out');
  }
  if (String(last.availability_state || '').toLowerCase() && String(last.availability_state || '').toLowerCase() !== 'available') {
    warnings.push('Virtual Server down / unavailable');
  }
  if (last.timestamp) {
    const age = Date.now() - new Date(last.timestamp).getTime();
    if (age > 5000) warnings.push('Data has not updated for more than 5 seconds');
  }

  return warnings.map(escHtml).join('<br>');
}

function formatMetric(value) {
  if (value === null || value === undefined || value === '') return 'N/A';
  try {
    return Number(value).toLocaleString('en-US');
  } catch {
    return String(value);
  }
}

function formatMonitorTime(value) {
  if (!value) return 'N/A';
  try {
    return new Date(value).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return value;
  }
}

function drawLineChart(canvas, seriesList) {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const width = Math.max(320, Math.floor(rect.width));
  const height = Math.max(210, Math.floor(rect.height || 210));
  canvas.width = width * dpr;
  canvas.height = height * dpr;

  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, width, height);

  const pad = { left: 58, right: 18, top: 14, bottom: 34 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const allPoints = seriesList.flatMap(series => series.points || []).filter(point => point.current !== null && point.current !== undefined);

  if (!allPoints.length) {
    drawChartGrid(ctx, pad, plotW, plotH, 1, Date.now() - 300000, Date.now());
    ctx.fillStyle = '#738091';
    ctx.font = '12px Segoe UI';
    ctx.fillText('Waiting data...', pad.left + 8, pad.top + 24);
    return;
  }

  const minTs = Date.now() - 300000;
  const maxTs = Date.now();
  // Y-axis follows the max of VISIBLE window data only — scales up and down
  // as actual data changes, so the chart stays proportional at all times.
  const visiblePoints = allPoints.filter(p => p.ts >= minTs);
  const peakVal = Math.max(1, ...(visiblePoints.length ? visiblePoints : allPoints).map(p => Number(p.current || 0)));
  const maxVal = niceChartMax(peakVal);

  drawChartGrid(ctx, pad, plotW, plotH, maxVal, minTs, maxTs);

  seriesList.forEach((series, index) => {
    const points = (series.points || []).filter(point => point.ts >= minTs && point.current !== null && point.current !== undefined);
    if (!points.length) return;

    ctx.strokeStyle = series.color || MONITOR_COLORS[index % MONITOR_COLORS.length];
    ctx.lineWidth = 3;
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    ctx.beginPath();
    points.forEach((point, pointIndex) => {
      const x = pad.left + ((point.ts - minTs) / (maxTs - minTs)) * plotW;
      const y = pad.top + plotH - (Number(point.current || 0) / maxVal) * plotH;
      if (pointIndex === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  });
}

function niceChartMax(value) {
  if (value <= 10) return 10;
  const rawStep = value / 5;
  const exponent = Math.floor(Math.log10(rawStep));
  const base = Math.pow(10, exponent);
  const normalized = rawStep / base;
  const niceStep =
    normalized <= 1.2 ? 1 :
    normalized <= 2 ? 2 :
    normalized <= 5 ? 5 :
    10;
  const step = niceStep * base;
  return Math.max(step, Math.ceil(value / step) * step);
}

function drawChartGrid(ctx, pad, plotW, plotH, maxVal, minTs, maxTs) {
  const gridColor = '#24303d';
  const axisColor = '#354252';
  const labelColor = '#87919f';
  const steps = 5;

  ctx.save();
  ctx.font = '12px Consolas';
  ctx.fillStyle = labelColor;
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  ctx.strokeStyle = gridColor;
  ctx.lineWidth = 1;

  for (let i = 0; i <= steps; i++) {
    const ratio = i / steps;
    const y = pad.top + plotH - ratio * plotH;
    const value = Math.round(maxVal * ratio);
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(pad.left + plotW, y);
    ctx.stroke();
    ctx.fillText(formatAxisNumber(value), pad.left - 8, y);
  }

  ctx.strokeStyle = axisColor;
  ctx.beginPath();
  ctx.moveTo(pad.left, pad.top);
  ctx.lineTo(pad.left, pad.top + plotH);
  ctx.lineTo(pad.left + plotW, pad.top + plotH);
  ctx.stroke();

  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  const timeSteps = 6;
  for (let i = 0; i <= timeSteps; i++) {
    const ratio = i / timeSteps;
    const x = pad.left + ratio * plotW;
    const ts = minTs + (maxTs - minTs) * ratio;
    ctx.strokeStyle = axisColor;
    ctx.beginPath();
    ctx.moveTo(x, pad.top + plotH);
    ctx.lineTo(x, pad.top + plotH + 5);
    ctx.stroke();
    ctx.fillText(formatAxisTime(ts), x, pad.top + plotH + 9);
  }
  ctx.restore();
}

function formatAxisNumber(value) {
  return Number(value).toLocaleString('en-US');
}

function formatAxisTime(ts) {
  const date = new Date(ts);
  const hour = String(date.getHours()).padStart(2, '0');
  const minute = String(date.getMinutes()).padStart(2, '0');
  return `${hour}:${minute}`;
}
