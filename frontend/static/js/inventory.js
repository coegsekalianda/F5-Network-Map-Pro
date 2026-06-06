/**
 * inventory.js — Frontend logic untuk halaman Devices & Inventory
 * 
 * Halaman:
 *   - Devices    : CRUD device F5, test connection, sync per device
 *   - Inventory  : Sync All, Search IP, Load All
 */

const INV_API = '';  // relatif ke origin yang sama

// ─── Tab Navigation ────────────────────────────────────────────────────────────

function switchTab(tab) {
  // Sembunyikan semua sidebar panels & pages
  document.getElementById('sidebar-topology').style.display = 'none';
  document.getElementById('sidebar-devices').style.display  = 'none';
  document.getElementById('sidebar-inventory').style.display = 'none';

  document.getElementById('page-topology').style.display  = 'none';
  document.getElementById('page-devices').style.display   = 'none';
  document.getElementById('page-inventory').style.display = 'none';

  // Reset topbar
  const topbar = document.getElementById('topbar');
  document.getElementById('status-msg').textContent = '';
  document.getElementById('bulk-header-container').style.display = 'none';

  // Hapus active dari semua tab
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('tab-active'));
  document.getElementById(`tab-${tab}`).classList.add('tab-active');

  if (tab === 'topology') {
    document.getElementById('sidebar-topology').style.display = 'block';
    document.getElementById('page-topology').style.display    = 'flex';
    document.getElementById('status-msg').textContent = 'Hubungkan ke F5, lalu masukkan IP.';
    document.getElementById('bulk-header-container').style.display = 'flex';
  } else if (tab === 'devices') {
    document.getElementById('sidebar-devices').style.display = 'block';
    document.getElementById('page-devices').style.display    = 'block';
    document.getElementById('status-msg').textContent = 'Device Management';
    loadDevices();
  } else if (tab === 'inventory') {
    document.getElementById('sidebar-inventory').style.display = 'block';
    document.getElementById('page-inventory').style.display    = 'block';
    document.getElementById('status-msg').textContent = 'IP Inventory';
    loadInventoryDeviceOptions();
  }
}

// ─── Toast (reuse dari app.js) ─────────────────────────────────────────────────
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
function statusBadge(status) {
  if (!status || status === 'NEVER') return `<span class="badge badge-muted">NEVER</span>`;
  if (status === 'OK')               return `<span class="badge badge-ok">OK</span>`;
  if (status === 'FAILED')           return `<span class="badge badge-err">FAILED</span>`;
  return `<span class="badge badge-muted">${status}</span>`;
}

async function openTopologyFromInventory(hostname, ip) {
  if (!hostname) {
    invToast('Hostname F5 tidak valid', 'err');
    return;
  }

  invToast(`Muat config ${hostname}...`, '');

  try {
    const r = await fetch(
      `${INV_API}/devices/topology-config/by-hostname/${encodeURIComponent(hostname)}`
    );
    const data = await r.json();
    if (!r.ok) {
      throw new Error(data.detail || 'Device tidak ditemukan');
    }

    document.getElementById('inp-host').value = data.management_ip || '';
    document.getElementById('inp-user').value = data.username || '';
    document.getElementById('inp-pass').value = data.password || '';
    document.getElementById('chk-ssl').checked = Boolean(data.verify_ssl);
    document.getElementById('inp-ip').value = ip || '';

    switchTab('topology');
    document.getElementById('conn-status').textContent = `Siap konek ke ${data.name || hostname}`;
    setStatus(`Config ${data.name || hostname} sudah dimuat. Klik Connect jika ingin login.`, '');
    invToast(`Config ${data.name || hostname} dimuat`, 'ok');
  } catch (e) {
    invToast('Gagal login topology: ' + e.message, 'err');
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
    tbody.innerHTML = `<tr><td colspan="6" class="inv-empty inv-err">Gagal memuat device: ${e.message}</td></tr>`;
  }
}

function renderDevicesTable(devices) {
  const tbody = document.getElementById('devices-tbody');
  if (!devices || devices.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" class="inv-empty">Belum ada device. Klik "+ Add Device" untuk menambahkan.</td></tr>`;
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
      <td>${statusBadge(d.last_status)}</td>
      <td class="inv-td-date">${fmtDate(d.last_sync)}</td>
      <td class="inv-td-actions">
        <button class="btn btn-sm" onclick="showDeviceForm(${d.id})" title="Edit">Edit</button>
        <button class="btn btn-sm" onclick="syncDeviceById(${d.id})" title="Sync">Sync</button>
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
    title.textContent = 'Tambah Device Baru';
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
    statusEl.textContent = 'Name, IP, dan Username wajib diisi.';
    statusEl.className = 'dform-status dform-err';
    return;
  }

  if (!id && !pass) {
    statusEl.textContent = 'Password wajib diisi untuk device baru.';
    statusEl.className = 'dform-status dform-err';
    return;
  }

  statusEl.textContent = 'Menyimpan...';
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

    statusEl.textContent = id ? 'Device berhasil diupdate!' : 'Device berhasil ditambahkan!';
    statusEl.className = 'dform-status dform-ok';
    invToast(id ? 'Device diupdate' : 'Device ditambahkan', 'ok');

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
  if (!confirm(`Hapus device "${name}"?`)) return;

  try {
    const r = await fetch(`${INV_API}/devices/${id}`, { method: 'DELETE' });
    if (r.status === 204 || r.ok) {
      invToast(`Device "${name}" dihapus`, 'ok');
      loadDevices();
    } else {
      const d = await r.json();
      invToast('Gagal hapus: ' + (d.detail || r.status), 'err');
    }
  } catch (e) {
    invToast('Error: ' + e.message, 'err');
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
    const data = await r.json();
    if (data.ok) {
      statusEl.textContent = `✓ Connected — ${data.version || data.host}`;
      statusEl.className = 'dform-status dform-ok';
    } else {
      statusEl.textContent = `✗ ${data.error}`;
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

  invToast(`Syncing ${name}...`, '');

  try {
    const r = await fetch(`${INV_API}/sync/device/${id}`, { method: 'POST' });
    const data = await r.json();

    if (data.status === 'OK') {
      invToast(
        `✓ ${name}: ${data.vs_ip_synced} Virtual Server, ${data.pool_member_ip_synced || data.node_ip_synced || 0} Pool Member, ${data.self_ip_synced || 0} Self IP, ${data.forwarding_vs_skipped} fwd skipped`,
        'ok'
      );
    } else {
      invToast(`✗ ${name}: ${data.error || 'FAILED'}`, 'err');
    }
    loadDevices();
  } catch (e) {
    invToast('Sync error: ' + e.message, 'err');
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// INVENTORY — SYNC
// ═══════════════════════════════════════════════════════════════════════════════

async function syncAll() {
  const btn = document.getElementById('btn-sync-all');
  if (!btn) return;
  btn.disabled = true;
  btn.textContent = '⟳ Syncing...';

  try {
    const r = await fetch(`${INV_API}/sync/all`, { method: 'POST' });
    const data = await r.json();
    invToast(
      `Sync selesai: ${data.success}/${data.total_devices} OK - ${data.vs_ip_synced || 0} Virtual Server, ${data.pool_member_ip_synced || data.node_ip_synced || 0} Pool Member, ${data.self_ip_synced || 0} Self IP`,
      data.failed > 0 ? 'err' : 'ok'
    );
    loadDevices(); // Refresh status device di tabel management
  } catch (e) {
    invToast('Sync gagal: ' + e.message, 'err');
  } finally {
    btn.disabled = false;
    btn.textContent = '⟳ Sync All';
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// INVENTORY — SEARCH
// ═══════════════════════════════════════════════════════════════════════════════

async function searchInventory() {
  const ip = (document.getElementById('inv-search-ip').value || '').trim();
  if (!ip) { invToast('Masukkan IP terlebih dahulu', 'err'); return; }

  const panel = document.getElementById('inv-search-result');
  const label = document.getElementById('inv-search-label');
  const wrap  = document.getElementById('inv-search-table-wrap');

  panel.style.display = 'block';
  label.textContent   = `Mencari "${ip}"...`;
  wrap.innerHTML      = '';

  try {
    const r = await fetch(`${INV_API}/inventory/search?ip=${encodeURIComponent(ip)}`);
    const data = await r.json();

    if (!data.results || data.results.length === 0) {
      label.textContent = `IP "${ip}" tidak ditemukan di inventory`;
      wrap.innerHTML    = '<div class="inv-not-found">Tidak ada data untuk IP ini.</div>';
      return;
    }

    label.textContent = `Ditemukan ${data.results.length} record untuk IP "${ip}"`;
    wrap.innerHTML = `
      <div class="inv-table-wrap">
        <table class="inv-table">
          <thead>
            <tr><th>Hostname F5</th><th>IP</th><th>Type</th><th>Last Seen</th></tr>
          </thead>
          <tbody>
            ${data.results.map(r => `
              <tr>
                <td class="inv-td-name">${inventoryHostLink(r.hostname, r.ip)}</td>
                <td class="inv-td-mono">${escHtml(r.ip)}</td>
                <td>${typeBadge(r.type)}</td>
                <td class="inv-td-date">${fmtDate(r.last_seen)}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    `;
  } catch (e) {
    label.textContent = 'Error saat search';
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
    if (deviceInput) deviceInput.placeholder = 'Gagal memuat hostname';
    invToast('Gagal memuat list device: ' + e.message, 'err');
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
    invToast('Pilih device terlebih dahulu', 'err');
    return;
  }
  if (!device) {
    invToast('Pilih hostname dari daftar device', 'err');
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
      tbody.innerHTML = '<tr><td colspan="5" class="inv-empty">Inventory kosong untuk device ini. Lakukan Sync terlebih dahulu.</td></tr>';
      return;
    }

    tbody.innerHTML = data.map((item, i) => `
      <tr>
        <td class="inv-td-num">${i + 1}</td>
        <td class="inv-td-name">${inventoryHostLink(item.hostname, item.ip)}</td>
        <td class="inv-td-mono">${escHtml(item.ip)}</td>
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
    invToast('Pilih device terlebih dahulu', 'err');
    return;
  }

  if (device) {
    params.set('device_id', device.id);
  }

  const suffix = params.toString() ? `?${params.toString()}` : '';
  const scope = device ? device.hostname : 'semua device';

  invToast(`Export XLSX ${scope}...`, '');
  window.location.href = `${INV_API}/inventory/export.xlsx${suffix}`;
}

async function clearInventoryForSelected() {
  const device = findDeviceByHostnameInput('inv-device-select', false);
  if (device === undefined) {
    invToast('Pilih device terlebih dahulu', 'err');
    return;
  }
  if (!device) {
    invToast('Pilih hostname dari daftar device', 'err');
    return;
  }

  const deviceId = device.id;
  const deviceName = device.hostname;

  if (!confirm(`Yakin ingin menghapus data inventory untuk device "${deviceName}"? Aksi ini tidak bisa dibatalkan.`)) return;

  try {
    const r = await fetch(`${INV_API}/inventory/clear?device_id=${deviceId}`, { method: 'DELETE' });
    const data = await r.json();
    if (!r.ok) {
      throw new Error(data.detail || 'Gagal hapus');
    }
    invToast(data.message || 'Inventory device berhasil dikosongkan', 'ok');
    document.getElementById('inv-all-wrap').style.display = 'none';
    document.getElementById('inv-all-tbody').innerHTML = '';
    document.getElementById('inv-all-count').textContent = '';
  } catch (e) {
    invToast('Gagal hapus inventory: ' + e.message, 'err');
  }
}

async function clearAllInventory() {
  if (!confirm('Yakin ingin menghapus semua data inventory? Aksi ini tidak bisa dibatalkan.')) return;

  try {
    const r = await fetch(`${INV_API}/inventory/clear`, { method: 'DELETE' });
    const data = await r.json();
    if (!r.ok) {
      throw new Error(data.detail || 'Gagal hapus');
    }

    invToast(data.message || 'Semua inventory berhasil dikosongkan', 'ok');
    document.getElementById('inv-search-result').style.display = 'none';
    document.getElementById('inv-search-label').textContent = '';
    document.getElementById('inv-search-table-wrap').innerHTML = '';
    document.getElementById('inv-all-wrap').style.display = 'none';
    document.getElementById('inv-all-tbody').innerHTML = '';
    document.getElementById('inv-all-count').textContent = '';
  } catch (e) {
    invToast('Gagal hapus semua inventory: ' + e.message, 'err');
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
  return `
    <button
      type="button"
      class="inv-host-link"
      data-hostname="${escAttr(hostname)}"
      data-ip="${escAttr(ip)}"
      onclick="openTopologyFromInventory(this.dataset.hostname, this.dataset.ip)"
      title="Muat config ke Topology"
    >${escHtml(hostname)}</button>
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
});
