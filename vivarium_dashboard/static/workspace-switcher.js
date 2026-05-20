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
          <button type="button" class="viv-ws-modal-new viv-ws-modal-new-primary">+ New Workspace…</button>
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
    modal.querySelector('.viv-ws-modal-new').addEventListener('click', () => {
      close();
      openCreate();
    });
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

  // -----------------------------------------------------------------------
  // New Workspace modal (todo #8 Phase B)
  //
  // Lives in this file (rather than its own static asset) because it
  // composes with the existing switcher modal and shares its styling.
  // Org field is a free-text input in Phase B; Phase B-extension upgrades
  // it to a dropdown sourced from /api/auth/github/orgs.
  // -----------------------------------------------------------------------

  // Slug rule: matches the server-side study-slug regex used by Phase C.
  // ``^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$`` — underscores allowed; leading
  // / trailing must be alphanumeric. Single-char names like "x" pass.
  const SLUG_RE = /^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$|^[a-z0-9]$/;

  // Backend dropdown options — hardcoded for v1 per Phase B plan. Switch to
  // GET /api/compute-backends once we have a second HPC site.
  const BACKENDS = [
    { value: 'local',    label: 'local',
      hint: 'Run simulations on this machine.' },
    { value: 'hpc:ccam', label: 'hpc:ccam',
      hint: 'Scaffolds a Singularity.def 1:1 with the Dockerfile; runs via SLURM/Singularity over SSH.' },
  ];

  let createModal = null;
  let createEscHandler = null;

  function normaliseOrg(raw) {
    let v = String(raw || '').trim();
    if (!v) return '';
    // Strip URL prefix variants.
    v = v.replace(/^https?:\/\/github\.com\//i, '');
    v = v.replace(/\/$/, '');
    // After stripping, the remainder should be a single path segment.
    if (v.indexOf('/') !== -1) v = v.split('/')[0];
    return v;
  }

  function ensureCreateMounted() {
    if (createModal) return;
    createModal = document.createElement('div');
    createModal.className = 'viv-ws-modal viv-ws-modal-create';
    const backendOptions = BACKENDS.map((b) =>
      `<option value="${escapeHtml(b.value)}">${escapeHtml(b.label)}</option>`
    ).join('');
    createModal.innerHTML = `
      <div class="viv-ws-modal-card" role="dialog" aria-label="New Workspace">
        <div class="viv-ws-modal-header">
          <h2>New Workspace</h2>
          <button type="button" class="viv-ws-modal-close" aria-label="Close">✕</button>
        </div>
        <form class="viv-ws-create-form" novalidate>
          <label for="viv-ws-create-name">Workspace name <span class="viv-ws-req">*</span></label>
          <input id="viv-ws-create-name" name="name" type="text"
                 placeholder="Enter Workspace Name" autocomplete="off" required>
          <div class="viv-ws-field-hint">
            Lowercase letters, digits, ``-``, ``_``. Must start and end with
            alphanumeric.
          </div>

          <label for="viv-ws-create-org">GitHub Organization <span class="viv-ws-optional">(optional)</span></label>
          <select id="viv-ws-create-org-select" name="github_org_select"
                  style="display:none">
          </select>
          <input id="viv-ws-create-org" name="github_org" type="text"
                 placeholder="https://github.com/&lt;org&gt;" autocomplete="off">
          <div class="viv-ws-field-hint viv-ws-org-hint">
            Leave blank to keep the workspace local-only. Bare org name or
            full URL both work.
          </div>

          <label for="viv-ws-create-backend">Compute backend <span class="viv-ws-req">*</span></label>
          <select id="viv-ws-create-backend" name="backend" required>
            ${backendOptions}
          </select>
          <div class="viv-ws-field-hint viv-ws-backend-hint"></div>

          <div class="viv-ws-create-error" role="alert" aria-live="polite"></div>

          <div class="viv-ws-create-actions">
            <button type="button" class="viv-ws-create-cancel">Cancel</button>
            <button type="submit" class="viv-ws-create-submit viv-ws-modal-new-primary">
              Create workspace
            </button>
          </div>
        </form>
      </div>
    `;
    document.body.appendChild(createModal);

    createModal.addEventListener('click', (e) => {
      if (e.target === createModal) closeCreate();
    });
    createModal.querySelector('.viv-ws-modal-close').addEventListener('click', closeCreate);
    createModal.querySelector('.viv-ws-create-cancel').addEventListener('click', closeCreate);

    const backendSel = createModal.querySelector('#viv-ws-create-backend');
    const backendHint = createModal.querySelector('.viv-ws-backend-hint');
    const updateBackendHint = () => {
      const b = BACKENDS.find((x) => x.value === backendSel.value);
      backendHint.textContent = b ? b.hint : '';
    };
    backendSel.addEventListener('change', updateBackendHint);
    updateBackendHint();

    createModal.querySelector('.viv-ws-create-form').addEventListener('submit', (e) => {
      e.preventDefault();
      submitCreate();
    });
  }

  // Sentinel value used by the org <select> to switch back to free-text
  // mode ("+ Other…" option).
  const ORG_SELECT_OTHER = '__viv_other__';
  const ORG_SELECT_NONE = '';  // "Leave blank — local-only workspace"

  async function loadOrgsIntoSelect() {
    """Fetch the user's GitHub orgs and populate the org <select>.

    On 200: replace the free-text input with a populated <select>.
    On 401 (unauthenticated): leave the input visible and append a hint.
    On other errors: leave the input visible silently.
    """
    const sel = createModal.querySelector('#viv-ws-create-org-select');
    const inp = createModal.querySelector('#viv-ws-create-org');
    const hint = createModal.querySelector('.viv-ws-org-hint');

    // Reset to a known state every time the modal opens.
    sel.innerHTML = '';
    sel.style.display = 'none';
    inp.style.display = '';
    inp.value = '';

    let resp;
    try {
      resp = await fetch('/api/auth/github/orgs');
    } catch (_e) {
      return; // Network error — leave as free-text.
    }
    if (resp.status === 401) {
      hint.textContent =
        'Sign in with GitHub (header chip) to pick from your orgs in a dropdown. ' +
        'Or leave this field blank for a local-only workspace.';
      return;
    }
    if (!resp.ok) return;
    const data = await resp.json().catch(() => null);
    if (!data || !Array.isArray(data.orgs)) return;

    // Build options. Default = empty value (local-only).
    const blankOpt = document.createElement('option');
    blankOpt.value = ORG_SELECT_NONE;
    blankOpt.textContent = '— None (local-only) —';
    sel.appendChild(blankOpt);

    data.orgs.forEach((o) => {
      const opt = document.createElement('option');
      opt.value = o.name;
      const tag = o.kind === 'personal' ? ' (personal)' : '';
      opt.textContent = o.name + tag;
      sel.appendChild(opt);
    });
    // "+ Other…" entry toggles back to free-text input.
    const otherOpt = document.createElement('option');
    otherOpt.value = ORG_SELECT_OTHER;
    otherOpt.textContent = '+ Other…';
    sel.appendChild(otherOpt);

    sel.style.display = '';
    inp.style.display = 'none';
    hint.textContent =
      'Pick a personal namespace or an org you have access to. "+ Other…" reverts to free text.';

    sel.onchange = () => {
      if (sel.value === ORG_SELECT_OTHER) {
        sel.style.display = 'none';
        inp.style.display = '';
        inp.value = '';
        inp.focus();
      }
    };
  }

  function openCreate() {
    ensureCreateMounted();
    // Reset state in case the user opened-cancelled-reopened.
    createModal.querySelector('#viv-ws-create-name').value = '';
    createModal.querySelector('#viv-ws-create-org').value = '';
    createModal.querySelector('#viv-ws-create-backend').selectedIndex = 0;
    createModal.querySelector('.viv-ws-create-error').textContent = '';
    createModal.querySelector('.viv-ws-create-submit').disabled = false;
    createModal.querySelector('.viv-ws-create-submit').textContent = 'Create workspace';
    createModal.classList.add('open');
    createEscHandler = (e) => { if (e.key === 'Escape') closeCreate(); };
    document.addEventListener('keydown', createEscHandler);
    // Focus name field for fast typing.
    setTimeout(() => {
      createModal.querySelector('#viv-ws-create-name').focus();
    }, 0);
    // Phase B-extension: try to populate the org dropdown. Non-blocking —
    // the form is usable as free-text while the request is in flight or if
    // it fails.
    loadOrgsIntoSelect().catch(() => { /* leave as free-text */ });
  }

  function closeCreate() {
    if (!createModal) return;
    createModal.classList.remove('open');
    if (createEscHandler) {
      document.removeEventListener('keydown', createEscHandler);
      createEscHandler = null;
    }
  }

  function setCreateError(msg) {
    if (!createModal) return;
    createModal.querySelector('.viv-ws-create-error').textContent = msg;
  }

  async function submitCreate() {
    const nameEl = createModal.querySelector('#viv-ws-create-name');
    const orgSelect = createModal.querySelector('#viv-ws-create-org-select');
    const orgInput = createModal.querySelector('#viv-ws-create-org');
    const backendEl = createModal.querySelector('#viv-ws-create-backend');
    const submitBtn = createModal.querySelector('.viv-ws-create-submit');

    const name = String(nameEl.value || '').trim();
    // Read from whichever element is visible: the <select> when orgs were
    // loaded (Phase B-extension), or the free-text <input> when the user
    // chose "+ Other…" or the fetch failed / returned 401.
    const orgEl = orgSelect.style.display !== 'none' ? orgSelect : orgInput;
    const orgRaw = String(orgEl.value || '').trim();
    const backend = String(backendEl.value || '').trim();

    if (!name) { setCreateError('Workspace name is required.'); nameEl.focus(); return; }
    if (!SLUG_RE.test(name)) {
      setCreateError(
        'Invalid name. Use lowercase letters, digits, hyphens, and underscores; ' +
        'must start and end with an alphanumeric character.'
      );
      nameEl.focus();
      return;
    }
    if (!backend) { setCreateError('Compute backend is required.'); return; }

    const org = normaliseOrg(orgRaw);
    if (orgRaw && !org) {
      setCreateError('GitHub org could not be parsed from "' + orgRaw + '".');
      return;
    }

    setCreateError('');
    submitBtn.disabled = true;
    submitBtn.textContent = 'Scaffolding…';

    const payload = { name, backend };
    if (org) payload.github_org = org;

    try {
      const data = await postJson('/api/workspaces/create', payload);
      // Success: the endpoint spawns the child dashboard and returns its URL.
      // Redirect the browser there. If the response is missing url (older
      // server / future schema change), fall back to refreshing the switcher.
      if (data && data.url) {
        window.location.href = data.url;
        return;
      }
      closeCreate();
      // Re-open the switcher so the user sees the new workspace appear.
      open();
    } catch (err) {
      // postJson rejects with a structured error from the backend.
      let msg = err.message || 'Unknown error';
      if (err.body) {
        if (err.body.hint) msg += ' — ' + err.body.hint;
        if (err.body.detail) msg += ' (' + err.body.detail + ')';
      }
      setCreateError(msg);
      submitBtn.disabled = false;
      submitBtn.textContent = 'Create workspace';
    }
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
})();
