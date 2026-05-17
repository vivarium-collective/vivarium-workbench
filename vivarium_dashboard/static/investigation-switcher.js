// Investigation switcher dropdown for the left rail.
//
// Repurposes the dropdown trigger that previously opened the workspace
// switcher modal (#viv-workspace-switcher-trigger). It now lists every
// investigation in the workspace (GET /api/iset-list), shows each one's
// effective_status as a colored pill, and lets the user:
//
//   • click an investigation row → switch the active investigation
//     (delegates to window._openInvestigationDetail, which also navigates
//     to the Investigation tab),
//   • click "+ New Investigation" at the bottom → open the existing
//     new-investigation modal owned by walkthrough.js (window._openNewIsetModal).
//
// The dropdown is a small absolute-positioned panel anchored under the
// trigger button, not a centered modal. Clicking outside, pressing Escape,
// or selecting a row closes it.

(function () {
  const trigger = document.getElementById('viv-workspace-switcher-trigger');
  if (!trigger) return;

  let menu = null;
  let outsideHandler = null;
  let escHandler = null;

  function ensureMounted() {
    if (menu) return;
    menu = document.createElement('div');
    menu.className = 'viv-iset-menu';
    menu.setAttribute('role', 'menu');
    menu.innerHTML = `
      <div class="viv-iset-menu-header">Investigations</div>
      <ul class="viv-iset-menu-list">
        <li class="viv-iset-menu-loading">Loading…</li>
      </ul>
      <div class="viv-iset-menu-divider"></div>
      <button type="button" class="viv-iset-menu-new" role="menuitem">+ New Investigation</button>
    `;
    // Mount inside the same container as the trigger so it inherits the
    // rail's stacking + positioning context.
    const container = document.getElementById('viv-workspace-switcher') || document.body;
    container.appendChild(menu);

    menu.querySelector('.viv-iset-menu-new').addEventListener('click', (e) => {
      e.stopPropagation();
      close();
      if (typeof window._openNewIsetModal === 'function') {
        window._openNewIsetModal();
      } else {
        window.alert('New-investigation modal is not available on this page.');
      }
    });

    // Click inside the menu shouldn't propagate to the outside-click handler.
    menu.addEventListener('click', (e) => { e.stopPropagation(); });
  }

  function open() {
    ensureMounted();
    menu.classList.add('open');
    trigger.setAttribute('aria-expanded', 'true');
    refresh();

    // Outside-click closes (use a microtask delay so the trigger's own
    // click doesn't immediately close the menu we just opened).
    setTimeout(() => {
      outsideHandler = (e) => {
        if (menu && !menu.contains(e.target) && !trigger.contains(e.target)) close();
      };
      document.addEventListener('click', outsideHandler);
    }, 0);
    escHandler = (e) => { if (e.key === 'Escape') close(); };
    document.addEventListener('keydown', escHandler);
  }

  function close() {
    if (!menu) return;
    menu.classList.remove('open');
    trigger.setAttribute('aria-expanded', 'false');
    if (outsideHandler) {
      document.removeEventListener('click', outsideHandler);
      outsideHandler = null;
    }
    if (escHandler) {
      document.removeEventListener('keydown', escHandler);
      escHandler = null;
    }
  }

  trigger.addEventListener('click', (e) => {
    e.stopPropagation();
    const isOpen = menu && menu.classList.contains('open');
    if (isOpen) close(); else open();
  });

  async function refresh() {
    const list = menu.querySelector('.viv-iset-menu-list');
    list.innerHTML = '<li class="viv-iset-menu-loading">Loading…</li>';
    try {
      const resp = await fetch('/api/iset-list', { headers: { Accept: 'application/json' } });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const data = await resp.json();
      render(data.investigations || []);
    } catch (err) {
      list.innerHTML = '<li class="viv-iset-menu-error">Failed to load: '
        + escapeHtml(String(err)) + '</li>';
    }
  }

  function render(isets) {
    const list = menu.querySelector('.viv-iset-menu-list');
    list.innerHTML = '';
    if (!isets.length) {
      const li = document.createElement('li');
      li.className = 'viv-iset-menu-empty';
      li.textContent = 'No investigations yet.';
      list.appendChild(li);
      return;
    }
    const activeName = window._currentIset || '';
    isets.forEach((iset) => list.appendChild(renderRow(iset, activeName)));
  }

  function renderRow(iset, activeName) {
    const li = document.createElement('li');
    li.className = 'viv-iset-menu-row';
    li.setAttribute('role', 'menuitem');
    li.tabIndex = 0;
    if (iset.name === activeName) li.classList.add('viv-iset-menu-row-current');

    const effStatus = iset.effective_status || iset.status || 'planning';
    const pillClass = effStatus.replace(/[^a-z_]/g, '_');
    const title = iset.title || iset.name;

    li.innerHTML = `
      <div class="viv-iset-menu-row-line1">
        <strong class="viv-iset-menu-row-title">${escapeHtml(title)}</strong>
        <span class="status-pill ${escapeHtml(pillClass)} viv-iset-menu-row-pill">${escapeHtml(effStatus)}</span>
      </div>
      <div class="viv-iset-menu-row-slug">${escapeHtml(iset.name)}${
        iset.name === activeName ? ' <span class="viv-iset-menu-row-current-tag">(active)</span>' : ''
      }</div>
    `;

    li.addEventListener('click', (e) => {
      e.stopPropagation();
      close();
      switchInvestigation(iset.name);
    });
    li.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        close();
        switchInvestigation(iset.name);
      }
    });
    return li;
  }

  function switchInvestigation(name) {
    // Make sure the Investigation tab is active, then open the chosen iset.
    if (typeof window._switchPage === 'function') {
      try { window._switchPage('investigations'); } catch (_) { /* ignore */ }
    }
    if (typeof window._openInvestigationDetail === 'function') {
      window._openInvestigationDetail(name);
    } else {
      // Fallback — just navigate via hash and let the page router pick it up.
      window.location.hash = '#investigations';
    }
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
})();
