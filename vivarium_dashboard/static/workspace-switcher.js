// Workspace switcher v2: centered modal mounted to <body> on first open.
//
// The trigger button (#viv-workspace-switcher-trigger) lives in the rail
// from index.html.j2. Click → mount + open the modal. Reads GET
// /api/workspaces on each open. Per-row primary action = click anywhere
// except the right-side button. Secondary action = the button (Stop on
// running non-current, Forget on stopped, Clean up on stale, Forget on
// missing).

(function () {
  const trigger = document.getElementById('viv-workspace-switcher-trigger');
  if (!trigger) return;

  let modal = null;
  let card = null;
  let listEl = null;
  let escHandler = null;

  const GLYPH = {
    current: '●', running: '●', stopped: '○', stale: '⚠', missing: '⊘',
  };
  const GLYPH_CLASS = {
    current: 'viv-glyph-running', running: 'viv-glyph-running',
    stopped: 'viv-glyph-stopped', stale: 'viv-glyph-stale',
    missing: 'viv-glyph-missing',
  };

  function ensureMounted() {
    if (modal) return;
    modal = document.createElement('div');
    modal.className = 'viv-ws-modal';
    modal.innerHTML = `
      <div class="viv-ws-modal-card" role="dialog" aria-label="Workspaces">
        <div class="viv-ws-modal-header">
          <h2>Workspaces</h2>
          <button type="button" class="viv-ws-modal-close" aria-label="Close">✕</button>
        </div>
        <ul class="viv-ws-modal-list"></ul>
        <div class="viv-ws-modal-footer">
          <button type="button" class="viv-ws-modal-add">+ Add existing workspace…</button>
          <div class="viv-workspace-switcher-actions">
            <button type="button" class="viv-ws-action-start-workstream">+ Start workstream</button>
            <button type="button" class="viv-ws-action-end-workstream">End current workstream</button>
          </div>
        </div>
      </div>
    `;
    document.body.appendChild(modal);
    card = modal.querySelector('.viv-ws-modal-card');
    listEl = modal.querySelector('.viv-ws-modal-list');

    modal.addEventListener('click', (e) => {
      // Click on the dim overlay (outside the card) closes the modal.
      if (e.target === modal) close();
    });
    modal.querySelector('.viv-ws-modal-close').addEventListener('click', close);
    modal.querySelector('.viv-ws-modal-add').addEventListener('click', doAdd);
    modal.querySelector('.viv-ws-action-start-workstream').addEventListener('click', () => {
      close();
      if (typeof window._startWork === 'function') {
        window._startWork();
      }
    });
    modal.querySelector('.viv-ws-action-end-workstream').addEventListener('click', () => {
      close();
      if (typeof window._endWork === 'function') {
        window._endWork();
      }
    });
  }

  function open() {
    ensureMounted();
    modal.classList.add('open');
    listEl.innerHTML = '<li class="viv-ws-loading">Loading…</li>';
    refresh();
    escHandler = (e) => { if (e.key === 'Escape') close(); };
    document.addEventListener('keydown', escHandler);
  }

  function close() {
    if (!modal) return;
    modal.classList.remove('open');
    if (escHandler) {
      document.removeEventListener('keydown', escHandler);
      escHandler = null;
    }
  }

  trigger.addEventListener('click', (e) => {
    e.stopPropagation();
    open();
  });

  async function refresh() {
    try {
      const resp = await fetch('/api/workspaces');
      const data = await resp.json();
      render(data);
    } catch (err) {
      listEl.innerHTML = `<li class="viv-ws-error">Failed to load: ${escapeHtml(String(err))}</li>`;
    }
  }

  function render(data) {
    listEl.innerHTML = '';
    data.workspaces.forEach((ws) => listEl.appendChild(renderRow(ws)));
  }

  function renderRow(ws) {
    const li = document.createElement('li');
    li.className = 'viv-ws-row';
    if (ws.status === 'current') li.classList.add('viv-ws-row-current');

    const line1 = document.createElement('div');
    line1.className = 'viv-ws-line1';

    const glyph = document.createElement('span');
    glyph.className = `viv-ws-glyph ${GLYPH_CLASS[ws.status] || ''}`;
    glyph.textContent = GLYPH[ws.status] || '?';
    line1.appendChild(glyph);

    const name = document.createElement('span');
    name.className = 'viv-ws-name';
    name.innerHTML = `<strong>${escapeHtml(ws.name)}</strong>${
      ws.status === 'current' ? ' <small>(this)</small>' : ''
    }`;
    line1.appendChild(name);

    const btn = renderActionButton(ws, li);
    if (btn) line1.appendChild(btn);

    const line2 = document.createElement('div');
    line2.className = 'viv-ws-path';
    line2.textContent = ws.path;

    li.appendChild(line1);
    li.appendChild(line2);

    // Row click = primary action (except clicks on the button).
    if (ws.status !== 'current') {
      li.addEventListener('click', (e) => {
        if (e.target.closest('button')) return;
        doPrimary(ws, li);
      });
    }
    return li;
  }

  function renderActionButton(ws, li) {
    if (ws.status === 'current') return null;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'viv-ws-action';
    let label;
    if (ws.status === 'running') {
      label = 'Stop ■';
      btn.classList.add('viv-ws-action-danger');
      btn.addEventListener('click', () => doStop(ws, btn, li));
    } else if (ws.status === 'stopped') {
      label = 'Forget';
      btn.classList.add('viv-ws-action-muted');
      btn.addEventListener('click', () => doForget(ws, btn, li));
    } else if (ws.status === 'stale') {
      label = 'Clean up';
      btn.classList.add('viv-ws-action-warn');
      btn.addEventListener('click', () => doCleanup(ws, btn, li));
    } else if (ws.status === 'missing') {
      label = 'Forget ×';
      btn.classList.add('viv-ws-action-muted');
      btn.addEventListener('click', () => doForget(ws, btn, li));
    }
    btn.textContent = label;
    return btn;
  }

  function doPrimary(ws, li) {
    if (ws.status === 'running') {
      window.location.href = ws.url;
    } else if (ws.status === 'stopped') {
      doStart(ws, null, li);
    } else if (ws.status === 'stale') {
      doCleanup(ws, null, li);
    } else if (ws.status === 'missing') {
      doForget(ws, null, li);
    }
  }

  function busy(btn, label) {
    if (btn) { btn.disabled = true; btn.dataset.original = btn.textContent; btn.textContent = label; }
  }
  function unbusy(btn) {
    if (btn) { btn.disabled = false; if (btn.dataset.original) btn.textContent = btn.dataset.original; }
  }

  async function postJson(path, payload) {
    const resp = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) throw Object.assign(new Error(body.error || `HTTP ${resp.status}`), { body });
    return body;
  }

  function rowError(li, msg) {
    let err = li.querySelector('.viv-ws-error');
    if (!err) {
      err = document.createElement('div');
      err.className = 'viv-ws-error';
      li.appendChild(err);
    }
    err.textContent = msg;
  }

  async function doStart(ws, btn, li) {
    busy(btn, 'Starting…');
    try {
      const data = await postJson('/api/workspaces/start', { path: ws.path });
      window.location.href = data.url;
    } catch (err) {
      rowError(li, err.message + (err.body && err.body.log_path ? ` (log: ${err.body.log_path})` : ''));
      unbusy(btn);
    }
  }

  async function doStop(ws, btn, li) {
    busy(btn, 'Stopping…');
    try {
      await postJson('/api/workspaces/stop', { path: ws.path });
      refresh();
    } catch (err) {
      rowError(li, err.message + (err.body && err.body.hint ? ` — ${err.body.hint}` : ''));
      unbusy(btn);
    }
  }

  async function doCleanup(ws, btn, li) {
    busy(btn, 'Cleaning…');
    try {
      await postJson('/api/workspaces/cleanup-stale', { path: ws.path });
      refresh();
    } catch (err) {
      rowError(li, err.message);
      unbusy(btn);
    }
  }

  async function doForget(ws, btn, li) {
    busy(btn, 'Forgetting…');
    try {
      await postJson('/api/workspaces/forget', { path: ws.path });
      refresh();
    } catch (err) {
      rowError(li, err.message);
      unbusy(btn);
    }
  }

  async function doAdd() {
    const p = window.prompt('Path to workspace directory:');
    if (!p) return;
    try {
      await postJson('/api/workspaces/add', { path: p });
      refresh();
    } catch (err) {
      window.alert('Could not add: ' + err.message);
    }
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
})();
