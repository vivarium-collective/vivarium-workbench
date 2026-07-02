// GitHub OAuth Device Flow client (todo #8 Phase B-bis).
//
// On load: GET /api/auth/github/status; render the header chip
// (#viv-gh-chip). Unauthenticated → "Sign in with GitHub". Authenticated →
// "@<login>" + small "(via gh)" hint when source==gh_cli, with a click handler
// to log out.
//
// Click while unauthenticated: POST /api/auth/github/start → modal with the
// user_code + verification URL → opens the URL in a new tab → polls
// /api/auth/github/poll until ok/expired/denied. On success: refresh the chip.

(function () {
  const chip = document.getElementById('viv-gh-chip');
  if (!chip) return;

  let modal = null;
  let pollTimer = null;
  let currentFlowId = null;

  // -----------------------------------------------------------------------
  // Status & chip rendering
  // -----------------------------------------------------------------------

  async function fetchStatus() {
    try {
      const resp = await fetch('/api/auth/github/status');
      return await resp.json();
    } catch (_e) {
      return { authenticated: false };
    }
  }

  function renderChip(status) {
    chip.dataset.state = status.authenticated ? 'in' : 'out';
    chip.innerHTML = '';
    if (status.authenticated) {
      const label = document.createElement('span');
      label.textContent = '@' + (status.login || '?');
      chip.appendChild(label);
      if (status.source === 'gh_cli') {
        const src = document.createElement('span');
        src.className = 'viv-gh-source';
        src.textContent = '(via gh)';
        chip.appendChild(src);
      }
      chip.title = 'Click to sign out';
      chip.onclick = doLogout;
    } else {
      chip.textContent = 'Sign in with GitHub';
      chip.title = 'Start GitHub OAuth Device Flow';
      chip.onclick = startFlow;
    }
  }

  async function refreshChip() {
    chip.dataset.state = 'loading';
    chip.textContent = 'Loading…';
    chip.onclick = null;
    const status = await fetchStatus();
    renderChip(status);
  }

  // -----------------------------------------------------------------------
  // Logout
  // -----------------------------------------------------------------------

  async function doLogout() {
    chip.dataset.state = 'loading';
    chip.textContent = 'Signing out…';
    chip.onclick = null;
    try {
      await fetch('/api/auth/github/logout', { method: 'POST' });
    } catch (_e) { /* best-effort */ }
    refreshChip();
  }

  // -----------------------------------------------------------------------
  // Device Flow modal
  // -----------------------------------------------------------------------

  function ensureModal() {
    if (modal) return;
    modal = document.createElement('div');
    modal.className = 'viv-gh-modal';
    modal.style.cssText = `
      display: none; position: fixed; inset: 0;
      background: rgba(0,0,0,0.45); z-index: 2000;
      align-items: center; justify-content: center;
    `;
    modal.innerHTML = `
      <div class="viv-gh-card" style="
        background: var(--panel, #fff); color: var(--text, #1a1a1a);
        border-radius: 8px; padding: 24px 28px;
        min-width: 380px; max-width: 520px;
        box-shadow: 0 8px 32px rgba(0,0,0,0.18);
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      ">
        <h3 style="margin: 0 0 12px 0;">Sign in with GitHub</h3>
        <p class="viv-gh-instructions" style="margin: 0 0 16px 0; font-size: 14px; line-height: 1.5;">
          Enter this code on GitHub:
        </p>
        <div class="viv-gh-usercode" style="
          font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
          font-size: 28px; font-weight: 700; letter-spacing: 4px;
          text-align: center; padding: 14px;
          background: var(--page, #f7f7f8);
          border: 1px dashed var(--border, #d0d0d4);
          border-radius: 6px; cursor: pointer; user-select: all;
          margin-bottom: 16px;
        " title="Click to copy">······</div>
        <p style="margin: 0 0 16px 0; font-size: 13px;">
          <a class="viv-gh-link" href="#" target="_blank" rel="noopener" style="color: var(--accent, #2563eb); font-weight: 600;">
            Open github.com/login/device →
          </a>
        </p>
        <p class="viv-gh-poll-status" style="margin: 0 0 16px 0; font-size: 13px; color: var(--muted, #666);">
          Waiting for you to authorize…
        </p>
        <div style="display: flex; gap: 8px; justify-content: flex-end;">
          <button type="button" class="viv-gh-cancel" style="
            appearance: none; padding: 8px 16px; border-radius: 6px;
            border: 1px solid var(--border, #d0d0d4); background: var(--panel, #fff);
            color: var(--text, #1a1a1a); font-size: 14px; cursor: pointer;
          ">Cancel</button>
        </div>
      </div>
    `;
    document.body.appendChild(modal);
    modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });
    modal.querySelector('.viv-gh-cancel').addEventListener('click', closeModal);
    modal.querySelector('.viv-gh-usercode').addEventListener('click', (e) => {
      const txt = e.currentTarget.textContent;
      navigator.clipboard?.writeText(txt).catch(() => { /* no-op */ });
    });
  }

  function openModal(payload) {
    ensureModal();
    modal.querySelector('.viv-gh-usercode').textContent = payload.user_code;
    const link = modal.querySelector('.viv-gh-link');
    const verifyUrl = payload.verification_uri_complete || payload.verification_uri;
    link.href = verifyUrl;
    link.textContent = 'Open ' + payload.verification_uri + ' →';
    modal.querySelector('.viv-gh-poll-status').textContent = 'Waiting for you to authorize…';
    modal.style.display = 'flex';
    // Open the verification URL in a new tab so the user doesn't have to
    // copy/paste. Browsers block popups outside trusted gestures — startFlow
    // *is* a trusted gesture (the chip click), but the await before this
    // breaks that chain in some browsers. We try anyway; the link in the
    // modal is the fallback.
    try { window.open(verifyUrl, '_blank', 'noopener'); } catch (_e) { /* ignored */ }
  }

  function closeModal() {
    if (modal) modal.style.display = 'none';
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
    currentFlowId = null;
  }

  // -----------------------------------------------------------------------
  // Flow start + polling
  // -----------------------------------------------------------------------

  async function startFlow() {
    let resp;
    try {
      resp = await fetch('/api/auth/github/start', { method: 'POST' });
    } catch (e) {
      window.alert('Network error: ' + e.message);
      return;
    }
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const hint = body.hint ? ` — ${body.hint}` : '';
      const detail = body.detail ? ` (${body.detail})` : '';
      window.alert(`Could not start sign-in: ${body.error || resp.status}${detail}${hint}`);
      return;
    }
    currentFlowId = body.flow_id;
    openModal(body);
    schedulePoll(body.interval || 5);
  }

  function schedulePoll(intervalSeconds) {
    pollTimer = setTimeout(() => poll(intervalSeconds), Math.max(1, intervalSeconds) * 1000);
  }

  async function poll(prevInterval) {
    if (!currentFlowId) return;
    let resp;
    try {
      resp = await fetch('/api/auth/github/poll?flow_id=' + encodeURIComponent(currentFlowId));
    } catch (_e) {
      schedulePoll(prevInterval);
      return;
    }
    const body = await resp.json().catch(() => ({}));
    const setStatus = (msg) => {
      if (modal) modal.querySelector('.viv-gh-poll-status').textContent = msg;
    };
    if (body.status === 'ok') {
      setStatus('Signed in as @' + body.login + '. You can close this dialog.');
      currentFlowId = null;
      setTimeout(closeModal, 800);
      refreshChip();
      return;
    }
    if (body.status === 'pending') {
      schedulePoll(body.interval || prevInterval);
      return;
    }
    if (body.status === 'expired') {
      setStatus('Code expired. Close this dialog and try again.');
      currentFlowId = null;
      return;
    }
    if (body.status === 'denied') {
      setStatus('Access denied on GitHub. Close this dialog.');
      currentFlowId = null;
      return;
    }
    setStatus('Error: ' + (body.detail || resp.status));
    currentFlowId = null;
  }

  // -----------------------------------------------------------------------
  // Boot
  // -----------------------------------------------------------------------

  refreshChip();
})();
