let cfg = { host: '', username: 'admin', password: '', verify_ssl: false };
let activePopup = null;
let selectedMembers = new Map();
let activeSearchController = null;
let topologyDevices = [];
let lastTopologyDeviceValue = '';
const DEFAULT_TOPOLOGY_STATUS = 'Connect to F5, then enter an IP.';
let topologyStatusText = DEFAULT_TOPOLOGY_STATUS;
let topologyStatusType = '';
let topologyConnectionLabel = '';

const API = '';

function toggleSidebar() {
  const sidebar = document.getElementById('sidebar');
  const btn = document.getElementById('sidebar-toggle');

  sidebar.classList.toggle('sidebar-collapsed');

  if (sidebar.classList.contains('sidebar-collapsed')) {
    btn.textContent = '+';
  } else {
    btn.textContent = '-';
  }
}

// --- UTILS ---
function setStatus(msg, type) {
  const el = document.getElementById('status-msg');
  if (!el) return;
  topologyStatusText = msg || DEFAULT_TOPOLOGY_STATUS;
  topologyStatusType = type || '';
  el.textContent = topologyStatusText;
  el.style.color = topologyStatusType === 'err' ? '#f44336' : topologyStatusType === 'ok' ? '#4caf50' : '#999';
}

function restoreTopologyStatus() {
  setStatus(topologyStatusText || DEFAULT_TOPOLOGY_STATUS, topologyStatusType || '');
}

function toast(msg, type) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'toast' + (type ? ' toast-' + type : '');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.add('hidden'), 2800);
}

function getConfig() {
  return {
    host: document.getElementById('inp-host').value.trim(),
    username: document.getElementById('inp-user').value.trim(),
    password: document.getElementById('inp-pass').value,
    verify_ssl: document.getElementById('chk-ssl').checked,
  };
}

// --- TOPOLOGY DEVICE PICKER ---
async function loadTopologyDeviceOptions() {
  const list = document.getElementById('device-list');
  if (!list) return;

  try {
    const r = await fetch(`${API}/devices`);
    const devices = await r.json();
    topologyDevices = Array.isArray(devices) ? devices : [];
    list.innerHTML = topologyDevices.map(d => {
      const value = d.hostname || d.name || d.management_ip;
      const labelParts = [d.name, d.management_ip].filter(Boolean);
      const label = labelParts.join(' - ');
      return `<option value="${escAttrLocal(value)}" label="${escAttrLocal(label)}"></option>`;
    }).join('');
  } catch (e) {
    topologyDevices = [];
    console.warn('Failed to load topology devices', e);
  }
}

function escAttrLocal(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function findTopologyDevice(value) {
  const q = String(value || '').trim().toLowerCase();
  if (!q) return null;
  return topologyDevices.find(d => {
    const candidates = [d.hostname, d.name, d.management_ip].filter(Boolean);
    return candidates.some(item => String(item).trim().toLowerCase() === q);
  }) || null;
}

function topologyDeviceLabelForHost(host) {
  const q = String(host || '').trim().toLowerCase();
  if (!q) return '';
  const device = topologyDevices.find(d => {
    const candidates = [d.management_ip, d.hostname, d.name].filter(Boolean);
    return candidates.some(item => String(item).trim().toLowerCase() === q);
  });
  return device ? (device.hostname || device.name || device.management_ip || '') : '';
}

async function handleTopologyDeviceSelected() {
  const input = document.getElementById('inp-host');
  const statusEl = document.getElementById('conn-status');
  if (!input) return;

  const value = input.value.trim();
  if (!value || value === lastTopologyDeviceValue) return;

  const device = findTopologyDevice(value);
  if (!device) return;

  lastTopologyDeviceValue = value;
  const lookup = device.hostname || device.name || device.management_ip;
  if (statusEl) {
    statusEl.textContent = `Loading ${lookup}...`;
    statusEl.className = 'conn-status';
  }

  try {
    const r = await fetch(`${API}/devices/topology-config/by-hostname/${encodeURIComponent(lookup)}`);
    const data = await r.json();
    if (!r.ok) {
      throw new Error(data.detail || 'Device not found');
    }

    document.getElementById('inp-host').value = data.management_ip || '';
    document.getElementById('inp-user').value = data.username || '';
    document.getElementById('inp-pass').value = data.password || '';
    document.getElementById('chk-ssl').checked = Boolean(data.verify_ssl);
    topologyConnectionLabel = data.hostname || data.name || data.management_ip || lookup;
    setStatus(`Auto login to ${topologyConnectionLabel}...`, '');
    await testConnection();
  } catch (e) {
    if (statusEl) {
      statusEl.textContent = 'x ' + e.message;
      statusEl.className = 'conn-status conn-err';
    }
    toast('Failed to auto login device: ' + e.message, 'err');
  }
}

document.addEventListener('DOMContentLoaded', () => {
  loadTopologyDeviceOptions();
  const hostInput = document.getElementById('inp-host');
  if (hostInput) {
    hostInput.addEventListener('change', handleTopologyDeviceSelected);
    hostInput.addEventListener('keydown', e => {
      if (e.key === 'Enter') {
        e.preventDefault();
        handleTopologyDeviceSelected();
      }
    });
  }
});

// --- CONNECTION ---
async function testConnection() {
  const config = getConfig();
  if (!config.host || !config.password) { toast('Enter host and password first', 'err'); return; }
  cfg = config;
  const statusEl = document.getElementById('conn-status');
  statusEl.textContent = 'Connecting...';
  statusEl.className = 'conn-status';
  try {
    const r = await fetch(`${API}/api/test-connection`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    });
    const data = await r.json().catch(() => null);
    if (r.ok && data && data.ok) {
      const label = data.hostname || topologyConnectionLabel || topologyDeviceLabelForHost(config.host) || data.host || config.host;
      topologyConnectionLabel = label;
      statusEl.textContent = `OK ${data.version || 'connected'}`;
      statusEl.className = 'conn-status conn-ok';
      setStatus(`Connected to ${label}`, 'ok');
    } else {
      let error = 'Connection failed';
      if (data) {
        if (data.error) {
          error = data.error;
        } else if (data.detail) {
          if (Array.isArray(data.detail)) {
            error = data.detail.map(err => err.msg || JSON.stringify(err)).join(', ');
          } else {
            error = data.detail;
          }
        }
      } else if (!r.ok) {
        error = `HTTP ${r.status}`;
      }
      statusEl.textContent = 'x ' + error;
      statusEl.className = 'conn-status conn-err';
    }
  } catch (e) {
    statusEl.textContent = 'x ' + (e.message || 'Connection failed');
    statusEl.className = 'conn-status conn-err';
  }
}

// --- SEARCH ---
function setSearchCancelVisible(visible) {
  const btn = document.getElementById('btn-cancel-search');
  if (btn) btn.style.display = visible ? 'inline-flex' : 'none';
}

function cancelSearch() {
  if (activeSearchController) {
    activeSearchController.abort();
    activeSearchController = null;
  }
  setSearchCancelVisible(false);
  setStatus('Search canceled', 'err');
}

async function doSearch() {
  const config = getConfig();
  let q = document.getElementById('inp-ip').value.trim();
  if (!config.host || !config.password) { toast('Connect to F5 first', 'err'); return; }
  cfg = config;
  closePopup();
  setStatus(q ? `Searching "${q}"...` : 'Loading all Virtual Servers...');
  clearTree();

  if (activeSearchController) activeSearchController.abort();
  const controller = new AbortController();
  activeSearchController = controller;
  setSearchCancelVisible(true);

  try {
    const r = await fetch(`${API}/api/search-unified?q=${encodeURIComponent(q)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
      signal: controller.signal,
    });
    if (!r.ok) {
      const err = await r.json();
      setStatus('Error: ' + (err.detail || 'Unknown'), 'err');
      return;
    }
    const data = await r.json();
    if (!data.vsList || data.vsList.length === 0) {
      setStatus(q ? `No results found for "${q}"` : 'No Virtual Servers found', 'err');
      return;
    }
    renderTree(data);
    document.getElementById('panel-export').style.display = 'block';
    
    // Show Bulk Action button
    const btnBulkMode = document.getElementById('btn-bulk-mode');
    if (btnBulkMode) btnBulkMode.style.display = 'inline-block';

    const total   = data.vsList.length;
    const pools   = data.vsList.reduce((a, v) => a + v.pools.length, 0);
    const members = data.vsList.reduce((a, v) => a + v.pools.reduce((b, p) => b + p.members.length, 0), 0);
    const elapsed = data.elapsed ? ` - ${data.elapsed}s` : '';
    setStatus(`${total} Virtual Server - ${pools} pool - ${members} member${elapsed}`, 'ok');
  } catch (e) {
    if (e.name === 'AbortError') {
      setStatus('Search canceled', 'err');
    } else {
      setStatus('Error: ' + e.message, 'err');
    }
  } finally {
    if (activeSearchController === controller) {
      activeSearchController = null;
      setSearchCancelVisible(false);
    }
  }
}

// --- TREE RENDER ---
function clearTree() {
  selectedMembers.clear();
  document.getElementById('tree-root').innerHTML = '';
  document.getElementById('tree-empty').style.display = 'flex';

  // Hide Bulk Mode button and controls when tree is empty
  const btnBulkMode = document.getElementById('btn-bulk-mode');
  if (btnBulkMode) btnBulkMode.style.display = 'none';
  const bulkActionControls = document.getElementById('bulk-action-controls');
  if (bulkActionControls) bulkActionControls.style.display = 'none';

  const app = document.getElementById('app');
  if (app) app.classList.remove('bulk-mode-active');
}

function dotClass(status, noMonitor) {

  if (status === 'force-offline')
    return 'dot-force-offline';

  if (status === 'node-disabled')
    return 'dot-node-disabled';

  // all members are force offline
  if (status === 'all-force-offline')
    return 'dot-down';

  // no monitor uses neutral status
  if (noMonitor)
    return 'dot-no-monitor';

  if (status === 'up')
    return 'dot-up';

  if (status === 'down')
    return 'dot-down';

  if (status === 'warn')
    return 'dot-warn';

  return 'dot-down';
}

function renderTree(data) {
  const root = document.getElementById('tree-root');
  document.getElementById('tree-empty').style.display = 'none';
  root.innerHTML = '';

  data.vsList.forEach(vs => {
    const block = document.createElement('div');
    block.className = 'tree-vs-block';

    const poolStatuses = vs.pools.map(p =>
      p.members.length > 0 &&
      p.members.every(m => m.state === 'force-offline')
        ? 'all-force-offline'
        : p.status
    );
    
    const vsEffectiveStatus =
      !vs.enabled ? 'force-offline'
      : poolStatuses.length === 0 ? 'up'
      : poolStatuses.some(s => s === 'up') ? 'up'
      : poolStatuses.every(s => s === 'all-force-offline') ? 'all-force-offline'
      : 'down';
    
    const noPoolMonitor =
      vs.pools.length === 0 ||
      vs.pools.every(
        p => !p.monitor || p.monitor.trim() === ''
      );

    const vsRow = document.createElement('div');
    vsRow.className = 'tree-node';
    vsRow.innerHTML = `<span class="dot ${dotClass(vsEffectiveStatus, noPoolMonitor)}"></span><span class="node-label vs-label">${vs.name}</span>`;
    vsRow.onclick = (e) => showPopup(e, 'vs', vs);
    block.appendChild(vsRow);

    const vsSub = document.createElement('div');
    vsSub.className = 'node-sub';
    vsSub.textContent =
        vs.destination.replace(/^\/Common\//, '');
    block.appendChild(vsSub);
	if (vs.rules && vs.rules.length > 0) {
	  const ruleWrap = document.createElement('div');
	  ruleWrap.className = 'vs-rules-wrap';
	
	  vs.rules.forEach(rule => {
	    const ruleRow = document.createElement('div');
	    ruleRow.className = 'tree-node tree-irule';
	    ruleRow.innerHTML = `<span class="irule-dot"></span><span class="irule-label">${rule}</span>`;
	    ruleWrap.appendChild(ruleRow);
	  });
	
	  block.appendChild(ruleWrap);
	}

    vs.pools.forEach(pool => {
      const poolWrap = document.createElement('div');
      poolWrap.className = 'tree-pool-wrap';

      const hasNoMonitor = !pool.monitor || pool.monitor.trim() === '';

      const allForceOffline = pool.members.length > 0
        && pool.members.every(m => m.state === 'force-offline');

      const poolEffectiveStatus = allForceOffline ? 'all-force-offline' : pool.status;

      const poolDot = dotClass(poolEffectiveStatus, hasNoMonitor);

      const poolRow = document.createElement('div');
      poolRow.className = 'tree-node';
      poolRow.innerHTML = `<span class="dot ${poolDot}"></span><span class="node-label">${pool.name}</span>`;
      poolRow.onclick = (e) => showPopup(e, 'pool', pool);
      poolWrap.appendChild(poolRow);

      pool.members.forEach(m => {
        const memberWrap = document.createElement('div');
        memberWrap.className = 'tree-member-wrap';

        const memberDot = dotClass(m.state, hasNoMonitor && m.state !== 'force-offline');

        const memberRow = document.createElement('div');
        memberRow.className = 'tree-node';
        const memberKey = `${pool.partition}|${pool.name}|${m.name}`;
        
        memberRow.innerHTML = `
          <input
            type="checkbox"
            class="member-check"
            data-key="${memberKey}"
            onclick="toggleMemberSelect(event, this)"
          >
          <span class="dot ${memberDot}"></span>
          <span class="node-label">${m.address}:${m.port}</span>
        `;
        const mCtx = Object.assign({}, m, { _pool: pool.name, _partition: pool.partition });
		memberRow.dataset.memberKey = memberKey;
		memberRow.dataset.pool = pool.name;
		memberRow.dataset.partition = pool.partition;
		memberRow.dataset.member = m.name;
        memberRow.onclick = (e) => {
          if (document.getElementById('app').classList.contains('bulk-mode-active')) {
            e.stopPropagation();
            const checkbox = memberRow.querySelector('.member-check');
            if (checkbox) {
              checkbox.checked = !checkbox.checked;
              toggleMemberSelect(e, checkbox);
            }
          } else {
            showPopup(e, 'member', mCtx);
          }
        };
        memberWrap.appendChild(memberRow);
        poolWrap.appendChild(memberWrap);
      });

      block.appendChild(poolWrap);
    });

    root.appendChild(block);
  });
}

const dpRow = (l, v) => `<div class="dp-row"><span class="dp-label">${l}</span><span class="dp-val">${v}</span></div>`;

function toggleMemberSelect(e, checkbox) {
  e.stopPropagation();

  const row = checkbox.closest('.tree-node');

  const item = {
    partition: row.dataset.partition,
    pool_name: row.dataset.pool,
    member_name: row.dataset.member,
  };

  if (checkbox.checked) {
    selectedMembers.set(checkbox.dataset.key, item);
  } else {
    selectedMembers.delete(checkbox.dataset.key);
  }

  updateBulkButtons();
}

function updateBulkButtons() {
  const count = selectedMembers.size;

  // Header elements
  const headerEl = document.getElementById('bulk-header-count');
  if (headerEl) {
    headerEl.textContent = count ? `${count} selected` : '';
  }
  const headerEnableBtn = document.getElementById('btn-bulk-enable');
  const headerForceBtn = document.getElementById('btn-bulk-force');
  if (headerEnableBtn) headerEnableBtn.disabled = count === 0;
  if (headerForceBtn) headerForceBtn.disabled = count === 0;
}

function enterBulkMode() {
  const app = document.getElementById('app');
  if (app) app.classList.add('bulk-mode-active');
  
  const btnBulkMode = document.getElementById('btn-bulk-mode');
  if (btnBulkMode) btnBulkMode.style.display = 'none';
  
  const bulkActionControls = document.getElementById('bulk-action-controls');
  if (bulkActionControls) bulkActionControls.style.display = 'flex';
  
  updateBulkButtons();
}

function exitBulkMode() {
  const app = document.getElementById('app');
  if (app) app.classList.remove('bulk-mode-active');
  
  const btnBulkMode = document.getElementById('btn-bulk-mode');
  if (btnBulkMode) btnBulkMode.style.display = 'inline-block';
  
  const bulkActionControls = document.getElementById('bulk-action-controls');
  if (bulkActionControls) bulkActionControls.style.display = 'none';
  
  // Uncheck all checkboxes in the DOM
  document.querySelectorAll('.member-check').forEach(chk => {
    chk.checked = false;
  });
  
  selectedMembers.clear();
  updateBulkButtons();
}

function tlsBadgeClass(ver) {
  if (ver === 'TLS 1.3') return 'tls-badge-strong';
  if (ver === 'TLS 1.2') return 'tls-badge-ok';
  if (ver === 'TLS 1.1') return 'tls-badge-warn';
  return 'tls-badge-danger'; // TLS 1.0 or SSL
}

function profileTlsHtml(profiles) {
  const tlsVersions = [];
  (profiles || []).forEach(p => {
    if (p.type === 'client-ssl' && p.tls_versions && p.tls_versions.length) {
      p.tls_versions.forEach(v => { if (!tlsVersions.includes(v)) tlsVersions.push(v); });
    }
  });

  if (!tlsVersions.length) return '';

  return `
    <div class="tls-dropdown-container">
      <button class="tls-dropdown-trigger" onclick="toggleTlsDropdown(event, this)">
        <span>TLS Versions Used (${tlsVersions.length})</span>
        <span class="tls-dropdown-arrow">▼</span>
      </button>
      <div class="tls-dropdown-content" style="display: none;">
        <div class="tls-badges">
          ${tlsVersions.map(v => `<span class="tls-badge ${tlsBadgeClass(v)}">${v}</span>`).join('')}
        </div>
      </div>
    </div>
  `;
}

function profileRowsHtml(profiles) {
  if (!profiles || !profiles.length) {
    return `
      <div class="profile-table">
        <div class="profile-row">
          <div class="profile-type">-</div>
          <div class="profile-name">No profiles</div>
        </div>
      </div>
    `;
  }

  return `
    <div class="profile-table">
      ${profiles.map(p => `
        <div class="profile-row">
          <div class="profile-type">${p.type || 'unknown'}</div>
          <div class="profile-name">${p.name || '-'}</div>
        </div>
      `).join('')}
    </div>
  `;
}

function renderVsProfiles(popup, profiles, errorMessage) {
  const section = popup.querySelector('[data-vs-profile-section]');
  if (!section) return;

  if (errorMessage) {
    section.innerHTML = `
      <div class="profile-title">Profiles</div>
      <div class="profile-table">
        <div class="profile-row">
          <div class="profile-type">error</div>
          <div class="profile-name dp-warn">${errorMessage}</div>
        </div>
      </div>
    `;
    return;
  }

  section.innerHTML = `
    ${profileTlsHtml(profiles)}
    <div class="profile-title">Profiles</div>
    ${profileRowsHtml(profiles)}
  `;
}

async function loadVsProfiles(popup, vsData) {
  if (!popup || !vsData || !vsData.name) return;

  if (vsData._profilesLoaded) {
    renderVsProfiles(popup, vsData._profiles || []);
    return;
  }

  try {
    const r = await fetch(`${API}/api/vs-extra`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        host: cfg.host,
        username: cfg.username,
        password: cfg.password,
        verify_ssl: cfg.verify_ssl,
        partition: vsData.partition || 'Common',
        vs_name: vsData.name,
      }),
    });
    const data = await r.json().catch(() => null);
    if (!r.ok || !data || data.ok === false) {
      throw new Error((data && (data.detail || data.error)) || `HTTP ${r.status}`);
    }

    vsData.rules = data.rules || vsData.rules || [];
    vsData._profiles = data.profiles || [];
    vsData._profilesLoaded = true;

    if (activePopup === popup) {
      renderVsProfiles(popup, vsData._profiles);
    }
  } catch (err) {
    if (activePopup === popup) {
      renderVsProfiles(popup, [], 'Failed to load profile/TLS: ' + err.message);
    }
  }
}

function toggleTlsDropdown(e, btn) {
  e.preventDefault();
  e.stopPropagation();
  const container = btn.closest('.tls-dropdown-container');
  const content = container.querySelector('.tls-dropdown-content');
  const arrow = btn.querySelector('.tls-dropdown-arrow');
  if (content.style.display === 'none') {
    content.style.display = 'block';
    arrow.style.transform = 'rotate(180deg)';
    btn.classList.add('active');
  } else {
    content.style.display = 'none';
    arrow.style.transform = 'rotate(0deg)';
    btn.classList.remove('active');
  }
}

function showPopup(e, type, data) {
  e.stopPropagation();
  closePopup();

  const popup = document.createElement('div');
  popup.className = 'detail-popup';

  let html = `<button class="detail-close-btn" onclick="closePopup()">✕</button>`;

  if (type === 'vs') {
    const vsEnabled = data.enabled;
    html += `<div class="detail-popup-title">Virtual Server</div>`;
    html += `<div class="detail-popup-name">${data.name}</div>`;
    const dest = data.destination.replace(/^\/Common\//, '');
    html += dpRow('Destination', dest);
    if (data.snat) html += dpRow('SNAT', data.snat);
    data.profiles = [];
    if (data.profiles && data.profiles.length) {
      // Collect all TLS versions from client-ssl profiles
      const tlsVersions = [];
      data.profiles.forEach(p => {
        if (p.type === 'client-ssl' && p.tls_versions && p.tls_versions.length) {
          p.tls_versions.forEach(v => { if (!tlsVersions.includes(v)) tlsVersions.push(v); });
        }
      });

      if (tlsVersions.length) {
        html += `
          <div class="tls-dropdown-container">
            <button class="tls-dropdown-trigger" onclick="toggleTlsDropdown(event, this)">
              <span>TLS Versions Used (${tlsVersions.length})</span>
              <span class="tls-dropdown-arrow">▼</span>
            </button>
            <div class="tls-dropdown-content" style="display: none;">
              <div class="tls-badges">
                ${tlsVersions.map(v => `<span class="tls-badge ${tlsBadgeClass(v)}">${v}</span>`).join('')}
              </div>
            </div>
          </div>
        `;
      }

      html += `
        <div class="profile-section">
          <div class="profile-title">Profiles</div>
          <div class="profile-table">
            ${data.profiles.map(p => `
              <div class="profile-row">
                <div class="profile-type">${p.type}</div>
                <div class="profile-name">${p.name}</div>
              </div>
            `).join('')}
          </div>
        </div>
      `;
    }
    html += `
      <div class="profile-section" data-vs-profile-section>
        <div class="profile-title">Profiles</div>
        <div class="profile-table">
          <div class="profile-row">
            <div class="profile-type">loading</div>
            <div class="profile-name">Load profile/TLS...</div>
          </div>
        </div>
      </div>
    `;
    html += `<div class="dp-actions">`;
    if (vsEnabled) {
      html += `<button class="dp-btn dp-btn-force" onclick="vsAction('disable', event)">Disable Virtual Server</button>`;
    } else {
      html += `<button class="dp-btn dp-btn-enable" onclick="vsAction('enable', event)">Enable Virtual Server</button>`;
    }
    html += `</div>`;
    popup.dataset.vsName = data.name || '';
    popup.dataset.vsPartition = data.partition || 'Common';

  } else if (type === 'pool') {
    const up = data.members.filter(m => m.state === 'up').length;
    html += `<div class="detail-popup-title">Pool</div>`;
    html += `<div class="detail-popup-name">${data.name}</div>`;
    html += dpRow('Members', `${up}/${data.members.length} up`);
    html += dpRow('LB Mode', data.lbMode || 'round-robin');
    html += `
      <div class="dp-row">
        <span class="dp-label">Current Connections</span>
        <span class="dp-val" data-pool-connections>checking...</span>
      </div>
    `;
    if (data.monitor) html += dpRow('Monitor', data.monitor.replace(/^\/Common\//, ''));
    else html += dpRow('Monitor', '<span class="dp-warn">No monitor</span>');
    popup.dataset.pool = data.name || '';
    popup.dataset.partition = data.partition || 'Common';

  } else if (type === 'member') {
    const isForceOffline = data.state === 'force-offline';
    html += `<div class="detail-popup-title">Pool Member</div>`;
    html += `<div class="detail-popup-name">${data.name || data.address}</div>`;
    html += dpRow('Address', data.address);
    html += dpRow('Port', data.port);
    html += `<div class="dp-actions">`;
    if (isForceOffline) {
      html += `<button class="dp-btn dp-btn-enable" onclick="memberAction('enable', event)">Enable</button>`;
    } else {
      html += `<button class="dp-btn dp-btn-force" onclick="memberAction('force-offline', event)">Force Offline</button>`;
    }
    html += `</div>`;
    popup.dataset.pool = data._pool || '';
    popup.dataset.partition = data._partition || '';
    popup.dataset.member = data.name || '';
  }

  popup.innerHTML = html;

  if (type === 'vs') {
    popup.dataset.vsName = data.name || '';
    popup.dataset.vsPartition = data.partition || 'Common';
  } else if (type === 'member') {
    popup.dataset.pool = data._pool || '';
    popup.dataset.partition = data._partition || '';
    popup.dataset.member = data.name || '';
  }

  popup.style.opacity = '0';
  document.body.appendChild(popup);
  activePopup = popup;
  if (type === 'vs') loadVsProfiles(popup, data);
  if (type === 'pool') refreshPoolConnections(popup);

  // Position at cursor, then adjust if out of viewport
  const MARGIN = 12;
  const pw = popup.offsetWidth || 350;
  const ph = popup.offsetHeight || 200;
  let px = e.clientX + MARGIN;
  let py = e.clientY + MARGIN;
  if (px + pw > window.innerWidth - MARGIN)  px = e.clientX - pw - MARGIN;
  if (py + ph > window.innerHeight - MARGIN) py = e.clientY - ph - MARGIN;
  if (px < MARGIN) px = MARGIN;
  if (py < MARGIN) py = MARGIN;
  popup.style.left = px + 'px';
  popup.style.top  = py + 'px';

  // Re-check after layout paint (popup height now accurate)
  requestAnimationFrame(() => {
    const rect = popup.getBoundingClientRect();
    if (rect.right  > window.innerWidth  - MARGIN) popup.style.left = (e.clientX - rect.width  - MARGIN) + 'px';
    if (rect.bottom > window.innerHeight - MARGIN) popup.style.top  = (e.clientY - rect.height - MARGIN) + 'px';
    if (parseFloat(popup.style.left) < MARGIN) popup.style.left = MARGIN + 'px';
    if (parseFloat(popup.style.top)  < MARGIN) popup.style.top  = MARGIN + 'px';
    popup.style.opacity = '1';
  });

  makeDraggable(popup);
}

async function refreshPoolConnections(popup) {
  const valueEl = popup.querySelector('[data-pool-connections]');
  if (!valueEl) return;

  const pool = popup.dataset.pool;
  const partition = popup.dataset.partition || 'Common';
  if (!pool) return;

  const previous = valueEl.textContent;
  valueEl.textContent = previous && previous !== '-' ? previous : 'checking...';

  try {
    const r = await fetch(`${API}/api/pool-connections`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        host: cfg.host,
        username: cfg.username,
        password: cfg.password,
        verify_ssl: cfg.verify_ssl,
        partition,
        pool_name: pool,
      }),
    });
    const data = await r.json();
    if (activePopup !== popup) return;

    if (r.ok && data.ok) {
      valueEl.textContent = data.current_connections;
    } else {
      valueEl.textContent = previous || '-';
    }
  } catch (err) {
    if (activePopup === popup) valueEl.textContent = previous || '-';
  }
}

// --- ACTIONS ---
async function bulkMemberAction(action) {
  const members = Array.from(selectedMembers.values());

  if (!members.length) {
    toast('Select pool members first', 'err');
    return;
  }

  const confirmText =
    action === 'enable'
      ? `Enable ${members.length} pool member?`
      : `Force offline ${members.length} pool member?`;

  if (!confirm(confirmText)) return;

  try {
    const r = await fetch(`${API}/api/member-action-bulk`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        host: cfg.host,
        username: cfg.username,
        password: cfg.password,
        verify_ssl: cfg.verify_ssl,
        action,
        members,
      }),
    });

    const data = await r.json();

    if (r.ok) {
      toast(
        `Success: ${data.success}, Failed: ${data.failed}`,
        data.failed ? 'err' : 'ok'
      );

      selectedMembers.clear();
      closePopup();
      exitBulkMode();
      doSearch();

    } else {
      toast('Failed: ' + (data.detail || data.error || 'Unknown'), 'err');
    }

  } catch (err) {
    toast('Error: ' + err.message, 'err');
  }
}

async function vsAction(action, e) {
  e.stopPropagation();
  if (!activePopup) return;
  const vsName = activePopup.dataset.vsName;
  const partition = activePopup.dataset.vsPartition;
  if (!vsName) { toast('Incomplete Virtual Server data', 'err'); return; }

  const btn = e.target;
  btn.disabled = true;
  btn.textContent = 'Loading...';

  try {
    const r = await fetch(`${API}/api/vs-action`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        host: cfg.host, username: cfg.username,
        password: cfg.password, verify_ssl: cfg.verify_ssl,
        partition, vs_name: vsName, action,
      }),
    });
    const data = await r.json();
    if (r.ok && data.ok) {
      toast(`Virtual Server ${action === 'enable' ? 'enabled' : 'disabled'}: ${vsName}`, 'ok');
      closePopup();
      doSearch();
    } else {
      toast('Failed: ' + (data.detail || data.error || 'Unknown'), 'err');
      btn.disabled = false;
      btn.textContent = action === 'enable' ? 'Enable Virtual Server' : 'Disable Virtual Server';
    }
  } catch (err) {
    toast('Error: ' + err.message, 'err');
    btn.disabled = false;
    btn.textContent = action === 'enable' ? 'Enable Virtual Server' : 'Disable Virtual Server';
  }
}

async function memberAction(action, e) {
  e.stopPropagation();
  if (!activePopup) return;
  const pool = activePopup.dataset.pool;
  const partition = activePopup.dataset.partition;
  const member = activePopup.dataset.member;
  if (!pool || !member) { toast('Incomplete pool/member data', 'err'); return; }

  const btn = e.target;
  btn.disabled = true;
  btn.textContent = 'Loading...';

  try {
    const r = await fetch(`${API}/api/member-action`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        host: cfg.host, username: cfg.username,
        password: cfg.password, verify_ssl: cfg.verify_ssl,
        partition, pool_name: pool, member_name: member, action,
      }),
    });
    const data = await r.json();
    if (r.ok && data.ok) {
      toast(`${action === 'enable' ? 'Enabled' : 'Force offline'}: ${member}`, 'ok');
      closePopup();
      doSearch();
    } else {
      toast('Failed: ' + (data.detail || data.error || 'Unknown'), 'err');
      btn.disabled = false;
      btn.textContent = action === 'enable' ? 'Enable' : 'Force Offline';
    }
  } catch (err) {
    toast('Error: ' + err.message, 'err');
    btn.disabled = false;
    btn.textContent = action === 'enable' ? 'Enable' : 'Force Offline';
  }
}

function makeDraggable(popup) {
  const handle = popup.querySelector('.detail-popup-title');
  if (!handle) return;

  let isDragging = false;
  let offsetX = 0;
  let offsetY = 0;

  handle.addEventListener('mousedown', e => {
    isDragging = true;
    offsetX = e.clientX - popup.offsetLeft;
    offsetY = e.clientY - popup.offsetTop;
    document.body.style.userSelect = 'none';
  });

  document.addEventListener('mousemove', e => {
    if (!isDragging) return;

    popup.style.left = `${e.clientX - offsetX}px`;
    popup.style.top = `${e.clientY - offsetY}px`;
    popup.style.right = 'auto';
  });

  document.addEventListener('mouseup', () => {
    isDragging = false;
    document.body.style.userSelect = '';
  });
}

function closePopup() {
  if (activePopup) { activePopup.remove(); activePopup = null; }
}

document.addEventListener('click', e => {
  if (activePopup && !activePopup.contains(e.target)) closePopup();
});

// --- EXPORT ---
const EXPORT_LIBS = {
  html2canvas: 'https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js',
  jspdf: 'https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js',
};

function loadScriptOnce(src, globalCheck) {
  if (globalCheck()) return Promise.resolve();

  return new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[data-src="${src}"]`);
    if (existing) {
      existing.addEventListener('load', resolve, { once: true });
      existing.addEventListener('error', reject, { once: true });
      return;
    }

    const script = document.createElement('script');
    script.src = src;
    script.async = true;
    script.dataset.src = src;
    script.onload = resolve;
    script.onerror = () => reject(new Error(`Failed to load export library: ${src}`));
    document.head.appendChild(script);
  });
}

async function ensureCanvasExport() {
  await loadScriptOnce(EXPORT_LIBS.html2canvas, () => Boolean(window.html2canvas));
}

async function ensurePdfExport() {
  await ensureCanvasExport();
  await loadScriptOnce(EXPORT_LIBS.jspdf, () => Boolean(window.jspdf));
}

async function exportPNG() {
  const treeContainer = document.getElementById('tree-container');
  const treeRoot = document.getElementById('tree-root');

  if (!treeRoot || !treeRoot.children.length) {
    toast('No data', 'err');
    return;
  }

  try {
    await ensureCanvasExport();
  } catch (err) {
    toast(err.message, 'err');
    return;
  }

  const isSingleVS = treeRoot.children.length === 1;
  const targetElement = isSingleVS ? treeRoot.children[0] : treeRoot;

  const oldOverflow = treeContainer.style.overflow;
  const oldHeight = treeContainer.style.height;
  const oldMaxHeight = treeContainer.style.maxHeight;

  treeContainer.style.overflow = 'visible';
  treeContainer.style.height = (isSingleVS ? targetElement.scrollHeight : treeRoot.scrollHeight) + 80 + 'px';
  treeContainer.style.maxHeight = 'none';

  // Force 3 columns layout during export if not a single VS
  if (!isSingleVS) {
    treeRoot.classList.add('exporting-grid');
  } else {
    targetElement.style.padding = '24px';
    targetElement.style.backgroundColor = '#1a1a1a';
    targetElement.style.borderRadius = '10px';
  }

  const h2cOptions = {
    backgroundColor: '#1a1a1a',
    scale: 1.2,
    useCORS: true,
  };

  if (!isSingleVS) {
    h2cOptions.windowWidth = 1200;
    h2cOptions.windowHeight = treeRoot.scrollHeight;
  }

  html2canvas(targetElement, h2cOptions).then(canvas => {
    const a = document.createElement('a');
    a.download = `f5-topology-${Date.now()}.png`;
    a.href = canvas.toDataURL('image/png');
    a.click();

    if (!isSingleVS) {
      treeRoot.classList.remove('exporting-grid');
    } else {
      targetElement.style.padding = '';
      targetElement.style.backgroundColor = '';
      targetElement.style.borderRadius = '';
    }
    treeContainer.style.overflow = oldOverflow;
    treeContainer.style.height = oldHeight;
    treeContainer.style.maxHeight = oldMaxHeight;

    toast('PNG exported', 'ok');
  }).catch(err => {
    if (!isSingleVS) {
      treeRoot.classList.remove('exporting-grid');
    } else {
      targetElement.style.padding = '';
      targetElement.style.backgroundColor = '';
      targetElement.style.borderRadius = '';
    }
    treeContainer.style.overflow = oldOverflow;
    treeContainer.style.height = oldHeight;
    treeContainer.style.maxHeight = oldMaxHeight;

    toast('PNG export failed: ' + err.message, 'err');
  });
}

async function exportPDF() {
  const treeContainer = document.getElementById('tree-container');
  const treeRoot = document.getElementById('tree-root');

  if (!treeRoot || !treeRoot.children.length) {
    toast('No data', 'err');
    return;
  }

  try {
    await ensurePdfExport();
  } catch (err) {
    toast(err.message, 'err');
    return;
  }

  const isSingleVS = treeRoot.children.length === 1;
  const targetElement = isSingleVS ? treeRoot.children[0] : treeRoot;

  const oldOverflow = treeContainer.style.overflow;
  const oldHeight = treeContainer.style.height;
  const oldMaxHeight = treeContainer.style.maxHeight;

  treeContainer.style.overflow = 'visible';
  treeContainer.style.height = (isSingleVS ? targetElement.scrollHeight : treeRoot.scrollHeight) + 80 + 'px';
  treeContainer.style.maxHeight = 'none';

  // Force 3 columns layout during export if not a single VS
  if (!isSingleVS) {
    treeRoot.classList.add('exporting-grid');
  } else {
    targetElement.style.padding = '24px';
    targetElement.style.backgroundColor = '#1a1a1a';
    targetElement.style.borderRadius = '10px';
  }

  const h2cOptions = {
    backgroundColor: '#1a1a1a',
    scale: 1.2,
    useCORS: true,
  };

  if (!isSingleVS) {
    h2cOptions.windowWidth = 1200;
    h2cOptions.windowHeight = treeRoot.scrollHeight;
  }

  html2canvas(targetElement, h2cOptions).then(canvas => {
    const { jsPDF } = window.jspdf;

    const isLandscape = canvas.width > canvas.height;
    const pdf = new jsPDF({
      orientation: isLandscape ? 'landscape' : 'portrait',
      unit: 'px',
      format: 'a4'
    });

    const pageWidth = pdf.internal.pageSize.getWidth();
    const pageHeight = pdf.internal.pageSize.getHeight();

    let imgWidth = pageWidth;
    let imgHeight = canvas.height * imgWidth / canvas.width;
    let xOffset = 0;

    if (isSingleVS) {
      // If single VS, let's not stretch beyond its normal size if smaller than page
      if (imgWidth > canvas.width) {
        imgWidth = canvas.width;
        imgHeight = canvas.height;
      }
      // If it still exceeds page height, scale down to fit single page
      if (imgHeight > pageHeight - 40) {
        imgHeight = pageHeight - 40;
        imgWidth = canvas.width * imgHeight / canvas.height;
      }
      xOffset = (pageWidth - imgWidth) / 2;
    }

    const imgData = canvas.toDataURL('image/jpeg', 0.8);

    let y = isSingleVS ? 20 : 0;
    let remainingHeight = imgHeight;

    pdf.addImage(imgData, 'JPEG', xOffset, y, imgWidth, imgHeight);
    remainingHeight -= pageHeight;

    while (remainingHeight > 0) {
      y -= pageHeight;
      pdf.addPage();
      pdf.addImage(imgData, 'JPEG', xOffset, y, imgWidth, imgHeight);
      remainingHeight -= pageHeight;
    }

    pdf.save(`f5-topology-${Date.now()}.pdf`);

    if (!isSingleVS) {
      treeRoot.classList.remove('exporting-grid');
    } else {
      targetElement.style.padding = '';
      targetElement.style.backgroundColor = '';
      targetElement.style.borderRadius = '';
    }
    treeContainer.style.overflow = oldOverflow;
    treeContainer.style.height = oldHeight;
    treeContainer.style.maxHeight = oldMaxHeight;

    toast('PDF exported', 'ok');
  }).catch(err => {
    if (!isSingleVS) {
      treeRoot.classList.remove('exporting-grid');
    } else {
      targetElement.style.padding = '';
      targetElement.style.backgroundColor = '';
      targetElement.style.borderRadius = '';
    }
    treeContainer.style.overflow = oldOverflow;
    treeContainer.style.height = oldHeight;
    treeContainer.style.maxHeight = oldMaxHeight;

    toast('PDF export failed: ' + err.message, 'err');
  });
}

document.addEventListener('keydown', e => {
  if (e.key === 'Enter' && document.activeElement.id === 'inp-ip') doSearch();
  if (
    e.key === 'Enter' &&
    ['inp-host', 'inp-user', 'inp-pass'].includes(document.activeElement.id)
  ) {
    testConnection();
  }
  if (e.key === 'Escape') closePopup();
});
