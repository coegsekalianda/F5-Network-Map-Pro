let cfg = { host: '', username: 'admin', password: '', verify_ssl: false };
let activePopup = null;
let selectedMembers = new Map();
let activeSearchController = null;

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
  el.textContent = msg;
  el.style.color = type === 'err' ? '#f44336' : type === 'ok' ? '#4caf50' : '#999';
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

// --- CONNECTION ---
async function testConnection() {
  const config = getConfig();
  if (!config.host || !config.password) { toast('Isi host dan password dulu', 'err'); return; }
  cfg = config;
  const statusEl = document.getElementById('conn-status');
  statusEl.textContent = 'Connecting...';
  statusEl.className = 'conn-status';
  try {
    const r = await fetch(`${API}/api/test-connection`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    });
    const data = await r.json();
    if (data.ok) {
      statusEl.textContent = `✓ ${data.version || 'connected'}`;
      statusEl.className = 'conn-status conn-ok';
      setStatus(`Connected to ${data.host}`, 'ok');
      loadHealth();
    } else {
      statusEl.textContent = '✗ ' + data.error;
      statusEl.className = 'conn-status conn-err';
    }
  } catch (e) {
    statusEl.textContent = '✗ ' + e.message;
    statusEl.className = 'conn-status conn-err';
  }
}

async function loadHealth() {
  try {
    const r = await fetch(`${API}/api/health`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg),
    });
    const data = await r.json();
    if (data.summary) {
      const s = data.summary;
      document.getElementById('panel-health').style.display = 'block';
      document.getElementById('health-grid').innerHTML = `
        <div class="health-card"><div class="hc-val">${s.totalVS}</div><div class="hc-label">Total Virtual Server</div></div>
        <div class="health-card hc-up"><div class="hc-val">${s.vsUp}</div><div class="hc-label">Virtual Server Up</div></div>
        <div class="health-card hc-down"><div class="hc-val">${s.vsDown}</div><div class="hc-label">Virtual Server Down</div></div>
        <div class="health-card"><div class="hc-val">${s.totalPools}</div><div class="hc-label">Pools</div></div>
      `;
    }
  } catch (e) {}
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
  setStatus('Pencarian dibatalkan', 'err');
}

async function doSearch() {
  const config = getConfig();
  let q = document.getElementById('inp-ip').value.trim();
  if (!config.host || !config.password) { toast('Hubungkan ke F5 dulu', 'err'); return; }
  cfg = config;
  closePopup();
  setStatus(q ? `Mencari "${q}"...` : 'Memuat semua Virtual Server...');
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
      setStatus(q ? `Tidak ditemukan hasil untuk "${q}"` : 'Tidak ada Virtual Server yang ditemukan', 'err');
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
    const elapsed = data.elapsed ? ` · ${data.elapsed}s` : '';
    setStatus(`${total} Virtual Server · ${pools} pool · ${members} member${elapsed}`, 'ok');
  } catch (e) {
    if (e.name === 'AbortError') {
      setStatus('Pencarian dibatalkan', 'err');
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

  // khusus kondisi semua member force offline
  if (status === 'all-force-offline')
    return 'dot-down';

  // no monitor normal = biru
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
        <span class="dp-val" data-pool-connections>${data.current_connections !== undefined ? data.current_connections : '-'}</span>
      </div>
    `;
    if (data.monitor) html += dpRow('Monitor', data.monitor.replace(/^\/Common\//, ''));
    else html += dpRow('Monitor', '<span class="dp-warn">Tidak ada monitor</span>');
    popup.dataset.pool = data.name || '';
    popup.dataset.partition = data.partition || 'Common';
    html += `
      <div class="dp-actions">
        <button class="dp-btn dp-btn-clear" onclick="clearPoolConnections(event)">Clear Connections</button>
      </div>
    `;

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
    toast('Pilih pool member dulu', 'err');
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
      toast('Gagal: ' + (data.detail || data.error || 'Unknown'), 'err');
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
  if (!vsName) { toast('Data Virtual Server tidak lengkap', 'err'); return; }

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
      toast('Gagal: ' + (data.detail || data.error || 'Unknown'), 'err');
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
  if (!pool || !member) { toast('Data pool/member tidak lengkap', 'err'); return; }

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
      toast('Gagal: ' + (data.detail || data.error || 'Unknown'), 'err');
      btn.disabled = false;
      btn.textContent = action === 'enable' ? 'Enable' : 'Force Offline';
    }
  } catch (err) {
    toast('Error: ' + err.message, 'err');
    btn.disabled = false;
    btn.textContent = action === 'enable' ? 'Enable' : 'Force Offline';
  }
}

async function clearPoolConnections(e) {
  e.stopPropagation();
  if (!activePopup) return;
  const pool = activePopup.dataset.pool;
  const partition = activePopup.dataset.partition;
  if (!pool) { toast('Pool data missing', 'err'); return; }
  if (!confirm(`Clear active connections for pool ${pool}?`)) return;

  const btn = e.target;
  btn.disabled = true;
  btn.textContent = 'Clearing...';

  try {
    const r = await fetch(`${API}/api/clear-pool-connections`, {
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
    if (r.ok && data.ok) {
      toast(`Cleared ${data.before} -> ${data.after} connections`, 'ok');
      closePopup();
      doSearch();
    } else {
      toast('Gagal clear: ' + (data.detail || data.error || `${data.failed || 0} target failed`), 'err');
      btn.disabled = false;
      btn.textContent = 'Clear Connections';
    }
  } catch (err) {
    toast('Error: ' + err.message, 'err');
    btn.disabled = false;
    btn.textContent = 'Clear Connections';
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
    script.onerror = () => reject(new Error(`Gagal memuat library export: ${src}`));
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
    toast('Tidak ada data', 'err');
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

    toast('Export PNG gagal: ' + err.message, 'err');
  });
}

async function exportPDF() {
  const treeContainer = document.getElementById('tree-container');
  const treeRoot = document.getElementById('tree-root');

  if (!treeRoot || !treeRoot.children.length) {
    toast('Tidak ada data', 'err');
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

    toast('Export PDF gagal: ' + err.message, 'err');
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
