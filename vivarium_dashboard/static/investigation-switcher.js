// Cross-worktree Investigation switcher dropdown for the left rail.
//
// Reads /api/investigation-registry — an aggregated view across every
// dashboard server on this host AND every open investigation on every
// sibling git worktree, whether or not its dashboard is running.
//
// Layout (top → bottom):
//
//   ┌─────────────────────────────────────────────┐
//   │ Investigations                              │   header
//   ├─────────────────────────────────────────────┤
//   │ [active here] <current slug>      [pill]    │   THIS workspace, current iset
//   ├──── ALSO IN THIS WORKSPACE ─────────────────┤   (hidden if empty)
//   │ <slug>                            [pill]    │   click → activate locally
//   ├──── OTHER WORKTREES (LIVE) ────────────────┤   (hidden if empty)
//   │ <slug>                            [pill] →  │   click → open peer URL
//   ├──── DORMANT WORKTREES ─────────────────────┤   (hidden if empty)
//   │ <slug> · <branch> · <status>     [dormant]  │   click → boot-cmd prompt
//   ├─────────────────────────────────────────────┤
//   │ + New Investigation                         │
//   └─────────────────────────────────────────────┘
//
// Picker for "current": (a) investigation whose slug matches the current
// git branch, (b) any with effective_status=running, (c) first
// alphabetically — server side, in /api/investigation-registry.
//
// Click behaviour by section:
//   - CURRENT / ALSO IN THIS WORKSPACE → activate locally (in-place).
//   - OTHER WORKTREES (LIVE)           → open peer URL in a new tab.
//   - DORMANT WORKTREES                → prompt with the boot command
//                                        (/pbg-investigation open <slug>);
//                                        no peer URL to navigate to yet.
//
// "+ New Investigation" still calls window._openNewIsetModal (provided by
// walkthrough.js), which scaffolds the YAML in THIS workspace.

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
      <div class="viv-iset-menu-header">Repositories</div>
      <ul class="viv-iset-menu-list">
        <li class="viv-iset-menu-loading">Loading…</li>
      </ul>
      <div class="viv-iset-menu-divider"></div>
      <button type="button" class="viv-iset-menu-new" role="menuitem">+ New Investigation</button>
    `;
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

    menu.addEventListener('click', (e) => { e.stopPropagation(); });
  }

  function open() {
    ensureMounted();
    menu.classList.add('open');
    trigger.setAttribute('aria-expanded', 'true');
    refresh();

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
    // The top-left dropdown switches REPOS (workspaces). Investigation
    // switching now lives in the Investigations list view. Studies-sidebar
    // sync still rides the on-load /api/investigation-registry call below.
    const list = menu.querySelector('.viv-iset-menu-list');
    list.innerHTML = '<li class="viv-iset-menu-loading">Loading…</li>';
    try {
      const resp = await fetch('/api/workspaces', { headers: { Accept: 'application/json' } });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      renderRepos(await resp.json());
    } catch (err) {
      list.innerHTML = '<li class="viv-iset-menu-error">Failed to load repos: '
        + escapeHtml(String(err)) + '</li>';
    }
  }

  function renderRepos(data) {
    const list = menu.querySelector('.viv-iset-menu-list');
    list.innerHTML = '';
    const cur = (data && data.current) || {};
    // Filter the catalog to a usable list: drop missing/placeholder entries,
    // de-dupe by path, and order current -> running -> the rest.
    const RANK = { current: 0, running: 1, stale: 2, stopped: 3 };
    // One row per REPO (by name): multiple v2ecoli worktrees/servers collapse to a
    // single repo entry — those are investigations within the repo, not repos.
    // Sort by status first so de-dupe keeps the best (current > running > ...).
    const seen = new Set();
    const repos = ((data && data.workspaces) || [])
      .filter((w) => w && w.status !== 'missing' && (w.name || '') !== 'placeholder')
      .sort((a, b) => (RANK[a.status] ?? 4) - (RANK[b.status] ?? 4)
                       || String(a.name).localeCompare(String(b.name)))
      .filter((w) => { const k = w.name || w.path; if (seen.has(k)) return false; seen.add(k); return true; });
    if (!repos.length) {
      const li = document.createElement('li');
      li.className = 'viv-iset-menu-empty';
      li.textContent = 'No repos registered. Open one with /pbg-dashboard.';
      list.appendChild(li);
      return;
    }
    repos.forEach((w) => {
      const isCurrent = w.status === 'current' || (cur.path && w.path === cur.path);
      const status = isCurrent ? 'current' : (w.status || 'stopped');
      const li = document.createElement('li');
      li.className = 'viv-iset-menu-row' + (isCurrent ? ' viv-iset-menu-row-current' : '');
      li.setAttribute('role', 'menuitem');
      li.tabIndex = 0;
      li.innerHTML =
        '<div class="viv-iset-menu-row-line1">'
        + '<strong class="viv-iset-menu-row-title">' + escapeHtml(w.name || '') + '</strong>'
        + '<span class="status-pill ' + escapeHtml(status.replace(/[^a-z_]/g, '_'))
        + ' viv-iset-menu-row-pill">' + escapeHtml(status) + '</span></div>'
        + '<div class="viv-iset-menu-row-slug">' + escapeHtml(w.path || '') + '</div>';
      if (!isCurrent) {
        li.addEventListener('click', (e) => {
          e.stopPropagation();
          close();
          if (w.url) {
            window.location.assign(w.url);
          } else {
            window.alert('No running dashboard for "' + (w.name || '') + '".\nStart it from that repo:  /pbg-dashboard open');
          }
        });
      }
      list.appendChild(li);
    });
  }

  // Sync the workspace-switcher trigger button's label with the current
  // investigation from the registry. The static label is baked into
  // reports/index.html at /pbg-report time and only carries the iset
  // suffix when there's exactly one investigation (lib/report.py); this
  // updates it client-side for multi-iset workspaces and after iset
  // switches.
  function updateTriggerLabel(current) {
    const strong = trigger.querySelector('strong');
    if (!strong) return;
    const raw = (strong.textContent || '').trim();
    const workspaceName = raw.split(':')[0];
    if (!workspaceName) return;
    strong.textContent = workspaceName;  // repo-only label (top-left switches repos)
  }

  // Surface the current iset slug as a window-level signal so the rail's
  // STUDIES section (walkthrough.js `_renderRailInvestigationGroups`)
  // can scope itself to only the current investigation's studies.
  // Re-rendering the rail after we know the current slug collapses the
  // multi-investigation groups (e.g., colonies + v2ecoli-pdmp) down to
  // just one — matching the dropdown's "current" selection.
  function publishCurrentSlug(current) {
    const slug = current && current.slug ? current.slug : '';
    if (window._currentIsetSlug === slug) return;
    window._currentIsetSlug = slug;
    if (typeof window._renderRailInvestigationGroups === 'function'
        && Array.isArray(window._investigations)
        && Array.isArray(window._isetIndex)
        && window._investigations.length) {
      try { window._renderRailInvestigationGroups(); } catch (_) { /* ignore */ }
    } else if (typeof window._vivRefreshInvestigationsRail === 'function') {
      try { window._vivRefreshInvestigationsRail(); } catch (_) { /* ignore */ }
    }
    // If the Investigation tab is currently visible AND showing a
    // different iset, swap its detail view to the freshly-published
    // current slug. Without this, the tab stays stuck on whatever it
    // picked at mount time (typically the alphabetically-first iset).
    if (slug
        && typeof window._openInvestigationDetail === 'function'
        && window._currentIset
        && window._currentIset !== slug
        && Array.isArray(window._isetIndex)
        && window._isetIndex.some((i) => i && i.name === slug)) {
      const detailEl = document.getElementById('investigation-detail-view');
      const isVisible = detailEl && detailEl.offsetParent !== null;
      if (isVisible) {
        try { window._openInvestigationDetail(slug); } catch (_) { /* ignore */ }
      }
    }
  }

  // Refresh both label + current-slug on page load — without this, the
  // label stays stuck on the baked-in static value and the rail keeps
  // showing every investigation's studies until the user opens the
  // dropdown.
  // Top-left is a REPO switcher now: strip any baked ":investigation" suffix
  // from the trigger label and do NOT pre-select an investigation. The current
  // investigation (and the STUDIES sidebar scope) is driven by the user picking
  // a card in the Investigations list view (list-first UX).
  updateTriggerLabel(null);

  function render({ current, localSiblings, runningOthers, dormantOthers }) {
    const list = menu.querySelector('.viv-iset-menu-list');
    list.innerHTML = '';

    // ── INVESTIGATIONS — this worktree's current investigation. ──
    if (current && current.slug) {
      list.appendChild(renderCurrentRow(current));
    } else {
      const li = document.createElement('li');
      li.className = 'viv-iset-menu-empty';
      li.textContent = 'No investigations in this worktree yet.';
      list.appendChild(li);
    }

    // ── ALSO IN THIS WORKSPACE — other local investigation.yamls. ──
    // These are siblings on the same on-disk tree as `current` (no
    // separate worktree, no separate dashboard). Clicking activates
    // the investigation detail in this same dashboard.
    if (localSiblings && localSiblings.length) {
      appendSectionHeader(list, 'ALSO IN THIS WORKSPACE');
      localSiblings.forEach((s) => list.appendChild(renderLocalSiblingRow(s)));
    }

    // ── OTHER WORKTREES — peer dashboards live RIGHT NOW. ──
    if (runningOthers && runningOthers.length) {
      appendSectionHeader(list, 'OTHER WORKTREES (LIVE)');
      runningOthers.forEach((peer) => list.appendChild(renderPeerRow(peer)));
    }

    // ── DORMANT WORKTREES — open investigations on sibling worktrees ──
    // whose dashboard is not running. Click shows instructions for
    // booting the peer dashboard. Closed / archived investigations are
    // already filtered server-side.
    if (dormantOthers && dormantOthers.length) {
      appendSectionHeader(list, 'DORMANT WORKTREES');
      dormantOthers.forEach((d) => list.appendChild(renderDormantRow(d)));
    }
  }

  function appendSectionHeader(list, label) {
    const divider = document.createElement('li');
    divider.className = 'viv-iset-menu-section';
    divider.textContent = label;
    list.appendChild(divider);
  }

  function renderCurrentRow(current) {
    const li = document.createElement('li');
    li.className = 'viv-iset-menu-row viv-iset-menu-row-current';
    li.setAttribute('role', 'menuitem');
    li.tabIndex = 0;

    const effStatus = current.effective_status || 'planning';
    const pillClass = effStatus.replace(/[^a-z_]/g, '_');
    const title = current.title || current.slug;

    li.innerHTML = `
      <div class="viv-iset-menu-row-line1">
        <strong class="viv-iset-menu-row-title">${escapeHtml(title)}</strong>
        <span class="status-pill ${escapeHtml(pillClass)} viv-iset-menu-row-pill">${escapeHtml(effStatus)}</span>
      </div>
      <div class="viv-iset-menu-row-slug">${escapeHtml(current.slug)}
        <span class="viv-iset-menu-row-current-tag">(active here)</span>
      </div>
    `;

    const activate = () => {
      close();
      switchInvestigationLocal(current.slug);
    };
    li.addEventListener('click', (e) => { e.stopPropagation(); activate(); });
    li.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); activate(); }
    });
    return li;
  }

  function renderLocalSiblingRow(s) {
    const li = document.createElement('li');
    li.className = 'viv-iset-menu-row viv-iset-menu-row-local-sibling';
    li.setAttribute('role', 'menuitem');
    li.tabIndex = 0;

    const effStatus = s.effective_status || 'planning';
    const pillClass = effStatus.replace(/[^a-z_]/g, '_');
    const title = s.title || s.slug;

    li.innerHTML = `
      <div class="viv-iset-menu-row-line1">
        <strong class="viv-iset-menu-row-title">${escapeHtml(title)}</strong>
        <span class="status-pill ${escapeHtml(pillClass)} viv-iset-menu-row-pill">${escapeHtml(effStatus)}</span>
      </div>
      <div class="viv-iset-menu-row-slug">${escapeHtml(s.slug || '')}</div>
    `;

    const activate = () => {
      close();
      switchInvestigationLocal(s.slug);
    };
    li.addEventListener('click', (e) => { e.stopPropagation(); activate(); });
    li.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); activate(); }
    });
    return li;
  }

  function renderDormantRow(d) {
    const li = document.createElement('li');
    li.className = 'viv-iset-menu-row viv-iset-menu-row-dormant';
    li.setAttribute('role', 'menuitem');
    li.tabIndex = 0;

    const status   = d.status || 'open';
    const title    = d.title  || d.slug;
    const branch   = d.branch || '';
    // variants is server-side dedupe of worktrees carrying the same
    // investigation slug. Always includes the canonical worktree as the
    // first entry; older servers may omit it, so default to [d].
    const variants = Array.isArray(d.variants) && d.variants.length
      ? d.variants
      : [{ worktree_path: d.worktree_path, branch: d.branch, status: d.status }];
    const extraCount = Math.max(0, variants.length - 1);

    // Tooltip lists every worktree carrying this investigation.
    const tooltipLines = variants.map((v) => {
      const br = v.branch || '(detached)';
      return `${v.worktree_path} (${br})`;
    });
    li.title = `${title}\n${tooltipLines.join('\n')}\n` +
               `No dashboard running. Click for instructions to boot it.`;

    const variantsBadge = extraCount > 0
      ? `<span class="viv-iset-menu-row-variants" title="${escapeHtml(tooltipLines.join('\n'))}"> · +${extraCount} worktree${extraCount === 1 ? '' : 's'}</span>`
      : '';

    li.innerHTML = `
      <div class="viv-iset-menu-row-line1">
        <strong class="viv-iset-menu-row-title">${escapeHtml(title)}</strong>
        <span class="viv-iset-menu-row-pill viv-iset-menu-row-pill-dormant">dormant</span>
      </div>
      <div class="viv-iset-menu-row-slug">
        ${escapeHtml(d.slug || '')}
        ${branch ? `<span class="viv-iset-menu-row-branch"> · ${escapeHtml(branch)}</span>` : ''}
        ${status ? `<span class="viv-iset-menu-row-status"> · ${escapeHtml(status)}</span>` : ''}
        ${variantsBadge}
      </div>
    `;

    const activate = () => {
      close();
      // No live URL to navigate to. Show a small toast with the boot
      // command. The user invokes /pbg-investigation open <slug> in
      // their terminal, which creates the worktree (if missing) and
      // boots its dashboard, after which it'll show up in
      // OTHER WORKTREES (LIVE) on the next dropdown refresh.
      const cmd = `/pbg-investigation open ${d.slug}`;
      const ok = window.prompt(
        `${title} has no running dashboard.\n\n` +
        `Run this in your terminal to boot it:\n\n${cmd}\n\n` +
        `(Press OK to copy to clipboard; Cancel to dismiss.)`,
        cmd,
      );
      if (ok !== null && navigator.clipboard) {
        navigator.clipboard.writeText(cmd).catch(() => {});
      }
    };
    li.addEventListener('click', (e) => { e.stopPropagation(); activate(); });
    li.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); activate(); }
    });
    return li;
  }

  function renderPeerRow(peer) {
    const li = document.createElement('li');
    li.className = 'viv-iset-menu-row viv-iset-menu-row-peer';
    li.setAttribute('role', 'menuitem');
    li.tabIndex = 0;
    li.title = `Open ${peer.url} (worktree: ${peer.worktree_path})`;

    const effStatus = peer.effective_status || 'unknown';
    const pillClass = effStatus.replace(/[^a-z_]/g, '_');
    const title = peer.title || peer.slug || '(unnamed)';

    li.innerHTML = `
      <div class="viv-iset-menu-row-line1">
        <strong class="viv-iset-menu-row-title">${escapeHtml(title)}</strong>
        <span class="status-pill ${escapeHtml(pillClass)} viv-iset-menu-row-pill">${escapeHtml(effStatus)}</span>
        <span class="viv-iset-menu-row-arrow" aria-hidden="true">→</span>
      </div>
      <div class="viv-iset-menu-row-slug">${escapeHtml(peer.slug || '')}</div>
    `;

    const activate = () => {
      close();
      // Open the peer dashboard in a new tab — it lives in a different
      // worktree, so navigating to it in this tab would orphan the user's
      // state in the current worktree.
      window.open(peer.url, '_blank', 'noopener');
    };
    li.addEventListener('click', (e) => { e.stopPropagation(); activate(); });
    li.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); activate(); }
    });
    return li;
  }

  function switchInvestigationLocal(name) {
    if (typeof window._switchPage === 'function') {
      try { window._switchPage('investigations'); } catch (_) { /* ignore */ }
    }
    if (typeof window._openInvestigationDetail === 'function') {
      window._openInvestigationDetail(name);
    } else {
      window.location.hash = '#investigations';
    }
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
})();
