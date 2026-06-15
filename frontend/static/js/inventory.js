/**
 * inventory.js - Frontend logic for Devices & Inventory
 *
 * Pages:
 *   - Devices   : F5 device CRUD, test connection, per-device sync
 *   - Inventory : Sync All, Search IP, Load All
 */

const INV_API = '';  // relative to the same origin

function routeForTab(tab) {
  return tab === 'monitoring' ? '/monitoring' : '/';
}

function updateRouteForTab(tab, replace = false) {
  const nextPath = routeForTab(tab);
  if (window.location.pathname === nextPath) return;
  const method = replace ? 'replaceState' : 'pushState';
  window.history[method]({ tab }, '', nextPath);
}

function tabFromRoute() {
  return window.location.pathname === '/monitoring' ? 'monitoring' : 'topology';
}

function openMonitoringPage() {
  window.open('/monitoring', '_blank', 'noopener');
}

function setDisplay(id, value) {
  const el = document.getElementById(id);
  if (el) el.style.display = value;
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

// ─── Tab Navigation ────────────────────────────────────────────────────────────

function switchTab(tab, options = {}) {
  updateRouteForTab(tab, Boolean(options.replace));
  document.body.classList.toggle('monitoring-standalone', tab === 'monitoring');

  if (tab === 'monitoring') {
    document.title = 'F5 Network Map Pro - Monitoring';
  } else {
    document.title = 'F5 Network Map Pro';
  }

  // Hide all sidebar panels and pages.
  setDisplay('sidebar-topology', 'none');
  setDisplay('sidebar-devices', 'none');
  setDisplay('sidebar-inventory', 'none');
  setDisplay('sidebar-monitoring', 'none');

  setDisplay('page-topology', 'none');
  setDisplay('page-devices', 'none');
  setDisplay('page-inventory', 'none');
  setDisplay('page-monitoring', 'none');

  // Reset topbar.
  const topbar = document.getElementById('topbar');
  topbar.style.display = 'flex';
  setText('status-msg', '');
  setDisplay('bulk-header-container', 'none');

  // Clear active state from all tabs.
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('tab-active'));
  const tabButton = document.getElementById(`tab-${tab}`);
  if (tabButton) tabButton.classList.add('tab-active');

  if (tab === 'topology') {
    if (typeof stopMonitoringPolling === 'function') stopMonitoringPolling();
    setDisplay('sidebar-topology', 'block');
    setDisplay('page-topology', 'flex');
    if (typeof restoreTopologyStatus === 'function') restoreTopologyStatus();
    else setText('status-msg', 'Connect to F5, then enter an IP.');
    setDisplay('bulk-header-container', 'flex');
    if (typeof loadTopologyDeviceOptions === 'function') loadTopologyDeviceOptions();
  } else if (tab === 'devices') {
    if (typeof stopMonitoringPolling === 'function') stopMonitoringPolling();
    setDisplay('sidebar-devices', 'block');
    setDisplay('page-devices', 'block');
    setText('status-msg', 'Device Management');
    loadDevices();
  } else if (tab === 'inventory') {
    if (typeof stopMonitoringPolling === 'function') stopMonitoringPolling();
    setDisplay('sidebar-inventory', 'block');
    setDisplay('page-inventory', 'block');
    setText('status-msg', 'IP Inventory');
    loadInventoryDeviceOptions();
  } else if (tab === 'monitoring') {
    setDisplay('sidebar-monitoring', 'block');
    setDisplay('page-monitoring', 'block');
    setDisplay('topbar', 'none');
    setText('status-msg', 'VS Connection Monitor');
    if (typeof initMonitoringPage === 'function') initMonitoringPage();
  }
}

// Toast helpers reused from app.js.
function invToast(msg, type) {
  if (typeof toast === 'function') {
    toast(msg, type);
  } else {
    console.log(`[${type}] ${msg}`);
  }
}

// ─── Format Date ───────────────────────────────────────────────────────────────
function fmtDate(iso) {
  if (!iso) return '-';
  try {
    return new Date(iso).toLocaleString('id-ID', {
      day: '2-digit', month: '2-digit', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  } catch {
    return iso;
  }
}

// ─── Status Badge ──────────────────────────────────────────────────────────────
function statusBadge(status, deviceId = '') {
  const attr = deviceId ? `data-device-status data-device-id="${deviceId}"` : '';

  if (!status || status === 'NEVER') {
    return `<span ${attr} class="badge badge-muted">NEVER</span>`;
  }

  if (status === 'OK') {
    return `<span ${attr} class="badge badge-ok">OK</span>`;
  }

  if (status === 'FAILED') {
    return `<span ${attr} class="badge badge-err">FAILED</span>`;
  }

  if (status === 'SYNCING') {
    return `<span ${attr} class="badge badge-syncing">SYNCING</span>`;
  }

  return `<span ${attr} class="badge badge-muted">${escHtml(status)}</span>`;
}

function updateDeviceStatusBadge(deviceId, status) {
  _deviceList = _deviceList.map(d => (
    d.id === deviceId ? { ...d, last_status: status } : d
  ));

  const el = document.querySelector(`[data-device-status][data-device-id="${deviceId}"]`);
  if (!el) return;

  const wrap = document.createElement('template');
  wrap.innerHTML = statusBadge(status, deviceId).trim();
  el.replaceWith(wrap.content.firstElementChild);
}

async function openTopologyFromInventory(hostname, ip) {
  if (!hostname) {
    invToast('Invalid F5 hostname', 'err');
    return;
  }

  invToast(`Loading config ${hostname}...`, '');

  try {
    const r = await fetch(
      `${INV_API}/devices/topology-config/by-hostname/${encodeURIComponent(hostname)}`
    );
    const data = await r.json();
    if (!r.ok) {
      throw new Error(data.detail || 'Device not found');
    }

    document.getElementById('inp-host').value = data.management_ip || '';
    document.getElementById('inp-user').value = data.username || '';
    document.getElementById('inp-pass').value = data.password || '';
    document.getElementById('chk-ssl').checked = Boolean(data.verify_ssl);
    document.getElementById('inp-ip').value = ip || '';
    if (typeof topologyConnectionLabel !== 'undefined') {
      topologyConnectionLabel = data.hostname || data.name || hostname;
    }

    switchTab('topology');
    if (typeof testConnection === 'function') {
      await testConnection();
    }
    if (ip && typeof doSearch === 'function') {
      doSearch();
    }
  } catch (e) {
    invToast('Failed to login topology: ' + e.message, 'err');
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// DEVICES
// ═══════════════════════════════════════════════════════════════════════════════

let _deviceList = [];

async function loadDevices() {
  const tbody = document.getElementById('devices-tbody');
  tbody.innerHTML = `<tr><td colspan="6" class="inv-empty">Loading...</td></tr>`;

  try {
    const r = await fetch(`${INV_API}/devices`);
    const data = await r.json();
    _deviceList = data;
    renderDevicesTable(data);
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="6" class="inv-empty inv-err">Failed to load devices: ${e.message}</td></tr>`;
  }
}

function renderDevicesTable(devices) {
  const tbody = document.getElementById('devices-tbody');
  if (!devices || devices.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" class="inv-empty">No devices yet. Click "+ Add Device" to add one.</td></tr>`;
    return;
  }

  tbody.innerHTML = devices.map(d => `
    <tr id="device-row-${d.id}">
      <td class="inv-td-name">
        <span class="device-name">${escHtml(d.name)}</span>
        ${d.enabled ? '' : '<span class="badge badge-muted ml-4">Disabled</span>'}
      </td>
      <td class="inv-td-mono">${escHtml(d.management_ip)}</td>
      <td>${escHtml(d.username)}</td>
      <td>${statusBadge(d.last_status, d.id)}</td>
      <td class="inv-td-date">${fmtDate(d.last_sync)}</td>
      <td class="inv-td-actions">
        <button class="btn btn-sm" onclick="showDeviceForm(${d.id})" title="Edit">Edit</button>
        <button class="btn btn-sm" data-device-sync data-device-id="${d.id}" onclick="syncDeviceById(${d.id})" title="Sync">Sync</button>
        <button class="btn btn-sm btn-danger-sm" onclick="deleteDevice(${d.id}, '${escHtml(d.name)}')" title="Delete">Del</button>
      </td>
    </tr>
  `).join('');
}

function showDeviceForm(deviceId) {
  const wrap = document.getElementById('device-form-wrap');
  const title = document.getElementById('device-form-title');
  const testBtn = document.getElementById('dform-test-btn');

  document.getElementById('device-form-id').value = deviceId || '';
  document.getElementById('dform-name').value   = '';
  document.getElementById('dform-ip').value     = '';
  document.getElementById('dform-user').value   = 'admin';
  document.getElementById('dform-pass').value   = '';
  document.getElementById('dform-ssl').checked  = false;
  document.getElementById('dform-enabled').checked = true;
  document.getElementById('dform-status').textContent = '';

  if (deviceId) {
    const d = _deviceList.find(x => x.id === deviceId);
    if (d) {
      title.textContent = `Edit Device — ${d.name}`;
      document.getElementById('dform-name').value    = d.name;
      document.getElementById('dform-ip').value      = d.management_ip;
      document.getElementById('dform-user').value    = d.username;
      document.getElementById('dform-ssl').checked   = d.verify_ssl;
      document.getElementById('dform-enabled').checked = d.enabled;
    }
    testBtn.style.display = 'inline-block';
  } else {
    title.textContent = 'Add New Device';
    testBtn.style.display = 'none';
  }

  wrap.style.display = 'block';
  document.getElementById('dform-name').focus();
}

function cancelDeviceForm() {
  document.getElementById('device-form-wrap').style.display = 'none';
}

async function saveDevice() {
  const id       = document.getElementById('device-form-id').value;
  const name     = document.getElementById('dform-name').value.trim();
  const ip       = document.getElementById('dform-ip').value.trim();
  const user     = document.getElementById('dform-user').value.trim();
  const pass     = document.getElementById('dform-pass').value;
  const ssl      = document.getElementById('dform-ssl').checked;
  const enabled  = document.getElementById('dform-enabled').checked;
  const statusEl = document.getElementById('dform-status');

  if (!name || !ip || !user) {
    statusEl.textContent = 'Name, IP, and Username are required.';
    statusEl.className = 'dform-status dform-err';
    return;
  }

  if (!id && !pass) {
    statusEl.textContent = 'Password is required for a new device.';
    statusEl.className = 'dform-status dform-err';
    return;
  }

  statusEl.textContent = 'Saving...';
  statusEl.className = 'dform-status';

  const body = { name, management_ip: ip, username: user, verify_ssl: ssl, enabled };
  if (pass) body.password = pass;

  try {
    const url    = id ? `${INV_API}/devices/${id}` : `${INV_API}/devices`;
    const method = id ? 'PUT' : 'POST';
    if (!id && !body.password) body.password = pass || ''; // POST needs password

    const r = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await r.json();
    if (!r.ok) {
      throw new Error(data.detail || JSON.stringify(data));
    }

    statusEl.textContent = id ? 'Device updated!' : 'Device added!';
    statusEl.className = 'dform-status dform-ok';
    invToast(id ? 'Device updated' : 'Device added', 'ok');

    setTimeout(() => {
      cancelDeviceForm();
      loadDevices();
    }, 800);
  } catch (e) {
    statusEl.textContent = 'Error: ' + e.message;
    statusEl.className = 'dform-status dform-err';
  }
}

async function deleteDevice(id, name) {
  if (!confirm(`Delete device "${name}" and its inventory?`)) return;

  try {
    const r = await fetch(`${INV_API}/devices/${id}`, { method: 'DELETE' });
    if (r.status === 204 || r.ok) {
      invToast(`Device "${name}" deleted`, 'ok');
      loadDevices();
    } else {
      const d = await r.json();
      invToast('Delete failed: ' + (d.detail || r.status), 'err');
    }
  } catch (e) {
    invToast('Error: ' + e.message, 'err');
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// BULK UPDATE PASSWORD
// ═══════════════════════════════════════════════════════════════════════════════

function showBulkPasswordModal() {
  const modal = document.getElementById('bulk-password-modal');
  const targetsWrap = document.getElementById('bulk-pwd-targets');
  const statusEl = document.getElementById('bulk-pwd-status');
  const input = document.getElementById('bulk-pwd-input');

  // Reset state
  input.value = '';
  input.type = 'password';
  statusEl.textContent = '';
  statusEl.className = 'dform-status';

  // Render device checkboxes
  if (_deviceList.length === 0) {
    targetsWrap.innerHTML = '<div class="bulk-modal-no-device">No devices yet. Add a device first.</div>';
  } else {
    targetsWrap.innerHTML = `
      <div class="bulk-modal-targets-label">Select devices to update:</div>
      <div class="bulk-device-list">
        ${_deviceList.map(d => `
          <label class="bulk-device-item">
            <input type="checkbox" class="bulk-dev-chk" value="${d.id}" checked/>
            <span class="bulk-dev-name">${escHtml(d.name)}</span>
            <span class="bulk-dev-ip inv-td-mono">${escHtml(d.management_ip)}</span>
          </label>
        `).join('')}
      </div>
    `;
  }

  modal.style.display = 'flex';
  setTimeout(() => input.focus(), 50);
}

function hideBulkPasswordModal(e) {
  // Close only when the overlay is clicked.
  if (e && e.target !== document.getElementById('bulk-password-modal')) return;
  document.getElementById('bulk-password-modal').style.display = 'none';
}

function selectAllBulkDevices(checked) {
  document.querySelectorAll('.bulk-dev-chk').forEach(chk => {
    chk.checked = checked;
  });
}

function toggleBulkPassVis() {
  const inp = document.getElementById('bulk-pwd-input');
  inp.type = inp.type === 'password' ? 'text' : 'password';
}

async function saveBulkPassword() {
  const password = document.getElementById('bulk-pwd-input').value;
  const statusEl = document.getElementById('bulk-pwd-status');
  const saveBtn  = document.getElementById('bulk-pwd-save-btn');

  if (!password) {
    statusEl.textContent = 'Password cannot be empty.';
    statusEl.className = 'dform-status dform-err';
    return;
  }

  // Collect selected device IDs.
  const checkedIds = Array.from(document.querySelectorAll('.bulk-dev-chk:checked'))
    .map(chk => parseInt(chk.value, 10));

  if (checkedIds.length === 0) {
    statusEl.textContent = 'Select at least one device.';
    statusEl.className = 'dform-status dform-err';
    return;
  }

  const totalDevices = _deviceList.length;
  const isAll = checkedIds.length === totalDevices;
  const confirmMsg = isAll
    ? `Update password for ALL ${totalDevices} devices?`
    : `Update password for ${checkedIds.length} selected devices?`;

  if (!confirm(confirmMsg)) return;

  statusEl.textContent = 'Saving...';
  statusEl.className = 'dform-status';
  saveBtn.disabled = true;

  try {
    const body = { password };
    // Send device_ids only when not all devices are selected.
    if (!isAll) body.device_ids = checkedIds;

    const r = await fetch(`${INV_API}/devices/bulk-update-password`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await r.json();

    if (!r.ok) {
      throw new Error(data.detail || JSON.stringify(data));
    }

    statusEl.textContent = `Password updated on ${data.updated} devices!`;
    statusEl.className = 'dform-status dform-ok';
    invToast(`Password updated on ${data.updated} devices`, 'ok');

    setTimeout(() => {
      document.getElementById('bulk-password-modal').style.display = 'none';
      loadDevices();
    }, 1200);
  } catch (e) {
    statusEl.textContent = 'Error: ' + e.message;
    statusEl.className = 'dform-status dform-err';
  } finally {
    saveBtn.disabled = false;
  }
}

async function testDeviceFormConnection() {
  const id = document.getElementById('device-form-id').value;
  if (!id) return;

  const statusEl = document.getElementById('dform-status');
  statusEl.textContent = 'Testing connection...';
  statusEl.className = 'dform-status';

  const btn = document.getElementById('dform-test-btn');
  btn.disabled = true;

  try {
    const r = await fetch(`${INV_API}/devices/${id}/test-connection`, { method: 'POST' });
    const data = await r.json().catch(() => null);
    if (r.ok && data && data.ok) {
      statusEl.textContent = `✓ Connected — ${data.version || data.host}`;
      statusEl.className = 'dform-status dform-ok';
    } else {
      let errMsg = 'Connection failed';
      if (data) {
        if (data.error) {
          errMsg = data.error;
        } else if (data.detail) {
          if (Array.isArray(data.detail)) {
            errMsg = data.detail.map(err => err.msg || JSON.stringify(err)).join(', ');
          } else {
            errMsg = data.detail;
          }
        }
      } else if (!r.ok) {
        errMsg = `HTTP ${r.status}`;
      }
      statusEl.textContent = `✗ ${errMsg}`;
      statusEl.className = 'dform-status dform-err';
    }
  } catch (e) {
    statusEl.textContent = '✗ ' + e.message;
    statusEl.className = 'dform-status dform-err';
  } finally {
    btn.disabled = false;
  }
}

async function syncDeviceById(id) {
  const d = _deviceList.find(x => x.id === id);
  const name = d ? d.name : `ID ${id}`;
  const btn = document.querySelector(`[data-device-sync][data-device-id="${id}"]`);

  invToast(`Syncing ${name}...`, '');
  updateDeviceStatusBadge(id, 'SYNCING');
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Syncing...';
  }

  try {
    const r = await fetch(`${INV_API}/sync/device/${id}`, { method: 'POST' });
    const data = await r.json().catch(() => null);

    if (r.ok && data && data.status === 'OK') {
      invToast(
        `✓ ${name}: ${data.vs_ip_synced} Virtual Server, ${data.pool_member_ip_synced || data.node_ip_synced || 0} Pool Member, ${data.self_ip_synced || 0} Self IP, ${data.forwarding_vs_skipped} fwd skipped`,
        'ok'
      );
    } else {
      let errMsg = 'FAILED';
      if (data) {
        if (data.error) {
          errMsg = data.error;
        } else if (data.detail) {
          if (Array.isArray(data.detail)) {
            errMsg = data.detail.map(err => err.msg || JSON.stringify(err)).join(', ');
          } else {
            errMsg = data.detail;
          }
        }
      } else if (!r.ok) {
        errMsg = `HTTP ${r.status}`;
      }
      invToast(`✗ ${name}: ${errMsg}`, 'err');
    }
  } catch (e) {
    invToast('Sync error: ' + e.message, 'err');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Sync';
    }
    loadDevices();
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// INVENTORY — SYNC
// ═══════════════════════════════════════════════════════════════════════════════

async function syncAll() {
  const btn = document.getElementById('btn-sync-all');
  if (!btn) return;

  const startTime = performance.now();
  let timer = null;

  btn.disabled = true;

  // Update device status in memory
  _deviceList = _deviceList.map(d => ({
    ...d,
    last_status: 'SYNCING'
  }));

  // Update device status badges currently visible in the Devices table.
  document.querySelectorAll('[data-device-status]').forEach(el => {
    el.textContent = 'SYNCING';
    el.className = 'badge badge-syncing';
  });

  // Show live elapsed time on the Sync All button
  timer = setInterval(() => {
    const elapsedSec = ((performance.now() - startTime) / 1000).toFixed(1);
    btn.textContent = `Syncing... ${elapsedSec}s`;
  }, 200);

  try {
    const r = await fetch(`${INV_API}/sync/all`, { method: 'POST' });
    const data = await r.json().catch(() => null);

    if (!r.ok) {
      throw new Error(data?.detail || data?.error || `HTTP ${r.status}`);
    }

    const elapsedSec = ((performance.now() - startTime) / 1000).toFixed(2);

    invToast(
      `Sync completed in ${elapsedSec}s: ${data.success}/${data.total_devices} OK`,
      data.failed > 0 ? 'err' : 'ok'
    );

    await loadDevices();

  } catch (e) {
    const elapsedSec = ((performance.now() - startTime) / 1000).toFixed(2);

    invToast(`Sync failed after ${elapsedSec}s: ${e.message}`, 'err');

    await loadDevices();

  } finally {
    if (timer) clearInterval(timer);

    btn.disabled = false;
    btn.textContent = 'Sync All';
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// INVENTORY — SEARCH
// ═══════════════════════════════════════════════════════════════════════════════

async function searchInventory() {
  const ip = (document.getElementById('inv-search-ip').value || '').trim();
  if (!ip) { invToast('Enter an IP first', 'err'); return; }

  const panel = document.getElementById('inv-search-result');
  const label = document.getElementById('inv-search-label');
  const wrap  = document.getElementById('inv-search-table-wrap');

  panel.style.display = 'block';
  label.textContent   = `Searching "${ip}"...`;
  wrap.innerHTML      = '';

  try {
    const r = await fetch(`${INV_API}/inventory/search?ip=${encodeURIComponent(ip)}`);
    const data = await r.json();

    if (!data.results || data.results.length === 0) {
      label.textContent = `IP "${ip}" was not found in inventory`;
      wrap.innerHTML    = '<div class="inv-not-found">No data for this IP.</div>';
      return;
    }

    label.textContent = `Found ${data.results.length} records for IP "${ip}"`;
    wrap.innerHTML = `
      <div class="inv-table-wrap">
        <table class="inv-table">
          <thead>
            <tr><th>Hostname F5</th><th>IP</th><th>Port</th><th>Type</th><th>Last Seen</th></tr>
          </thead>
          <tbody>
            ${data.results.map(r => `
              <tr>
                <td class="inv-td-name">${inventoryHostLink(r.hostname, r.ip)}</td>
                <td class="inv-td-mono">${escHtml(r.ip)}</td>
                <td class="inv-td-mono">${escHtml(r.port || '-')}</td>
                <td>${typeBadge(r.type)}</td>
                <td class="inv-td-date">${fmtDate(r.last_seen)}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    `;
  } catch (e) {
    label.textContent = 'Search error';
    wrap.innerHTML = `<div class="inv-err">${escHtml(e.message)}</div>`;
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// INVENTORY — LOAD ALL & CLEAR
// ═══════════════════════════════════════════════════════════════════════════════

async function loadInventoryDeviceOptions() {
  const deviceInput = document.getElementById('inv-device-select');
  const deviceList = document.getElementById('inv-device-list');
  const exportList = document.getElementById('inv-export-device-list');
  if (!deviceList && !exportList) return;
  if (deviceInput) deviceInput.placeholder = 'Loading hostname...';

  try {
    const r = await fetch(`${INV_API}/devices`);
    const devices = await r.json();
    _deviceList = devices;

    const hostnameOptions = devices
      .map(d => (d.hostname || '').trim())
      .filter(Boolean)
      .sort((a, b) => a.localeCompare(b))
      .map(hostname => `<option value="${escAttr(hostname)}"></option>`)
      .join('');

    if (deviceList) deviceList.innerHTML = hostnameOptions;
    if (exportList) {
      exportList.innerHTML = `<option value="All Devices"></option>${hostnameOptions}`;
    }
    if (deviceInput) deviceInput.placeholder = 'Hostname...';
  } catch (e) {
    if (deviceInput) deviceInput.placeholder = 'Failed to load hostnames';
    invToast('Failed to load device list: ' + e.message, 'err');
  }
}

function findDeviceByHostnameInput(inputId, allowAll) {
  const input = document.getElementById(inputId);
  const value = input ? input.value.trim() : '';

  if (allowAll && value.toLowerCase() === 'all devices') {
    return null;
  }

  if (!value) {
    return undefined;
  }

  return _deviceList.find(d => (d.hostname || '').trim().toLowerCase() === value.toLowerCase());
}

async function loadInventoryForSelected() {
  const device = findDeviceByHostnameInput('inv-device-select', false);
  if (device === undefined) {
    invToast('Select a device first', 'err');
    return;
  }
  if (!device) {
    invToast('Select a hostname from the device list', 'err');
    return;
  }

  const deviceId = device.id;
  const deviceName = device.hostname;

  const wrap  = document.getElementById('inv-all-wrap');
  const tbody = document.getElementById('inv-all-tbody');
  const count = document.getElementById('inv-all-count');
  const title = document.getElementById('inv-table-title');

  wrap.style.display = 'block';
  tbody.innerHTML = '<tr><td colspan="5" class="inv-empty">Loading...</td></tr>';
  if (title) title.textContent = `Inventory — ${deviceName}`;

  try {
    const r = await fetch(`${INV_API}/inventory/all?device_id=${deviceId}&limit=2000`);
    const data = await r.json();

    count.textContent = `${data.length} records`;

    if (!data.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="inv-empty">Inventory is empty for this device. Run Sync first.</td></tr>';
      return;
    }

    tbody.innerHTML = data.map((item, i) => `
      <tr>
        <td class="inv-td-num">${i + 1}</td>
        <td class="inv-td-name">${inventoryHostLink(item.hostname, item.ip)}</td>
        <td class="inv-td-mono">${escHtml(item.ip)}</td>
        <td class="inv-td-mono">${escHtml(item.port || '-')}</td>
        <td>${typeBadge(item.type)}</td>
        <td class="inv-td-date">${fmtDate(item.last_seen)}</td>
      </tr>
    `).join('');
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="5" class="inv-empty inv-err">Error: ${escHtml(e.message)}</td></tr>`;
  }
}

function exportInventory() {
  const device = findDeviceByHostnameInput('inv-export-device-select', true);
  const params = new URLSearchParams();

  if (device === undefined) {
    invToast('Select a device first', 'err');
    return;
  }

  if (device) {
    params.set('device_id', device.id);
  }

  const suffix = params.toString() ? `?${params.toString()}` : '';
  const scope = device ? device.hostname : 'all devices';

  invToast(`Export XLSX ${scope}...`, '');
  window.location.href = `${INV_API}/inventory/export.xlsx${suffix}`;
}

async function clearInventoryForSelected() {
  const device = findDeviceByHostnameInput('inv-device-select', false);
  if (device === undefined) {
    invToast('Select a device first', 'err');
    return;
  }
  if (!device) {
    invToast('Select a hostname from the device list', 'err');
    return;
  }

  const deviceId = device.id;
  const deviceName = device.hostname;

  if (!confirm(`Delete inventory data for device "${deviceName}"? This cannot be undone.`)) return;

  try {
    const r = await fetch(`${INV_API}/inventory/clear?device_id=${deviceId}`, { method: 'DELETE' });
    const data = await r.json();
    if (!r.ok) {
      throw new Error(data.detail || 'Delete failed');
    }
    invToast(data.message || 'Device inventory cleared', 'ok');
    document.getElementById('inv-all-wrap').style.display = 'none';
    document.getElementById('inv-all-tbody').innerHTML = '';
    document.getElementById('inv-all-count').textContent = '';
  } catch (e) {
    invToast('Failed to clear inventory: ' + e.message, 'err');
  }
}

async function clearAllInventory() {
  if (!confirm('Delete all inventory data? This cannot be undone.')) return;

  try {
    const r = await fetch(`${INV_API}/inventory/clear`, { method: 'DELETE' });
    const data = await r.json();
    if (!r.ok) {
      throw new Error(data.detail || 'Delete failed');
    }

    invToast(data.message || 'All inventory cleared', 'ok');
    document.getElementById('inv-search-result').style.display = 'none';
    document.getElementById('inv-search-label').textContent = '';
    document.getElementById('inv-search-table-wrap').innerHTML = '';
    document.getElementById('inv-all-wrap').style.display = 'none';
    document.getElementById('inv-all-tbody').innerHTML = '';
    document.getElementById('inv-all-count').textContent = '';
  } catch (e) {
    invToast('Failed to clear all inventory: ' + e.message, 'err');
  }
}

// ─── Helpers ───────────────────────────────────────────────────────────────────

function escHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function escAttr(str) {
  return escHtml(str).replace(/'/g, '&#39;');
}

function inventoryHostLink(hostname, ip) {
  const url = `/?hostname=${encodeURIComponent(hostname)}${ip ? '&ip=' + encodeURIComponent(ip) : ''}`;
  return `
    <div class="inv-host-link-wrapper" style="display: inline-flex; align-items: center; gap: 6px; max-width: 100%;">
      <a
        href="${url}"
        class="inv-host-link"
        onclick="event.preventDefault(); openTopologyFromInventory('${escAttr(hostname)}', '${escAttr(ip)}')"
        title="Load in Topology in this tab"
      >${escHtml(hostname)}</a>
      <a
        href="${url}"
        target="_blank"
        class="inv-host-newtab"
        title="Open in a new tab"
        onclick="event.stopPropagation();"
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: block;"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></svg>
      </a>
    </div>
  `;
}

function typeBadge(type) {
  if (type === 'VS')   return `<span class="badge badge-vs">Virtual Server</span>`;
  if (type === 'NODE') return `<span class="badge badge-node">NODE</span>`;
  if (type === 'POOL_MEMBER') return `<span class="badge badge-pool-member">POOL MEMBER</span>`;
  if (type === 'SELF_IP') return `<span class="badge badge-self-ip">SELF IP</span>`;
  return `<span class="badge badge-muted">${escHtml(type)}</span>`;
}

// Enter key di search box
document.addEventListener('DOMContentLoaded', () => {
  const inp = document.getElementById('inv-search-ip');
  if (inp) {
    inp.addEventListener('keydown', e => {
      if (e.key === 'Enter') searchInventory();
    });
  }

  switchTab(tabFromRoute(), { replace: true });
  window.addEventListener('popstate', () => {
    switchTab(tabFromRoute(), { replace: true });
  });

  // Parse query parameters
  const urlParams = new URLSearchParams(window.location.search);
  const qHostname = urlParams.get('hostname');
  const qIp = urlParams.get('ip');
  if (qHostname) {
    setTimeout(() => {
      openTopologyFromInventory(qHostname, qIp);
    }, 200);
  }
});
