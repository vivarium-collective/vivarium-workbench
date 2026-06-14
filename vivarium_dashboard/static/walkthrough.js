// walkthrough.js — v0.6.0: system-deps awareness — pre-install check + consent modal (_installFromCatalog → _showSystemDepsModal; new _checkSystemDepsForInstalled on Registry rows); v0.5.3: investigation detail panel — Spec/Runs/Visualizations tabs + Run button + Delete; v0.5.2: composite explorer UX fixes (no focus-mode hijack, one-row-per-param layout, lazy-load composite cache); v0.5.1: composite explorer page (bigraph-viz + test run + promote to simulation); v0.4.14: Available Composites picker + Emitter Use feedback + drop process multi-select; v0.4.5: _renderInstallError structured diagnosis; v0.4.1: _loadCatalog + _installFromCatalog; v0.4.0b: active-branch workstream strip; v0.3.7-A: _installImport; v0.3.6: Registry tab; v0.1.9: drag-drop uploads; v0.1.7: interactive forms.
(function () {
  "use strict";

  // Module-level so EVERY render function can call it. It was previously only
  // defined nested inside the investigation-report builder, but called from
  // sibling scopes (tick / study-card / v4 renderers) — which threw
  // "ReferenceError: Can't find variable: _humanizeStudyName" and failed the
  // investigation report load (fixed 2026-06-10). Hoisted here = visible IIFE-wide.
  function _humanizeStudyName(slug) {
    var m = /^([a-z]+-\d+[a-z]*)-(.+)$/.exec(slug);
    if (!m) return {chip: '', title: String(slug).replace(/-/g, ' ')};
    var rest = m[2].replace(/-/g, ' ');
    rest = rest.charAt(0).toUpperCase() + rest.slice(1);
    if (rest.length > 60) rest = rest.slice(0, 57) + '…';
    return {chip: m[1], title: rest};
  }

  // -------------------------------------------------------------------------
  // Generic modal helpers
  // -------------------------------------------------------------------------

  function openModal(id) {
    var el = document.getElementById(id);
    if (el) el.style.display = "flex";
  }

  function closeModal(id) {
    var el = document.getElementById(id);
    if (el) {
      el.style.display = "none";
      // Clear inline errors.
      var errEl = el.querySelector(".form-error");
      if (errEl) errEl.textContent = "";
    }
  }

  // Close modals when clicking the overlay background.
  document.addEventListener("click", function (e) {
    if (e.target && e.target.classList.contains("modal-overlay")) {
      e.target.style.display = "none";
    }
  });

  // sn-collapse-hint click → toggle the parent <details.study-fold> closed.
  // The official click target for <details> is <summary>, but study-nav
  // (where this hint lives — see CSS comment) is INSIDE <section>, not
  // <summary>, so the native toggle doesn't fire. Manual handler.
  document.addEventListener("click", function (e) {
    var t = e.target;
    if (t && t.classList && (t.classList.contains("sn-collapse-hint") || t.classList.contains("sp-collapse-hint"))) {
      var details = t.closest("details.study-fold");
      if (details) {
        details.open = false;
        // After collapsing, scroll the (now-collapsed) study card into
        // view so the user keeps spatial context — otherwise the
        // scroll position jumps unpredictably.
        details.scrollIntoView({behavior: "smooth", block: "start"});
      }
      e.preventDefault();
      e.stopPropagation();
    }
  });

  // study-nav marker click -> open the (maybe-collapsed) study card + jump to
  // the section it points at. Without this, an anchor into a collapsed
  // <details> does nothing (the target is display:none).
  document.addEventListener("click", function (e) {
    var t = e.target;
    var a = (t && t.closest) ? t.closest(".study-nav a[href^='#']") : null;
    if (!a) return;
    var target = document.getElementById(a.getAttribute("href").slice(1));
    if (!target) return;
    var fold = target.closest("details.study-fold");
    if (fold && !fold.open) fold.open = true;
    e.preventDefault();
    setTimeout(function () {
      target.scrollIntoView({behavior: "smooth", block: "start"});
    }, 0);
  });

  // Global listener for postMessage events from bigraph-loom iframes.
  window.addEventListener('message', function(ev) {
    if (ev.data && ev.data.type === 'explore:ready') {
      // Mark the source iframe as ready so callers can post immediately.
      var ids = ['composite-explore-frame', 'inv-composite-explore-frame'];
      ids.forEach(function(id) {
        var iframe = document.getElementById(id);
        if (iframe && ev.source === iframe.contentWindow) {
          window._loomExploreReady = window._loomExploreReady || {};
          window._loomExploreReady[id] = true;
        }
      });
    }
    if (ev.data && ev.data.type === 'explore:inspect') {
      console.log('[bigraph-loom inspect]', ev.data);
      // TODO: cross-panel highlighting (out of scope for this task)
    }
    if (ev.data && ev.data.type === 'explore:emit-changed') {
      window._explorerEmitPaths = ev.data.paths || [];
    }
    if (ev.data && ev.data.type === 'explore:run-complete') {
      window._ceLastRunId = ev.data.simulation_id || null;
      var bar = document.getElementById('ce-post-run-bar');
      if (bar) bar.style.display = 'flex';
    }
  });

  // Pop the current bigraph-loom iframe contents into a separate window.
  // We re-send the last-posted {type:'composite:load', state, metadata} payload
  // once the popup signals explore:ready (with a 2s failsafe re-post).
  function _popoutLoom(iframeId) {
    var iframe = document.getElementById(iframeId);
    if (!iframe) return;
    var snapshot = window._loomLastState && window._loomLastState[iframeId];
    if (!snapshot) {
      alert('No composite loaded in this view yet — open a composite first.');
      return;
    }
    // Include id in the URL so the popup can call /api/composite-test-run
    // even before the parent has a chance to postMessage. The composite:load
    // message we re-send after explore:ready still wins for metadata, but the
    // URL gives the popup a synchronous bootstrap value.
    var meta = snapshot.metadata || {};
    var url = '/bigraph-loom/index.html';
    if (meta.id) {
      url += '?id=' + encodeURIComponent(meta.id);
    }
    var w = window.open(url, '_blank',
      'width=1200,height=800,menubar=no,toolbar=no,location=no,resizable=yes,scrollbars=yes');
    if (!w) {
      alert('Popup blocked. Allow popups from this site to pop out the wiring view.');
      return;
    }
    var listener = function(ev) {
      if (ev.source === w && ev.data && ev.data.type === 'explore:ready') {
        w.postMessage(snapshot, '*');
        window.removeEventListener('message', listener);
      }
    };
    window.addEventListener('message', listener);
    // Failsafe: if the popup never sends ready (older bundle?), post after a delay.
    setTimeout(function() {
      try { w.postMessage(snapshot, '*'); } catch(_) {}
    }, 2000);

    // Embedded-view handoff: show a "Popped out" placeholder over the iframe
    // so the original page doesn't compete with the popup window. Restore
    // when the popup closes (poll once a second).
    _showPopoutPlaceholder(iframeId, w);
  }

  function _showPopoutPlaceholder(iframeId, popupWin, message) {
    var iframe = document.getElementById(iframeId);
    if (!iframe) return;
    var placeholderId = iframeId + '-popout-placeholder';
    if (document.getElementById(placeholderId)) return; // already showing
    iframe.style.display = 'none';
    var placeholder = document.createElement('div');
    placeholder.id = placeholderId;
    placeholder.style.cssText =
      'width:100%;height:' + (iframe.style.height || '640px') + ';' +
      'border:1px dashed #93c5fd;background:#eff6ff;border-radius:4px;' +
      'display:flex;flex-direction:column;align-items:center;justify-content:center;' +
      'gap:10px;color:#1e3a8a;font-size:0.95em;';
    var msg = message || 'Wiring is open in a separate window.';
    placeholder.innerHTML =
      '<div>↗ ' + msg + '</div>' +
      '<div style="font-size:0.85em;color:#4b5563">Close the popup or click below to return it here.</div>' +
      '<button class="btn-mini" id="' + placeholderId + '-restore">Bring back here</button>';
    iframe.insertAdjacentElement('afterend', placeholder);
    var restoreBtn = document.getElementById(placeholderId + '-restore');
    var restore = function() {
      try { popupWin.close(); } catch(_) {}
      _restoreEmbeddedLoom(iframeId);
    };
    if (restoreBtn) restoreBtn.onclick = restore;
    // Poll until popup closes; then restore.
    var poller = setInterval(function() {
      if (!popupWin || popupWin.closed) {
        clearInterval(poller);
        _restoreEmbeddedLoom(iframeId);
      }
    }, 1000);
  }

  function _restoreEmbeddedLoom(iframeId) {
    var iframe = document.getElementById(iframeId);
    var placeholder = document.getElementById(iframeId + '-popout-placeholder');
    if (placeholder) placeholder.remove();
    if (iframe) iframe.style.display = '';
  }
  window._popoutLoom = _popoutLoom;

  // -------------------------------------------------------------------------
  // Embedded Study Detail
  //
  // Studies used to navigate the whole window to /studies/<name>. Now we host
  // that same route in an iframe inside the Investigations page (with an
  // optional Pop out into a separate window). The same /studies/<name> route
  // serves both contexts, so external/bookmarked links to it still resolve.
  // -------------------------------------------------------------------------

  // Build a /studies/<name> URL honoring the snapshot base-path. In a hosted
  // read-only snapshot the bundle lives at a subpath (e.g. /v2ecoli/dashboard),
  // so a root-absolute '/studies/<name>' 404s on GitHub Pages. basePath is ""
  // in local mode, leaving the URL unchanged.
  function _studyHref(name) {
    var base = (window.__DASH_CONFIG__ && window.__DASH_CONFIG__.basePath) || "";
    return base + '/studies/' + encodeURIComponent(name);
  }
  window._studyHref = _studyHref;

  function _openStudyEmbedded(name) {
    if (!name) return;
    var frame = document.getElementById('study-detail-frame');
    var panel = document.getElementById('study-detail-panel');
    var nameEl = document.getElementById('study-detail-name');
    if (!frame || !panel) {
      // Fallback for any host that doesn't have the embed shell yet.
      window.location = _studyHref(name);
      return;
    }
    // If a previous study is currently popped out, drop the placeholder
    // before reusing the iframe.
    _restoreEmbeddedLoom('study-detail-frame');
    frame.src = _studyHref(name);
    panel.style.display = '';
    if (nameEl) nameEl.textContent = name;
    window._studyDetailCurrent = name;
    panel.scrollIntoView({behavior: 'smooth', block: 'start'});
  }
  window._openStudyEmbedded = _openStudyEmbedded;

  function _popoutStudy() {
    var name = window._studyDetailCurrent;
    if (!name) return;
    var url = _studyHref(name);
    var w = _openDetachedWindow(url, 1200, 800);
    if (!w) {
      alert('Popup blocked. Allow popups from this site to pop out the study view.');
      return;
    }
    _showPopoutPlaceholder('study-detail-frame', w, 'Study is open in a separate window.');
    // Restore the embedded view once the popup closes.
    var poller = setInterval(function() {
      if (!w || w.closed) {
        clearInterval(poller);
        _restoreEmbeddedLoom('study-detail-frame');
      }
    }, 1000);
  }
  window._popoutStudy = _popoutStudy;

  // Try to open the URL as a true detached browser window (not a tab).
  // The `popup` keyword + concrete dimensions triggers a popup window in
  // Chromium / Safari; Firefox honors width/height with the
  // dom.disable_window_open_feature.* prefs left at defaults. Browsers
  // that hard-coded tab-only behavior (e.g. user pref) ignore us; that
  // is the user's setting and can't be overridden by JS.
  function _openDetachedWindow(url, width, height) {
    width = width || 1280;
    height = height || 900;
    var left = Math.max(0, (window.screen.availWidth  - width)  / 2);
    var top  = Math.max(0, (window.screen.availHeight - height) / 2);
    var features = [
      'popup=yes',
      'width=' + width,
      'height=' + height,
      'left=' + left,
      'top=' + top,
      'menubar=no',
      'toolbar=no',
      'location=no',
      'status=no',
      'resizable=yes',
      'scrollbars=yes',
      // NB: `noopener` removed. It was hinting at security hygiene but
      // some browsers treat noopener popups as fresh navigations that
      // lose the dashboard's session context, leaving the popup blank.
      // For a local dashboard this isn't a security risk.
    ].join(',');
    // NOTE: dropping `_blank` as the target name and using a unique name
    // ('detached-' + timestamp) makes Safari less inclined to merge the
    // popup into the opener tab's window. With a fresh name + popup
    // features the browser is more likely to honor the request.
    var target = 'detached-' + Date.now();
    var w = window.open(url, target, features);
    if (!w) return w;
    // Belt-and-suspenders: a few browsers (Chrome with certain prefs,
    // Firefox on Linux) ignore the popup hint at open() time but still
    // honor a post-open resizeTo/moveTo. Calling these is harmless when
    // they don't apply.
    try { w.resizeTo(width, height); } catch (_) {}
    try { w.moveTo(left, top);       } catch (_) {}
    return w;
  }
  window._openDetachedWindow = _openDetachedWindow;

  function _closeStudyEmbedded() {
    var frame = document.getElementById('study-detail-frame');
    var panel = document.getElementById('study-detail-panel');
    _restoreEmbeddedLoom('study-detail-frame');
    if (frame) frame.src = '';
    if (panel) panel.style.display = 'none';
    window._studyDetailCurrent = null;
  }
  window._closeStudyEmbedded = _closeStudyEmbedded;

  // -------------------------------------------------------------------------
  // UI feature flags (ui.composite_view)
  // -------------------------------------------------------------------------
  window._uiConfig = null;
  fetch('/api/ui-config').then(function(r) { return r.json(); }).then(function(cfg) {
    window._uiConfig = cfg || {};
    _applyCompositeViewMode();
  });

  function _applyCompositeViewMode() {
    var cfg = window._uiConfig || {};
    var mode = cfg.composite_view || 'bigraph-loom';
    var iframe = document.getElementById('composite-explore-frame');
    var svgLegacy = document.getElementById('composite-explore-svg-legacy');
    if (!iframe || !svgLegacy) return;
    if (mode === 'bigraph-viz') {
      iframe.style.display = 'none';
      svgLegacy.style.display = '';
    } else {
      iframe.style.display = '';
      svgLegacy.style.display = 'none';
    }
  }
  window._applyCompositeViewMode = _applyCompositeViewMode;

  // -------------------------------------------------------------------------
  // Form submission helper
  // -------------------------------------------------------------------------

  /**
   * submitForm — POST form data as JSON to endpoint.
   * On success: alert message, call /api/render, then reload.
   * On error: show inline error inside the form.
   *
   * @param {HTMLFormElement} form
   * @param {string} endpoint
   * @param {function} [dataFn] — optional fn(form) -> object; defaults to FormData extraction
   */
  function submitForm(form, endpoint, dataFn) {
    var errEl = form.querySelector(".form-error");
    if (errEl) errEl.textContent = "";

    var submitBtn = form.querySelector("button[type=submit]");
    if (submitBtn) submitBtn.disabled = true;

    var data = dataFn ? dataFn(form) : _formToObj(form);

    fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    })
      .then(function (res) {
        return res.json().then(function (json) {
          return { ok: res.ok, status: res.status, json: json };
        });
      })
      .then(function (r) {
        if (!r.ok) {
          var msg = (r.json && r.json.error) ? r.json.error : ("HTTP " + r.status);
          if (errEl) errEl.textContent = "Error: " + msg;
          if (submitBtn) submitBtn.disabled = false;
          return;
        }
        var branch = r.json.branch || "";
        var commit = r.json.commit || "";
        var note = r.json.note || "";
        var next = r.json.next_terminal_step || "";
        var msg = "Done!";
        if (branch) msg += " Branch: " + branch + (commit ? " (" + commit + ")" : "");
        if (next) msg += "\n\nNext terminal step:\n  " + next;
        if (note) msg += "\n\n" + note;
        // Re-render then reload (strip updates on reload).
        fetch("/api/render", { method: "POST" }).finally(function () {
          alert(msg);
          location.reload();
        });
      })
      .catch(function (err) {
        if (errEl) errEl.textContent = "Network error: " + String(err);
        if (submitBtn) submitBtn.disabled = false;
      });
  }

  function _formToObj(form) {
    var obj = {};
    var data = new FormData(form);
    data.forEach(function (val, key) {
      if (obj[key] !== undefined) {
        // Multi-value: accumulate into array.
        if (!Array.isArray(obj[key])) obj[key] = [obj[key]];
        obj[key].push(val);
      } else {
        obj[key] = val;
      }
    });
    return obj;
  }

  function _postPhaseAction(endpoint, data) {
    fetch("/api/" + endpoint, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(data),
    })
      .then(function(r) { return r.json().then(function(j) { return [r.ok, j]; }); })
      .then(function(parts) {
        var ok = parts[0], json = parts[1];
        if (!ok) {
          alert("Error: " + (json.error || "unknown"));
          return;
        }
        var msg = "Done! Branch: " + (json.branch || "?");
        fetch("/api/render", {method: "POST"}).finally(function() {
          _refreshGitStatus();
          alert(msg);
          location.reload();
        });
      })
      .catch(function(err) { alert("Network error: " + err); });
  }
  window._postPhaseAction = _postPhaseAction;

  // -------------------------------------------------------------------------
  // Menu navigation (v0.3.5)
  // -------------------------------------------------------------------------

  function _switchPage(pageId) {
    pageId = pageId || 'workspace-inputs';
    // Snapshot mode: redirect authoring-only tabs to the investigations view.
    // composite-explore needs live composite resolution (build_core) which is
    // unavailable in a static bundle → redirect to simulation-setup (composites list).
    if (document.body.classList.contains('snapshot')) {
      if (pageId === 'github' || pageId === 'studies') {
        pageId = 'investigations';
      }
    }
    document.querySelectorAll('.page').forEach(function(s) { s.classList.remove('active'); });
    document.querySelectorAll('.menu-link').forEach(function(a) { a.classList.remove('active'); });
    var page = document.getElementById('page-' + pageId);
    var link = document.querySelector('.menu-link[data-page="' + pageId + '"]');
    if (page) page.classList.add('active');
    if (link) link.classList.add('active');
    // Lazy-load catalog + registry on switch to Registry, Simulation Setup, or Visualizations page.
    if (pageId === 'registry') {
      _loadCatalog();
    }
    if (pageId === 'registry' || pageId === 'simulation-setup' || pageId === 'visualizations') {
      if (!window._registryLoaded) {
        window._registryLoaded = true;
        _loadRegistry(false);
      }
    }
    // Analyses page: always refresh from /api/visualization-classes on navigate.
    if (pageId === 'visualizations') {
      _loadAnalysesPage();
    }
    if (pageId === 'simulation-setup') {
      _loadComposites();
    }
    // Stop any running poll-loop started by the Composite Explorer's Run tab
    // before activating a new page. _ceLoadRunFromId will restart polling if
    // the next page is the explorer with a still-running run.
    if (typeof _ceStopRunPoll === 'function') _ceStopRunPoll();

    // Initialize composite explorer when switching to that page.
    if (pageId === 'composite-explore') {
      _initCompositeExplorer();
    }
    if (pageId === 'simulations') {
      _wireSimulationsUiOnce();
      _initSimulations();
    }
    if (pageId === 'studies') {
      // Always retry if we don't have any studies in memory yet — the prior
      // load may have failed (server still booting, transient 404) and the
      // memo flag stuck without a way to recover. Only the first SUCCESS
      // permanently silences the auto-retry.
      var alreadyLoaded = window._investigationsLoaded
        && Array.isArray(window._investigations)
        && window._investigations.length > 0;
      if (!alreadyLoaded) {
        window._investigationsLoaded = true;
        _loadInvestigations();
      }
    }
    if (pageId === 'investigations') {
      _loadInvestigationSets();
    }
    if (pageId === 'workspace-inputs') {
      _loadInputs();
    }
  }

  function _initMenuNav() {
    // Focus mode: ?focus=<panel> hides everything except the named panel.
    var params = new URLSearchParams(window.location.search);
    var focus = params.get('focus');
    var focusedPage = null;
    if (focus) {
      var _snapshot = document.body.classList.contains('snapshot');
      var validPages = _snapshot
        ? ['workspace-inputs', 'simulation-setup', 'registry', 'investigations', 'simulations', 'visualizations', 'composite-explore']
        : ['workspace-inputs', 'simulation-setup', 'visualizations', 'registry', 'investigations', 'studies', 'simulations', 'composite-explore', 'github'];
      if (validPages.indexOf(focus) >= 0) {
        document.body.classList.add('focus-mode', 'focus-' + focus);
        _switchPage(focus);
        focusedPage = focus;
        // DO NOT return — fall through so the ?investigation=<name> auto-open
        // handler below also fires (it was previously skipped by the early
        // return, leaving popouts blank when the iset auto-open in
        // _loadInvestigationSets didn't fire in time).
      }
    }

    if (!focusedPage) {
      function fromHash() {
        var h = (window.location.hash || '').replace(/^#/, '');
        var _snap = document.body.classList.contains('snapshot');
        var validPages = _snap
          ? ['workspace-inputs', 'registry', 'simulation-setup', 'investigations', 'simulations', 'visualizations', 'composite-explore']
          : ['workspace-inputs', 'registry', 'simulation-setup', 'visualizations', 'investigations', 'studies', 'simulations', 'composite-explore', 'github'];
        _switchPage(validPages.indexOf(h) >= 0 ? h : 'workspace-inputs');
      }
      window.addEventListener('hashchange', fromHash);
      fromHash();
    }

    // ?investigation=<name> → auto-open that investigation's detail view.
    // The setTimeout retries to handle the race where the iframe / API
    // load races with the page swap.
    var qInv = new URLSearchParams(window.location.search).get('investigation');
    if (qInv) {
      if (!focusedPage) _switchPage('investigations');
      var tries = 0;
      var attemptOpen = function() {
        var detailEl = document.getElementById('investigation-detail-view');
        if (detailEl && typeof _openInvestigationDetail === 'function') {
          _openInvestigationDetail(qInv);
        } else if (tries++ < 20) {
          setTimeout(attemptOpen, 100);
        }
      };
      setTimeout(attemptOpen, 50);
    }
  }

  window._switchPage = _switchPage;
  window._initMenuNav = _initMenuNav;

  // -------------------------------------------------------------------------
  // Inputs tab — investigation-first render from /api/inputs
  // -------------------------------------------------------------------------
  // Mirrors the SimulationsDB current-investigation-first layout: the loaded
  // investigation's owned inputs render at the TOP, then repo-wide / shared
  // data sources below. Replaces the server-rendered dataset/reference lists
  // as the single source of truth (the management panels below the container
  // keep the add/edit actions + bib explorer).
  function _loadInputs() {
    var el = document.getElementById('inputs-api-render');
    if (!el) return;
    el.innerHTML = '<p class="muted" style="font-style:italic">Loading inputs…</p>';
    // Prefer the Sources-page picker selection over the git-branch-current slug.
    var _slug = window._inputsSelectedSlug || window._currentIsetSlug || '';
    var _pInputs = window.DataSource
      ? window.DataSource.loadInputs(_slug)
      : (function() {
          var _url = '/api/inputs' + (_slug ? ('?investigation=' + encodeURIComponent(_slug)) : '');
          return fetch(_url).then(function(r) { return r.json(); });
        })();
    // Also load the investigation list so the panel can offer a picker when no
    // investigation is branch-current — the user chooses which investigation to
    // load sources INTO (its own sources, not the repo-wide shared sources).
    var _pList = fetch('/api/iset-list')
      .then(function(r) { return r.json(); })
      .then(function(d) { return (d && d.investigations) || []; })
      .catch(function() { return []; });
    Promise.all([_pInputs, _pList])
      .then(function (arr) {
        var data = arr[0] || {};
        data._investigations = arr[1] || [];
        _renderInputs(el, data);
      })
      .catch(function (err) {
        el.innerHTML = '<p style="color:#c00">Could not load inputs: ' +
          _esc(String(err)) +
          ' <button class="action-btn" onclick="_loadInputs()">Retry</button></p>';
      });
  }
  window._loadInputs = _loadInputs;

  // Sources-page investigation picker: set the selected slug and reload so the
  // panel shows that investigation's sources + investigation-scoped +Add buttons.
  function _inputsSelectInvestigation(slug) {
    window._inputsSelectedSlug = slug || '';
    _loadInputs();
  }
  window._inputsSelectInvestigation = _inputsSelectInvestigation;

  // A reference entry is either a bare bib key (investigation.references) or a
  // parsed bib-entry dict (global.references). Normalize to a display label.
  function _inputsRefLabel(ref) {
    if (ref == null) return '';
    if (typeof ref === 'string') return ref;
    return ref.key || ref.bib_key || ref.name || ref.title || JSON.stringify(ref);
  }

  function _inputsNone() {
    return '<p class="muted" style="font-style:italic;margin:4px 0">none</p>';
  }

  // A download link to a workspace-relative path. The server GET-serves any
  // file under the workspace by its workspace-relative path (do_GET ->
  // WORKSPACE / rel), so the href is simply '/' + path.
  function _inputsDownloadLink(path, label) {
    if (!path) return '';
    var href = '/' + String(path).replace(/^\/+/, '');
    return '<a href="' + _esc(href) + '" download class="action-btn" ' +
      'style="font-size:0.8em;padding:1px 8px;text-decoration:none">⬇ ' +
      _esc(label || 'Download') + '</a>';
  }

  // Render a datasets list (name + path + download) as a compact table, or a
  // "none" line.
  function _inputsDatasetsHtml(datasets) {
    if (!datasets || !datasets.length) return _inputsNone();
    var rows = datasets.map(function (ds) {
      ds = ds || {};
      var name = _esc(ds.name || ds.path || '(unnamed)');
      var path = ds.path || '';
      var src = ds.path || ds.url || '';
      var dl = path ? _inputsDownloadLink(path, 'Download') :
        (ds.url ? '<a href="' + _esc(ds.url) + '" target="_blank" rel="noopener" ' +
          'class="action-btn" style="font-size:0.8em;padding:1px 8px;text-decoration:none">↗ Source</a>' : '');
      return '<tr><td><code>' + name + '</code></td><td><small class="muted">' +
        _esc(src) + '</small></td><td style="text-align:right">' + dl + '</td></tr>';
    }).join('');
    return '<table><thead><tr><th>Name</th><th>Source</th><th></th></tr></thead><tbody>' +
      rows + '</tbody></table>';
  }

  // Render references as informative cards: title (linked to the paper online),
  // a muted author/year/journal line, a collapsible BibTeX block with a copy
  // button, and an optional PDF download. Used for BOTH investigation + global
  // references. Unmatched bare keys render as a labeled stub.
  function _inputsRefsHtml(refs) {
    if (!refs || !refs.length) return _inputsNone();
    return '<div style="display:flex;flex-direction:column;gap:10px">' +
      refs.map(_inputsRefCardHtml).join('') + '</div>';
  }

  function _inputsRefCardHtml(ref) {
    ref = ref || {};
    if (typeof ref === 'string') ref = { key: ref, title: ref, _unmatched: true };
    var key = ref.key || ref.bib_key || '';

    if (ref._unmatched) {
      return '<div style="border:1px solid #e2e8f0;border-radius:6px;padding:8px 10px">' +
        '<code>' + _esc(key || _inputsRefLabel(ref)) + '</code> ' +
        '<small class="muted">(no bib entry)</small></div>';
    }

    // Many minimal bib entries have only url + note (no title); fall back to
    // the note (a human description), then the key.
    var title = ref.title || ref.note || key || '(untitled)';
    // Link target: explicit url, else doi.org/<doi>.
    var link = '';
    if (ref.url) link = ref.url;
    else if (ref.doi) link = 'https://doi.org/' + ref.doi;

    var titleHtml = link
      ? '<a href="' + _esc(link) + '" target="_blank" rel="noopener" ' +
        'style="font-weight:600">' + _esc(title) + '</a> ' +
        '<small class="muted">↗</small>'
      : '<strong>' + _esc(title) + '</strong>';

    var metaParts = [];
    if (ref.author) metaParts.push(_esc(ref.author));
    if (ref.year) metaParts.push(_esc(ref.year));
    if (ref.journal) metaParts.push(_esc(ref.journal));
    var meta = metaParts.length
      ? '<div class="muted" style="font-size:0.85em;margin-top:2px">' +
        metaParts.join(' · ') + '</div>'
      : '';

    var actions = '';
    if (ref.pdf_path) actions += ' ' + _inputsDownloadLink(ref.pdf_path, 'PDF');

    var bibtex = ref.bibtex || '';
    var bibBlock = '';
    if (bibtex) {
      var bibId = 'bibtex-' + (key || Math.random().toString(36).slice(2));
      bibBlock = '<details style="margin-top:6px">' +
        '<summary style="cursor:pointer;font-size:0.82em;color:#475569">BibTeX</summary>' +
        '<pre id="' + _esc(bibId) + '" style="background:#f8fafc;border:1px solid #e2e8f0;' +
        'border-radius:4px;padding:8px;font-size:0.78em;overflow:auto;margin:6px 0">' +
        _esc(bibtex) + '</pre>' +
        '<button class="action-btn" style="font-size:0.78em;padding:1px 8px" ' +
        'onclick="_copyBibtex(\'' + _esc(bibId) + '\', this)">Copy BibTeX</button>' +
        '</details>';
    }

    // Show the note as a sub-line only when it isn't already the headline.
    var noteHtml = (ref.note && ref.note !== title)
      ? '<div class="muted" style="font-size:0.85em;margin-top:2px;font-style:italic">' + _esc(ref.note) + '</div>'
      : '';

    return '<div style="border:1px solid #e2e8f0;border-radius:6px;padding:8px 10px">' +
      '<div>' + titleHtml + actions + '</div>' + meta + noteHtml + bibBlock + '</div>';
  }

  // Copy the text content of a <pre> to the clipboard; flash the button label.
  function _copyBibtex(preId, btn) {
    var pre = document.getElementById(preId);
    if (!pre) return;
    var text = pre.textContent || '';
    var done = function () {
      if (!btn) return;
      var orig = btn.textContent;
      btn.textContent = 'Copied!';
      setTimeout(function () { btn.textContent = orig; }, 1200);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () {});
    } else {
      try {
        var ta = document.createElement('textarea');
        ta.value = text; document.body.appendChild(ta); ta.select();
        document.execCommand('copy'); document.body.removeChild(ta); done();
      } catch (e) { /* ignore */ }
    }
  }
  window._copyBibtex = _copyBibtex;

  // Render expert docs (name + optional path + download), or a "none" line.
  function _inputsExpertDocsHtml(docs) {
    if (!docs || !docs.length) return _inputsNone();
    return '<ul style="margin:4px 0 0 0;padding:0;list-style:none;' +
      'display:flex;flex-direction:column;gap:4px">' +
      docs.map(function (doc) {
        doc = doc || {};
        var name = _esc(doc.name || doc.path || '(unnamed)');
        var path = doc.path ? ' <small class="muted">' + _esc(doc.path) + '</small>' : '';
        var dl = doc.path ? ' ' + _inputsDownloadLink(doc.path, 'Download') : '';
        return '<li><strong>' + name + '</strong>' + path + dl + '</li>';
      }).join('') + '</ul>';
  }

  // A small "+ Add" button that launches the investigation-scoped upload flow
  // for the given category ('dataset' | 'reference' | 'expert').
  function _inputsAddBtn(category) {
    return '<button class="action-btn js-authoring" style="font-size:0.78em;padding:1px 8px;' +
      'font-weight:normal" onclick="_inputsAdd(\'' + category + '\')">+ Add</button>';
  }

  // Read a File object to pure base64 (sans data: prefix) and invoke cb.
  function _inputsReadFileB64(file, cb) {
    var reader = new FileReader();
    reader.onload = function (ev) {
      var dataUrl = ev.target.result;
      var comma = dataUrl.indexOf(',');
      cb(comma >= 0 ? dataUrl.slice(comma + 1) : dataUrl);
    };
    reader.readAsDataURL(file);
  }

  // POST an investigation-scoped input upload, then refresh the page.
  function _inputsPost(endpoint, body) {
    body = body || {};
    var slug = window._inputsSelectedSlug || window._currentIsetSlug || '';
    if (slug) body.investigation = slug;
    fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        if (!res.ok || (res.d && res.d.error)) {
          alert('Upload failed: ' + ((res.d && res.d.error) || 'unknown error'));
          return;
        }
        if (typeof _loadInputs === 'function') _loadInputs();
      })
      .catch(function (err) { alert('Upload failed: ' + String(err)); });
  }

  // Hidden file picker -> base64 -> cb({file_b64, filename}).
  function _inputsPickFile(cb) {
    var inp = document.createElement('input');
    inp.type = 'file';
    inp.style.display = 'none';
    inp.onchange = function () {
      if (inp.files && inp.files[0]) {
        var f = inp.files[0];
        _inputsReadFileB64(f, function (b64) { cb({ file_b64: b64, filename: f.name }); });
      }
      setTimeout(function () { if (inp.parentNode) inp.parentNode.removeChild(inp); }, 0);
    };
    document.body.appendChild(inp);
    inp.click();
  }

  // Entry point for the "+ Add" buttons on the investigation inputs panel.
  function _inputsAdd(category) {
    var slug = window._inputsSelectedSlug || window._currentIsetSlug || '';
    if (!slug) { alert('Select an investigation first (Load sources into: …).'); return; }

    if (category === 'dataset') {
      var dsName = window.prompt('Dataset name?');
      if (!dsName) return;
      _inputsPickFile(function (picked) {
        _inputsPost('/api/dataset', {
          name: dsName, filename: picked.filename, file_b64: picked.file_b64
        });
      });
      return;
    }

    if (category === 'expert') {
      var edName = window.prompt('Expert-doc name?');
      if (!edName) return;
      _inputsPickFile(function (picked) {
        _inputsPost('/api/expert-doc', {
          name: edName, filename: picked.filename, file_b64: picked.file_b64
        });
      });
      return;
    }

    if (category === 'reference') {
      // PDF drop-and-go, or BibTeX paste.
      var mode = window.prompt(
        'Add reference — type "pdf" to upload a PDF, or "bibtex" to paste BibTeX:',
        'bibtex');
      if (mode == null) return;
      mode = mode.trim().toLowerCase();
      if (mode === 'pdf') {
        _inputsPickFile(function (picked) {
          _inputsPost('/api/reference-pdf', { pdf_b64: picked.file_b64 });
        });
      } else if (mode === 'bibtex') {
        var bib = window.prompt('Paste a BibTeX entry:');
        if (!bib || !bib.trim()) return;
        _inputsPost('/api/reference-bibtex', { bibtex_text: bib.trim() });
      }
      return;
    }
  }
  window._inputsAdd = _inputsAdd;

  function _renderInputs(el, data) {
    var inv = data.investigation || {};
    var glob = data.global || {};
    var current = data.current || null;

    var invList = data._investigations || [];

    var html = '';

    // --- This investigation's inputs (top) ---
    var invHeading = 'This investigation’s sources';
    if (current) invHeading += ' — ' + _esc(current);
    html += '<div class="panel">';
    html += '<h3>' + invHeading + '</h3>';

    // Investigation picker. One dashboard per repo, but a repo can hold several
    // investigations and the Sources page isn't always opened from inside one
    // (git-branch detection may yield no current). Let the user choose which
    // investigation to view and load sources INTO — its own sources, not the
    // repo-wide shared sources below.
    if (invList.length) {
      var opts = '<option value="">— select an investigation —</option>' +
        invList.map(function (it) {
          var slug = it.name || it.slug || '';
          var label = it.title || slug;
          var sel = (slug === current) ? ' selected' : '';
          return '<option value="' + _esc(slug) + '"' + sel + '>' + _esc(label) + '</option>';
        }).join('');
      html += '<div style="margin:4px 0 12px;display:flex;align-items:center;gap:6px">' +
        '<label style="font-size:0.85em;color:#475569">Load sources into:</label>' +
        '<select onchange="_inputsSelectInvestigation(this.value)" ' +
        'style="font-size:0.9em;padding:3px 6px;border:1px solid #cbd5e1;border-radius:4px">' +
        opts + '</select></div>';
    }

    if (!current) {
      html += '<p class="muted" style="font-style:italic">' +
        (invList.length
          ? 'Select an investigation above to view and add its sources.'
          : 'No investigation loaded.') + '</p>';
    } else {
      if (inv._repo_fallback) {
        html += '<p class="muted" style="font-style:italic;font-size:0.85em">' +
          'migrating: showing repo-level inputs</p>';
      }
      html += '<h4 style="margin:12px 0 4px">Datasets ' +
        _inputsAddBtn('dataset') + '</h4>' +
        _inputsDatasetsHtml(inv.datasets);
      html += '<h4 style="margin:12px 0 4px">References ' +
        _inputsAddBtn('reference') + '</h4>' +
        _inputsRefsHtml(inv.references);
      html += '<h4 style="margin:12px 0 4px">Expert docs ' +
        _inputsAddBtn('expert') + '</h4>' +
        _inputsExpertDocsHtml(inv.expert_docs);
    }
    html += '</div>';

    // --- Repo-wide data sources (below) ---
    html += '<div class="panel">';
    html += '<h3>Repo-wide data sources</h3>';
    // Data-source bundle (workspace.yaml dashboard.data_sources provider).
    // Populated asynchronously; the host is hidden until sources arrive so
    // workspaces without a provider see no extra UI. Rendered FIRST (above the
    // shared datasets/references) as the primary repo-wide source.
    html += '<div id="data-sources-host" style="display:none;margin-bottom:16px"></div>';
    html += '<h4 style="margin:12px 0 4px">Datasets</h4>' +
      _inputsDatasetsHtml(glob.datasets);
    html += '<h4 style="margin:12px 0 4px">References</h4>' +
      _inputsRefsHtml(glob.references);
    html += '</div>';

    el.innerHTML = html;

    _loadDataSources();
  }

  // -------------------------------------------------------------------------
  // Repo-wide data sources — provider-backed bundle (workspace.yaml hook).
  // Grouped-by-category, searchable list with click-to-open file preview.
  // -------------------------------------------------------------------------
  var _dataSourcesCache = null;  // [{key, path, category, kind, size_bytes}]

  function _fmtBytes(n) {
    n = Number(n) || 0;
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    return (n / 1024 / 1024).toFixed(2) + ' MB';
  }

  function _loadDataSources() {
    var host = document.getElementById('data-sources-host');
    if (!host) return;
    var _p = window.DataSource
      ? window.DataSource.loadDataSources()
      : fetch('/api/data-sources').then(function(r) { return r.json(); });
    _p
      .then(function(j) {
        var sources = (j && j.sources) || [];
        if (!sources.length) {
          host.style.display = 'none';
          return;
        }
        _dataSourcesCache = sources;
        host.style.display = 'block';
        _renderDataSources(host, j.label || 'data sources', sources, j.error);
      })
      .catch(function() { host.style.display = 'none'; });
  }

  function _renderDataSources(host, label, sources, error) {
    var n = sources.length;
    var nOv = sources.filter(function(s) { return s.kind === 'override'; }).length;
    var h = '';
    h += '<h4 style="margin:12px 0 4px">' + _esc(label) +
      ' <span class="muted" style="font-weight:normal">(' + n + ' files' +
      (nOv ? ', ' + nOv + ' override' + (nOv === 1 ? '' : 's') : '') + ')</span></h4>';
    if (error) {
      h += '<p class="muted" style="font-style:italic;font-size:0.85em">' +
        'provider error: ' + _esc(error) + '</p>';
    }
    h += '<input type="text" id="ds-filter" placeholder="Filter by key…" ' +
      'oninput="_filterDataSources(this.value)" ' +
      'style="width:100%;box-sizing:border-box;padding:6px 8px;margin:4px 0 8px;' +
      'border:1px solid #d1d5db;border-radius:6px;font-size:0.85em">';
    h += '<div id="ds-list"></div>';
    host.innerHTML = h;
    _filterDataSources('');
  }

  function _filterDataSources(q) {
    var listEl = document.getElementById('ds-list');
    if (!listEl || !_dataSourcesCache) return;
    q = (q || '').toLowerCase().trim();
    var matched = _dataSourcesCache.filter(function(s) {
      return !q || s.key.toLowerCase().indexOf(q) !== -1;
    });

    // Group by category.
    var groups = {};
    matched.forEach(function(s) {
      (groups[s.category] = groups[s.category] || []).push(s);
    });
    var cats = Object.keys(groups).sort();
    if (!cats.length) {
      listEl.innerHTML = '<p class="muted" style="font-size:0.85em">No matching files.</p>';
      return;
    }

    var html = '';
    cats.forEach(function(cat) {
      var items = groups[cat];
      html += '<details ' + (q ? 'open' : '') + ' style="margin-bottom:6px">';
      html += '<summary style="cursor:pointer;font-weight:600;font-size:0.85em;' +
        'padding:4px 0;color:#374151">' + _esc(cat) +
        ' <span class="muted" style="font-weight:normal">(' + items.length + ')</span></summary>';
      html += '<div style="margin:2px 0 6px 8px">';
      items.forEach(function(s) {
        var badgeColor = s.kind === 'override' ? '#9333ea' : '#6b7280';
        var badgeBg = s.kind === 'override' ? '#f3e8ff' : '#f3f4f6';
        html += '<div style="display:flex;align-items:center;gap:8px;padding:3px 0;' +
          'border-bottom:1px solid #f3f4f6;font-size:0.82em">';
        html += '<code style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" ' +
          'title="' + _esc(s.key) + '">' + _esc(s.key) + '</code>';
        html += '<span style="flex:none;font-size:0.72em;font-weight:700;padding:1px 6px;' +
          'border-radius:9999px;color:' + badgeColor + ';background:' + badgeBg + '">' +
          _esc(s.kind) + '</span>';
        html += '<span class="muted" style="flex:none;width:64px;text-align:right">' +
          _fmtBytes(s.size_bytes) + '</span>';
        // Prefer an external hyperlink when the provider supplied one (e.g. a
        // GitHub raw URL) — this is the only access path that works in the
        // published static snapshot. Fall back to the server-only "Open"
        // button for local mode when no url is present.
        if (s.url) {
          html += '<a class="action-btn" style="flex:none;padding:1px 8px;font-size:0.85em;text-decoration:none" ' +
            'href="' + _esc(s.url) + '" target="_blank" rel="noopener">open ↗</a>';
        } else {
          html += '<button class="action-btn" style="flex:none;padding:1px 8px;font-size:0.85em" ' +
            'onclick="_openDataSourceFile(\'' + _esc(s.key).replace(/'/g, "\\'") + '\')">Open</button>';
        }
        html += '</div>';
      });
      html += '</div></details>';
    });
    listEl.innerHTML = html;
  }
  window._filterDataSources = _filterDataSources;

  function _openDataSourceFile(key) {
    var url = '/api/data-source-file?key=' + encodeURIComponent(key);
    var titleEl = document.getElementById('ds-preview-title');
    var bodyEl = document.getElementById('ds-preview-body');
    var dlEl = document.getElementById('ds-preview-download');
    if (titleEl) titleEl.textContent = key;
    if (dlEl) dlEl.setAttribute('href', url);
    if (bodyEl) bodyEl.textContent = 'Loading…';
    openModal('modal-ds-preview');
    fetch(url)
      .then(function(r) {
        var ct = r.headers.get('Content-Type') || '';
        if (ct.indexOf('text/') === 0 || ct.indexOf('json') !== -1 ||
            ct.indexOf('yaml') !== -1 || ct.indexOf('csv') !== -1 ||
            ct.indexOf('tab-separated') !== -1) {
          return r.text().then(function(t) {
            if (bodyEl) bodyEl.textContent = t;
          });
        }
        if (bodyEl) {
          bodyEl.textContent =
            '(binary file — use Download to save it)';
        }
      })
      .catch(function(e) {
        if (bodyEl) bodyEl.textContent = 'Error loading file: ' + e;
      });
  }
  window._openDataSourceFile = _openDataSourceFile;

  // -------------------------------------------------------------------------
  // Registry tab (v0.3.6)
  // -------------------------------------------------------------------------

  function _renderRegistryTable(items, container, kind) {
    if (!items || items.length === 0) {
      container.innerHTML = '<p class="empty-state">No ' + kind + ' registered.</p>';
      return;
    }
    var rows = items.map(function(it) {
      var schemaPreview = it.schema_preview || '';
      var escaped = schemaPreview.replace(/[<>&]/g, function(c) {
        return {'<': '&lt;', '>': '&gt;', '&': '&amp;'}[c];
      });
      var schemaCol = '<code class="registry-schema">' + (escaped ? escaped : '<em class="muted">—</em>') + '</code>';
      var addrCol = it.address ? '<code>' + it.address + '</code>' : '';
      if (kind === 'processes') {
        return '<tr><td><code>' + it.name + '</code></td><td>' + addrCol + '</td><td>' + schemaCol + '</td></tr>';
      } else {
        return '<tr><td><code>' + it.name + '</code></td><td>' + schemaCol + '</td></tr>';
      }
    }).join('');
    var headers = kind === 'processes'
      ? '<thead><tr><th>Name</th><th>Address</th><th>Config schema (preview)</th></tr></thead>'
      : '<thead><tr><th>Name</th><th>Definition (preview)</th></tr></thead>';
    container.innerHTML = '<table>' + headers + '<tbody>' + rows + '</tbody></table>';
  }

  function _esc(s) {
    return String(s || '').replace(/[<>&"]/g, function(c) {
      return {'<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;'}[c];
    });
  }

  // Coerce a value to an Array. Use everywhere a YAML/JSON field is
  // SUPPOSED to be a list but a caller might supply a dict (e.g. a
  // grouped/nested shape). Prevents
  //   "(x || []).map is not a function"
  // class of bugs from crashing report generation. Logs a single warning
  // per (label, type) so we notice schema drift without spamming the
  // console. Returns []; the caller's report degrades gracefully (empty
  // section) instead of throwing.
  var _asListWarned = new Set();
  function _asList(value, label) {
    if (Array.isArray(value)) return value;
    if (value === null || value === undefined) return [];
    var type = (typeof value === 'object') ? 'object' : (typeof value);
    var key = (label || '?') + ':' + type;
    if (!_asListWarned.has(key)) {
      _asListWarned.add(key);
      console.warn('[walkthrough] expected array for ' + (label || '<unlabeled field>') +
                   ', got ' + type + ' — degrading to empty list. ' +
                   'Check the workspace yaml schema.');
    }
    return [];
  }

  function _filterVizCatalog(query) {
    var rows = document.querySelectorAll('#viz-picker-container .picker-row');
    var q = (query || '').toLowerCase().trim();
    rows.forEach(function(row) {
      if (!q) { row.style.display = ''; return; }
      var hay = (row.textContent || '').toLowerCase();
      row.style.display = hay.indexOf(q) === -1 ? 'none' : '';
    });
  }
  window._filterVizCatalog = _filterVizCatalog;

  // -------------------------------------------------------------------------
  // Analyses page: fetch /api/visualization-classes and render two groups —
  // "Analyses" (kind === "analysis") and "Visualizations" (kind === "visualization").
  // -------------------------------------------------------------------------

  function _renderAnalysesGroups(classes, container) {
    if (!classes || classes.length === 0) {
      container.innerHTML = '<p class="empty-state">No classes found. Install a pbg-* package or a v2ecoli workspace to populate this page.</p>';
      return;
    }
    var analyses = classes.filter(function(c) { return c.kind === 'analysis'; });
    var vizzes   = classes.filter(function(c) { return c.kind !== 'analysis'; });

    function _renderClassCard(c) {
      var previewBtn = (c.kind !== 'analysis')
        ? '<button class="btn-mini js-authoring" onclick="_vizClassPreview(\'' + _esc(c.address) + '\',\'' + _esc(c.name) + '\')">Preview</button>'
        : '';
      return '<div class="picker-row" data-kind="' + _esc(c.kind || 'visualization') + '">' +
        '<div class="picker-row-main">' +
          '<strong>' + _esc(c.name) + '</strong>' +
          ' <code class="muted" style="font-size:0.82em">' + _esc(c.address) + '</code>' +
          (c.doc ? '<br><span class="muted" style="font-size:0.85em">' + _esc(c.doc) + '</span>' : '') +
        '</div>' +
        '<div class="picker-row-actions">' +
          previewBtn +
          (c.kind !== 'analysis'
            ? '<button class="btn-mini js-authoring" onclick="_useRegistryClass(\'visualization\', \'' + _esc(c.name) + '\')">Use</button>'
            : '') +
        '</div>' +
      '</div>';
    }

    var html = '';

    // ── Analyses group ───────────────────────────────────────────────────────
    html += '<div class="analyses-group" style="margin-bottom:20px">' +
      '<h4 style="margin:0 0 8px;font-size:0.95em;text-transform:uppercase;letter-spacing:0.06em;color:#374151">Analyses' +
      ' <span class="count-badge" style="font-size:0.8em">' + analyses.length + '</span></h4>';
    if (analyses.length === 0) {
      html += '<p class="empty-state muted" style="margin:0">No Analysis classes found. v2ecoli must be installed in this workspace\'s environment.</p>';
    } else {
      html += analyses.map(_renderClassCard).join('');
    }
    html += '</div>';

    // ── Visualizations group ─────────────────────────────────────────────────
    html += '<div class="analyses-group">' +
      '<h4 style="margin:0 0 8px;font-size:0.95em;text-transform:uppercase;letter-spacing:0.06em;color:#374151">Visualizations' +
      ' <span class="count-badge" style="font-size:0.8em">' + vizzes.length + '</span></h4>';
    if (vizzes.length === 0) {
      html += '<p class="empty-state muted" style="margin:0">No Visualization classes found. Install a pbg-* package that provides one (Registry tab &rarr; Available modules).</p>';
    } else {
      html += vizzes.map(_renderClassCard).join('');
    }
    html += '</div>';

    container.innerHTML = html;
  }

  function _loadAnalysesPage() {
    var container = document.getElementById('viz-picker-container');
    var countEl   = document.getElementById('viz-count');
    if (!container) return;
    window.DataSource.loadVisualizationClasses()
      .then(function(data) {
        var classes = (data && data.classes) || [];
        _renderAnalysesGroups(classes, container);
        if (countEl) countEl.textContent = '(' + classes.length + ')';
      })
      .catch(function(err) {
        container.innerHTML = '<p class="empty-state" style="color:#991b1b">Error loading classes: ' + _esc(String(err)) + '</p>';
      });
  }
  window._loadAnalysesPage = _loadAnalysesPage;

  // Source-rank for picker sort: in_workspace classes are the ones the user
  // can act on directly (they live in this workspace's package or an
  // explicit `imports:` entry); framework comes next; environment-only is
  // last (installed but not declared by this workspace). Matches the
  // server-side _source_order map in /api/registry so the picker reads in
  // the same order as the Registry tab.
  var _SOURCE_RANK = { in_workspace: 0, framework: 1, environment_only: 2 };

  function _renderKindPicker(items, container, kind) {
    if (!items || items.length === 0) {
      container.innerHTML = '<p class="empty-state">No ' + kind + 's registered. Install a pbg-* package that provides one (Registry tab &rarr; Available modules).</p>';
      return;
    }
    // Sort: in_workspace → framework → environment_only, then alpha by name.
    // Stable across loads so the list doesn't jitter between fetches.
    var sorted = items.slice().sort(function(a, b) {
      var ra = _SOURCE_RANK[a.source] != null ? _SOURCE_RANK[a.source] : 99;
      var rb = _SOURCE_RANK[b.source] != null ? _SOURCE_RANK[b.source] : 99;
      if (ra !== rb) return ra - rb;
      return (a.name || '').toLowerCase().localeCompare((b.name || '').toLowerCase());
    });
    var lastSource = null;
    var rows = sorted.map(function(it) {
      var schemaSnippet = '';
      if (it.schema_preview) {
        schemaSnippet = '<details><summary class="muted" style="cursor:pointer;font-size:0.85em">config_schema</summary><code class="registry-schema">' + _esc(it.schema_preview) + '</code></details>';
      }
      var previewBtn = (kind === 'visualization')
        ? '<button class="btn-mini js-authoring" onclick="_vizClassPreview(\'' + _esc(it.address) + '\',\'' + _esc(it.name) + '\')">Preview</button>'
        : '';
      // Section divider when source group changes. Lightweight — keeps the
      // sort intent visible without committing to a full grouped-list layout.
      var divider = '';
      if (it.source !== lastSource) {
        var labels = {
          in_workspace: 'Workspace',
          framework: 'Framework',
          environment_only: 'Environment (installed but not declared in workspace.yaml)',
        };
        var label = labels[it.source] || (it.source || 'other');
        divider = '<div class="picker-section-label muted" style="margin:10px 0 4px;font-size:0.78em;text-transform:uppercase;letter-spacing:0.05em">' + _esc(label) + '</div>';
        lastSource = it.source;
      }
      return divider + '<div class="picker-row" data-source="' + _esc(it.source || '') + '">' +
        '<div class="picker-row-main">' +
          '<strong>' + _esc(it.name) + '</strong>' +
          ' <code class="muted" style="font-size:0.82em">' + _esc(it.address) + '</code>' +
          schemaSnippet +
        '</div>' +
        '<div class="picker-row-actions">' +
          previewBtn +
          '<button class="btn-mini js-authoring" onclick="_useRegistryClass(\'' + kind + '\', \'' + _esc(it.name) + '\')">Use</button>' +
        '</div>' +
      '</div>';
    }).join('');
    container.innerHTML = rows;
  }

  function _useRegistryClass(kind, name) {
    if (kind === 'emitter') {
      _switchPage('simulation-setup');
      // Find the inline simulation form (inside a <details> in Simulation Setup)
      var form = document.getElementById('form-simulation');
      if (!form) return;
      var details = form.closest('details');
      if (details) details.open = true;
      var ta = form.querySelector('textarea[name=emitter_config]');
      if (ta) {
        ta.value = JSON.stringify({address: 'local:' + name, config: {}}, null, 2);
        // Highlight the textarea so user notices
        ta.classList.add('highlight-flash');
        setTimeout(function() { ta.classList.remove('highlight-flash'); }, 1500);
        // Scroll into view
        ta.scrollIntoView({behavior: 'smooth', block: 'center'});
      }
      // Show a transient banner
      var banner = document.createElement('div');
      banner.className = 'apply-banner';
      banner.textContent = name + ' applied to next Add simulation — review and submit below';
      form.parentNode.insertBefore(banner, form);
      setTimeout(function() { banner.remove(); }, 4000);
    } else if (kind === 'visualization') {
      // Open the workspace Add-Visualization modal pre-configured as a
      // class-backed instance of this Visualization class.
      _openWorkspaceVizModal();
      // Defer until the modal's promise has populated the class dropdown.
      var attempts = 0;
      var tryFill = function() {
        var sel = document.getElementById('viz-class-picker');
        if (!sel || sel.options.length <= 1) {
          if (attempts++ < 20) return setTimeout(tryFill, 60);
          return;
        }
        var modal = document.getElementById('modal-visualization');
        var nameInput = modal && modal.querySelector('input[name=viz_name]');
        if (nameInput && !nameInput.value) {
          nameInput.value = name.toLowerCase().replace(/[^a-z0-9_-]+/g, '-');
        }
        // Select the matching class option
        for (var i = 0; i < sel.options.length; i++) {
          if (sel.options[i].value === name) { sel.selectedIndex = i; break; }
        }
      };
      setTimeout(tryFill, 60);
    }
  }
  window._useRegistryClass = _useRegistryClass;

  function _renderRegistryEntry(p) {
    var aliases = (p.aliases || []).length
      ? ' <small style="color:#888">(aliases: ' + p.aliases.map(_esc).join(', ') + ')</small>'
      : '';
    var sourceAttr = p.source ? ' data-source="' + _esc(p.source) + '"' : '';
    // Workspace-default badge: shown only on emitter entries whose class
    // matches workspace.yaml::runtime.default_emitter (see server-side
    // _mark_default_emitter()). Keeps users aware which emitter their
    // study runs will pick by default.
    var defaultBadge = p.is_workspace_default
      ? ' <span class="count-badge" style="background:#1f7a36;color:#fff;font-size:0.7em;padding:1px 6px;border-radius:3px;margin-left:6px;vertical-align:middle" title="Workspace default per runtime.default_emitter in workspace.yaml">DEFAULT</span>'
      : '';
    return '<div class="registry-entry"' + sourceAttr + '>' +
      '<strong>' + _esc(p.name) + '</strong>' + defaultBadge + aliases + '<br>' +
      '<small><code>' + _esc(p.address) + '</code></small>' +
      (p.schema_preview
        ? '<details><summary>config schema</summary><pre class="json-tree">' + _esc(p.schema_preview) + '</pre></details>'
        : '') +
    '</div>';
  }

  function _renderRegistryGrid(containerId, entries) {
    var el = document.getElementById(containerId);
    if (!el) return;
    if (!entries || !entries.length) {
      el.innerHTML = '<p class="empty-state">None registered.</p>';
      return;
    }

    // Partition by source: in_workspace first, then framework, then environment_only.
    var inWs = entries.filter(function(p) { return p.source === 'in_workspace'; });
    var framework = entries.filter(function(p) { return p.source === 'framework'; });
    var envOnly = entries.filter(function(p) { return p.source === 'environment_only' || !p.source; });

    var html = '';

    // In-workspace and framework entries render normally.
    var primary = inWs.concat(framework);
    if (primary.length) {
      html += primary.map(_renderRegistryEntry).join('');
    } else {
      html += '<p class="empty-state muted" style="font-size:0.9em">No workspace-declared entries of this kind.</p>';
    }

    // Environment-only entries: collapsible section, dimmed.
    if (envOnly.length) {
      html +=
        '<details class="registry-env-section" style="margin-top:12px">' +
        '<summary style="cursor:pointer;color:#6b7280;font-size:0.9em;padding:4px 0">' +
        'Also available in environment (' + envOnly.length + ') — not declared in workspace.yaml' +
        '</summary>' +
        '<div style="opacity:0.6;margin-top:6px">' +
        envOnly.map(_renderRegistryEntry).join('') +
        '</div>' +
        '<p style="font-size:0.8em;color:#9ca3af;margin:4px 0 0">Run <code>/pbg-install &lt;pkg&gt;</code> to add a package to this workspace\'s imports.</p>' +
        '</details>';
    }

    el.innerHTML = html;
  }

  function _renderRegistryTypesGrid(containerId, types) {
    var el = document.getElementById(containerId);
    if (!el) return;
    if (!types || !types.length) {
      el.innerHTML = '<p class="empty-state">None registered.</p>';
      return;
    }
    el.innerHTML = types.map(function(t) {
      return '<div class="registry-entry">' +
        '<strong>' + _esc(t.name) + '</strong><br>' +
        (t.schema_preview
          ? '<small style="color:#666">' + _esc(t.schema_preview) + '</small>'
          : '') +
      '</div>';
    }).join('');
  }

  function _setRegistryTab(kind) {
    document.querySelectorAll('.registry-tab').forEach(function(b) {
      b.classList.toggle('active', b.dataset.kind === kind);
    });
    document.querySelectorAll('.registry-tab-panel').forEach(function(p) {
      p.classList.toggle('active', p.dataset.kind === kind);
    });
    // Re-apply filter to the now-visible panel.
    var q = (document.getElementById('registry-search') || {value: ''}).value;
    _filterRegistry(q);
  }
  window._setRegistryTab = _setRegistryTab;

  function _filterRegistry(query) {
    var q = (query || '').toLowerCase();
    var activePanel = document.querySelector('.registry-tab-panel.active');
    if (!activePanel) return;
    activePanel.querySelectorAll('.registry-entry').forEach(function(row) {
      var text = row.textContent.toLowerCase();
      row.style.display = (!q || text.indexOf(q) !== -1) ? '' : 'none';
    });
    // Auto-open the environment-only details section when a search matches entries inside it.
    activePanel.querySelectorAll('.registry-env-section').forEach(function(details) {
      if (!q) { details.open = false; return; }
      var hasVisible = false;
      details.querySelectorAll('.registry-entry').forEach(function(row) {
        if (row.style.display !== 'none') hasVisible = true;
      });
      if (hasVisible) details.open = true;
    });
  }
  window._filterRegistry = _filterRegistry;

  function _loadRegistry(refresh) {
    var status = document.getElementById('registry-status');
    if (status) status.textContent = 'Loading…';
    var _p = window.DataSource
      ? window.DataSource.loadRegistry(refresh)
      : fetch('/api/registry' + (refresh ? '?refresh=1' : '')).then(function(r) { return r.json(); });
    _p
      .then(function(data) {
        if (status) {
          if (data.error) {
            status.innerHTML = '<span style="color:#991b1b">⚠ ' + data.error + '</span>';
          } else {
            status.textContent = '';
          }
        }
        var processes = data.processes || [];
        var types = data.types || [];
        var byKind = {process: [], step: [], emitter: [], visualization: [], other: []};
        processes.forEach(function(p) {
          var k = p.kind || 'other';
          if (!byKind[k]) byKind[k] = [];
          byKind[k].push(p);
        });

        // Render tabbed Registry browser (Registry page).
        _renderRegistryGrid('registry-processes-container', byKind.process);
        _renderRegistryGrid('registry-steps-container', byKind.step);
        _renderRegistryGrid('registry-emitters-container', byKind.emitter);
        _renderRegistryGrid('registry-visualizations-container', byKind.visualization);
        _renderRegistryTypesGrid('registry-types-container', types);

        // Per-tab count badges: show workspace-declared count + total in parens.
        // "in_workspace" entries are the actionable ones; environment_only are dimmed.
        var setCount = function(id, entries) {
          var el = document.getElementById(id);
          if (!el) return;
          var wsCount = entries.filter(function(e) { return e.source === 'in_workspace'; }).length;
          var total = entries.length;
          if (wsCount === total) {
            el.textContent = total;
          } else {
            el.textContent = wsCount + ' / ' + total;
            el.title = wsCount + ' from this workspace, ' + (total - wsCount) + ' from environment';
          }
        };
        setCount('registry-process-count', byKind.process);
        setCount('registry-step-count', byKind.step);
        setCount('registry-emitter-count', byKind.emitter);
        setCount('registry-visualization-count', byKind.visualization);
        var typeCountEl = document.getElementById('registry-type-count');
        if (typeCountEl) typeCountEl.textContent = types.length;
        var total = document.getElementById('registry-total-count');
        if (total) {
          var wsProcessCount = processes.filter(function(p) { return p.source === 'in_workspace'; }).length;
          if (wsProcessCount < processes.length) {
            total.textContent = wsProcessCount + ' workspace + ' + (processes.length - wsProcessCount) + ' env / ' + types.length + ' types';
          } else {
            total.textContent = (processes.length + types.length) + ' total';
          }
        }

        // Populate sim-process picker if present (Composite Explorer / setup forms).
        // Only show in-workspace processes in the picker; environment-only are not
        // declared by this workspace and using them would be unreliable.
        var picker = document.getElementById('sim-process-picker');
        if (picker) {
          var wsProcesses = processes.filter(function(p) {
            return p.source === 'in_workspace' || p.source === 'framework';
          });
          if (wsProcesses.length === 0) {
            picker.innerHTML = '<p class="muted">No workspace processes registered yet.</p>';
          } else {
            picker.innerHTML = wsProcesses.map(function(p) {
              return '<label style="display:inline-block; margin-right:12px">' +
                '<input type="checkbox" name="processes" value="' + p.name + '"> ' + p.name +
                '</label>';
            }).join('');
          }
        }

        // Note: the Analyses page (viz-picker-container) is now populated by
        // _loadAnalysesPage() (called from _switchPage), which fetches
        // /api/visualization-classes and renders two groups (Analyses + Visualizations).
      })
      .catch(function(err) {
        if (status) status.innerHTML = '<span style="color:#991b1b">Network error: ' + err + '</span>';
      });
  }

  window._loadRegistry = _loadRegistry;

  // -------------------------------------------------------------------------
  // Composites browser (v0.5.6: search + tag chips + list view)
  // -------------------------------------------------------------------------

  window._composites = [];
  window._compositesFilter = { search: '', tags: new Set() };
  window._compositesView = 'grid';
  // Default sort: workspace-local composites first, then alphabetical.
  // Surfaces the composites the current investigation actually needs
  // ahead of the full list of every installed pbg-* package's composites
  // — the Composites tab grew unwieldy as more pbg-* packages came
  // online. Other sorts (name / module / kind) remain available via the
  // dropdown.
  window._compositesSort = 'workspace-first';

  function _buildCompositeChips() {
    var chipsEl = document.getElementById('composite-tag-chips');
    if (!chipsEl) return;
    var allTags = [];
    window._composites.forEach(function(c) {
      (c.tags || []).forEach(function(t) {
        if (allTags.indexOf(t) === -1) allTags.push(t);
      });
    });
    allTags.sort();
    chipsEl.innerHTML = allTags.map(function(t) {
      return '<button class="card-browse-chip" onclick="_toggleCompositeChip(this,\'' + _esc(t) + '\')">' + _esc(t) + '</button>';
    }).join('');
  }

  function _toggleCompositeChip(btn, tag) {
    if (window._compositesFilter.tags.has(tag)) {
      window._compositesFilter.tags.delete(tag);
      btn.classList.remove('active');
    } else {
      window._compositesFilter.tags.add(tag);
      btn.classList.add('active');
    }
    _renderComposites();
  }
  window._toggleCompositeChip = _toggleCompositeChip;

  function _setCompositeView(view) {
    window._compositesView = view;
    var btns = document.querySelectorAll('#composite-toolbar .view-btn');
    btns.forEach(function(b) {
      b.classList.toggle('active', b.getAttribute('data-view') === view);
    });
    _renderComposites();
  }
  window._setCompositeView = _setCompositeView;

  function _setCompositesSort(value) {
    window._compositesSort = value || 'workspace-first';
    _renderComposites();
  }
  window._setCompositesSort = _setCompositesSort;

  function _renderComposites() {
    var container = document.getElementById('composite-cards');
    if (!container) return;
    var f = window._compositesFilter;
    var search = f.search.toLowerCase();
    var activeTags = f.tags;
    var composites = window._composites.filter(function(c) {
      if (search) {
        var haystack = (c.name + ' ' + (c.description || '') + ' ' + (c.tags || []).join(' ') + ' ' + (c.module || '')).toLowerCase();
        if (haystack.indexOf(search) === -1) return false;
      }
      if (activeTags.size > 0) {
        var cTags = c.tags || [];
        var match = false;
        activeTags.forEach(function(t) { if (cTags.indexOf(t) !== -1) match = true; });
        if (!match) return false;
      }
      return true;
    });

    // Apply sort toggle (Workspace first / Name / Module / Kind). Ties
    // break on name. Workspace-first puts composites whose `module` starts
    // with the workspace's own package prefix (backend-annotated as
    // `workspace_local: true` on each /api/composites record) at the top,
    // followed by every-installed-pbg-* composites alphabetically. When
    // grouping, _renderGroupedComposites below inserts a visual section
    // divider between the two groups.
    var sorted = composites.slice();
    if (window._compositesSort === 'module') {
      sorted.sort(function(a, b) {
        return (a.module || '').localeCompare(b.module || '')
          || (a.name || '').localeCompare(b.name || '');
      });
    } else if (window._compositesSort === 'kind') {
      sorted.sort(function(a, b) {
        return (a.kind || '').localeCompare(b.kind || '')
          || (a.name || '').localeCompare(b.name || '');
      });
    } else if (window._compositesSort === 'workspace-first') {
      sorted.sort(function(a, b) {
        var aw = a.workspace_local ? 0 : 1;
        var bw = b.workspace_local ? 0 : 1;
        return (aw - bw)
          || (a.name || '').localeCompare(b.name || '');
      });
    } else {
      sorted.sort(function(a, b) {
        return (a.name || '').localeCompare(b.name || '');
      });
    }
    composites = sorted;

    if (!composites.length) {
      container.innerHTML = '<p class="empty-state">No composites match the current filter.</p>';
      container.className = '';
      return;
    }

    function _moduleLine(c) {
      var mod = c.module || '';
      var kind = c.kind || 'spec';
      var kindBadge = (kind === 'generator')
        ? ' <span class="kind-badge">generator</span>' : '';
      if (!mod) return '';
      return '<div class="composite-module"><small>Module:</small> ' +
        '<code>' + _esc(mod) + '</code>' + kindBadge + '</div>';
    }
    function _wsTag(c) {
      // Small "📦 workspace" pill on cards whose composite lives in the
      // workspace's own package. Helps the user scan quickly even when
      // the workspace-first sort isn't active.
      return c.workspace_local
        ? '<span class="composite-ws-tag">📦 workspace</span>' : '';
    }
    // Section-divider injector — emits a thin "Other modules" separator
    // between the last workspace-local item and the first non-local item
    // when the workspace-first sort is active and both groups are present.
    // Returns '' otherwise so existing layouts are byte-identical.
    function _maybeDivider(prev, cur) {
      if (window._compositesSort !== 'workspace-first') return '';
      if (!prev || !cur) return '';
      if (prev.workspace_local && !cur.workspace_local) {
        return '<div class="composite-section-divider">'
             + '<span>Other installed pbg-* modules</span></div>';
      }
      return '';
    }

    var _isSnapshot = document.body.classList.contains('snapshot');
    if (window._compositesView === 'list') {
      container.className = 'composite-list';
      var prevC = null;
      var rows = composites.map(function(c) {
        var tagPills = (c.tags || []).map(function(t) {
          return '<span class="tag-pill">' + _esc(t) + '</span>';
        }).join('');
        var divider = _maybeDivider(prevC, c);
        prevC = c;
        var exploreBtn = (_isSnapshot && !c.has_wiring)
          ? ''
          : '<button class="action-btn" onclick="_openCompositeExplorer(\'' + _esc(c.id) + '\')">Explore</button>';
        return divider + '<div class="composite-list-row">' +
          '<span class="name">' + _esc(c.name) + ' ' + _wsTag(c) + '</span>' +
          '<span class="desc">' + tagPills + ' ' + _esc(c.description || '(no description)') +
            _moduleLine(c) +
          '</span>' +
          '<span>' + exploreBtn + '</span>' +
          '</div>';
      });
      container.innerHTML = rows.join('');
    } else {
      container.className = 'module-grid';
      var prevG = null;
      var cards = composites.map(function(c) {
        var paramSummary = '';
        var paramKeys = Object.keys(c.parameters || {});
        if (paramKeys.length) {
          paramSummary = '<div class="module-tags">' +
            paramKeys.map(function(k) {
              return '<span class="tag-pill">' + _esc(k) + '</span>';
            }).join('') + '</div>';
        }
        var requires = '';
        if (c.requires && c.requires.processes && c.requires.processes.length) {
          requires = '<small class="muted">Requires: ' +
            c.requires.processes.map(_esc).join(', ') + '</small><br>';
        }
        var tagSummary = '';
        if (c.tags && c.tags.length) {
          tagSummary = '<div class="module-tags">' +
            c.tags.map(function(t) {
              return '<span class="tag-pill" style="background:#e0e7ff;color:#3730a3">' + _esc(t) + '</span>';
            }).join(' ') + '</div>';
        }
        var divider = _maybeDivider(prevG, c);
        prevG = c;
        var exploreBtn = (_isSnapshot && !c.has_wiring)
          ? ''
          : '<button class="action-btn" onclick="_openCompositeExplorer(\'' + _esc(c.id) + '\')">Explore</button>';
        return divider + '<div class="module-card' + (c.workspace_local ? ' module-card-workspace' : '') + '">' +
          '<div class="module-card-header"><strong>' + _esc(c.name) + '</strong> ' + _wsTag(c) + '</div>' +
          '<p class="module-desc">' + _esc(c.description || '(no description)') + '</p>' +
          _moduleLine(c) +
          requires +
          tagSummary +
          paramSummary +
          '<div class="module-action">' +
            exploreBtn +
          '</div>' +
        '</div>';
      });
      container.innerHTML = cards.join('');
    }
  }
  window._renderComposites = _renderComposites;

  function _loadComposites() {
    var _p = window.DataSource
      ? window.DataSource.loadComposites()
      : fetch('/api/composites').then(function(r) { return r.json(); });
    _p
      .then(function(data) {
        var container = document.getElementById('composite-cards');
        var countBadge = document.getElementById('composite-count');
        if (!container) return;
        var composites = data.composites || [];
        // Cache by id so onclick handlers pass just the id; _useComposite
        // looks the full object up. Inline JSON.stringify in onclick attrs
        // breaks when descriptions contain apostrophes / quotes.
        window._compositesById = {};
        composites.forEach(function(c) { window._compositesById[c.id] = c; });
        if (countBadge) countBadge.textContent = '(' + composites.length + ')';
        if (!composites.length) {
          container.innerHTML =
            '<p class="empty-state">No composite specs found yet. Add a <code>*.composite.yaml</code> file under ' +
            '<code>pbg_&lt;slug&gt;/composites/</code> to register one. See ' +
            '<a href="https://github.com/vivarium-collective/pbg-superpowers/blob/main/docs/conventions/composites.md" target="_blank">' +
            'the composite spec convention</a> for the format.</p>';
          return;
        }
        window._composites = composites;
        // Wire up search input
        var searchEl = document.getElementById('composite-search');
        if (searchEl && !searchEl._pbgWired) {
          searchEl._pbgWired = true;
          searchEl.oninput = function() {
            window._compositesFilter.search = this.value.toLowerCase();
            _renderComposites();
          };
        }
        _buildCompositeChips();
        _renderComposites();
      });
  }
  window._loadComposites = _loadComposites;

  function _useComposite(compositeOrId) {
    // Accept either a full composite object (legacy) or an id string.
    var composite = (typeof compositeOrId === 'string')
      ? (window._compositesById || {})[compositeOrId]
      : compositeOrId;
    if (!composite) {
      alert("Composite not found in cache. Reload the page and try again.");
      return;
    }
    var modal = document.getElementById('modal-configure-composite');
    if (!modal) return;
    var nameSpan = document.getElementById('cc-composite-name');
    if (nameSpan) {
      nameSpan.innerHTML = 'Composite: <code>' + _esc(composite.id) + '</code>';
    }
    var hiddenId = modal.querySelector('input[name=composite_id]');
    if (hiddenId) hiddenId.value = composite.id;
    // Pre-fill sim_name with a sensible default
    var simNameInput = modal.querySelector('input[name=sim_name]');
    if (simNameInput) simNameInput.value = composite.name + '-run';
    // Render parameter fields
    var fieldsContainer = document.getElementById('cc-parameter-fields');
    if (fieldsContainer) {
      var params = composite.parameters || {};
      var keys = Object.keys(params);
      if (!keys.length) {
        fieldsContainer.innerHTML = '<p class="muted" style="font-size:0.9em">No parameters to configure.</p>';
      } else {
        fieldsContainer.innerHTML = '<h4 style="margin:14px 0 6px;font-size:0.95em">Parameters</h4>' +
          keys.map(function(pname) {
            var pdef = params[pname];
            var inputType = (pdef.type === 'int' || pdef.type === 'float') ? 'number' : 'text';
            var step = (pdef.type === 'float') ? 'any' : (pdef.type === 'int' ? '1' : '');
            var def = pdef.default === undefined ? '' : String(pdef.default);
            var desc = pdef.description ? ('<small class="muted">' + _esc(pdef.description) + '</small>') : '';
            return '<label>' + _esc(pname) + ' <span class="muted">(' + (pdef.type || 'string') + ')</span>' +
              '<input name="param_' + _esc(pname) + '" type="' + inputType + '"' +
              (step ? ' step="' + step + '"' : '') +
              ' value="' + _esc(def) + '">' +
              desc +
            '</label>';
          }).join('');
      }
    }
    openModal('modal-configure-composite');
  }
  window._useComposite = _useComposite;

  function _submitConfigureComposite(form) {
    var data = {
      name: form.sim_name.value.trim(),
      composite: form.composite_id.value,
      t_start: parseFloat(form.t_start.value),
      t_end: parseFloat(form.t_end.value),
      parameter_overrides: {},
    };
    // Collect param_<name> fields
    Array.from(form.elements).forEach(function(el) {
      if (el.name && el.name.indexOf('param_') === 0 && el.value !== '') {
        var pname = el.name.substring('param_'.length);
        var v = el.value;
        // Cast based on input type
        if (el.type === 'number') v = parseFloat(v);
        data.parameter_overrides[pname] = v;
      }
    });
    submitForm(form, '/api/simulation', function() { return data; });
  }
  window._submitConfigureComposite = _submitConfigureComposite;

  // -------------------------------------------------------------------------
  // Catalog browser (v0.5.6: search + tag chips + list view + installed filter)
  // -------------------------------------------------------------------------

  window._catalogModules = [];
  window._catalogFilter = { search: '', tags: new Set(), installed: 'all' };
  window._catalogView = 'grid';

  function _buildCatalogChips() {
    var chipsEl = document.getElementById('catalog-tag-chips');
    if (!chipsEl) return;
    chipsEl.innerHTML = ''; return; // tag filter chips hidden
    var allTags = [];
    window._catalogModules.forEach(function(m) {
      (m.tags || []).forEach(function(t) {
        if (allTags.indexOf(t) === -1) allTags.push(t);
      });
    });
    allTags.sort();
    chipsEl.innerHTML = allTags.map(function(t) {
      return '<button class="card-browse-chip" onclick="_toggleCatalogChip(this,\'' + _esc(t) + '\')">' + _esc(t) + '</button>';
    }).join('');
  }

  function _toggleCatalogChip(btn, tag) {
    if (window._catalogFilter.tags.has(tag)) {
      window._catalogFilter.tags.delete(tag);
      btn.classList.remove('active');
    } else {
      window._catalogFilter.tags.add(tag);
      btn.classList.add('active');
    }
    _renderCatalog();
  }
  window._toggleCatalogChip = _toggleCatalogChip;

  function _setCatalogView(view) {
    window._catalogView = view;
    var btns = document.querySelectorAll('#catalog-toolbar .view-btn');
    btns.forEach(function(b) {
      b.classList.toggle('active', b.getAttribute('data-view') === view);
    });
    _renderCatalog();
  }
  window._setCatalogView = _setCatalogView;

  function _renderCatalog() {
    var grid = document.getElementById('catalog-modules-grid');
    if (!grid) return;
    var f = window._catalogFilter;
    var search = f.search.toLowerCase();
    var activeTags = f.tags;
    var modules = window._catalogModules.filter(function(m) {
      // The workspace's own first-party package (kind === 'workspace')
      // used to be filtered out here because it had its own dedicated
      // row in the now-removed "Installed modules" table. After the
      // page-reorg (separate Installed panel folded into this catalog
      // grid), it gets pinned at the very top instead — see the
      // installed-first sort below.
      // Search filter — workspace package is exempt from text-search
      // hiding (it should always show as the pinned "your workspace"
      // card so users have a stable anchor at the top of the grid).
      if (search && m.kind !== 'workspace') {
        var haystack = (m.name + ' ' + (m.description || '') + ' ' + (m.tags || []).join(' ')).toLowerCase();
        if (haystack.indexOf(search) === -1) return false;
      }
      // Installed filter — workspace package always passes (it's
      // structurally installed).
      if (f.installed === 'installed' && !m.installed && m.kind !== 'workspace') return false;
      if (f.installed === 'uninstalled' && (m.installed || m.kind === 'workspace')) return false;
      // Tag chip filter (OR within: pass if any selected tag matches).
      // Workspace package is exempt (no tags).
      if (activeTags.size > 0 && m.kind !== 'workspace') {
        var mTags = m.tags || [];
        var match = false;
        activeTags.forEach(function(t) { if (mTags.indexOf(t) !== -1) match = true; });
        if (!match) return false;
      }
      return true;
    });

    // Sort: workspace package first (anchor), then installed modules
    // (alphabetical), then everything else (alphabetical). This is the
    // visible expression of "what's in your workspace surfaces first;
    // browse-everything is secondary" — the same pattern the Composites
    // tab adopted (workspace-first sort) for the same reason.
    modules.sort(function(a, b) {
      var aw = a.kind === 'workspace' ? 0 : 1;
      var bw = b.kind === 'workspace' ? 0 : 1;
      if (aw !== bw) return aw - bw;
      var ai = a.installed ? 0 : 1;
      var bi = b.installed ? 0 : 1;
      if (ai !== bi) return ai - bi;
      return (a.name || '').localeCompare(b.name || '');
    });

    if (!modules.length) {
      grid.innerHTML = '<p class="empty-state">No modules match the current filter.</p>';
      grid.className = '';
      return;
    }

    function _installedMeta(m) {
      // Source / ref / path rows that used to live in the now-removed
      // "Installed modules" table. Surface them inline on installed
      // cards so the info isn't lost.
      if (!m.installed && m.kind !== 'workspace') return '';
      var bits = [];
      if (m.source) bits.push('<small class="muted">Source: <code>' + _esc(m.source) + '</code>' +
        (m.ref ? ' @ <code>' + _esc(m.ref) + '</code>' : '') + '</small>');
      var path = m.install_path || m.path;
      if (path) bits.push('<small class="muted">Path: <code>' + _esc(path) + '</code></small>');
      return bits.length ? '<div class="module-installed-meta">' + bits.join('<br>') + '</div>' : '';
    }

    function _actionFor(m) {
      // Workspace's own first-party package is not uninstallable — show
      // a "first-party" pill. Otherwise: Install or Uninstall.
      if (m.kind === 'workspace') {
        return '<span class="status-pill installed" title="The workspace\'s own first-party package. Always present; cannot be uninstalled.">first-party</span>';
      }
      if (m.installed) {
        // Render the install-source badge + a context-appropriate action.
        // Three install-source layers (see server.py:_get_catalog):
        //   imports   — workspace.yaml.imports declared it; uninstall is
        //               the simple two-file edit (workspace.yaml + pyproject)
        //   pyproject — declared in pyproject.toml only; uninstall flow
        //               still works (drops the dep + re-locks the venv)
        //   venv      — present in venv via another package's transitive
        //               dep; cannot be uninstalled directly (the user has
        //               to remove the parent). Show "via X, Y" hint instead.
        var src = m.install_source || 'imports';
        var srcBadge = '';
        var action = '';
        if (src === 'venv') {
          var via = (m.installed_via || []);
          if (via.length === 0) {
            // No parent claims it — orphaned editable / hand-installed pkg.
            // Workspace.yaml doesn't declare it and no installed dep requires
            // it. User can uninstall directly from the dashboard.
            srcBadge = '<span class="install-src-pill install-src-unmanaged" title="Installed in the venv but not declared in workspace.yaml.imports and not required by any installed package. Safe to uninstall.">📦 unmanaged</span>';
            action = '<button class="action-btn action-btn--secondary js-authoring" onclick="_uninstallFromCatalog(\'' + _esc(m.name) + '\')">Uninstall</button>';
          } else {
            var viaText = 'via ' + via.slice(0, 3).map(_esc).join(', ') + (via.length > 3 ? ' +' + (via.length - 3) : '');
            srcBadge = '<span class="install-src-pill install-src-venv" title="Brought in by another installed package; cannot be uninstalled directly.">📦 ' + viaText + '</span>';
            action = '<span class="muted" style="font-size:0.78em" title="Remove the parent package to drop this transitive dependency.">(remove parent to uninstall)</span>';
          }
        } else if (src === 'pyproject') {
          srcBadge = '<span class="install-src-pill install-src-pyproject" title="Declared in pyproject.toml [project.dependencies]; workspace.yaml.imports does not have an explicit entry.">📋 via pyproject</span>';
          action = '<button class="action-btn action-btn--secondary js-authoring" onclick="_uninstallFromCatalog(\'' + _esc(m.name) + '\')">Uninstall</button>';
        } else {
          srcBadge = '<span class="status-pill installed">installed</span>';
          action = '<button class="action-btn action-btn--secondary js-authoring" onclick="_uninstallFromCatalog(\'' + _esc(m.name) + '\')">Uninstall</button>';
        }
        return srcBadge + ' ' + action;
      }
      return '<button class="action-btn js-authoring" onclick="_installFromCatalog(\'' + _esc(m.name) + '\')">Install</button>';
    }

    // Section divider injected at boundaries: workspace → installed →
    // available. Spans all grid columns; styled in style.css under
    // .module-section-divider.
    function _maybeSectionDivider(prev, cur) {
      if (!prev || !cur) return '';
      var prevSection = prev.kind === 'workspace' ? 0 : (prev.installed ? 1 : 2);
      var curSection  = cur.kind  === 'workspace' ? 0 : (cur.installed  ? 1 : 2);
      if (prevSection === curSection) return '';
      var label = (curSection === 1)
        ? 'Installed in this workspace'
        : 'Available to install';
      return '<div class="module-section-divider"><span>' + label + '</span></div>';
    }

    if (window._catalogView === 'list') {
      grid.className = 'module-list';
      var prevL = null;
      var rows = modules.map(function(m) {
        var divider = _maybeSectionDivider(prevL, m);
        prevL = m;
        var tagPills = ''; // tag pills hidden
        return divider + '<div class="module-list-row' + (m.kind === 'workspace' ? ' module-row-workspace' : '') + '">' +
          '<span class="name">' + _esc(m.name) + '</span>' +
          '<span class="desc">' + tagPills + ' ' + _esc(m.description || '') + _installedMeta(m) + '</span>' +
          '<span>' + _actionFor(m) + '</span>' +
          '</div>';
      });
      grid.innerHTML = rows.join('');
    } else {
      grid.className = 'module-grid';
      var prevG = null;
      var cards = modules.map(function(m) {
        var divider = _maybeSectionDivider(prevG, m);
        prevG = m;
        var tags = ''; // tag pills hidden
        var homepage = m.homepage
          ? '<a href="' + _esc(m.homepage) + '" target="_blank" class="module-link">GitHub &#8599;</a>'
          : '';
        var workspaceCls = (m.kind === 'workspace') ? ' module-card-workspace'
                          : (m.installed ? ' module-card-installed' : '');
        return divider + '<div class="module-card' + workspaceCls + '">' +
          '<div class="module-card-header"><strong>' + _esc(m.name) + '</strong> ' + homepage + '</div>' +
          '<p class="module-desc">' + _esc(m.description) + '</p>' +
          '<div class="module-tags">' + tags + '</div>' +
          _installedMeta(m) +
          '<div class="module-action">' + _actionFor(m) + '</div>' +
          '</div>';
      });
      grid.innerHTML = cards.join('');
    }
  }
  window._renderCatalog = _renderCatalog;

  // Registry page sub-tab toggle. Two sub-tabs: "modules" (the catalog
  // grid above, where the workspace package + installed modules now
  // pin at top) and "discovered" (the live build_core() introspection
  // — Processes / Steps / Emitters / Visualizations / Types). The old
  // layout stacked these as three scrolling panels; sub-tabs let users
  // flip without scrolling.
  function _setRegistrySubtab(name) {
    name = name || 'modules';
    document.querySelectorAll('.registry-subtab').forEach(function(el) {
      el.classList.toggle('active', el.dataset.subtab === name);
    });
    document.querySelectorAll('.registry-subtab-panel').forEach(function(el) {
      el.classList.toggle('active', el.dataset.subtab === name);
    });
    // First time the discovered subtab opens, ensure registry is
    // populated (it's lazy-loaded). _loadRegistry no-ops on second call
    // unless force=true, so this is cheap when called repeatedly.
    if (name === 'discovered' && typeof _loadRegistry === 'function') {
      _loadRegistry(false);
    }
  }
  window._setRegistrySubtab = _setRegistrySubtab;

  // -------------------------------------------------------------------------
  // Installed modules: dynamic render from /api/catalog (single source of truth)
  // -------------------------------------------------------------------------

  function _renderInstalledModules(modules) {
    var container = document.getElementById('installed-modules-list');
    if (!container) return;
    var installed = (modules || []).filter(function(m) { return m.installed === true; });
    var countEl = document.getElementById('installed-modules-count');
    if (countEl) countEl.textContent = installed.length ? String(installed.length) : '';

    if (!installed.length) {
      container.innerHTML = '<p class="empty-state">No modules installed yet. Pick one from Available modules above.</p>';
      return;
    }

    // Pin the workspace's own first-party package row at the top.
    installed.sort(function(a, b) {
      var aw = a.kind === 'workspace' ? 0 : 1;
      var bw = b.kind === 'workspace' ? 0 : 1;
      if (aw !== bw) return aw - bw;
      return (a.name || '').localeCompare(b.name || '');
    });

    var rows = installed.map(function(m) {
      var name = _esc(m.name);
      var source = _esc(m.source || '');
      var ref = _esc(m.ref || 'main');
      var path = _esc(m.install_path || m.path || '—');
      var pkg = _esc(m.package || m.name);

      // The workspace's own package isn't uninstallable — it's the workspace.
      // Render with a "first-party" pill and no Uninstall button.
      if (m.kind === 'workspace') {
        return '<tr style="background:#f8fafc">' +
          '<td><code>' + name + '</code><br><small style="color:#6b7280">' + pkg + '</small></td>' +
          '<td><code>' + source + '</code> @ <code>' + ref + '</code></td>' +
          '<td><code>' + path + '</code></td>' +
          '<td><span class="status-pill installed" title="The workspace\'s own first-party package. Always present; cannot be uninstalled.">first-party</span></td>' +
          '<td><span style="color:#6b7280;font-size:0.85em">workspace package</span></td>' +
          '</tr>';
      }

      var sysDepsBtn = '';
      // Only surface a "Run system-deps check" button when the module is
      // installed AND the catalog flagged drift OR the entry declares
      // native deps. Keeps the table clean for the common case.
      var hasSysDeps = m.system_dependencies && (m.system_dependencies.checks || []).length;
      if (hasSysDeps || m.out_of_sync) {
        sysDepsBtn = ' <button class="action-btn action-btn--secondary" onclick="_checkSystemDepsForInstalled(\'' + name + '\')">Check system deps</button>';
      }
      return '<tr>' +
        '<td><code>' + name + '</code><br><small style="color:#6b7280">' + pkg + '</small></td>' +
        '<td><code>' + source + '</code> @ <code>' + ref + '</code></td>' +
        '<td><code>' + path + '</code></td>' +
        '<td><span class="status-pill installed">installed</span></td>' +
        '<td><button class="action-btn action-btn--secondary" onclick="_uninstallFromInstalled(\'' + name + '\')">Uninstall</button>' + sysDepsBtn + '</td>' +
        '</tr>';
    }).join('');

    container.innerHTML =
      '<table>' +
      '<thead><tr><th>Name</th><th>Source</th><th>Path</th><th>Status</th><th>Actions</th></tr></thead>' +
      '<tbody>' + rows + '</tbody>' +
      '</table>';
  }
  window._renderInstalledModules = _renderInstalledModules;

  function _checkInstalledModulesSync(modules) {
    var warningEl = document.getElementById('installed-modules-sync-warning');
    if (!warningEl) return;
    var drifted = (modules || []).filter(function(m) { return m.installed && m.out_of_sync; });
    if (!drifted.length) { warningEl.style.display = 'none'; return; }
    warningEl.style.cssText = 'display:block;background:#fef3c7;border:1px solid #fcd34d;border-radius:4px;padding:10px;margin-top:12px;font-size:0.9em;color:#92400e';
    warningEl.innerHTML =
      '<strong>⚠ Modules out of sync:</strong> ' +
      drifted.map(function(m) {
        return '<code>' + _esc(m.name) + '</code> — ' + _esc(m.out_of_sync_reason || 'state mismatch');
      }).join('; ') +
      '. The Installed list above reflects <code>workspace.yaml</code>, but the workspace venv disagrees. ' +
      'Try uninstalling + reinstalling, or restart the workspace.';
  }
  window._checkInstalledModulesSync = _checkInstalledModulesSync;

  function _uninstallFromInstalled(name) {
    if (!confirm('Uninstall ' + name + '? This removes it from this workspace\'s dependencies.')) return;
    fetch('/api/catalog-uninstall', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({name: name}),
    })
      .then(function(r) { return r.json().then(function(j) { return {ok: r.ok, json: j}; }); })
      .then(function(p) {
        if (!p.ok) {
          alert('Uninstall failed: ' + (p.json.error || 'unknown'));
          return;
        }
        var msg = p.json.already_uninstalled ? 'Already uninstalled.' : 'Uninstalled ' + name + '.';
        if (typeof _showToast === 'function') _showToast(msg);
        else alert(msg);
        // Refresh catalog (which now also refreshes the Installed list via _renderInstalledModules)
        if (typeof _loadCatalog === 'function') _loadCatalog();
        if (typeof _loadRegistry === 'function') _loadRegistry(true);
      })
      .catch(function(err) {
        alert('Network error: ' + err);
      });
  }
  window._uninstallFromInstalled = _uninstallFromInstalled;

  function _checkSystemDepsForInstalled(name) {
    fetch('/api/system-deps-check?name=' + encodeURIComponent(name))
      .then(function(r) { return r.json().then(function(j) { return [r.ok, j]; }); })
      .then(function(parts) {
        var ok = parts[0], j = parts[1];
        if (!ok || !j || !j.checks) {
          alert('System-deps check failed: ' + ((j && j.error) || 'unknown'));
          return;
        }
        if (j.ok) {
          alert('All system dependencies for ' + name + ' are satisfied.\n\n' +
            (j.checks || []).map(function(c) { return '  • ' + c.name + ' OK'; }).join('\n'));
          return;
        }
        // Reuse the install-flow modal so the user can choose to install deps.
        _showSystemDepsModal(name, j);
      })
      .catch(function(err) {
        alert('Network error: ' + String(err));
      });
  }
  window._checkSystemDepsForInstalled = _checkSystemDepsForInstalled;

  function _loadCatalog() {
    var _p = window.DataSource
      ? window.DataSource.loadCatalog()
      : fetch('/api/catalog').then(function(r) { return r.json(); });
    _p
      .then(function(data) {
        var grid = document.getElementById('catalog-modules-grid');
        if (!grid) return;
        if (!data.modules || data.modules.length === 0) {
          grid.innerHTML = '<p class="empty-state">Catalog empty.</p>';
          // Still refresh Installed list (will show empty-state).
          _renderInstalledModules([]);
          _checkInstalledModulesSync([]);
          return;
        }
        window._catalogModules = data.modules;
        // Wire up toolbar interactions
        var searchEl = document.getElementById('catalog-search');
        if (searchEl && !searchEl._pbgWired) {
          searchEl._pbgWired = true;
          searchEl.oninput = function() {
            window._catalogFilter.search = this.value.toLowerCase();
            _renderCatalog();
          };
        }
        var radios = document.querySelectorAll('input[name="catalog-installed-filter"]');
        radios.forEach(function(r) {
          if (!r._pbgWired) {
            r._pbgWired = true;
            r.onchange = function() {
              window._catalogFilter.installed = this.value;
              _renderCatalog();
            };
          }
        });
        _buildCatalogChips();
        _renderCatalog();
        _renderInstalledModules(data.modules);
        _checkInstalledModulesSync(data.modules);
      })
      .catch(function(err) {
        var grid = document.getElementById('catalog-modules-grid');
        if (grid) grid.innerHTML = '<p class="empty-state" style="color:#c00">Catalog load failed: ' + _esc(String(err)) + '</p>';
      });
  }
  window._loadCatalog = _loadCatalog;

  // -------------------------------------------------------------------------
  // Install error rendering (v0.4.5)
  // -------------------------------------------------------------------------

  function _renderInstallError(json) {
    // Returns the alert text to show.
    if (json.diagnosis) {
      var d = json.diagnosis;
      return (
        "⚠ " + d.summary + "\n\n" +
        "→ " + d.suggestion + "\n\n" +
        "(error excerpt: " + (d.raw_excerpt || '').slice(0, 200) + "…)"
      );
    }
    return "Install failed:\n" + (json.error || 'unknown') + "\n\n" + (json.log || '').slice(0, 500);
  }

  function _installFromCatalog(name) {
    // First check whether the catalog entry declares any native/system
    // dependencies and, if so, that they're satisfied in the workspace venv.
    // If anything is missing, show the consent modal instead of jumping
    // straight to the pip-install path (which would fail with a cryptic
    // dlopen error at first Run).
    fetch('/api/system-deps-check?name=' + encodeURIComponent(name))
      .then(function(r) { return r.json().then(function(j) { return [r.ok, j]; }); })
      .then(function(parts) {
        var rOk = parts[0], j = parts[1];
        if (!rOk || !j || !j.checks || !j.checks.length || j.ok) {
          // No checks declared, all green, or the check endpoint itself
          // errored — fall through to the existing install flow.
          return _proceedWithCatalogInstall(name);
        }
        _showSystemDepsModal(name, j);
      })
      .catch(function() {
        // Network/parse error: don't block the user — let the install try.
        _proceedWithCatalogInstall(name);
      });
  }
  window._installFromCatalog = _installFromCatalog;

  function _proceedWithCatalogInstall(name, opts) {
    if (!confirm("Install '" + name + "' on the active investigation branch?\n\nThis adds a submodule, pip installs the package, and appends it to pyproject.toml. Requires an active investigation branch.")) return;
    var body = {name: name};
    if (opts && opts.skip_system_deps_check) body.skip_system_deps_check = true;
    fetch('/api/catalog-install', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    })
      .then(function(r) { return r.json().then(function(j) { return [r.ok, r.status, j]; }); })
      .then(function(parts) {
        var ok = parts[0], status = parts[1], json = parts[2];
        if (!ok) {
          // 409 = system-deps gate (defence-in-depth — UI should have
          // already shown the modal; re-show if it happens anyway).
          if (status === 409 && json && json.missing) {
            _showSystemDepsModal(name, {
              name: name,
              platform: json.platform,
              ok: false,
              checks: json.missing.map(function(m) {
                return {
                  name: m.name, description: m.description,
                  ok: false, reason: m.reason,
                  install: m.install, notes: m.notes,
                };
              }),
            });
            return;
          }
          alert(_renderInstallError(json));
          return;
        }
        var msg = "Installed " + name + ".\nCommit: " + (json.commit || 'n/a');
        alert(msg);
        window._registryLoaded = false;  // force registry reload on next switch
        fetch('/api/render', {method: 'POST'}).finally(function() {
          location.reload();
        });
      })
      .catch(function(err) {
        alert("Network error: " + String(err));
      });
  }
  window._proceedWithCatalogInstall = _proceedWithCatalogInstall;

  // -------------------------------------------------------------------------
  // System dependencies modal
  // -------------------------------------------------------------------------

  function _closeSystemDepsModal() {
    var el = document.getElementById('modal-system-deps');
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }
  window._closeSystemDepsModal = _closeSystemDepsModal;

  function _showSystemDepsModal(name, depsResult) {
    _closeSystemDepsModal();
    var checks = (depsResult && depsResult.checks) || [];
    var missing = checks.filter(function(c) { return !c.ok; });
    var installableNames = missing
      .filter(function(c) { return c.install && (c.install.commands || []).length; })
      .map(function(c) { return c.name; });

    // Build per-check sections.
    var sections = missing.map(function(c) {
      var statusIcon = '<span style="color:#c00;font-weight:bold;">FAIL</span>';
      var header =
        '<div style="margin-top:10px;"><strong><code>' + _esc(c.name) + '</code></strong> ' +
        statusIcon + '</div>' +
        (c.description ? '<div class="muted" style="font-size:0.9em;margin:2px 0;">' + _esc(c.description) + '</div>' : '');
      var reason = c.reason
        ? '<div style="font-family:monospace;font-size:0.85em;background:#fef3c7;border-left:3px solid #fcd34d;padding:6px 8px;margin:4px 0;">' +
            _esc(c.reason) +
          '</div>'
        : '';
      var installBlock = '';
      if (c.install && (c.install.commands || []).length) {
        var cmds = c.install.commands.map(function(cmd) {
          return '<pre style="margin:2px 0;padding:6px 8px;background:#f3f4f6;border-radius:3px;font-size:0.85em;overflow-x:auto;">' +
            '$ ' + _esc(cmd) + '</pre>';
        }).join('');
        var mgr = c.install.manager ? ' (' + _esc(c.install.manager) + ')' : '';
        var notes = c.install.notes
          ? '<div class="muted" style="font-size:0.85em;margin-top:4px;">' + _esc(c.install.notes) + '</div>'
          : '';
        installBlock =
          '<div style="margin-top:4px;"><em>Install commands' + mgr + ':</em></div>' +
          cmds + notes;
      } else {
        var nots = c.notes
          ? '<div class="muted" style="font-size:0.85em;margin-top:4px;">' + _esc(c.notes) + '</div>'
          : '<div class="muted" style="font-size:0.85em;margin-top:4px;">No automated install path on this platform — manual intervention required.</div>';
        installBlock = nots;
      }
      return header + reason + installBlock;
    }).join('');

    var plat = _esc((depsResult && depsResult.platform) || '?');
    var installBtn = installableNames.length
      ? '<button type="button" class="action-btn" id="sysdeps-install-btn">Install all (' + installableNames.length + ')</button> '
      : '';

    var modal = document.createElement('div');
    modal.id = 'modal-system-deps';
    modal.className = 'modal-overlay';
    modal.style.display = 'flex';
    modal.innerHTML =
      '<div class="modal-box" style="max-width:680px;">' +
        '<button class="modal-close" onclick="_closeSystemDepsModal()">&times;</button>' +
        '<h3>System dependencies missing for <code>' + _esc(name) + '</code></h3>' +
        '<p class="muted" style="margin:4px 0;">' +
          'Platform: <code>' + plat + '</code>. ' +
          'These native libraries are required for the module to run but are not present in the workspace venv. ' +
          'Review the install commands below before continuing.' +
        '</p>' +
        '<div id="sysdeps-checks-body">' + sections + '</div>' +
        '<div id="sysdeps-error" class="form-error" style="color:#c00;min-height:1em;margin-top:8px;"></div>' +
        '<div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;">' +
          installBtn +
          '<button type="button" class="btn-mini" id="sysdeps-skip-btn">Skip checks &amp; install anyway</button>' +
          '<button type="button" class="btn-mini" onclick="_closeSystemDepsModal()">Cancel</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(modal);

    var installBtnEl = document.getElementById('sysdeps-install-btn');
    if (installBtnEl) {
      installBtnEl.addEventListener('click', function() {
        _installSystemDeps(name, installableNames);
      });
    }
    var skipBtnEl = document.getElementById('sysdeps-skip-btn');
    if (skipBtnEl) {
      skipBtnEl.addEventListener('click', function() {
        if (!confirm("Skip system-deps check and install '" + name + "' anyway?\n\nThis is unsafe — the install will likely succeed at the pip step but fail with a native-library error at first Run.")) return;
        _closeSystemDepsModal();
        _proceedWithCatalogInstall(name, {skip_system_deps_check: true});
      });
    }
  }
  window._showSystemDepsModal = _showSystemDepsModal;

  function _installSystemDeps(name, checkNames) {
    var errEl = document.getElementById('sysdeps-error');
    var btn = document.getElementById('sysdeps-install-btn');
    if (errEl) errEl.textContent = '';
    if (btn) { btn.disabled = true; btn.textContent = 'Installing…'; }
    fetch('/api/system-deps-install', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name, check_names: checkNames}),
    })
      .then(function(r) { return r.json().then(function(j) { return [r.ok, j]; }); })
      .then(function(parts) {
        var ok = parts[0], j = parts[1];
        if (btn) { btn.disabled = false; btn.textContent = 'Install all (' + checkNames.length + ')'; }
        if (!ok) {
          if (errEl) errEl.textContent = (j && j.error) || 'install failed';
          return;
        }
        // Show recheck status; if all green, proceed; otherwise keep modal up.
        var stillFailing = (j.recheck || []).filter(function(r) { return !r.ok; });
        if (stillFailing.length === 0) {
          _closeSystemDepsModal();
          _proceedWithCatalogInstall(name);
          return;
        }
        // Surface the remaining failures so the user can decide what to do.
        if (errEl) {
          errEl.textContent = 'After install attempts, still failing: ' +
            stillFailing.map(function(r) { return r.name + ' (' + (r.reason || '?') + ')'; }).join('; ');
        }
      })
      .catch(function(err) {
        if (btn) { btn.disabled = false; btn.textContent = 'Install all (' + checkNames.length + ')'; }
        if (errEl) errEl.textContent = 'Network error: ' + String(err);
      });
  }
  window._installSystemDeps = _installSystemDeps;

  // -------------------------------------------------------------------------
  // Catalog uninstall (v0.5.5)
  // -------------------------------------------------------------------------

  function _uninstallFromCatalog(name) {
    if (!confirm('Uninstall "' + name + '"? This removes the package from the workspace venv, pyproject.toml, and workspace.yaml imports.')) return;
    fetch('/api/catalog-uninstall', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name}),
    })
      .then(function(r) { return r.json().then(function(j) { return {ok: r.ok, json: j}; }); })
      .then(function(p) {
        if (!p.ok) { alert('Uninstall failed: ' + (p.json.error || 'unknown')); return; }
        var msg = p.json.already_uninstalled ? 'Already uninstalled.' : 'Uninstalled ' + name + '.';
        if (p.json.branch) msg += '\n\nBranch: ' + p.json.branch + (p.json.commit ? ' (' + p.json.commit + ')' : '');
        alert(msg);
        if (typeof _loadCatalog === 'function') _loadCatalog();
        if (typeof _loadRegistry === 'function') _loadRegistry(true);
      })
      .catch(function(e) { alert('Network error: ' + e); });
  }
  window._uninstallFromCatalog = _uninstallFromCatalog;

  // -------------------------------------------------------------------------
  // Simulation CRUD (v0.3.5)
  // -------------------------------------------------------------------------

  function _parseJSONorNull(s) {
    s = (s || '').trim();
    if (!s) return null;
    try { return JSON.parse(s); }
    catch (e) { throw new Error("Invalid JSON: " + e.message); }
  }

  function _submitSimulation(form) {
    try {
      var data = {
        name: form.sim_name.value.trim(),
        description: form.description.value.trim() || null,
        t_start: parseFloat(form.t_start.value),
        t_end: parseFloat(form.t_end.value),
        initial_state: _parseJSONorNull(form.initial_state.value),
        parameter_overrides: _parseJSONorNull(form.parameter_overrides.value),
        emitter_config: _parseJSONorNull(form.emitter_config.value),
        phases: Array.from(form.querySelectorAll('input[name=phases]:checked'))
                      .map(function(el) { return parseInt(el.value, 10); }),
      };
      submitForm(form, '/api/simulation', function() { return data; });
    } catch (e) {
      alert("Error: " + e.message);
    }
  }

  function _deleteSimulation(name) {
    if (!confirm("Remove simulation '" + name + "'?")) return;
    fetch('/api/simulation', {
      method: 'DELETE',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name}),
    })
      .then(function(r) { return r.json().then(function(j) { return [r.ok, j]; }); })
      .then(function(parts) {
        if (!parts[0]) { alert("Error: " + (parts[1].error || "unknown")); return; }
        fetch('/api/render', {method: 'POST'}).finally(function() { location.reload(); });
      });
  }

  window._submitSimulation = _submitSimulation;
  window._deleteSimulation = _deleteSimulation;
  window._parseJSONorNull = _parseJSONorNull;

  // -------------------------------------------------------------------------
  // Import install (v0.3.7-A)
  // -------------------------------------------------------------------------

  function _installImport(name) {
    if (!confirm("Pip install '" + name + "' into workspace venv?\nThis runs `.venv/bin/pip install -e <path>` and may take a minute.")) return;
    var btn = event.target;
    btn.disabled = true;
    btn.textContent = "Installing…";
    fetch('/api/import-install', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name}),
    })
      .then(function(r) { return r.json().then(function(j) { return [r.ok, j]; }); })
      .then(function(parts) {
        var ok = parts[0], json = parts[1];
        if (!ok) {
          alert(_renderInstallError(json));
          btn.disabled = false;
          btn.textContent = "Install";
          return;
        }
        alert("Installed.\nBranch: " + json.branch + "\n\nRegistry will refresh; new processes may appear after pip-cached subprocess restarts.");
        // Drop registry cache, switch to Registry tab so user sees the change.
        window._registryLoaded = false;
        fetch('/api/render', {method: 'POST'}).finally(function() {
          location.hash = '#registry';
          location.reload();
        });
      })
      .catch(function(err) { alert("Network error: " + err); btn.disabled = false; });
  }
  window._installImport = _installImport;

  function _toggleDirtyPanel() {
    var panel = document.getElementById('ws-dirty-panel');
    if (panel) { panel.remove(); return; }
    fetch('/api/dirty-status')
      .then(function(r){ return r.json(); })
      .then(_renderDirtyPanel)
      .catch(function(err){ console.warn('dirty-status failed:', err); });
  }
  window._toggleDirtyPanel = _toggleDirtyPanel;

  function _renderDirtyPanel(d) {
    var existing = document.getElementById('ws-dirty-panel');
    if (existing) existing.remove();
    if (!d || !d.files || d.files.length === 0) return;
    var anchor = document.getElementById('viv-content');
    if (!anchor) return;
    var div = document.createElement('div');
    div.id = 'ws-dirty-panel';
    div.style.cssText = 'background:#fef3c7;border:1px solid #fcd34d;border-radius:4px;padding:8px;margin:6px 0;font-size:0.85em';
    var rows = d.files.map(function(f){
      return '<div><code>' + _esc(f.status) + '</code> ' + _esc(f.path) + '</div>';
    }).join('');
    div.innerHTML =
      '<div style="margin-bottom:6px"><strong>' + d.count + ' uncommitted file' + (d.count === 1 ? '' : 's') + '</strong></div>' +
      rows +
      '<div style="margin-top:8px">' +
        '<button class="ws-btn ws-primary" onclick="_commitDirtyAll()">Commit all</button> ' +
        '<button class="ws-btn" onclick="_refreshGitStatus(); _toggleDirtyPanel()">Refresh</button> ' +
        '<button class="ws-btn" onclick="_toggleDirtyPanel()">Close</button>' +
      '</div>';
    anchor.insertAdjacentElement('beforebegin', div);
  }

  function _commitDirtyAll() {
    fetch('/api/dirty-commit-all', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: '{}',
    })
      .then(function(r){ return r.json().then(function(j){ return {ok: r.ok, body: j}; }); })
      .then(function(res){
        if (!res.ok) {
          alert(res.body.error || 'Commit failed');
          return;
        }
        if (typeof _showToast === 'function') _showToast('Committed: ' + res.body.message);
        _toggleDirtyPanel();
        _refreshGitStatus();
      })
      .catch(function(e){ alert('Network error: ' + e); });
  }
  window._commitDirtyAll = _commitDirtyAll;

  function _linkBranch() {
    openModal('modal-link-branch');
  }
  window._linkBranch = _linkBranch;

  function _submitLinkBranch(form) {
    var fd = new FormData(form);
    var body = {
      upstream_repo: (fd.get('upstream_repo') || '').trim(),
      branch_name:   (fd.get('branch_name')   || '').trim(),
      mode: fd.get('mode') || 'branch',
    };
    var submitBtn = form.querySelector('button[type=submit]');
    if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = 'Pushing…'; }
    fetch('/api/work-link-branch', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    }).then(function (r) { return r.json().then(function (j) { return [r.ok, j]; }); })
      .then(function (pair) {
        var ok = pair[0], j = pair[1];
        if (!ok) {
          alert('Push failed: ' + (j.error || 'unknown error'));
          return;
        }
        closeModal('modal-link-branch');
        var url = j.branch_url || '#';
        var msg;
        if (j.fork) {
          msg = 'Fork created at ' + j.fork + '; branch pushed to fork.\nBranch URL: ' + url;
        } else {
          msg = 'Branch pushed: ' + j.branch + ' → ' + j.upstream_repo;
          msg += '\n\nOpen in browser: ' + url;
        }
        alert(msg);
        // Refresh workstream state UI if there is one.
        if (typeof _refreshWorkstreamState === 'function') _refreshWorkstreamState();
      })
      .catch(function (e) { alert('Push failed: ' + e.message); })
      .finally(function () {
        if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = 'Push branch'; }
      });
  }
  window._submitLinkBranch = _submitLinkBranch;

  function _startWork() {
    var name = prompt("Investigation branch name (suggested: investigation/<short-slug>):", "investigation/");
    if (!name) return;
    fetch('/api/work-start', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({branch: name.trim()}),
    })
      .then(function(r){ return r.json().then(function(j){ return [r.ok, j]; }); })
      .then(function(parts){
        if (!parts[0]) { alert("Could not start investigation branch:\n" + (parts[1].error || 'unknown')); return; }
        _refreshGitStatus();
        location.reload();
      });
  }
  window._startWork = _startWork;

  function _pushWork() {
    fetch('/api/work-push', {method: 'POST'})
      .then(function(r){ return r.json().then(function(j){ return [r.ok, j]; }); })
      .then(function(parts){
        var ok = parts[0], json = parts[1];
        if (!ok) {
          var msg = "Push failed:\n" + (json.error || 'unknown');
          if (json.diagnosis) {
            msg = "⚠ " + json.diagnosis.summary + "\n→ " + json.diagnosis.suggestion;
          }
          alert(msg);
          _refreshGitStatus();
          return;
        }
        alert("Pushed.");
        _refreshGitStatus();
      });
  }
  window._pushWork = _pushWork;

  function _createPR() {
    openModal('modal-create-pr');
  }
  window._createPR = _createPR;

  function _submitCreatePR(form) {
    var data = {
      title: form.title.value.trim(),
      body: form.body.value.trim() || null,
    };
    var errEl = form.querySelector('.form-error');
    errEl.textContent = '';
    fetch('/api/work-create-pr', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(data),
    })
      .then(function(r){ return r.json().then(function(j){ return [r.ok, j]; }); })
      .then(function(parts){
        var ok = parts[0], json = parts[1];
        if (!ok) {
          var msg = json.error || 'unknown';
          if (json.manual_url) msg += "\n\nOpen manually: " + json.manual_url;
          errEl.textContent = msg;
          return;
        }
        closeModal('modal-create-pr');
        window.open(json.pr_url, '_blank');
        _refreshGitStatus();
      });
  }
  window._submitCreatePR = _submitCreatePR;

  // Generic Suggest button: writes a request, polls for response, fills the input.
  function _suggestInto(btn, kind, fieldName) {
    var form = btn.closest('form');
    var input = form.elements[fieldName];
    btn.disabled = true;
    btn.textContent = "…";
    fetch('/api/suggest', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({kind: kind}),
    })
      .then(function(r){ return r.json().then(function(j){ return [r.ok, j]; }); })
      .then(function(parts){
        var ok = parts[0], json = parts[1];
        if (!ok) { alert("Suggest request failed: " + (json.error || 'unknown')); btn.disabled = false; btn.textContent = "Suggest"; return; }
        var msg = json.instructions + "\n\nClick OK to start polling.";
        if (!confirm(msg)) { btn.disabled = false; btn.textContent = "Suggest"; return; }
        _pollSuggestion(json.id, input, btn, 0);
      });
  }
  window._suggestInto = _suggestInto;

  function _pollSuggestion(id, input, btn, attempts) {
    if (attempts > 90) {  // ~3 minutes
      btn.disabled = false; btn.textContent = "Suggest";
      alert("Timed out waiting for /pbg-suggest. Click Suggest again to retry.");
      return;
    }
    btn.textContent = "polling (" + attempts + ")";
    fetch('/api/suggest-poll?id=' + encodeURIComponent(id))
      .then(function(r){ return r.json(); })
      .then(function(json){
        if (json.ready) {
          input.value = json.suggestion;
          if (json.rationale) input.title = json.rationale;
          btn.disabled = false; btn.textContent = "Suggest";
          return;
        }
        setTimeout(function(){ _pollSuggestion(id, input, btn, attempts + 1); }, 2000);
      })
      .catch(function(){
        btn.disabled = false; btn.textContent = "Suggest";
      });
  }

  function _endWork() {
    if (!confirm("End this investigation branch? Switches you back to base; the branch is preserved.")) return;
    fetch('/api/work-end', {method: 'POST'})
      .then(function(r){ return r.json().then(function(j){ return [r.ok, j]; }); })
      .then(function(parts){
        if (!parts[0]) { alert("Could not end investigation branch:\n" + (parts[1].error || 'unknown')); return; }
        location.reload();
      });
  }
  window._endWork = _endWork;

  // -------------------------------------------------------------------------
  // Run tests
  // -------------------------------------------------------------------------

  function runTests(model) {
    var btn = document.getElementById("run-tests-btn");
    var out = document.getElementById("run-tests-output");
    var spinner = document.getElementById("run-tests-spinner");
    if (btn) btn.disabled = true;
    if (spinner) spinner.style.display = "inline";
    if (out) out.textContent = "Running…";

    fetch("/api/run-tests", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: model }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (btn) btn.disabled = false;
        if (spinner) spinner.style.display = "none";
        if (data.error) {
          if (out) out.textContent = "Error: " + data.error;
          return;
        }
        var text = (data.stdout || "") + (data.stderr ? "\n--- stderr ---\n" + data.stderr : "");
        var rc = data.returncode;
        if (out) {
          out.textContent = text || "(no output)";
          out.style.background = rc === 0 ? "#f0fff0" : "#fff0f0";
          out.style.borderColor = rc === 0 ? "#4caf50" : "#f44336";
        }
      })
      .catch(function (err) {
        if (btn) btn.disabled = false;
        if (spinner) spinner.style.display = "none";
        if (out) out.textContent = "Network error: " + String(err);
      });
  }

  // -------------------------------------------------------------------------
  // Drop-zone helper (v0.1.9)
  // -------------------------------------------------------------------------

  /**
   * setupDropZone(zoneId, storeKey)
   *
   * Attaches drag-drop behaviour to the element with id=zoneId.
   * On drop:
   *   1. Reads the first file as a DataURL.
   *   2. Strips the data:*;base64, prefix to get pure base64.
   *   3. Computes a browser-side sha256 (transparency only; server recomputes).
   *   4. Updates the drop zone with filename + size + hash.
   *   5. Stores {file_b64, filename} in _dropZoneStore[storeKey].
   */
  var _dropZoneStore = {};

  function setupDropZone(zoneId, storeKey) {
    var zone = document.getElementById(zoneId);
    if (!zone) return;

    function prevent(e) { e.preventDefault(); e.stopPropagation(); }

    zone.addEventListener("dragenter", function(e) { prevent(e); zone.classList.add("drag-over"); });
    zone.addEventListener("dragover",  function(e) { prevent(e); zone.classList.add("drag-over"); });
    zone.addEventListener("dragleave", function(e) { prevent(e); zone.classList.remove("drag-over"); });
    zone.addEventListener("drop", function(e) {
      prevent(e);
      zone.classList.remove("drag-over");
      var file = e.dataTransfer.files[0];
      if (!file) return;
      _readFile(file, zone, storeKey);
    });

    // Also allow click-to-select (creates a hidden file input).
    zone.addEventListener("click", function() {
      var inp = document.createElement("input");
      inp.type = "file";
      inp.style.display = "none";
      inp.onchange = function() {
        if (inp.files && inp.files[0]) {
          _readFile(inp.files[0], zone, storeKey);
        }
      };
      document.body.appendChild(inp);
      inp.click();
      setTimeout(function() { document.body.removeChild(inp); }, 30000);
    });
  }

  function _readFile(file, zone, storeKey) {
    var reader = new FileReader();
    reader.onload = function(ev) {
      var dataUrl = ev.target.result;
      // Strip "data:<mime>;base64," prefix.
      var comma = dataUrl.indexOf(",");
      var b64 = comma >= 0 ? dataUrl.slice(comma + 1) : dataUrl;

      // Browser-side sha256 for transparency.
      var rawBytes = _b64ToUint8Array(b64);
      crypto.subtle.digest("SHA-256", rawBytes).then(function(hashBuf) {
        var hashArr = Array.from(new Uint8Array(hashBuf));
        var hashHex = hashArr.map(function(b) { return b.toString(16).padStart(2, "0"); }).join("");

        _dropZoneStore[storeKey] = { file_b64: b64, filename: file.name };

        var sizeKb = (file.size / 1024).toFixed(1);
        var infoEl = zone.querySelector(".file-info");
        var hashEl = zone.querySelector(".file-hash");
        if (infoEl) infoEl.textContent = file.name + " (" + sizeKb + " KB)";
        if (hashEl) hashEl.textContent = "sha256: " + hashHex;
        zone.style.borderColor = "#3a8";
        zone.querySelector && (zone.querySelectorAll(".drop-hint").forEach(function(h) { h.style.display = "none"; }));
      });
    };
    reader.readAsDataURL(file);
  }

  function _b64ToUint8Array(b64) {
    var binary = atob(b64);
    var bytes = new Uint8Array(binary.length);
    for (var i = 0; i < binary.length; i++) {
      bytes[i] = binary.charCodeAt(i);
    }
    return bytes;
  }

  // -------------------------------------------------------------------------
  // Expose globals
  // -------------------------------------------------------------------------

  window.openModal = openModal;
  window.closeModal = closeModal;
  window.submitForm = submitForm;
  window.runTests = runTests;
  window.setupDropZone = setupDropZone;
  window._dropZoneStore = _dropZoneStore;

  document.addEventListener("DOMContentLoaded", function () {
    // Snapshot read-only mode: set body.snapshot so CSS hides authoring controls.
    var _dashCfg = window.__DASH_CONFIG__ || {};
    if (_dashCfg.mode === "snapshot") {
      document.body.classList.add("snapshot");
      // The snapshot banner link is a static href to the vivarium-dashboard
      // GitHub repo (set in the template); there is no hosted interactive
      // version, so nothing to wire here.
      // Show repo-name label from config (Task 5).
      var repoLabel = document.getElementById('snapshot-repo-label');
      if (repoLabel && _dashCfg.repo) {
        repoLabel.textContent = _dashCfg.repo.replace(/^.*\/([^/]+?)(?:\.git)?$/, '$1');
      }
    }

    // Initialize menu navigation.
    _initMenuNav();

    // Restore Vivarium left-rail collapsed state (V4).
    _vivRestoreRailState();

    // _refreshGitStatus is registered on DOMContentLoaded at the bottom of this file;
    // no duplicate call needed here.

    // Populate the Investigations rail section (V4).
    _vivRefreshInvestigationsRail();

    // (The GitHub Branches tab has been removed.)
  });

  // -------------------------------------------------------------------------
  // Vivarium left rail — collapse toggle (V4)
  // -------------------------------------------------------------------------

  function _vivToggleRail() {
    var rail = document.getElementById('viv-rail');
    if (!rail) return;
    var collapsed = rail.classList.toggle('viv-rail-collapsed');
    try { localStorage.setItem('vivarium.rail-collapsed', collapsed ? '1' : '0'); } catch (e) {}
  }
  window._vivToggleRail = _vivToggleRail;

  function _vivRestoreRailState() {
    var stored = null;
    try { stored = localStorage.getItem('vivarium.rail-collapsed'); } catch (e) {}
    if (stored === '1') {
      var rail = document.getElementById('viv-rail');
      if (rail) rail.classList.add('viv-rail-collapsed');
    }
  }
  window._vivRestoreRailState = _vivRestoreRailState;

  // -------------------------------------------------------------------------
  // Vivarium left rail — Investigations grouping (V4)
  // -------------------------------------------------------------------------

  function _vivRefreshInvestigationsRail() {
    var host = document.getElementById('viv-rail-investigations');
    if (!host) return;
    // New flow: fetch both isets (groups) and studies (members), then render
    // the grouped/collapsible view via _renderRailInvestigationGroups. The
    // legacy fallback _vivRenderInvestigationsRail() is kept below for
    // workspaces with no investigation.yaml files.
    var hasIsetUI = (typeof _renderRailInvestigationGroups === 'function')
                 && document.getElementById('investigations-list');
    var p1 = (window.DataSource
      ? window.DataSource.loadInvestigationsFlat()
      : fetch('/api/investigations').then(function(r) { return r.json(); })
    ).catch(function() { return {investigations: []}; });
    var p2 = hasIsetUI
      ? fetch('/api/iset-list').then(function(r) { return r.json(); }).catch(function() { return {investigations: []}; })
      : Promise.resolve({investigations: []});
    Promise.all([p1, p2]).then(function(arr) {
      window._investigations = arr[0].investigations || [];
      window._isetIndex      = arr[1].investigations || [];
      if (hasIsetUI && window._isetIndex.length) {
        _renderRailInvestigationGroups();
      } else {
        _vivRenderInvestigationsRail(window._investigations);
      }
    });
  }
  window._vivRefreshInvestigationsRail = _vivRefreshInvestigationsRail;

  function _vivRenderInvestigationsRail(investigations) {
    var host = document.getElementById('viv-rail-investigations');
    if (!host) return;
    if (!investigations.length) {
      host.innerHTML =
        '<p class="viv-rail-empty" style="font-size:0.85em;color:#9ca3af;padding:4px 12px">' +
        'No studies yet' +
        '</p>';
      return;
    }
    // Focus mode: a specific study is open. Replace the grouped sub-list with
    // a single highlighted entry + a "back to index" affordance so the rail
    // visibly tracks the index/detail split.
    var active = window._currentInvestigation || '';
    if (active) {
      var match = null;
      for (var i = 0; i < investigations.length; i++) {
        if (investigations[i] && investigations[i].name === active) {
          match = investigations[i];
          break;
        }
      }
      if (!match) {
        host.innerHTML =
          '<p class="viv-rail-empty" style="font-size:0.85em;color:#9ca3af;padding:4px 12px">' +
          'Loading study…' +
          '</p>';
        return;
      }
      var topic = (match.topic && match.topic.trim()) ? match.topic.trim() : 'Ungrouped';
      host.innerHTML =
        '<div class="viv-rail-focused-study">' +
          '<a href="#" class="viv-rail-link viv-rail-study-link active" ' +
             'onclick="return false;">' +
            '<span class="viv-rail-link-icon viv-rail-study-icon">●</span>' +
            '<span class="viv-rail-link-label">' + _esc(match.name) + '</span>' +
          '</a>' +
          '<small class="viv-rail-focused-hint">in <em>' + _esc(topic) + '</em></small>' +
          '<a href="#" class="viv-rail-link viv-rail-back-link" ' +
             'onclick="_closeInvestigationFocus(); return false;">' +
            '<span class="viv-rail-link-label">← All investigations</span>' +
          '</a>' +
        '</div>';
      return;
    }
    // Group by topic. Investigations with empty/missing topic go to "Ungrouped".
    var groups = {};
    var order = [];
    investigations.forEach(function(inv) {
      var topic = (typeof inv.topic === 'string' && inv.topic.trim()) ? inv.topic.trim() : '';
      var key = topic || '__ungrouped__';
      if (!groups[key]) {
        groups[key] = { topic: topic, items: [] };
        order.push(key);
      }
      groups[key].items.push(inv);
    });
    // Sort named topics alphabetically, push Ungrouped last.
    order.sort(function(a, b) {
      if (a === '__ungrouped__') return 1;
      if (b === '__ungrouped__') return -1;
      return groups[a].topic.localeCompare(groups[b].topic);
    });
    var active = window._currentInvestigation || '';
    var html = order.map(function(key) {
      var g = groups[key];
      var label = g.topic ? g.topic : 'Ungrouped';
      var items = g.items.map(function(inv) {
        var baseline = inv.baseline ? inv.baseline : (inv.composite || '—');
        var nRuns = (inv.n_runs !== undefined) ? inv.n_runs
                  : (inv.n_simulations !== undefined ? inv.n_simulations : 0);
        var isActive = (inv.name === active) ? ' active' : '';
        return '<a class="viv-rail-link viv-rail-study-link' + isActive + '" ' +
               'href="#studies" ' +
               'onclick="_vivOpenInvestigationFromRail(\'' + _esc(inv.name) + '\'); return false;">' +
                 '<span class="viv-rail-link-label">' + _esc(inv.name) + '</span>' +
                 '<small class="viv-rail-link-sublabel">' + _esc(baseline) +
                   ' · ' + nRuns + ' run' + (nRuns === 1 ? '' : 's') +
                 '</small>' +
               '</a>';
      }).join('');
      return '<div class="viv-rail-investigations-group" data-topic="' + _esc(label) + '">' +
               '<div class="viv-rail-investigations-group-header" onclick="_vivToggleInvGroup(this)">' +
                 '<span class="viv-rail-investigations-group-arrow viv-arrow">▾</span>' +
                 '<span class="viv-rail-investigations-group-name viv-investigations-topic-name">' +
                   _esc(label) +
                 '</span>' +
                 '<span class="viv-rail-investigations-group-count viv-investigations-count">' +
                   g.items.length +
                 '</span>' +
               '</div>' +
               '<div class="viv-rail-investigations-group-items">' + items + '</div>' +
             '</div>';
    }).join('');
    host.innerHTML = html;
  }

  function _vivToggleInvGroup(headerEl) {
    if (!headerEl) return;
    var group = headerEl.closest ? headerEl.closest('.viv-rail-investigations-group')
                                 : headerEl.parentNode;
    if (group) group.classList.toggle('collapsed');
  }
  window._vivToggleInvGroup = _vivToggleInvGroup;

  function _vivOpenInvestigationFromRail(name) {
    // Switch to Studies page first, then open the detail panel and
    // refresh the rail so the active-state moves with the selection.
    if (typeof _switchPage === 'function') _switchPage('studies');
    if (typeof _openInvestigation === 'function') _openInvestigation(name);
    _vivRefreshInvestigationsRail();
  }
  window._vivOpenInvestigationFromRail = _vivOpenInvestigationFromRail;

  // -------------------------------------------------------------------------
  // Internal helpers
  // -------------------------------------------------------------------------

  function _esc(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // -------------------------------------------------------------------------
  // Visualization lifecycle (v0.4.2)
  // -------------------------------------------------------------------------

  function _vizRefreshStatus(name) {
    fetch('/api/visualization-status?name=' + encodeURIComponent(name))
      .then(function(r) { return r.json(); })
      .then(function(s) {
        var el = document.getElementById('viz-status-' + name);
        if (!el) return;
        el.textContent = s.status;
        el.className = 'status-pill viz-status-' + s.status;
      });
  }
  function _vizRefreshAll() {
    document.querySelectorAll('[id^="viz-status-"]').forEach(function(el) {
      var name = el.id.substring('viz-status-'.length);
      _vizRefreshStatus(name);
    });
  }
  window._vizRefreshAll = _vizRefreshAll;

  function _vizCreate(name) {
    fetch('/api/visualization-create', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name}),
    })
      .then(function(r) { return r.json().then(function(j) { return [r.ok, j]; }); })
      .then(function(pair) {
        var ok = pair[0], json = pair[1];
        if (!ok) { alert('Create failed: ' + (json.error || 'unknown')); return; }
        var msg =
          'Request written to ' + json.request_path + '\n\n' +
          json.instructions + '\n\n' +
          "Click 'Refresh status' below when the skill finishes.";
        alert(msg);
        _vizPollUntilCreated(name, 0);
      });
  }
  window._vizCreate = _vizCreate;

  function _vizPollUntilCreated(name, attempts) {
    if (attempts > 60) return;  // ~2 minutes
    fetch('/api/visualization-status?name=' + encodeURIComponent(name))
      .then(function(r) { return r.json(); })
      .then(function(s) {
        _vizRefreshStatus(name);
        if (s.has_response) return;  // Done
        setTimeout(function() { _vizPollUntilCreated(name, attempts + 1); }, 2000);
      });
  }

  function _vizAddToProject(name) {
    fetch('/api/visualization-add-to-project', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name}),
    })
      .then(function(r) { return r.json().then(function(j) { return [r.ok, j]; }); })
      .then(function(pair) {
        var ok = pair[0], json = pair[1];
        if (!ok) { alert('Add to project failed: ' + (json.error || 'unknown')); return; }
        _vizRefreshStatus(name);
      });
  }
  window._vizAddToProject = _vizAddToProject;

  function _vizCommit(names) {
    if (!confirm('Commit ' + names.length + ' visualization(s) to the active branch?')) return;
    fetch('/api/visualization-commit-batch', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({names: names}),
    })
      .then(function(r) { return r.json().then(function(j) { return [r.ok, j]; }); })
      .then(function(pair) {
        var ok = pair[0], json = pair[1];
        if (!ok) { alert('Commit failed: ' + (json.error || 'unknown')); return; }
        alert('Committed: ' + (json.committed || []).join(', '));
        fetch('/api/render', {method: 'POST'}).finally(function() { location.reload(); });
      });
  }
  window._vizCommit = _vizCommit;

  function _vizCommitAll() {
    fetch('/api/visualization-commit-batch', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({}),
    })
      .then(function(r) { return r.json().then(function(j) { return [r.ok, j]; }); })
      .then(function(pair) {
        var ok = pair[0], json = pair[1];
        if (!ok) { alert('Commit-all failed: ' + (json.error || 'unknown')); return; }
        alert('Committed: ' + (json.committed || []).join(', '));
        fetch('/api/render', {method: 'POST'}).finally(function() { location.reload(); });
      });
  }
  window._vizCommitAll = _vizCommitAll;

  function _renderVizPreviewInModal(title, html, sourceUsed, notes) {
    var titleEl = document.getElementById('viz-preview-title');
    var srcEl = document.getElementById('viz-preview-source-row');
    var notesEl = document.getElementById('viz-preview-notes');
    var iframe = document.getElementById('viz-preview-iframe');
    if (titleEl) titleEl.textContent = 'Preview: ' + title;
    if (srcEl) srcEl.textContent = 'Source: ' + (sourceUsed || 'demo');
    if (notesEl) notesEl.textContent = notes || '';
    if (iframe) iframe.srcdoc = '<!DOCTYPE html><html><body style="margin:0;padding:8px">' + (html || '<p>(empty)</p>') + '</body></html>';
    openModal('modal-viz-preview');
  }

  function _vizPreview(name) {
    // Preview a registered workspace.yaml instance by name. The server
    // looks up its class+config and renders against demo data (or a real
    // investigation if source is set later via the modal).
    fetch('/api/visualization-preview-instance', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name, source: 'demo'}),
    }).then(function(r) { return r.json().then(function(j) { return [r.ok, j]; }); })
      .then(function(parts) {
        var ok = parts[0], j = parts[1];
        if (!ok) {
          alert(j.error || 'Preview failed');
          return;
        }
        _renderVizPreviewInModal(name, j.html, j.source_used, j.notes);
      });
  }
  window._vizPreview = _vizPreview;

  function _vizClassPreview(address, className) {
    // Preview a raw Visualization class (no config) against demo data.
    fetch('/api/visualization-preview', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({address: address, source: 'demo'}),
    }).then(function(r) { return r.json().then(function(j) { return [r.ok, j]; }); })
      .then(function(parts) {
        var ok = parts[0], j = parts[1];
        if (!ok) {
          alert(j.error || 'Preview failed');
          return;
        }
        _renderVizPreviewInModal(className + ' (demo)', j.html, j.source_used, j.notes);
      });
  }
  window._vizClassPreview = _vizClassPreview;

  function _vizRemove(name) {
    if (!confirm("Remove visualization '" + name + "'?")) return;
    fetch('/api/visualization', {
      method: 'DELETE',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name}),
    })
      .then(function(r) { return r.json().then(function(j) { return [r.ok, j]; }); })
      .then(function(pair) {
        var ok = pair[0], json = pair[1];
        if (!ok) { alert('Remove failed: ' + (json.error || 'unknown')); return; }
        fetch('/api/render', {method: 'POST'}).finally(function() { location.reload(); });
      });
  }
  window._vizRemove = _vizRemove;

  // Auto-refresh viz statuses on page load
  window.addEventListener('DOMContentLoaded', function() { setTimeout(_vizRefreshAll, 200); });

  // ---------------------------------------------------------------------------
  // Composite explorer (v0.5.1)
  // ---------------------------------------------------------------------------

  window._ceCurrent = null;  // current composite + overrides state

  function _openCompositeExplorer(id) {
    // Navigate to the explorer as a normal tab (menu stays visible — user can
    // click another menu item to leave). The id lives in ?id= so deep-linking
    // / reload works; the hash drives which page is shown.
    var url = new URL(window.location.href);
    url.searchParams.set('id', id);
    url.hash = '#composite-explore';
    window.history.pushState({}, '', url.toString());
    _switchPage('composite-explore');
  }
  window._openCompositeExplorer = _openCompositeExplorer;

  function _initCompositeExplorer() {
    // Called when the explorer page is activated. Parses ?id=<spec_id> from
    // the URL, fetches the resolved composite, populates the page. Also
    // parses ?run_id=<run_id> — when present, loads that run's results and
    // viz into the Run tab (a Simulations-row deep link or a refresh of a
    // URL captured after kicking off a run).
    var params = new URLSearchParams(window.location.search);
    var id = params.get('id');
    var run_id = params.get('run_id');
    if (!id) {
      document.getElementById('ce-loading').textContent =
        'No composite id specified. Open via the Use button on a composite card.';
      return;
    }
    window._ceCurrent = {id: id, overrides: {}, run_id: run_id || null};
    window._ceLastRunId = run_id || null;
    // Hide the post-run bar when loading a fresh composite (it's set by the
    // explore:run-complete postMessage path).
    var bar = document.getElementById('ce-post-run-bar');
    if (bar) bar.style.display = 'none';
    // Eagerly populate the composite card cache so "Create simulation" can
    // open the Configure modal even when the user lands here directly
    // (deep-link / Use button) without ever visiting Simulation Setup.
    if (!window._compositesById || !window._compositesById[id]) {
      _loadComposites();
    }
    _ceFetch();
    if (run_id) {
      // Run tab loads in parallel with _ceFetch's wiring fetch; no need to
      // await, the two writes target different DOM containers.
      _ceLoadRunFromId(run_id);
    }
  }
  window._initCompositeExplorer = _initCompositeExplorer;

  function _beginStudyFromComposite() {
    var id = window._ceCurrent && window._ceCurrent.id;
    if (!id) { alert('No composite loaded.'); return; }
    // id is the dotted ref (pkg.composites.name); the endpoint accepts the bare composite name.
    // Take the last segment after the final '.' as the composite_name.
    var name = id.indexOf('.') >= 0 ? id.split('.').pop() : id;
    var btn = document.getElementById('ce-begin-study-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Starting study…'; }
    fetch('/api/investigation-create-from-composite', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({composite_name: name}),
    })
      .then(function(r) { return r.json().then(function(j) { return {ok: r.ok, body: j}; }); })
      .then(function(res) {
        if (!res.ok) {
          alert(res.body.error || ('Begin Study failed (' + JSON.stringify(res.body) + ')'));
          if (btn) { btn.disabled = false; btn.textContent = 'Begin Study'; }
          return;
        }
        // Navigate to the new investigation's detail view.
        var newName = res.body.name;
        var url = new URL(window.location.href);
        url.searchParams.delete('id');
        url.hash = '#studies';
        window.history.pushState({}, '', url.toString());
        window._currentInvestigation = newName;
        _switchPage('studies');
        // Open the detail pane. Prefer the existing helper if available.
        if (typeof _openInvestigation === 'function') {
          _openInvestigation(newName);
        } else {
          fetch('/api/investigation/' + encodeURIComponent(newName))
            .then(function(r) { return r.json(); })
            .then(function(data) {
              if (typeof _renderInvestigationDetail === 'function') {
                _renderInvestigationDetail(newName, data);
              }
            });
        }
      })
      .catch(function(e) {
        alert('Network error: ' + e);
        if (btn) { btn.disabled = false; btn.textContent = 'Begin Study'; }
      });
  }
  window._beginStudyFromComposite = _beginStudyFromComposite;

  function _ceSwitchTab(tab) {
    document.querySelectorAll('.ce-tab').forEach(function(b) {
      b.classList.toggle('active', b.dataset.tab === tab);
    });
    document.querySelectorAll('.ce-tab-panel').forEach(function(p) {
      p.classList.toggle('active', p.dataset.tab === tab);
    });
    // Lazy-load Results tab content (History/Compare/State now folded into Results)
    if (tab === 'results') {
      if (!window._ceHistoryLoaded) {
        window._ceHistoryLoaded = true;
        if (typeof _ceLoadHistory === 'function') _ceLoadHistory();
      }
      if (window._ceCompareSet && window._ceCompareSet.size >= 2) {
        if (typeof _ceRenderCompare === 'function') _ceRenderCompare();
      }
    }
  }
  window._ceSwitchTab = _ceSwitchTab;

  function _ceOpenPopout() {
    if (!window._ceCurrent || !window._ceCurrent.id) return;
    var url = location.pathname + '?focus=composite-explore&id=' +
              encodeURIComponent(window._ceCurrent.id);
    var w = window.open(url, '_blank', 'width=1200,height=900');
    if (!w) {
      // Popup blocked — same-tab fallback
      window.location.search = '?focus=composite-explore&id=' +
                                encodeURIComponent(window._ceCurrent.id);
    }
  }
  window._ceOpenPopout = _ceOpenPopout;

  // ─── History tab ──────────────────────────────────────────────────────
  window._ceRuns = {};            // run_id → run dict (cache)
  window._ceCompareSet = new Set();// selected run_ids for Compare

  function _ceLoadHistory() {
    if (window._ceHistoryFetching) return;
    window._ceHistoryFetching = true;
    var id = window._ceCurrent.id;
    fetch('/api/composite-runs?spec_id=' + encodeURIComponent(id))
      .then(function(r) { return r.json(); })
      .then(function(data) {
        var runs = data.runs || [];
        var body = document.getElementById('ce-history-body');
        var countBadge = document.getElementById('ce-history-count');
        if (countBadge) countBadge.textContent = runs.length ? '(' + runs.length + ')' : '';
        var resultsCount = document.getElementById('ce-results-count');
        if (resultsCount) resultsCount.textContent = runs.length ? '(' + runs.length + ')' : '';
        if (!runs.length) {
          body.innerHTML = '<p class="empty-state">No runs yet — click <em>Run</em> on the View tab.</p>';
          window._ceHistoryFetching = false;
          return;
        }
        runs.forEach(function(r) { window._ceRuns[r.run_id] = r; });
        var rows = runs.map(_ceRenderHistoryRow).join('');
        body.innerHTML =
          '<table><thead><tr>' +
            '<th style="width:30px"></th><th>Label</th><th>Params</th>' +
            '<th>Started</th><th>Steps</th><th>Status</th><th></th>' +
          '</tr></thead><tbody>' + rows + '</tbody></table>';
        window._ceHistoryFetching = false;
      })
      .catch(function(err) {
        var body = document.getElementById('ce-history-body');
        if (body) body.innerHTML = '<p style="color:#c00">Failed to load history: ' + _esc(String(err)) + '</p>';
        window._ceHistoryLoaded = false;
        window._ceHistoryFetching = false;
      });
  }
  window._ceLoadHistory = _ceLoadHistory;

  function _ceRenderHistoryRow(run) {
    var checked = window._ceCompareSet.has(run.run_id) ? 'checked' : '';
    var statusClass = ({completed: 'completed', running: 'running', failed: 'failed'})[run.status] || 'unknown';
    var paramStr = Object.keys(run.params || {})
      .map(function(k) { return k + '=' + run.params[k]; }).join(', ') || '—';
    var startedStr = new Date(run.started_at * 1000).toLocaleString();
    return '<tr>' +
      '<td><input type="checkbox" ' + checked +
        ' onchange="_ceToggleCompareSelection(\'' + _esc(run.run_id) + '\', this.checked)"></td>' +
      '<td>' + _esc(run.label || '') + '</td>' +
      '<td><code>' + _esc(paramStr) + '</code></td>' +
      '<td>' + _esc(startedStr) + '</td>' +
      '<td>' + (run.n_steps || 0) + '</td>' +
      '<td><span class="ce-history-status ' + statusClass + '">' + _esc(run.status) + '</span></td>' +
      '<td><button class="btn-mini" onclick="_ceViewRun(\'' + _esc(run.run_id) + '\')">View</button></td>' +
    '</tr>';
  }

  function _ceViewRun(run_id) {
    window._ceSelectedRunId = run_id;
    _ceSwitchTab('results');
    var statePanel = document.getElementById('ce-state-panel');
    if (statePanel) statePanel.style.display = '';
    if (typeof _ceLoadState === 'function') _ceLoadState(run_id, 0);
  }
  window._ceViewRun = _ceViewRun;

  function _ceToggleCompareSelection(run_id, checked) {
    if (checked) window._ceCompareSet.add(run_id);
    else window._ceCompareSet.delete(run_id);
    var count = window._ceCompareSet.size;
    var comparePanel = document.getElementById('ce-compare-panel');
    if (comparePanel) comparePanel.style.display = count >= 2 ? '' : 'none';
    if (count >= 2 && typeof _ceRenderCompare === 'function') _ceRenderCompare();
  }
  window._ceToggleCompareSelection = _ceToggleCompareSelection;

  function _ceClearCompareSelection() {
    window._ceCompareSet.clear();
    document.querySelectorAll('input[type="checkbox"][onchange*="_ceToggleCompareSelection"]')
      .forEach(function(cb) { cb.checked = false; });
    _ceToggleCompareSelection('', false);  // refresh badge + tab visibility
  }
  window._ceClearCompareSelection = _ceClearCompareSelection;

  // ─── Compare tab ──────────────────────────────────────────────────────
  var _CE_COMPARE_PALETTE = ['#6366f1', '#10b981', '#f43f5e', '#f59e0b',
                              '#8b5cf6', '#06b6d4', '#84cc16', '#ec4899'];

  function _ceRenderCompare() {
    var ids = Array.from(window._ceCompareSet);
    if (ids.length < 2) return;
    var body = document.getElementById('ce-compare-body');
    body.innerHTML = '<p class="empty-state">Loading&hellip;</p>';
    Promise.all(ids.map(function(id) {
      return fetch('/api/composite-run/' + encodeURIComponent(id))
        .then(function(r) { return r.json(); });
    })).then(function(results) {
      var runs = ids.map(function(id, i) {
        return { run_id: id, meta: window._ceRuns[id] || {},
                  trajectory: results[i].trajectory || [],
                  color: _CE_COMPARE_PALETTE[i % _CE_COMPARE_PALETTE.length] };
      });

      // Find observable keys (numeric leaves) across all trajectories
      var observables = {};
      runs.forEach(function(run) {
        run.trajectory.forEach(function(point) {
          Object.keys(point.state || {}).forEach(function(k) {
            var v = point.state[k];
            if (typeof v === 'number') observables[k] = true;
          });
        });
      });
      var obsList = Object.keys(observables);

      // Legend
      var legend = '<div class="ce-compare-legend">' + runs.map(function(run) {
        return '<span><span class="swatch" style="background:' + run.color + '"></span>' +
                _esc(run.meta.label || run.run_id.slice(-12)) + '</span>';
      }).join('') + '</div>';

      // One chart div per observable
      var chartContainers = obsList.map(function(k) {
        return '<div id="ce-cmp-' + _esc(k) + '" style="height:280px;margin-bottom:12px"></div>';
      }).join('');

      // Param diff table
      var allKeys = new Set();
      runs.forEach(function(run) {
        Object.keys(run.meta.params || {}).forEach(function(k) { allKeys.add(k); });
      });
      var paramKeys = Array.from(allKeys);
      var diffHead = '<tr><th>parameter</th>' + runs.map(function(run) {
        return '<th style="border-bottom:3px solid ' + run.color + '">' +
                _esc(run.meta.label || run.run_id.slice(-12)) + '</th>';
      }).join('') + '</tr>';
      var diffRows = paramKeys.map(function(k) {
        var values = runs.map(function(run) { return (run.meta.params || {})[k]; });
        var uniq = new Set(values.map(function(v) { return JSON.stringify(v); }));
        var differs = uniq.size > 1;
        return '<tr><td><code>' + _esc(k) + '</code></td>' +
                values.map(function(v) {
                  return '<td' + (differs ? ' class="differs"' : '') + '>' +
                          _esc(String(v === undefined ? '—' : v)) + '</td>';
                }).join('') + '</tr>';
      }).join('');
      var diffTable = '<table class="ce-diff-table"><thead>' + diffHead +
                      '</thead><tbody>' + diffRows + '</tbody></table>';

      body.innerHTML = legend + chartContainers + diffTable;

      // Plot each observable
      obsList.forEach(function(k) {
        var traces = runs.map(function(run) {
          var times = run.trajectory.map(function(p) { return p.time; });
          var ys = run.trajectory.map(function(p) { return p.state[k]; });
          return { x: times, y: ys, type: 'scatter', mode: 'lines',
                    name: run.meta.label || run.run_id.slice(-12),
                    line: { color: run.color, width: 2 } };
        });
        Plotly.newPlot('ce-cmp-' + _esc(k), traces, {
          title: { text: k, font: { size: 13 } },
          margin: { l: 55, r: 15, t: 35, b: 40 },
          showlegend: false,
        }, { responsive: true, displayModeBar: false });
      });
    }).catch(function(err) {
      body.innerHTML = '<span style="color:#c00">Failed to fetch runs: ' + _esc(String(err)) + '</span>';
    });
  }
  window._ceRenderCompare = _ceRenderCompare;

  // ─── State tab ────────────────────────────────────────────────────────
  window._ceTrajectoryCache = {};  // run_id → trajectory array

  function _ceLoadState(run_id, step) {
    var cached = window._ceTrajectoryCache[run_id];
    if (cached) {
      _ceShowState(run_id, step, cached);
      return;
    }
    fetch('/api/composite-run/' + encodeURIComponent(run_id))
      .then(function(r) { return r.json(); })
      .then(function(data) {
        var trajectory = data.trajectory || [];
        window._ceTrajectoryCache[run_id] = trajectory;
        _ceShowState(run_id, step, trajectory);
      })
      .catch(function(err) {
        var tree = document.getElementById('ce-state-tree');
        if (tree) tree.innerHTML = '<span style="color:#c00">Failed to fetch run: ' + _esc(String(err)) + '</span>';
      });
  }
  window._ceLoadState = _ceLoadState;

  function _ceShowState(run_id, step, trajectory) {
    var ctrls = document.getElementById('ce-state-controls');
    var tree = document.getElementById('ce-state-tree');
    var actions = document.getElementById('ce-state-actions');
    if (!trajectory.length) {
      ctrls.innerHTML = '<p class="empty-state">No state recorded for this run.</p>';
      tree.innerHTML = '';
      actions.style.display = 'none';
      return;
    }
    var maxStep = trajectory.length - 1;
    var safeStep = Math.max(0, Math.min(step, maxStep));
    ctrls.innerHTML =
      '<label>run: <code>' + _esc(run_id) + '</code></label>' +
      '<br><label>step: <input type="range" id="ce-state-slider" min="0" max="' +
        maxStep + '" value="' + safeStep + '"' +
        ' oninput="_ceShowState(\'' + _esc(run_id) + '\', parseInt(this.value), window._ceTrajectoryCache[\'' + _esc(run_id) + '\'])"></label> ' +
      '<span id="ce-state-step-val">step ' + safeStep + ' of ' + maxStep + '</span>';
    document.getElementById('ce-state-step-label').textContent = safeStep;
    var pt = trajectory[safeStep];
    tree.innerHTML = '';
    _ceRenderStateTree(pt && pt.state || {}, tree, 0);
    actions.style.display = '';
    window._ceCurrentStateForSnapshot = pt && pt.state || {};
  }
  window._ceShowState = _ceShowState;

  function _ceRenderStateTree(obj, container, depth) {
    var node = _ceRenderJSON(obj, depth);
    if (typeof node === 'string') container.innerHTML = node;
    else { container.innerHTML = ''; container.appendChild(node); }
  }
  window._ceRenderStateTree = _ceRenderStateTree;

  function _ceRenderJSON(obj, depth) {
    if (obj === null) return '<span class="ce-jt-null">null</span>';
    if (typeof obj === 'boolean') return '<span class="ce-jt-bool">' + obj + '</span>';
    if (typeof obj === 'number') return '<span class="ce-jt-num">' + obj + '</span>';
    if (typeof obj === 'string') return '<span class="ce-jt-str">"' + _esc(obj) + '"</span>';
    if (Array.isArray(obj)) {
      if (obj.length === 0) return '<span class="ce-jt-bracket">[]</span>';
      if (depth >= 5) return '<span class="ce-jt-bracket">[…' + obj.length + ' items]</span>';
      var id = 'ce-jt-' + Math.random().toString(36).slice(2, 9);
      var html = '<span class="ce-jt-toggle" onclick="_ceToggleJt(\'' + id + '\')">&blacktriangledown;</span>';
      html += '<span class="ce-jt-bracket">[</span><span style="color:#94a3b8;font-size:0.85em"> ' + obj.length + ' items</span>';
      html += '<div id="' + id + '" style="margin-left:1.2em">';
      obj.forEach(function(v, i) {
        html += '<div>' + _ceRenderJSON(v, depth + 1) + (i < obj.length - 1 ? ',' : '') + '</div>';
      });
      html += '</div><span class="ce-jt-bracket">]</span>';
      return html;
    }
    if (typeof obj === 'object') {
      var keys = Object.keys(obj);
      if (keys.length === 0) return '<span class="ce-jt-bracket">{}</span>';
      if (depth >= 5) return '<span class="ce-jt-bracket">{…' + keys.length + ' keys}</span>';
      var id = 'ce-jt-' + Math.random().toString(36).slice(2, 9);
      var html = '<span class="ce-jt-toggle" onclick="_ceToggleJt(\'' + id + '\')">&blacktriangledown;</span>';
      html += '<span class="ce-jt-bracket">{</span>';
      html += '<div id="' + id + '" style="margin-left:1.2em">';
      keys.forEach(function(k, i) {
        html += '<div><span class="ce-jt-key">' + _esc(k) + '</span>: ' +
                _ceRenderJSON(obj[k], depth + 1) + (i < keys.length - 1 ? ',' : '') + '</div>';
      });
      html += '</div><span class="ce-jt-bracket">}</span>';
      return html;
    }
    return String(obj);
  }

  function _ceToggleJt(id) {
    var el = document.getElementById(id);
    if (!el) return;
    el.classList.toggle('ce-jt-collapsed');
  }
  window._ceToggleJt = _ceToggleJt;

  // ─── Snapshot to initial ──────────────────────────────────────────────
  function _ceSnapshotToInitial() {
    var state = window._ceCurrentStateForSnapshot || {};
    var paramInputs = document.querySelectorAll('#ce-parameters input[data-param]');
    var matched = [], skipped = [];
    function walk(obj, prefix) {
      Object.keys(obj || {}).forEach(function(k) {
        var v = obj[k];
        var path = prefix ? prefix + '.' + k : k;
        if (v !== null && typeof v === 'object' && !Array.isArray(v)) {
          walk(v, path);
        } else {
          // Try to find a parameter input whose name matches the leaf key
          var target = null;
          paramInputs.forEach(function(inp) {
            if (inp.dataset.param === k) target = inp;
          });
          if (!target) {
            skipped.push({ path: path, reason: 'no matching parameter' });
            return;
          }
          var declaredType = target.dataset.type;
          var ok = (declaredType === 'float' && typeof v === 'number')
                || (declaredType === 'int'   && typeof v === 'number' && Number.isInteger(v))
                || (declaredType === 'string' && typeof v === 'string')
                || (declaredType === 'bool'  && typeof v === 'boolean');
          if (!ok) {
            skipped.push({ path: path, reason: 'type mismatch (' + declaredType + ' vs ' + typeof v + ')' });
            return;
          }
          target.value = v;
          matched.push({ path: path, value: v });
        }
      });
    }
    walk(state, '');
    var report = document.getElementById('ce-snapshot-report');
    var skippedHtml = skipped.length
      ? '<details style="margin-top:4px"><summary>Show ' + skipped.length + ' skipped</summary><ul style="font-size:0.85em">' +
          skipped.map(function(s) { return '<li><code>' + _esc(s.path) + '</code> — ' + _esc(s.reason) + '</li>'; }).join('') +
        '</ul></details>'
      : '';
    report.innerHTML = 'Mapped ' + matched.length + ' of ' +
                       (matched.length + skipped.length) + ' leaves. ' + skippedHtml;
    _ceSwitchTab('view');
  }
  window._ceSnapshotToInitial = _ceSnapshotToInitial;

  function _ceFetch() {
    var id = window._ceCurrent.id;
    var isSnapshot = document.body.classList.contains('snapshot');
    var p;
    if (isSnapshot) {
      // Snapshot mode: load pre-built state from static bundle via DataSource.
      p = window.DataSource.loadCompositeResolve(id);
    } else {
      // Live mode: fetch resolve endpoint with overrides.
      var url = '/api/composite-resolve?id=' + encodeURIComponent(id) +
        '&overrides=' + encodeURIComponent(JSON.stringify(window._ceCurrent.overrides));
      p = fetch(url).then(function(r) { return r.json(); });
    }
    p.then(function(data) {
        if (data.unresolved) {
          // Honest degrade: the ref doesn't resolve to a registered composite.
          // Don't render a bare "error composite" node — explain it plainly.
          document.getElementById('ce-loading').innerHTML =
            '<div style="color:#92400e;background:#fffbeb;border:1px solid #f59e0b;' +
            'border-radius:6px;padding:10px 14px">⚠ Composite not found in the ' +
            'registry: <code>' + _esc(data.ref || id) + '</code>. This study may not ' +
            'declare a real composite — check the study’s baseline composite ref.</div>';
          return;
        }
        if (data.error) {
          document.getElementById('ce-loading').innerHTML =
            '<span style="color:#c00">Error: ' + _esc(data.error) + '</span>';
          return;
        }
        document.getElementById('ce-loading').style.display = 'none';
        document.getElementById('ce-main').style.display = '';
        document.getElementById('ce-name').textContent = data.name;
        document.getElementById('ce-description').textContent = data.description || '';
        document.getElementById('ce-id').textContent = data.id;
        // Module + kind metadata (added in support of @composite_generator).
        var moduleEl = document.getElementById('ce-module');
        var kindEl = document.getElementById('ce-kind');
        if (moduleEl) moduleEl.textContent = data.module || '(unknown)';
        if (kindEl) {
          if ((data.kind || 'spec') === 'generator') {
            kindEl.textContent = 'generator';
            kindEl.style.display = '';
          } else {
            kindEl.textContent = '';
            kindEl.style.display = 'none';
          }
        }
        window._ceCurrent.parameters = data.parameters;
        // Pre-fill the steps input from default_n_steps when the composite
        // declares one; otherwise fall back to 5.
        var stepsInput = document.getElementById('ce-steps');
        if (stepsInput) {
          stepsInput.value = (data.default_n_steps != null) ? data.default_n_steps : 5;
        }
        // Send wiring state to bigraph-loom iframe via postMessage
        // "library" = the package the composite ships in; data.module is the
        // submodule path (e.g. "pbg_biomodels.composites") — drop the
        // conventional .composites suffix to get the library name.
        // parameters + overrides + default_n_steps feed the Configure + Run
        // tabs inside the loom iframe.
        _loadCompositeExplorer(
          data.id, data.state, data.name,
          (data.module || '').replace(/\.composites$/, ''),
          data.parameters,
          window._ceCurrent.overrides || {},
          data.default_n_steps,
        );
        // Render parameter editor
        _ceRenderParameters(data.parameters);
        // Render state JSON (Document tab now lives inside the iframe — this
        // outer #ce-state-json element was removed when the outer tab strip
        // was retired. Null-guard for resilience if it's ever reintroduced.)
        var stateJsonEl = document.getElementById('ce-state-json');
        if (stateJsonEl) stateJsonEl.textContent = JSON.stringify(data.state, null, 2);
      })
      .catch(function(err) {
        var msg = document.body.classList.contains('snapshot')
          ? 'Wiring snapshot not available for this composite in the read-only view.'
          : 'Network error: ' + _esc(String(err));
        document.getElementById('ce-loading').innerHTML =
          '<span style="color:#c00">' + msg + '</span>';
      });
  }

  function _legacyLoadCompositeSvg(ref) {
    var el = document.getElementById('composite-explore-svg-legacy');
    if (!el) return;
    el.innerHTML = '<p style="color:#888">Loading SVG…</p>';
    fetch('/api/composite-resolve?id=' + encodeURIComponent(ref))
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (data.svg) {
          el.innerHTML = data.svg;
        } else {
          el.innerHTML = '<p style="color:#666">No SVG returned from legacy render.</p>';
        }
      })
      .catch(function() {
        el.innerHTML = '<p style="color:#666">Legacy SVG render unavailable.</p>';
      });
  }

  // _loadCompositeExplorer: send composite state to the bigraph-loom iframe.
  // Can be called with a pre-resolved state object (from _ceFetch) or with
  // just a ref string, in which case it fetches /api/composite-state first.
  // When ui.composite_view === 'bigraph-viz', uses the legacy SVG path instead.
  function _loadCompositeExplorer(ref, stateObj, nameHint, libraryHint, parametersHint, overridesHint, defaultStepsHint) {
    // Apply visibility toggle each time the explorer is loaded (catches cases
    // where the config fetch completed after the first render).
    _applyCompositeViewMode();

    var cfg = window._uiConfig || {};
    if ((cfg.composite_view || 'bigraph-loom') === 'bigraph-viz') {
      _legacyLoadCompositeSvg(ref);
      return;
    }

    var iframe = document.getElementById('composite-explore-frame');
    if (!iframe) return;

    // Snapshot mode: set iframe src to ?static=1&stateUrl= (read-only loom view).
    // bigraph-loom fetches the stateUrl and renders it in View-only mode.
    // basePath is non-empty when the bundle is hosted at a URL subpath (e.g.
    // GitHub Pages project sites).  Prefix both the loom entry point and the
    // stateUrl so all paths resolve under the configured subpath.
    if (document.body.classList.contains('snapshot')) {
      var _snapshotBase = (window.__DASH_CONFIG__ && window.__DASH_CONFIG__.basePath) || "";
      var stateUrl = _snapshotBase + '/api/composite-state/' + encodeURIComponent(ref) + '.json';
      iframe.src = _snapshotBase + '/bigraph-loom/index.html?static=1&stateUrl=' + encodeURIComponent(stateUrl);
      iframe.style.display = '';
      return;
    }

    function _postState(state, name) {
      var payload = {
        type: 'composite:load',
        state: state,
        parameters: parametersHint || undefined,
        overrides: overridesHint || {},
        default_n_steps: defaultStepsHint,
        metadata: { name: name || ref, library: libraryHint || '', id: ref },
      };
      window._loomLastState = window._loomLastState || {};
      window._loomLastState[iframe.id] = payload;
      // New composite → reset any emit-toggle selections from the previous one.
      window._explorerEmitPaths = [];
      var post = function() {
        iframe.contentWindow.postMessage(payload, '*');
      };
      if (window._loomExploreReady && window._loomExploreReady[iframe.id]) {
        post();
      } else {
        var listener = function(ev) {
          if (ev.source === iframe.contentWindow && ev.data && ev.data.type === 'explore:ready') {
            window._loomExploreReady = window._loomExploreReady || {};
            window._loomExploreReady[iframe.id] = true;
            window.removeEventListener('message', listener);
            post();
          }
        };
        window.addEventListener('message', listener);
      }
    }

    if (stateObj !== undefined) {
      // Caller already has the resolved state (e.g. from _ceFetch via composite-resolve)
      _postState(stateObj, nameHint || ref);
    } else {
      // Fetch state independently via DataSource (snapshot → /api/composite-state/<id>.json; live → /api/composite-resolve)
      window.DataSource.loadCompositeResolve(ref)
        .then(function(data) {
          if (data.error) {
            console.error('composite-state error:', data.error);
            return;
          }
          _postState(data.state, nameHint || ref);
        })
        .catch(function(err) { console.error('composite load failed:', err); });
    }
  }
  window._loadCompositeExplorer = _loadCompositeExplorer;


  function _ceRenderParameters(params) {
    var container = document.getElementById('ce-parameters');
    if (!container) return;  // Parameters panel removed from Composite Explorer; no-op.
    var keys = Object.keys(params || {});
    if (!keys.length) {
      container.innerHTML = '<p class="muted">No parameters.</p>';
      return;
    }
    container.innerHTML = keys.map(function(k) {
      var pdef = params[k];
      var def = pdef.default;
      var current = (window._ceCurrent.overrides && window._ceCurrent.overrides[k] !== undefined)
        ? window._ceCurrent.overrides[k] : def;
      var type = pdef.type || 'string';
      var inputType = (type === 'int' || type === 'float') ? 'number' : 'text';
      var step = (type === 'float') ? 'any' : (type === 'int' ? '1' : '');
      var desc = pdef.description
        ? '<div class="ce-param-desc muted"><small>' + _esc(pdef.description) + '</small></div>'
        : '';
      return '<div class="ce-param-row">' +
        '<label class="ce-param-label">' +
          '<span class="ce-param-name"><code>' + _esc(k) + '</code> ' +
            '<span class="muted">(' + _esc(type) + ')</span></span>' +
          '<input class="ce-param-input" data-param="' + _esc(k) +
            '" data-type="' + _esc(type) + '" type="' + inputType + '"' +
            (step ? ' step="' + step + '"' : '') +
            ' value="' + _esc(String(current !== undefined && current !== null ? current : '')) + '">' +
        '</label>' +
        desc +
      '</div>';
    }).join('');
  }

  function _ceCollectOverrides() {
    var inputs = document.querySelectorAll('#ce-parameters input[data-param]');
    var out = {};
    inputs.forEach(function(el) {
      var k = el.dataset.param, t = el.dataset.type;
      var v = el.value;
      if (v === '') return;
      if (t === 'float') v = parseFloat(v);
      else if (t === 'int') v = parseInt(v, 10);
      else if (t === 'bool') v = (v === 'true' || v === '1');
      out[k] = v;
    });
    return out;
  }

  function _ceUpdateDiagram() {
    window._ceCurrent.overrides = _ceCollectOverrides();
    document.getElementById('ce-diagram').innerHTML = '<p class="empty-state">Re-rendering diagram&hellip;</p>';
    _ceFetch();
  }
  window._ceUpdateDiagram = _ceUpdateDiagram;

  function _ceTestRun() {
    var steps = parseInt(document.getElementById('ce-steps').value, 10) || 5;
    var overrides = _ceCollectOverrides();
    var resultsEl = document.getElementById('ce-test-results');
    resultsEl.innerHTML = '<p class="empty-state">Starting run&hellip;</p>';
    fetch('/api/composite-test-run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        id: window._ceCurrent.id,
        overrides: overrides,
        steps: steps,
        emit_paths: window._explorerEmitPaths || [],
      }),
    })
      .then(function(r) { return r.json().then(function(j) { return [r.status, j]; }); })
      .then(function(parts) {
        var code = parts[0], body = parts[1];
        if (code !== 202) {
          var errMsg = body && body.error
            ? body.error
            : ('HTTP ' + code);
          resultsEl.innerHTML =
            '<div style="color:#c00;"><strong>Could not start run:</strong> ' +
            _esc(errMsg) + '</div>';
          return;
        }
        // Successful 202 — server accepted the run, returned a run_id.
        var run_id = body.run_id;
        window._ceLastRunId = run_id;
        // Bookmark the new run in the URL so refresh / share works.
        try {
          var url = new URL(window.location.href);
          url.searchParams.set('run_id', run_id);
          window.history.replaceState({}, '', url.toString());
          if (window._ceCurrent) window._ceCurrent.run_id = run_id;
        } catch (e) { /* non-critical */ }
        // Invalidate the cached History list so the new run shows up the next
        // time the Results tab is opened; refresh it now if it's already active.
        window._ceHistoryLoaded = false;
        var resultsPanel = document.querySelector('.ce-tab-panel[data-tab="results"]');
        if (resultsPanel && resultsPanel.classList.contains('active')
            && typeof _ceLoadHistory === 'function') {
          _ceLoadHistory();
        }
        // Hand off to the shared loader — same render path as URL deep-link.
        _ceLoadRunFromId(run_id);
      })
      .catch(function(err) {
        resultsEl.innerHTML =
          '<div style="color:#c00;"><strong>Network error:</strong> ' +
          _esc(String(err)) + '</div>';
      });
  }
  window._ceTestRun = _ceTestRun;

  // ---------------------------------------------------------------------------
  // Save-as-Study modal (wired to explore:run-complete postMessage from loom iframe)
  // ---------------------------------------------------------------------------

  function _ceOpenSaveAsStudyModal() {
    var nameInput = document.getElementById('sas-name');
    if (nameInput) {
      // Pre-fill: <composite-leaf>-<YYMMDD>
      var composite = (window._ceCurrent && window._ceCurrent.id) || '';
      var leaf = composite.indexOf('.') >= 0 ? composite.split('.').pop() : composite;
      leaf = leaf.toLowerCase().replace(/_/g, '-');   // match server slug regex
      var date = new Date();
      var yymmdd = String(date.getFullYear()).slice(2) +
        String(date.getMonth() + 1).padStart(2, '0') +
        String(date.getDate()).padStart(2, '0');
      nameInput.value = leaf ? (leaf + '-' + yymmdd) : '';
    }
    var objEl = document.getElementById('sas-objective');
    if (objEl) objEl.value = '';
    var descEl = document.getElementById('sas-description');
    if (descEl) descEl.value = '';
    var errEl = document.getElementById('sas-error');
    if (errEl) { errEl.textContent = ''; errEl.style.display = 'none'; }
    openModal('modal-save-as-study');
  }
  window._ceOpenSaveAsStudyModal = _ceOpenSaveAsStudyModal;

  function _ceSubmitSaveAsStudy() {
    var name = (document.getElementById('sas-name') || {}).value || '';
    name = name.trim();
    var objective = (document.getElementById('sas-objective') || {}).value || '';
    var description = (document.getElementById('sas-description') || {}).value || '';
    var sourceRunId = window._ceLastRunId || '';
    var errEl = document.getElementById('sas-error');

    if (!name) {
      if (errEl) { errEl.textContent = 'Study name is required.'; errEl.style.display = 'block'; }
      return;
    }
    if (!sourceRunId) {
      if (errEl) { errEl.textContent = 'No run ID — please complete a test run first.'; errEl.style.display = 'block'; }
      return;
    }

    var submitBtn = document.querySelector('#form-save-as-study button[type="submit"]');
    if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = 'Creating…'; }

    fetch('/api/study-create-from-run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        name: name,
        objective: objective,
        description: description,
        source_run_id: sourceRunId,
      }),
    })
      .then(function(r) { return r.json().then(function(d) { return {status: r.status, body: d}; }); })
      .then(function(res) {
        if (res.status === 200) {
          closeModal('modal-save-as-study');
          // Bring the user to Studies with the new study already
          // embedded. The legacy /studies/<name> URL still works as a direct
          // link (in res.body.url) but full-window navigation is reserved
          // for that fallback path.
          window.location.hash = '#studies';
          _switchPage('studies');
          _loadInvestigations();
          _openStudyEmbedded(name);
        } else {
          if (errEl) {
            errEl.textContent = res.body.error || 'Unknown error';
            errEl.style.display = 'block';
          }
          if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = 'Create Study'; }
        }
      })
      .catch(function(err) {
        if (errEl) { errEl.textContent = 'Network error: ' + String(err); errEl.style.display = 'block'; }
        if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = 'Create Study'; }
      });
  }
  window._ceSubmitSaveAsStudy = _ceSubmitSaveAsStudy;

  function _cePromoteSimulation() {
    // Re-use the existing _useComposite flow (Configure modal) with current overrides pre-applied.
    var id = window._ceCurrent.id;

    function _openModalAndApplyOverrides() {
      _useComposite(id);
      var modal = document.getElementById('modal-configure-composite');
      if (modal) {
        Object.keys(window._ceCurrent.overrides || {}).forEach(function(k) {
          var inp = modal.querySelector('input[name="param_' + k + '"]');
          if (inp) inp.value = window._ceCurrent.overrides[k];
        });
      }
    }

    if ((window._compositesById || {})[id]) {
      _openModalAndApplyOverrides();
      return;
    }
    // Cache not populated yet (user landed here without visiting
    // Simulation Setup). Fetch synchronously-as-possible, then open.
    fetch('/api/composites')
      .then(function(r) { return r.json(); })
      .then(function(data) {
        var composites = data.composites || [];
        window._compositesById = window._compositesById || {};
        composites.forEach(function(c) { window._compositesById[c.id] = c; });
        if (!window._compositesById[id]) {
          alert('Composite "' + id + '" not found on the server. It may have been removed.');
          return;
        }
        _openModalAndApplyOverrides();
      })
      .catch(function(err) {
        alert('Failed to load composites: ' + err);
      });
  }
  window._cePromoteSimulation = _cePromoteSimulation;

  // ─── Investigations tab (v0.5.0) ──────────────────────────────────────
  window._investigations = [];
  window._investigationsFilter = { search: '', tags: new Set() };
  window._investigationsView = 'grid';

  function _loadInvestigations() {
    var _p = window.DataSource
      ? window.DataSource.loadInvestigationsFlat()
      : fetch('/api/investigations').then(function(r) {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.json();
        });
    _p
      .then(function(data) {
        window._investigations = data.investigations || [];
        _buildInvestigationTagChips();
        _renderInvestigations();
      })
      .catch(function(err) {
        // Reset the memo so the next navigation to Studies retries.
        window._investigationsLoaded = false;
        var grid = document.getElementById('investigations-grid');
        if (grid) grid.innerHTML = '<p class="empty-state" style="color:#c00">' +
            'Failed to load studies: ' + _esc(String(err)) +
            ' <button class="btn-mini" onclick="window._investigationsLoaded=false;_loadInvestigations()">Retry</button></p>';
      });
  }
  window._loadInvestigations = _loadInvestigations;

  // ─── Investigation-sets (v3 "Investigations" tab) ──────────────────────
  // An investigation-set (iset) is a named collection of studies with
  // dependencies — populated from investigations/<name>/investigation.yaml.
  // Distinct from `window._investigations` which is the FLAT list of every
  // study in the workspace (legacy naming).
  window._isetIndex = [];        // [{name, title, status, studies:[slug, ...]}]
  window._currentIset = null;    // name of the iset currently open in detail view

  function _loadInvestigationSets() {
    var list = document.getElementById('investigations-list');
    if (list) list.innerHTML = '<p class="empty-state">Loading…</p>';
    var _p = window.DataSource
      ? window.DataSource.loadIsetList()
      : fetch('/api/iset-list', {headers: {Accept: 'application/json'}})
          .then(function(r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); });
    _p
      .then(function(j) {
        window._isetIndex = j.investigations || [];
        _renderInvestigationSets();
        _renderRailInvestigationGroups();
        if (!window._isetIndex.length) return;
        // LIST-FIRST UX: show the cards and let the user pick. Auto-open only
        // when (a) a detail is already open (refresh/deep-link), or (b) there
        // is exactly one investigation (a one-item list is pointless).
        var switchBtn = document.getElementById('investigation-switch-btn');
        if (switchBtn) switchBtn.style.display = window._isetIndex.length > 1 ? '' : 'none';
        // List-first: clicking the Investigations menu always returns to the
        // card list. Auto-open only when there is exactly one investigation;
        // cards (and deep-links) open a detail explicitly via _openInvestigationDetail.
        var cur = (window._isetIndex || []).filter(function(i){return i.current;})[0];
        if (cur) {
          _openInvestigationDetail(cur.name);
        } else if (window._isetIndex.length === 1) {
          _openInvestigationDetail(window._isetIndex[0].name);
        } else {
          _showInvestigationList();
        }
      })
      .catch(function(err) {
        if (list) list.innerHTML = '<p class="empty-state" style="color:#b91c1c">' +
          'Failed to load investigations: ' + _esc(String(err)) + '</p>';
      });
  }
  window._loadInvestigationSets = _loadInvestigationSets;

  // Exposed by the "Switch investigation ↓" button when more than one iset
  // exists in the workspace. Shows the list-of-cards UI; clicking a card
  // opens its detail view (existing _openInvestigationDetail flow).
  function _showInvestigationList() {
    var list = document.getElementById('investigations-list');
    var detail = document.getElementById('investigation-detail-view');
    if (list) list.style.display = '';
    if (detail) detail.style.display = 'none';
    window._currentIset = null;
    window._currentIsetSlug = '';
    if (typeof window._renderRailInvestigationGroups === 'function') {
      try { window._renderRailInvestigationGroups(); } catch (_) { /* ignore */ }
    }
    _renderInvestigationSets();
  }
  window._showInvestigationList = _showInvestigationList;

  function _renderInvestigationSets() {
    var list = document.getElementById('investigations-list');
    if (!list) return;
    if (!window._isetIndex.length) {
      list.innerHTML = '<p class="empty-state">No investigations declared. Author one at <code>investigations/&lt;name&gt;/investigation.yaml</code>.</p>';
      return;
    }
    // Closed/archived investigations sink to the bottom (stable sort).
    var ordered = (window._isetIndex || []).map(function(it, idx) { return [it, idx]; });
    ordered.sort(function(a, b) {
      var ac = (a[0].status === 'archived' || a[0].status === 'closed') ? 1 : 0;
      var bc = (b[0].status === 'archived' || b[0].status === 'closed') ? 1 : 0;
      if (ac !== bc) return ac - bc;
      return a[1] - b[1];
    });
    list.innerHTML = ordered.map(function(pair) {
      var iset = pair[0];
      var closed = (iset.status === 'archived' || iset.status === 'closed');
      var desc = (iset.description || '').split('\n')[0].slice(0, 240);
      // Prefer the server-computed effective_status (derived from member
      // studies' live statuses). Fall back to the author-declared yaml
      // status only if the server didn't send effective_status (e.g. an
      // older backend). When the two diverge, surface the author intent
      // as a small subtitle.
      var effStatus  = iset.effective_status || iset.status || 'planning';
      var authStatus = iset.status || 'planning';
      var pillClass  = effStatus.replace(/[^a-z_]/g, '_');
      var intentLine = (authStatus && authStatus !== effStatus)
        ? '<div class="muted" style="font-size:0.72em; margin-top:-2px; margin-bottom:6px;">intent: ' + _esc(authStatus) + '</div>'
        : '';
      var currentPill = iset.current
        ? '<span class="status-pill" style="font-size:0.72em;background:#dcfce7;color:#166534;border:1px solid #86efac">● current branch</span>'
        : '';
      // Closed/archived: show a gray "Closed" pill INSTEAD of effective-status.
      var statusPill = closed
        ? '<span class="status-pill" style="font-size:0.78em;background:#e5e7eb;color:#4b5563;border:1px solid #d1d5db">Closed</span>'
        : '<span class="status-pill ' + pillClass + '" style="font-size:0.78em">' + _esc(effStatus) + '</span>';
      var cardStyle = 'background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:14px 16px;cursor:pointer;transition:box-shadow 0.1s,border-color 0.1s;' +
        (closed ? 'opacity:0.6;' : '');
      var actionLabel = closed ? 'Reopen' : 'Close';
      var actionStatus = closed ? 'in-progress' : 'archived';
      var actionBtn = '<button type="button" class="js-authoring" onclick="event.stopPropagation();_setInvestigationStatus(this,\'' +
        _esc(iset.name) + '\',\'' + actionStatus + '\')" ' +
        'style="font-size:0.78em;padding:3px 10px;border:1px solid #cbd5e1;border-radius:6px;background:#f8fafc;color:#334155;cursor:pointer">' +
        actionLabel + '</button>';
      return '<div class="investigation-set-card" onclick="_openInvestigationDetail(\'' + _esc(iset.name) + '\')" ' +
             'style="' + cardStyle + '">' +
        '<div style="display:flex;align-items:baseline;gap:10px;margin-bottom:6px;">' +
          '<strong style="font-size:1.05em;flex:1">' + _esc(iset.title || iset.name) + '</strong>' +
          currentPill +
          statusPill +
        '</div>' +
        intentLine +
        '<div class="muted" style="font-size:0.78em;font-family:monospace;margin-bottom:6px">' + _esc(iset.name) + '</div>' +
        (desc ? '<p style="margin:0 0 8px 0;font-size:0.9em;color:#475569">' + _esc(desc) + (iset.description.length > 240 ? '…' : '') + '</p>' : '') +
        '<div style="display:flex;align-items:center;gap:10px;font-size:0.85em;color:#64748b">' +
          '<span style="flex:1"><strong>' + iset.n_studies + '</strong> stud' + (iset.n_studies === 1 ? 'y' : 'ies') +
          ' &nbsp;·&nbsp; click to open DAG</span>' +
          actionBtn +
        '</div>' +
      '</div>';
    }).join('');
  }

  // Close/Reopen an investigation: POST the new status, then reload the list.
  // Resilient — never throws; surfaces a brief inline error on the button.
  function _setInvestigationStatus(btn, name, status) {
    var orig = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = '…'; }
    fetch('/api/investigation-set-status', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({investigation: name, status: status}),
    })
      .then(function(r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function() {
        if (typeof _loadInvestigationSets === 'function') _loadInvestigationSets();
      })
      .catch(function(err) {
        if (btn) {
          btn.disabled = false;
          btn.textContent = orig;
          btn.style.color = '#b91c1c';
          btn.title = 'Failed: ' + String(err);
        }
      });
  }
  window._setInvestigationStatus = _setInvestigationStatus;

  // ─── "+ New Investigation" modal ──────────────────────────────────────
  // Slug the user-typed name client-side for a live preview. Matches the
  // server-side validator: ^[a-z0-9][a-z0-9-]*$.
  function _slugifyIsetName(s) {
    if (!s) return '';
    return String(s).toLowerCase()
      .replace(/[\s_]+/g, '-')          // spaces, underscores → dashes
      .replace(/[^a-z0-9-]/g, '')       // strip anything not alnum-or-dash
      .replace(/^-+/, '')               // strip leading dashes
      .replace(/-+/g, '-');             // collapse runs of dashes
  }
  window._slugifyIsetName = _slugifyIsetName;

  function _updateNewIsetSlugPreview() {
    var raw = (document.getElementById('new-iset-name') || {}).value || '';
    var slug = _slugifyIsetName(raw);
    var el = document.getElementById('new-iset-slug-preview');
    if (el) el.textContent = slug || '—';
  }
  window._updateNewIsetSlugPreview = _updateNewIsetSlugPreview;

  function _openNewIsetModal() {
    // Reset fields.
    document.getElementById('new-iset-name').value = '';
    document.getElementById('new-iset-overview').value = '';
    document.getElementById('new-iset-slug-preview').textContent = '—';
    var errEl = document.getElementById('new-iset-error');
    errEl.style.display = 'none';
    errEl.textContent = '';
    // Populate the parent-studies dropdown from the already-loaded
    // _investigations list (the flat studies list; legacy name). Falls
    // back to a fetch if it's empty.
    var select = document.getElementById('new-iset-parent-studies');
    select.innerHTML = '';
    var studies = Array.isArray(window._investigations) ? window._investigations : [];
    function _fill(arr) {
      arr.forEach(function(s) {
        var opt = document.createElement('option');
        opt.value = s.name;
        opt.textContent = s.name + (s.status ? ' (' + s.status + ')' : '');
        select.appendChild(opt);
      });
    }
    if (studies.length) {
      _fill(studies);
    } else {
      fetch('/api/studies', {headers: {Accept: 'application/json'}})
        .then(function(r) { return r.ok ? r.json() : {investigations: []}; })
        .then(function(j) {
          var arr = j.investigations || j.studies || [];
          window._investigations = arr;
          _fill(arr);
        })
        .catch(function() { /* fail silent — parent_studies is optional */ });
    }
    document.getElementById('new-iset-modal').style.display = 'flex';
  }
  window._openNewIsetModal = _openNewIsetModal;

  function _closeNewIsetModal() {
    document.getElementById('new-iset-modal').style.display = 'none';
  }
  window._closeNewIsetModal = _closeNewIsetModal;

  function _submitNewIset() {
    var rawName = (document.getElementById('new-iset-name').value || '').trim();
    var slug    = _slugifyIsetName(rawName);
    var overview = (document.getElementById('new-iset-overview').value || '').trim();
    var select  = document.getElementById('new-iset-parent-studies');
    var parents = Array.from(select.selectedOptions || []).map(function(o) { return o.value; });
    var btn     = document.getElementById('new-iset-submit-btn');
    var errEl   = document.getElementById('new-iset-error');

    if (!slug) {
      errEl.textContent = 'Name is required.';
      errEl.style.display = '';
      return;
    }

    var body = {name: slug};
    if (overview) body.overview = overview;
    if (parents.length) body.parent_studies = parents;

    btn.disabled = true;
    btn.textContent = 'Creating…';
    errEl.style.display = 'none';

    fetch('/api/iset-create', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', Accept: 'application/json'},
      body: JSON.stringify(body),
    }).then(function(r) {
      return r.json().then(function(j) { return {ok: r.ok, status: r.status, body: j}; });
    }).then(function(res) {
      if (!res.ok) {
        var msg = (res.body && res.body.error) ? res.body.error : ('HTTP ' + res.status);
        errEl.textContent = msg;
        errEl.style.display = '';
        return;
      }
      _closeNewIsetModal();
      // Refresh the Investigations tab so the new card appears.
      if (typeof _loadInvestigationSets === 'function') _loadInvestigationSets();
    }).catch(function(err) {
      errEl.textContent = 'Network error: ' + String(err);
      errEl.style.display = '';
    }).then(function() {
      btn.disabled = false;
      btn.textContent = 'Create';
    });
  }
  window._submitNewIset = _submitNewIset;

  // ─── "Clone investigation" modal ─────────────────────────────────────
  function _openCloneIsetModal() {
    var source = window._currentIset || '';
    if (!source) {
      alert('Open an investigation first, then click Clone.');
      return;
    }
    var srcEl = document.getElementById('clone-iset-source');
    var tgtEl = document.getElementById('clone-iset-target');
    var prefEl = document.getElementById('clone-iset-target-prefix');
    var errEl = document.getElementById('clone-iset-error');
    if (srcEl) srcEl.value = source;
    if (tgtEl) tgtEl.value = source + '-fresh';
    if (prefEl) prefEl.value = '';
    if (errEl) errEl.style.display = 'none';
    _updateCloneIsetSlugPreview();
    var modal = document.getElementById('clone-iset-modal');
    if (modal) modal.style.display = 'flex';
  }
  window._openCloneIsetModal = _openCloneIsetModal;

  function _closeCloneIsetModal() {
    var modal = document.getElementById('clone-iset-modal');
    if (modal) modal.style.display = 'none';
  }
  window._closeCloneIsetModal = _closeCloneIsetModal;

  function _updateCloneIsetSlugPreview() {
    var raw = (document.getElementById('clone-iset-target') || {}).value || '';
    var slug = _slugifyIsetName(raw);
    var preview = document.getElementById('clone-iset-slug-preview');
    if (preview) preview.textContent = slug || '—';
  }
  window._updateCloneIsetSlugPreview = _updateCloneIsetSlugPreview;

  function _submitCloneIset() {
    var source = (document.getElementById('clone-iset-source') || {}).value || '';
    var rawTarget = (document.getElementById('clone-iset-target') || {}).value || '';
    var target = _slugifyIsetName(rawTarget);
    var targetPrefix = ((document.getElementById('clone-iset-target-prefix') || {}).value || '').trim();
    var errEl = document.getElementById('clone-iset-error');
    var btn = document.getElementById('clone-iset-submit-btn');

    if (!source) { errEl.textContent = 'No source investigation.'; errEl.style.display = ''; return; }
    if (!target) { errEl.textContent = 'Target name is required.'; errEl.style.display = ''; return; }
    if (target === source) { errEl.textContent = 'Target must differ from source.'; errEl.style.display = ''; return; }

    var body = {source: source, target: target};
    if (targetPrefix) body.target_prefix = targetPrefix;

    btn.disabled = true;
    btn.textContent = 'Cloning…';
    errEl.style.display = 'none';

    fetch('/api/iset-clone', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', Accept: 'application/json'},
      body: JSON.stringify(body),
    }).then(function(r) {
      return r.json().then(function(j) { return {ok: r.ok, status: r.status, body: j}; });
    }).then(function(res) {
      if (!res.ok) {
        var msg = (res.body && res.body.error) ? res.body.error : ('HTTP ' + res.status);
        if (res.body && res.body.stderr) msg += '\n' + res.body.stderr;
        errEl.textContent = msg;
        errEl.style.display = '';
        return;
      }
      _closeCloneIsetModal();
      if (typeof _loadInvestigationSets === 'function') {
        window._currentIset = target;
        _loadInvestigationSets();
      }
    }).catch(function(err) {
      errEl.textContent = 'Network error: ' + String(err);
      errEl.style.display = '';
    }).then(function() {
      btn.disabled = false;
      btn.textContent = 'Clone';
    });
  }
  window._submitCloneIset = _submitCloneIset;

  // ─── Investigation intro renderers (textbook-style) ────────────────
  function _escInv(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  // Light Markdown subset for the lead paragraph. Supports:
  //   blank-line paragraph breaks · bulleted lists ("- " or "* ") ·
  //   numbered lists ("N. ") · **bold** · `inline code`.
  // Anything else is rendered as plain text, HTML-escaped. Deliberately
  // small so the intro stays readable as plain yaml too.
  function _renderInvLeadMarkdown(text) {
    var lines = text.split('\n');
    var html = '', i = 0;
    function inline(s) {
      s = _escInv(s);
      s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
      s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
      return s;
    }
    while (i < lines.length) {
      var line = lines[i];
      if (/^\s*$/.test(line)) { i++; continue; }
      // Bulleted list (-, *, or • prefix)
      if (/^\s*[-*•]\s+/.test(line)) {
        html += '<ul>';
        while (i < lines.length && /^\s*[-*•]\s+/.test(lines[i])) {
          html += '<li>' + inline(lines[i].replace(/^\s*[-*•]\s+/, '')) + '</li>';
          i++;
        }
        html += '</ul>';
        continue;
      }
      // Numbered list
      if (/^\s*\d+\.\s+/.test(line)) {
        html += '<ol>';
        while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
          html += '<li>' + inline(lines[i].replace(/^\s*\d+\.\s+/, '')) + '</li>';
          i++;
        }
        html += '</ol>';
        continue;
      }
      // Paragraph: keep gluing until blank line or list start
      var para = [line];
      i++;
      while (i < lines.length && !/^\s*$/.test(lines[i])
             && !/^\s*[-*•]\s+/.test(lines[i])
             && !/^\s*\d+\.\s+/.test(lines[i])) {
        para.push(lines[i]); i++;
      }
      html += '<p>' + inline(para.join(' ')) + '</p>';
    }
    return html;
  }

  function _renderInvAtAGlance(d) {
    var host = document.getElementById('investigation-at-a-glance');
    if (!host) return;
    host.innerHTML = '';
    // Prefer authored at_a_glance; fall back to studies' one-line role
    // derived from study.question (first sentence) when available.
    var tiles = [];
    var authored = Array.isArray(d.at_a_glance) ? d.at_a_glance : [];
    if (authored.length) {
      tiles = authored.map(function(t, i) {
        return { num: i + 1, slug: t.study || '', role: t.role || '' };
      });
    } else {
      var studies = d.studies || [];
      tiles = studies.map(function(s, i) {
        var role = '';
        var q = (s.question || (s.purpose && s.purpose.question) || '').trim();
        if (q) {
          role = q.split(/[.!?]\s/)[0]; // first sentence
          if (role.length > 140) role = role.slice(0, 137) + '…';
        }
        return { num: i + 1, slug: s.name, role: role };
      });
    }
    if (!tiles.length) { host.style.display = 'none'; return; }
    host.innerHTML = tiles.map(function(t) {
      // Linkable tile: clicking opens the study INLINE (same iframe
      // panel a DAG-node click uses). Plain-text href is kept so
      // middle-click / cmd-click still opens the standalone study
      // detail page in a new tab.
      var href = t.slug ? _studyHref(t.slug) : '#';
      var slugAttr = _escInv(t.slug || '');
      return '<a class="inv-aag-tile" href="' + href + '" '
        +    'data-study-slug="' + slugAttr + '" '
        +    'title="Open ' + slugAttr + ' in this view (Cmd-click for new tab)" '
        +    'onclick="return _vivOpenAagTile(event, \'' + slugAttr.replace(/&amp;/g, '&').replace(/\x27/g, '\\x27') + '\')">'
        + '<span class="inv-aag-num">' + t.num + '</span>'
        + '<span class="inv-aag-slug">' + slugAttr + '</span>'
        + (t.role ? '<span class="inv-aag-role">' + _escInv(t.role) + '</span>' : '')
        + '</a>';
    }).join('');
    host.style.display = '';
  }

  // Click handler for at-a-glance tiles. Behaves like a DAG-node click
  // (inline iframe embed) for plain clicks; passes through to default
  // navigation when the user holds a modifier (Cmd/Ctrl/Shift/middle).
  function _vivOpenAagTile(ev, slug) {
    if (!slug) return true;
    if (ev && (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button === 1)) {
      return true;  // let the browser open in a new tab / window
    }
    ev.preventDefault();
    if (typeof _openStudyInsideInvestigation === 'function') {
      _openStudyInsideInvestigation(slug);
    } else {
      // Fallback: navigate to the detail page.
      window.location.href = '/studies/' + encodeURIComponent(slug);
    }
    return false;
  }
  window._vivOpenAagTile = _vivOpenAagTile;

  function _renderInvHowToRead(items) {
    var host = document.getElementById('investigation-how-to-read');
    if (!host) return;
    var ol = host.querySelector('ol');
    if (!Array.isArray(items) || !items.length) {
      host.style.display = 'none';
      if (ol) ol.innerHTML = '';
      return;
    }
    ol.innerHTML = items.map(function(s) {
      return '<li>' + _renderInvLeadMarkdown(String(s)).replace(/^<p>|<\/p>$/g, '') + '</li>';
    }).join('');
    host.style.display = '';
  }

  function _renderInvGlossary(items) {
    var host = document.getElementById('investigation-glossary');
    if (!host) return;
    var dl = host.querySelector('dl');
    if (!Array.isArray(items) || !items.length) {
      host.style.display = 'none';
      if (dl) dl.innerHTML = '';
      return;
    }
    dl.innerHTML = items.map(function(g) {
      var term = _escInv(g.term || g.name || '');
      var def  = _escInv(g.definition || g.def || '');
      return '<dt>' + term + '</dt><dd>' + def + '</dd>';
    }).join('');
    host.style.display = '';
  }

  // Investigation opening — state-first, and synchronized with the downloaded
  // report's "Executive summary": both read the SAME canonical investigation.yaml
  // fields (executive.{what_is_this,verdict,verdict_status} + question + hypothesis).
  // The free-form `lead` ("replaces prior work…") is demoted to a Background fold.
  function _renderInvOpening(d) {
    d = d || {};
    var ex = d.executive || {};
    var whatIs  = (ex.what_is_this || '').trim();
    var verdict = (ex.verdict || '').trim();
    var vs      = (ex.verdict_status || 'in-progress').trim();
    var oneline = function(t) { return (t || '').replace(/\s+/g, ' ').trim(); };
    var q   = oneline(d.question);
    var hyp = oneline(d.hypothesis);
    var leadProse = (d.lead || d.description || '').trim();

    // Legacy investigations with no executive content fall back to the lead.
    if (!whatIs && !verdict && !q && !hyp) {
      return leadProse ? _renderInvLeadMarkdown(leadProse) : '';
    }

    var key = String(vs).toLowerCase().replace(/[^a-z0-9]+/g, '-');
    var vColor = ({ 'passed':'#166534','complete':'#166534','in-progress':'#854d0e',
                    'blocked':'#991b1b','failed':'#991b1b','planning':'#1e40af' })[key] || '#475569';
    var vBg = ({ 'passed':'#dcfce7','complete':'#dcfce7','in-progress':'#fef9c3',
                 'blocked':'#fee2e2','failed':'#fee2e2','planning':'#dbeafe' })[key] || '#e2e8f0';

    var out = '';
    if (whatIs)
      out += '<div style="margin:2px 0 10px;color:#334155;line-height:1.5">' + _renderInvLeadMarkdown(whatIs) + '</div>';
    if (verdict)
      out += '<div style="background:#f8fafc;border-left:5px solid ' + vColor + ';border-radius:8px;padding:10px 14px;margin:10px 0">' +
        '<span style="display:inline-block;font-size:0.7em;font-weight:700;letter-spacing:0.03em;background:' + vBg +
          ';color:' + vColor + ';padding:2px 9px;border-radius:9999px;margin-right:8px">' + _esc(vs.toUpperCase()) + '</span>' +
        '<strong style="color:#1e293b">Current verdict.</strong> <span style="color:#334155">' + _esc(verdict) + '</span></div>';
    if (q)
      out += '<p style="margin:8px 0;color:#334155;line-height:1.5"><strong style="color:#1e293b">Question.</strong> ' + _esc(q) + '</p>';
    if (hyp)
      out += '<p style="margin:8px 0;color:#475569;line-height:1.5"><strong style="color:#1e293b">Hypothesis.</strong> ' + _esc(hyp) + '</p>';
    if (leadProse)
      out += '<details style="margin-top:10px"><summary style="cursor:pointer;font-size:0.88em;color:#64748b">Background &amp; context</summary>' +
        '<div style="margin-top:6px;color:#475569;line-height:1.5">' + _renderInvLeadMarkdown(leadProse) + '</div></details>';
    return out;
  }

  function _openInvestigationDetail(name) {
    window._currentIset = name;
    // Sync the left-rail STUDIES section to the selected investigation
    // (the top-left now switches repos, so selection drives the sidebar).
    if (window._currentIsetSlug !== name) {
      window._currentIsetSlug = name;
      if (typeof window._renderRailInvestigationGroups === 'function') {
        try { window._renderRailInvestigationGroups(); } catch (_) { /* ignore */ }
      }
    }
    document.getElementById('investigations-list').style.display = 'none';
    document.getElementById('investigation-detail-view').style.display = '';
    document.getElementById('investigation-detail-title').textContent = name;
    document.getElementById('investigation-detail-description').textContent = 'Loading…';

    // Route through DataSource so snapshot mode reads api/iset/<name>.json from
    // the static bundle instead of hitting the live /api/iset/<name> endpoint
    // (which would 404 in a hosted read-only bundle). Direct-fetch fallback keeps
    // local-server mode identical — the ternary branch only triggers under snapshot.
    var _isetDetailFetch = (window.DataSource && window.DataSource.loadInvestigation)
      ? window.DataSource.loadInvestigation(name)
      : fetch('/api/iset/' + encodeURIComponent(name), {headers: {Accept: 'application/json'}})
          .then(function(r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); });
    _isetDetailFetch
      .then(function(d) {
        window._currentIsetData = d;
        document.getElementById('investigation-detail-title').textContent = d.title || d.name;
        var statusEl   = document.getElementById('investigation-detail-status');
        var effStatus  = d.effective_status || d.status || 'planning';
        var authStatus = d.status || 'planning';
        statusEl.textContent = effStatus;
        // Drop any stale status class then apply the one matching effStatus.
        statusEl.className = 'status-pill ' + effStatus.replace(/[^a-z_]/g, '_');
        statusEl.title = (authStatus && authStatus !== effStatus)
          ? 'effective: ' + effStatus + '  (intent: ' + authStatus + ')'
          : 'status: ' + effStatus;
        // Lead paragraph: render lead (preferred) or fall back to description.
        // Light markdown: paragraph splits, * bullets, `code`, **bold**.
        var leadEl = document.getElementById('investigation-detail-description');
        leadEl.innerHTML = _renderInvOpening(d);

        // At-a-glance study-card row removed (user request 2026-06-07): the
        // dependency DAG below shows the same studies, so the top row was
        // redundant. Clear + hide the host so no empty band remains.
        var _aagHost = document.getElementById('investigation-at-a-glance');
        if (_aagHost) { _aagHost.innerHTML = ''; _aagHost.style.display = 'none'; }

        // How to read: yaml-driven list of evaluator tips. Hidden if absent.
        _renderInvHowToRead(d.how_to_read);

        // Glossary: yaml-driven list of {term, definition}. Hidden if absent.
        _renderInvGlossary(d.glossary);

        // Biology-story banner: populated only when investigation.yaml
        // declares `biological_story:`. Hidden otherwise.
        var storyBox = document.getElementById('investigation-biology-story');
        var storyText = document.getElementById('investigation-biology-story-text');
        if (storyBox && storyText) {
          var story = (d.biological_story || '').trim();
          if (story) {
            // Render as reflowing paragraphs (split on blank lines, collapse
            // intra-paragraph hard newlines to spaces) so the text uses the full
            // width instead of breaking at the YAML's source newlines.
            storyText.innerHTML = story.split(/\n\s*\n/).map(function(para) {
              return '<p>' + _esc(para.replace(/\s*\n\s*/g, ' ').trim()) + '</p>';
            }).join('');
            storyBox.style.display = '';
          } else {
            storyText.textContent = '';
            storyBox.style.display = 'none';
          }
        }
        _renderInvestigationDag(d.studies || []);
        // SP5: needs-attention panel (deterministic scan, code-computed, AI-free).
        _renderInvNeedsAttention(name);
      })
      .catch(function(err) {
        document.getElementById('investigation-detail-description').textContent = 'Failed to load: ' + err;
      });
  }
  window._openInvestigationDetail = _openInvestigationDetail;

  // SP5: "Needs attention" panel on the investigation-detail page. Fetches the
  // deterministic scan (GET /api/needs-attention) — uncovered ACs, verdict
  // divergences, open feedback, param drift, stale findings, phantom
  // observables — and renders it as a collapsible <details> dropdown that
  // mirrors the study-detail readiness panel. Items arrive PRE-SORTED
  // high→medium→low. The dashboard computes nothing here; it renders the
  // scan's output (AI-free). Tolerant: an absent/failed endpoint just leaves
  // the panel empty.
  function _naSeverityStyle(sev) {
    var s = (sev || '').toString().toLowerCase();
    if (s === 'high')   return { dot: '#dc2626', bg: '#fef2f2', bd: '#dc2626', col: '#991b1b' };
    if (s === 'medium') return { dot: '#f59e0b', bg: '#fffbeb', bd: '#f59e0b', col: '#92400e' };
    return { dot: '#3b82f6', bg: '#eff6ff', bd: '#3b82f6', col: '#1e40af' };  // low / default
  }
  function _renderInvNeedsAttention(name) {
    var container = document.getElementById('investigation-needs-attention');
    if (!container) return;
    container.innerHTML = '';
    var _fetch = fetch('/api/needs-attention?investigation=' + encodeURIComponent(name),
                       {headers: {Accept: 'application/json'}})
      .then(function(r) { return r.ok ? r.json() : null; });
    _fetch.then(function(d) {
      if (!d || !d.summary) return;
      var lbl = '<span class="muted" style="font-size:0.85em">code-computed by the needs-attention scan (deterministic)</span>';
      var total = (d.summary.total) || 0;
      if (!total) {
        // Quiet "nothing needs attention" state — not an empty dropdown.
        container.innerHTML =
          '<div class="needs-attention-banner" style="margin:10px 0 14px 0;padding:10px 14px;'
          + 'background:#f0fdf4;border:1px solid #16a34a;border-left-width:5px;border-radius:6px;color:#166534">'
          + '<strong>✓ Nothing needs attention</strong> ' + lbl + '</div>';
        return;
      }
      var bySev = d.summary.by_severity || {};
      var high = bySev.high || 0;
      var head = '⚠ Needs attention — ' + high + ' high, ' + total + ' total';
      var items = (d.items || []).map(function(it) {
        var st = _naSeverityStyle(it.severity);
        var ref = (it.study || it.ref || '').toString();
        var kind = _esc((it.kind || '').toString());
        var refHtml = ref ? '<code>' + _esc(ref) + '</code>' : '<span class="muted">—</span>';
        var hint = it.action_hint ? ' &nbsp;·&nbsp; ' + _esc(it.action_hint.toString()) : '';
        var titleLine = it.title
          ? '<div style="font-size:0.9em;margin-top:2px">' + _esc(it.title.toString()) + '</div>'
          : '';
        return '<li style="margin-top:7px;padding-left:10px;border-left:3px solid ' + st.bd + '">'
          + '<span style="color:' + st.dot + ';font-weight:700">●</span> '
          + '<code style="font-size:0.85em">' + kind + '</code> &nbsp;·&nbsp; ' + refHtml + hint
          + titleLine + '</li>';
      }).join('');
      var byKind = d.summary.by_kind || {};
      var breakdown = Object.keys(byKind).sort(function(a, b) {
        return (byKind[b] || 0) - (byKind[a] || 0);
      }).map(function(k) { return (byKind[k] || 0) + '× ' + _esc(k); }).join(' &nbsp;·&nbsp; ');
      var sev = _naSeverityStyle(high ? 'high' : 'medium');
      container.innerHTML =
        '<details class="needs-attention-banner" style="margin:10px 0 14px 0;background:' + sev.bg
        + ';border:1px solid ' + sev.bd + ';border-left-width:5px;border-radius:6px;color:' + sev.col + '">'
        + '<summary style="padding:10px 14px;cursor:pointer;list-style:none;outline:none">'
        + '<strong>' + head + '</strong> ' + lbl
        + (breakdown ? '<div class="muted" style="font-size:0.82em;margin-top:5px">' + breakdown
            + ' &nbsp;·&nbsp; <span style="opacity:.7;font-style:italic">click to expand</span></div>' : '')
        + '</summary>'
        + '<ul style="margin:4px 0 12px 0;padding:0 14px 0 18px;list-style:none;font-size:0.92em">'
        + items + '</ul>'
        + '</details>';
    }).catch(function() { /* tolerant — leave the panel empty */ });
  }
  window._renderInvNeedsAttention = _renderInvNeedsAttention;

  // "Run unblocked" — kick off every variant in the current investigation
  // whose required-before-run gates are satisfied. POSTs to start a
  // background job, then polls /api/investigation-run-unblocked-status
  // every 2 s and re-renders the progress panel. Once all items finish,
  // re-loads the investigation so charts pick up the fresh runs.db data.
  var _vivRunUnblockedTimer = null;
  function _runUnblockedSimulations() {
    var name = window._currentIset;
    if (!name) return;
    var btn = document.getElementById('investigation-run-unblocked');
    var panel = document.getElementById('investigation-run-progress');
    if (btn) { btn.disabled = true; btn.textContent = '… queuing'; }
    if (panel) { panel.style.display = ''; panel.innerHTML = '<div class="inv-run-progress-banner">Queuing run-unblocked job…</div>'; }
    fetch('/api/investigation-run-unblocked', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({investigation: name}),
    }).then(function(r) {
      return r.json().then(function(j) { return {ok: r.ok, body: j, status: r.status}; });
    }).then(function(res) {
      if (!res.ok) {
        var msg = (res.body && res.body.error) || ('HTTP ' + res.status);
        var itemsHtml = '';
        // mem3dg-readdy friction #34: when the server returns the per-item
        // breakdown, render each item's reason so the user has an
        // actionable next step instead of an opaque "no variants to queue".
        var items = res.body && Array.isArray(res.body.items) ? res.body.items : [];
        if (items.length) {
          itemsHtml = '<details class="inv-run-error-detail" style="margin-top:8px"><summary style="cursor:pointer;font-size:0.85em">Per-item reasons (' + items.length + ')</summary>'
            + '<table style="width:100%;font-size:0.83em;margin-top:6px;border-collapse:collapse">'
            + '<thead><tr><th style="text-align:left;padding:4px 8px;background:#f3f4f6">Study</th><th style="text-align:left;padding:4px 8px;background:#f3f4f6">Variant</th><th style="text-align:left;padding:4px 8px;background:#f3f4f6">Status</th><th style="text-align:left;padding:4px 8px;background:#f3f4f6">Reason</th></tr></thead><tbody>'
            + items.map(function(it) {
                return '<tr>'
                  + '<td style="padding:4px 8px;border-bottom:1px solid #e5e7eb">' + _h(it.study || '?') + '</td>'
                  + '<td style="padding:4px 8px;border-bottom:1px solid #e5e7eb">' + _h(it.variant || '?') + '</td>'
                  + '<td style="padding:4px 8px;border-bottom:1px solid #e5e7eb"><span class="status-pill ' + _h(it.status || '?') + '" style="font-size:0.78em">' + _h(it.status || '?') + '</span></td>'
                  + '<td style="padding:4px 8px;border-bottom:1px solid #e5e7eb;color:#6b7280">' + _h(it.error || '—') + '</td>'
                  + '</tr>';
              }).join('')
            + '</tbody></table></details>';
        }
        if (panel) panel.innerHTML = '<div class="inv-run-progress-banner inv-run-error">Failed to queue: ' + _h(msg) + itemsHtml + '</div>';
        if (btn) { btn.disabled = false; btn.textContent = '▶ Run unblocked'; }
        return;
      }
      var jobId = res.body.job_id;
      _vivRenderRunProgress(res.body);
      _vivPollRunProgress(jobId);
    }).catch(function(err) {
      if (panel) panel.innerHTML = '<div class="inv-run-progress-banner inv-run-error">Network error: ' + _h(String(err)) + '</div>';
      if (btn) { btn.disabled = false; btn.textContent = '▶ Run unblocked'; }
    });
  }
  window._runUnblockedSimulations = _runUnblockedSimulations;

  function _vivPollRunProgress(jobId) {
    if (_vivRunUnblockedTimer) clearTimeout(_vivRunUnblockedTimer);
    function tick() {
      fetch('/api/investigation-run-unblocked-status?job_id=' + encodeURIComponent(jobId))
        .then(function(r) { return r.json().then(function(j) { return {ok: r.ok, body: j}; }); })
        .then(function(res) {
          if (!res.ok) return;
          _vivRenderRunProgress(res.body);
          if (res.body.status === 'done' || res.body.status === 'failed') {
            var btn = document.getElementById('investigation-run-unblocked');
            if (btn) { btn.disabled = false; btn.textContent = '▶ Run unblocked'; }
            // Refresh the investigation so new runs surface in charts.
            if (typeof _refreshInvestigationDetail === 'function') {
              setTimeout(_refreshInvestigationDetail, 500);
            }
            return;
          }
          _vivRunUnblockedTimer = setTimeout(tick, 2000);
        });
    }
    tick();
  }

  function _vivRenderRunProgress(job) {
    var panel = document.getElementById('investigation-run-progress');
    if (!panel) return;
    var items = (job.items || []).map(function(it) {
      var statusCls = 'inv-run-item inv-run-' + (it.status || 'queued');
      var icon = ({queued: '⋯', running: '▶', done: '✓', failed: '✗',
                   blocked: '⛔', skipped: '—'})[it.status] || '?';
      var err = it.error ? ' <span class="inv-run-err">' + _h(it.error) + '</span>' : '';
      return '<div class="' + statusCls + '">'
        + '<span class="inv-run-icon">' + icon + '</span>'
        + '<code>' + _h(it.study) + '</code>'
        + ' <span class="inv-run-arrow">›</span> '
        + '<code>' + _h(it.variant) + '</code>'
        + err
        + '</div>';
    }).join('');
    var prog = job.progress || {total: 0, done: 0, running: 0};
    var headline;
    if (job.status === 'done') {
      headline = '<strong>✓ All done.</strong> ' + prog.done + ' / ' + prog.total + ' runs completed.';
    } else if (job.status === 'failed') {
      headline = '<strong>✗ Job failed.</strong> ' + prog.done + ' / ' + prog.total + ' attempted.';
    } else {
      headline = '<strong>Running…</strong> ' + prog.done + ' / ' + prog.total + ' complete' +
                 (prog.running ? ' · ' + prog.running + ' in flight' : '');
    }
    panel.innerHTML = '<div class="inv-run-progress-banner">' + headline + '</div>'
                    + '<div class="inv-run-list">' + items + '</div>';
  }

  // Manual refresh: re-fetch /api/iset/<current> + re-render. Use after editing
  // investigation.yaml / study.yaml files directly on disk (which the dashboard
  // has no other way to learn about — there's no file watcher or auto-poll).
  function _refreshInvestigationDetail() {
    var name = window._currentIset;
    if (!name) return;
    var btn = document.getElementById('investigation-detail-refresh');
    if (btn) { btn.disabled = true; btn.textContent = '↻ Refreshing…'; }
    try {
      _openInvestigationDetail(name);
    } finally {
      // _openInvestigationDetail kicks off an async fetch; restore the button
      // shortly after so the user sees the click registered.
      setTimeout(function() {
        if (btn) { btn.disabled = false; btn.textContent = '↻ Refresh'; }
      }, 400);
    }
  }
  window._refreshInvestigationDetail = _refreshInvestigationDetail;

  function _closeInvestigationDetail() {
    window._currentIset = null;
    document.getElementById('investigations-list').style.display = '';
    document.getElementById('investigation-detail-view').style.display = 'none';
  }
  window._closeInvestigationDetail = _closeInvestigationDetail;

  // W13 — canonical DAG-edge read. The server already feeds
  // normalize_dag_edges() output into each study's `parent_studies` key
  // (carrying study/condition/relation/outputs_used), but prefer the raw
  // canonical Pass A field `pipeline_gate.prerequisites` when a full spec is
  // present so the renderer always reads the canonical location, never the
  // legacy `parent_studies` field directly.
  function _dagEdges(s) {
    var pg = s && s.pipeline_gate;
    var raw = (pg && pg.prerequisites && pg.prerequisites.length)
                ? pg.prerequisites
                : ((s && s.parent_studies) || []);
    var out = [];
    (raw || []).forEach(function(entry) {
      if (typeof entry === 'string') {
        out.push({ study: entry, condition: 'tests-passed', relation: 'leads-to' });
      } else if (entry && entry.study) {
        var e = {};
        for (var k in entry) { if (entry.hasOwnProperty(k)) e[k] = entry[k]; }
        if (!e.condition) e.condition = 'tests-passed';
        if (!e.relation) {
          e.relation = (e.outputs_used && e.outputs_used.length) ? 'model-input' : 'leads-to';
        }
        out.push(e);
      }
    });
    return out;
  }
  // W13 — edge-relation vocabulary → stroke styling + legend label.
  var _DAG_REL_STYLE = {
    'leads-to':             { color: '#94a3b8', dash: null,  label: 'leads to' },
    'model-input':          { color: '#2563eb', dash: null,  label: 'model input' },
    'evidence':             { color: '#0d9488', dash: '5 3', label: 'evidence' },
    'calibrates-threshold': { color: '#ca8a04', dash: '2 3', label: 'calibrates threshold' },
    'refutes-alternative':  { color: '#dc2626', dash: '5 3', label: 'refutes alternative' },
  };
  function _dagRelStyle(rel) {
    // Map legacy aliases onto the canonical vocabulary.
    if (rel === 'regulatory') rel = 'calibrates-threshold';
    if (rel === 'refutes')    rel = 'refutes-alternative';
    if (rel === 'leads to')   rel = 'leads-to';
    return _DAG_REL_STYLE[rel] || _DAG_REL_STYLE['leads-to'];
  }

  // Layout + render the DAG of study nodes for the active investigation.
  // VERTICAL flow: y = topological depth (top = roots), x = within-depth slot.
  // Cards as absolute-positioned <div>s; edges as SVG cubic-Bezier paths.
  function _renderInvestigationDag(studies) {
    var nodesHost = document.getElementById('investigation-dag-nodes');
    var edgesSvg  = document.getElementById('investigation-dag-edges');
    nodesHost.innerHTML = '';
    edgesSvg.innerHTML  = '';

    if (!studies.length) {
      nodesHost.innerHTML = '<p class="empty-state" style="padding:24px">No studies in this investigation.</p>';
      return;
    }

    // Build name->study + child map.
    var byName = {};
    var children = {};
    studies.forEach(function(s) { byName[s.name] = s; children[s.name] = []; });
    studies.forEach(function(s) {
      _dagEdges(s).forEach(function(p) {
        var pn = p.study;
        if (children[pn]) children[pn].push(s.name);
      });
    });

    // BFS depth from roots.
    var depth = {};
    var queue = [];
    studies.forEach(function(s) {
      if (!_dagEdges(s).length) { depth[s.name] = 0; queue.push(s.name); }
    });
    var guard = studies.length * 4;
    while (queue.length && guard-- > 0) {
      var n = queue.shift();
      (children[n] || []).forEach(function(c) {
        if (depth[c] === undefined || depth[c] < depth[n] + 1) {
          depth[c] = depth[n] + 1;
          queue.push(c);
        }
      });
    }
    studies.forEach(function(s) { if (depth[s.name] === undefined) depth[s.name] = 0; });

    // Bin by depth.
    var byDepth = {};
    studies.forEach(function(s) {
      var d = depth[s.name];
      (byDepth[d] = byDepth[d] || []).push(s);
    });
    Object.keys(byDepth).forEach(function(d) {
      byDepth[d].sort(function(a, b) { return a.name.localeCompare(b.name); });
    });

    // Horizontal layout (depth flows left->right). Card HEIGHT is NOT fixed:
    // each card grows to fit its full text. We render once, measure each card,
    // then stack + center the columns by the measured heights (two passes) so
    // nothing is clipped.
    var CARD_W = 210;
    var X_GAP = 64, Y_GAP = 22;
    var PAD_X = 24, PAD_Y = 16;
    var svgNS = 'http://www.w3.org/2000/svg';
    var pos = {};
    var depths = Object.keys(byDepth).map(Number).sort(function(a, b) { return a - b; });

    // -- Pass 1: build every card at its column x (top TBD), append, measure --
    studies.forEach(function(s) {
      var liveStatus = s.effective_status || s.status || 'planned';
      var confidence = s.confidence || (function(st) {
        if (st === 'completed' || st === 'complete' || st === 'ran') return 'Accepted';
        if (st === 'in_progress' || st === 'running') return 'Investigating';
        if (st === 'failed' || st === 'invalid') return 'Refuted';
        return 'Planned';
      })(liveStatus);
      var ss = ({
        Accepted:      {color: '#16a34a', icon: '✓'},
        Investigating: {color: '#ca8a04', icon: '◐'},
        Planned:       {color: '#2563eb', icon: '○'},
        Refuted:       {color: '#dc2626', icon: '✗'},
      })[confidence] || {color: '#9ca3af', icon: '○'};
      var followUps = s.follow_up_studies || [];

      // Single display name everywhere: authored title:, else the shared
      // _humanizeStudyName derivation (same as control panel + study page).
      var prettyTitle = s.title || _humanizeStudyName(s.name).title;
      // Show the FULL question + claim (no truncation) — the card grows to fit.
      var asks = (s.question || '').replace(/\s+/g, ' ').split(/[.?]/)[0].trim();
      var findings = s.findings || [];
      var claim = (s.claim ||
        (findings[0] && (findings[0].summary || findings[0].statement || findings[0].id)) || ''
      ).replace(/\s+/g, ' ').trim();
      var moreN = findings.length > 1 ? findings.length - 1 : 0;

      var node = document.createElement('div');
      node.className = 'iset-dag-node';
      node.onclick = function() { _openStudyInsideInvestigation(s.name); };
      node.title = s.name + ' — ' + confidence;
      var x = PAD_X + depth[s.name] * (CARD_W + X_GAP);
      node.style.cssText =
        'position:absolute;left:' + x + 'px;top:0px;' +
        'width:' + CARD_W + 'px;' +
        'background:#fff;border:1px solid #e5e7eb;border-top:3px solid ' + ss.color + ';' +
        'border-radius:8px;padding:10px 12px;cursor:pointer;box-sizing:border-box;' +
        'box-shadow:0 1px 2px rgba(0,0,0,0.05);transition:box-shadow 0.1s,border-color 0.1s;';

      var followUpsChip = '';
      if (s.phase === 'Decide' && followUps.length) {
        followUpsChip =
          '<button class="dag-followups-btn" ' +
          'onclick="event.stopPropagation(); _openDagFollowupsPopover(\'' + _esc(s.name) + '\', this)" ' +
          'style="margin-top:8px;font-size:0.68em;padding:2px 7px;border:1px solid #10b981;background:#d1fae5;color:#065f46;border-radius:9999px;cursor:pointer">' +
          '▸ ' + followUps.length + ' follow-up' + (followUps.length === 1 ? '' : 's') +
          '</button>';
      }
      node.innerHTML =
        '<div style="display:flex;align-items:flex-start;gap:6px">' +
          '<span style="color:' + ss.color + ';font-size:1.05em;line-height:1.1;flex:none">' + ss.icon + '</span>' +
          '<strong style="font-size:0.85em;line-height:1.25;color:#1e293b;flex:1">' + _esc(prettyTitle) + '</strong>' +
          '<span style="font-size:0.62em;font-weight:700;color:' + ss.color + ';white-space:nowrap;margin-top:1px">' + _esc(confidence) + '</span>' +
        '</div>' +
        (asks
          ? '<div style="font-size:0.72em;margin-top:7px;line-height:1.35;color:#64748b">' +
              '<span style="font-weight:600;color:#475569">Asks:</span> ' + _esc(asks) + '</div>'
          : '') +
        '<div style="font-size:0.72em;margin-top:5px;line-height:1.35;color:#64748b">' +
          '<span style="font-weight:600;color:#475569">Finds:</span> ' +
          (claim ? _esc(claim) : '<em style="color:#94a3b8">pending evidence</em>') +
          (moreN ? ' <span style="color:#94a3b8">+' + moreN + ' more</span>' : '') +
        '</div>' +
        followUpsChip;
      node._followUps = followUps;
      nodesHost.appendChild(node);
      pos[s.name] = { x: x, node: node, depth: depth[s.name] };
    });

    // Measure now that content is in the DOM (container is already visible).
    studies.forEach(function(s) { pos[s.name].h = pos[s.name].node.offsetHeight || 120; });

    // -- Pass 2: stack each column vertically by measured height, then center --
    var colTotals = {};
    depths.forEach(function(d) {
      var sum = 0;
      byDepth[d].forEach(function(s) { sum += pos[s.name].h; });
      colTotals[d] = sum + Math.max(0, byDepth[d].length - 1) * Y_GAP;
    });
    var maxCol = 0;
    depths.forEach(function(d) { if (colTotals[d] > maxCol) maxCol = colTotals[d]; });
    var canvasH = Math.max(PAD_Y * 2 + maxCol, 180);
    depths.forEach(function(d) {
      var yc = PAD_Y + Math.max(0, (canvasH - PAD_Y * 2 - colTotals[d]) / 2);
      byDepth[d].forEach(function(s) {
        pos[s.name].y = yc;
        pos[s.name].node.style.top = yc + 'px';
        yc += pos[s.name].h + Y_GAP;
      });
    });
    var canvasW = PAD_X * 2 + (depths.length ? depths[depths.length - 1] : 0) * (CARD_W + X_GAP) + CARD_W;

    nodesHost.style.width = canvasW + 'px';
    nodesHost.style.height = canvasH + 'px';
    edgesSvg.setAttribute('width', canvasW);
    edgesSvg.setAttribute('height', canvasH);
    edgesSvg.style.width = canvasW + 'px';
    edgesSvg.style.height = canvasH + 'px';
    var shellSize = document.getElementById('investigation-dag-shell');
    if (shellSize) shellSize.style.height = canvasH + 'px';

    // Edges (drawn after positions are known), using measured heights.
    edgesSvg.innerHTML =
      '<defs><marker id="dag-arrowhead" viewBox="0 0 10 10" refX="9" refY="5" ' +
      'markerWidth="7" markerHeight="7" orient="auto-start-reverse">' +
      '<path d="M 0 0 L 10 5 L 0 10 z" fill="#94a3b8"/></marker></defs>';
    studies.forEach(function(s) {
      _dagEdges(s).forEach(function(p) {
        var pn = p.study;
        if (!pos[pn] || !pos[s.name]) return;
        var x1 = pos[pn].x + CARD_W;
        var y1 = pos[pn].y + pos[pn].h / 2;
        var x2 = pos[s.name].x;
        var y2 = pos[s.name].y + pos[s.name].h / 2;
        var dx = Math.max(28, (x2 - x1) / 2);
        var rel = p.relation || 'leads-to';
        var st = _dagRelStyle(rel);
        var path = document.createElementNS(svgNS, 'path');
        path.setAttribute('d', 'M ' + x1 + ' ' + y1 +
                              ' C ' + (x1 + dx) + ' ' + y1 +
                              ', ' + (x2 - dx) + ' ' + y2 +
                              ', ' + x2 + ' ' + y2);
        path.setAttribute('fill', 'none');
        path.setAttribute('stroke', st.color);
        path.setAttribute('stroke-width', '1.5');
        path.setAttribute('marker-end', 'url(#dag-arrowhead)');
        if (st.dash) path.setAttribute('stroke-dasharray', st.dash);
        edgesSvg.appendChild(path);
        var labelText = st.label;
        // model-input edges name the consumed upstream outputs when present.
        if (rel === 'model-input' && p.outputs_used && p.outputs_used.length) {
          labelText += ' (' + p.outputs_used.join(', ') + ')';
        }
        var label = document.createElementNS(svgNS, 'text');
        label.setAttribute('x', (x1 + x2) / 2);
        label.setAttribute('y', (y1 + y2) / 2 - 6);
        label.setAttribute('text-anchor', 'middle');
        label.setAttribute('font-size', '10');
        label.setAttribute('fill', st.color);
        label.textContent = labelText;
        edgesSvg.appendChild(label);
      });
    });

    // Auto-scroll the shell so the top of the DAG is in view.
    var shell = document.getElementById('investigation-dag-shell');
    if (shell) shell.scrollTop = 0;

    // Legend (status colors + edge types) — created once below the shell.
    var legendHost = document.getElementById('investigation-dag-legend');
    if (!legendHost && shell && shell.parentNode) {
      legendHost = document.createElement('div');
      legendHost.id = 'investigation-dag-legend';
      shell.parentNode.insertBefore(legendHost, shell.nextSibling);
    }
    if (legendHost) {
      var _lg = function(color, icon, label) {
        return '<span style="display:inline-flex;align-items:center;gap:4px;margin-right:14px">' +
          '<span style="color:' + color + ';font-size:1em">' + icon + '</span>' +
          '<span>' + label + '</span></span>';
      };
      legendHost.style.cssText = 'display:flex;flex-wrap:wrap;align-items:center;' +
        'font-size:0.74em;color:#64748b;padding:8px 4px 0;border-top:1px solid #f1f5f9;margin-top:8px';
      // W13 — edge-relation legend swatches (colored solid/dashed lines).
      var _edgeLg = function(rel) {
        var st = _dagRelStyle(rel);
        var line = 'border-bottom:2px ' + (st.dash ? 'dashed' : 'solid') + ' ' + st.color;
        return '<span style="display:inline-flex;align-items:center;gap:5px;margin-right:12px">' +
          '<span style="width:18px;' + line + ';display:inline-block;line-height:0">&nbsp;</span>' +
          '<span>' + st.label + '</span></span>';
      };
      legendHost.innerHTML =
        '<span style="font-weight:600;color:#475569;margin-right:10px">Confidence:</span>' +
        _lg('#16a34a', '✓', 'Accepted') + _lg('#ca8a04', '◐', 'Investigating') +
        _lg('#2563eb', '○', 'Planned') + _lg('#dc2626', '✗', 'Refuted') +
        '<span style="flex-basis:100%;height:0"></span>' +
        '<span style="font-weight:600;color:#475569;margin:6px 10px 0 0">Edges:</span>' +
        '<span style="margin-top:6px">' +
          _edgeLg('leads-to') + _edgeLg('model-input') + _edgeLg('evidence') +
          _edgeLg('calibrates-threshold') + _edgeLg('refutes-alternative') +
        '</span>';
    }
  }
  window._renderInvestigationDag = _renderInvestigationDag;

  // ── DAG follow-ups popover ───────────────────────────────────────────────
  // Surfaced when phase=Decide. Lists each follow_up_studies entry with a
  // "Seed →" button that POSTs to /api/study-seed-followup (existing
  // endpoint) and navigates to the newly-created child study.
  function _openDagFollowupsPopover(studyName, anchorBtn) {
    // Find this study's follow-ups from the most recent iset payload.
    var isetStudies = (window._currentIsetData && window._currentIsetData.studies) || [];
    var match = null;
    for (var i = 0; i < isetStudies.length; i++) {
      if (isetStudies[i].name === studyName) { match = isetStudies[i]; break; }
    }
    // Prefer the richer discovery_implications.followup_study_proposals;
    // fall back to legacy follow_up_studies for back-compat.
    var di = (match && match.discovery_implications) || {};
    var proposals = di.followup_study_proposals || [];
    var usingProposals = proposals.length > 0;
    var followUps = usingProposals ? proposals : ((match && match.follow_up_studies) || []);
    if (!followUps.length) {
      alert('No follow-ups recorded for ' + studyName + '.');
      return;
    }
    // Close any existing popover
    var prior = document.getElementById('dag-followups-popover');
    if (prior) prior.remove();

    var pop = document.createElement('div');
    pop.id = 'dag-followups-popover';
    var rect = anchorBtn.getBoundingClientRect();
    pop.style.cssText =
      'position:fixed;top:' + (rect.bottom + 6) + 'px;left:' + Math.max(8, rect.left - 80) + 'px;' +
      'width:520px;max-height:60vh;overflow-y:auto;background:#fff;border:1px solid #d1d5db;' +
      'border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,0.18);z-index:1000;padding:14px;';

    var header =
      '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;">' +
        '<strong>' + _esc(studyName) + ' — follow-ups</strong>' +
        '<button onclick="document.getElementById(\'dag-followups-popover\').remove()" ' +
        'style="background:transparent;border:0;font-size:1.3em;cursor:pointer;color:#64748b">×</button>' +
      '</div>' +
      '<p style="font-size:0.85em;color:#64748b;margin:0 0 10px 0">Click <em>Seed →</em> to spawn a new child study from any entry. The new study inherits this one as a pipeline_gate prerequisite.</p>';

    var rows = followUps.map(function(f, idx) {
      // Normalize across the two shapes: legacy follow_up_studies use
      // kind/why/effort; followup_study_proposals use study_type/
      // proposed_experiment/expected_information_gain.
      var kind = f.kind || f.study_type || 'other';
      var kindColors = {
        infrastructure_fix: {bg: '#fef2f2', fg: '#991b1b', border: '#dc2626'},
        calibration_task:   {bg: '#fefce8', fg: '#92400e', border: '#f59e0b'},
        expert_question:    {bg: '#faf5ff', fg: '#6b21a8', border: '#a855f7'},
        existing:           {bg: '#eff6ff', fg: '#1e40af', border: '#3b82f6'},
        new:                {bg: '#f0fdf4', fg: '#065f46', border: '#10b981'},
        other:              {bg: '#f8fafc', fg: '#475569', border: '#94a3b8'},
      };
      var kc = kindColors[kind] || kindColors.other;
      var canSeed = kind !== 'existing';
      var seedCall = usingProposals
        ? '_seedFollowupProposal(\'' + _esc(studyName) + '\', ' + JSON.stringify(f.id != null ? String(f.id) : '') + ', ' + idx + ', this)'
        : '_seedFollowupAndOpen(\'' + _esc(studyName) + '\', ' + idx + ')';
      var seedBtn = canSeed
        ? '<button onclick="event.stopPropagation(); ' + seedCall + '" ' +
          'style="font-size:0.8em;padding:3px 10px;border:1px solid ' + kc.border + ';background:#fff;color:' + kc.fg +
          ';border-radius:4px;cursor:pointer;white-space:nowrap">Seed →</button>'
        : '<span style="font-size:0.78em;color:#64748b;font-style:italic">(existing study)</span>';
      var statusBadge = f.status
        ? '<span style="font-size:0.7em;padding:1px 6px;border-radius:9999px;background:#fef3c7;color:#92400e;margin-left:6px">' + _esc(f.status) + '</span>'
        : '';
      var effortText = f.effort || f.expected_information_gain;
      var effortBadge = effortText
        ? '<span style="font-size:0.7em;padding:1px 6px;border-radius:9999px;background:#e0e7ff;color:#3730a3;margin-left:6px;font-family:monospace">' + _esc(effortText) + '</span>'
        : '';
      var whyText = f.why || f.proposed_experiment || '';
      var why = whyText
        ? '<div style="font-size:0.83em;color:#475569;margin-top:4px;line-height:1.4">' + _esc(whyText.slice(0, 280)) + (whyText.length > 280 ? '…' : '') + '</div>'
        : '';
      return '<div style="padding:10px 12px;border:1px solid ' + kc.border + ';border-left:4px solid ' + kc.border +
             ';border-radius:4px;background:' + kc.bg + ';margin-bottom:8px">' +
               '<div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start">' +
                 '<div style="flex:1;min-width:0">' +
                   '<span style="font-size:0.7em;text-transform:uppercase;letter-spacing:0.05em;padding:1px 8px;border-radius:9999px;background:#fff;color:' + kc.fg + '">' + _esc(kind) + '</span>' +
                   effortBadge + statusBadge +
                   '<div style="font-weight:600;margin-top:4px;font-size:0.93em">' + _esc(f.title || '(untitled)') + '</div>' +
                   why +
                 '</div>' +
                 seedBtn +
               '</div>' +
             '</div>';
    }).join('');

    pop.innerHTML = header + rows;
    document.body.appendChild(pop);

    // Click-outside to close
    setTimeout(function() {
      document.addEventListener('click', function _closer(e) {
        if (!pop.contains(e.target)) {
          pop.remove();
          document.removeEventListener('click', _closer);
        }
      });
    }, 0);
  }
  window._openDagFollowupsPopover = _openDagFollowupsPopover;

  // Seed-then-open helper used by the popover. Shares the POST endpoint with
  // the study-detail page's _seedFollowupStudy (in study-detail.js) so both
  // surfaces converge on the same backend.
  function _seedFollowupAndOpen(parentName, idx) {
    if (!confirm('Seed a new study from this follow-up?\n\nA new study.yaml will be created under studies/<new-name>/.')) return;
    fetch('/api/study-seed-followup', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({parent: parentName, followup_idx: idx}),
    }).then(function(r) { return r.json().then(function(d) { return {status: r.status, body: d}; }); })
      .then(function(res) {
        if (res.status !== 200 || res.body.error) {
          alert('Seed failed: ' + (res.body.error || res.status));
          return;
        }
        var pop = document.getElementById('dag-followups-popover');
        if (pop) pop.remove();
        alert('Created: ' + res.body.new_study_name + '\nOpening it now.');
        window.location.href = '/studies/' + encodeURIComponent(res.body.new_study_name);
      });
  }
  window._seedFollowupAndOpen = _seedFollowupAndOpen;

  // Seed a child study from a discovery_implications.followup_study_proposals
  // entry (the richer successor to follow_up_studies). Identifies the proposal
  // by id (preferred) or index. On success, refreshes the current
  // investigation so the new node appears in the graph (no full navigation —
  // the expert stays in the investigation they're working in).
  function _seedFollowupProposal(parentName, proposalId, proposalIdx, btn) {
    if (!confirm('Spawn a new study node from this follow-up proposal?\n\n'
        + 'A new study.yaml will be created under studies/<new-name>/ with a '
        + 'leads-to edge back to ' + parentName + '.')) return;
    var origText = btn ? btn.textContent : null;
    if (btn) { btn.disabled = true; btn.textContent = '… seeding'; }
    var payload = {parent: parentName};
    if (proposalId) payload.proposal_id = proposalId;
    payload.proposal_idx = proposalIdx;
    fetch('/api/study-seed-followup', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    }).then(function(r) { return r.json().then(function(d) { return {status: r.status, body: d}; }); })
      .then(function(res) {
        if (res.status !== 200 || res.body.error) {
          alert('Seed failed: ' + (res.body.error || res.status));
          if (btn) { btn.disabled = false; btn.textContent = origText; }
          return;
        }
        var pop = document.getElementById('dag-followups-popover');
        if (pop) pop.remove();
        if (btn) { btn.textContent = '✓ added'; }
        // Refresh the investigation view so the new node + edge render.
        if (window._currentIset && typeof _openInvestigationDetail === 'function') {
          _openInvestigationDetail(window._currentIset);
        } else {
          alert('Created: ' + res.body.new_study_name);
        }
      })
      .catch(function(err) {
        alert('Seed failed: ' + err);
        if (btn) { btn.disabled = false; btn.textContent = origText; }
      });
  }
  window._seedFollowupProposal = _seedFollowupProposal;

  // Click a DAG node → load the full study in an in-page iframe BELOW the
  // DAG (no jump to the legacy Studies tab). The iframe is the same
  // /studies/<name> route the standalone embed uses.
  function _openStudyInsideInvestigation(name) {
    var panel = document.getElementById('investigation-study-embed-panel');
    var frame = document.getElementById('investigation-study-embed-frame');
    var nameEl = document.getElementById('investigation-study-embed-name');
    if (!panel || !frame) {
      // This view (e.g. the report / deep-link investigation view) has no
      // in-place study-embed panel — navigate to the study page directly so the
      // sidebar study link still works instead of dying silently.
      window.location = _studyHref(name);
      return;
    }
    window._currentInvestigationStudy = name;
    frame.src = _studyHref(name);
    if (nameEl) nameEl.textContent = name;
    panel.style.display = '';
    panel.scrollIntoView({behavior: 'smooth', block: 'start'});
  }
  window._openStudyInsideInvestigation = _openStudyInsideInvestigation;

  function _closeInvestigationStudyEmbed() {
    var panel = document.getElementById('investigation-study-embed-panel');
    var frame = document.getElementById('investigation-study-embed-frame');
    if (frame) frame.src = '';
    if (panel) panel.style.display = 'none';
    window._currentInvestigationStudy = null;
  }
  window._closeInvestigationStudyEmbed = _closeInvestigationStudyEmbed;

  function _popoutInvestigationStudy() {
    var name = window._currentInvestigationStudy;
    if (!name) return;
    var w = _openDetachedWindow(_studyHref(name), 1200, 800);
    if (!w) {
      console.warn('_popoutInvestigationStudy: popup blocked');
      alert('Popup blocked. Allow popups from this site to pop out the study view.');
    }
  }
  window._popoutInvestigationStudy = _popoutInvestigationStudy;

  // Build a self-contained HTML report of the current investigation and
  // trigger a download. The report is for sharing with a domain expert
  // (over email) BEFORE simulations run — so it surfaces the predictions,
  // assumptions, and gaps in a form that lets the expert validate the
  // design without needing the dashboard.
  function _generateInvestigationReport() {
    var name = window._currentIset;
    if (!name) {
      console.warn('_generateInvestigationReport: no current investigation');
      return;
    }
    var btn = event && event.target;
    var orig = btn ? btn.textContent : null;
    if (btn) { btn.textContent = 'Generating…'; btn.disabled = true; }
    // Use window.DataSource.loadInvestigation if available (client-fetch seam,
    // sub-project #1).  Falls back to a direct fetch so local mode is unchanged.
    var _isetFetch = (window.DataSource && window.DataSource.loadInvestigation)
      ? window.DataSource.loadInvestigation(name)
      : fetch('/api/iset/' + encodeURIComponent(name), {headers: {Accept: 'application/json'}})
          .then(function(r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); });
    _isetFetch
      .then(function(iset) {
        var studyFetches = (iset.studies || []).map(function(s) {
          return ((window.DataSource && window.DataSource.loadStudy)
            ? window.DataSource.loadStudy(s.name)
            : fetch('/api/study/' + encodeURIComponent(s.name))
                .then(function(r) { return r.ok ? r.json() : {spec: {name: s.name, error: 'load-failed'}}; })
                .then(function(j) { return j.spec || j; })
          );
        });
        var bibFetch = fetch('/api/references-bib')
          .then(function(r) { return r.ok ? r.json() : {entries: []}; })
          .then(function(j) { return j.entries || []; })
          .catch(function() { return []; });
        var chartFetches = (iset.studies || []).map(function(s) {
          return fetch('/api/study-charts/' + encodeURIComponent(s.name))
            .then(function(r) { return r.ok ? r.json() : {charts: []}; })
            .then(function(j) { return {name: s.name, charts: j.charts || []}; })
            .catch(function() { return {name: s.name, charts: []}; });
        });
        // Current coordinated generation — stamps the report's provenance
        // banner (expert-feedback A.3). Best-effort: null when none active.
        var genFetch = fetch('/api/generation')
          .then(function(r) { return r.ok ? r.json() : {generation: null}; })
          .then(function(j) { return (j && j.generation) || null; })
          .catch(function() { return null; });
        // Workspace GitHub repo (owner/name) — injected into the exported
        // report's inline-feedback widget so its "Open GitHub issue" button
        // pre-fills against the right repo with no reviewer prompt. Best-
        // effort: null when the workspace has no GitHub origin (widget then
        // falls back to host-detection / a one-time prompt).
        var ghRepoFetch = fetch('/api/github-repo')
          .then(function(r) { return r.ok ? r.json() : {repo: null}; })
          .then(function(j) { return (j && j.repo) || null; })
          .catch(function() { return null; });
        // Evidence & rigor roll-up — deterministic skeptic-feedback computed
        // by pbg_superpowers.rigor (replication, controls, alternatives,
        // claim discipline, falsifiability, adversarial coverage).
        var rigorFetch = fetch('/api/investigation-rigor?investigation=' + encodeURIComponent(iset.name))
          .then(function(r) { return r.ok ? r.json() : null; })
          .catch(function() { return null; });
        // Wave 3a #26 — framework-self metrics across every study + investigation
        // (deterministic, pbg_superpowers.rigor.framework_metrics). Renders the
        // "Framework scorecard" section. Best-effort: null → section omitted.
        var fmFetch = fetch('/api/framework-metrics')
          .then(function(r) { return r.ok ? r.json() : null; })
          .catch(function() { return null; });
        // Wave 3b #6/#16 — competing hypotheses with the COMPUTED support_log
        // (pbg_superpowers.hypotheses.rollup_support, via the report-data path).
        // Best-effort: [] → the panel falls back to authored iset.hypotheses.
        var hypFetch = fetch('/api/investigation-hypotheses?investigation=' + encodeURIComponent(iset.name))
          .then(function(r) { return r.ok ? r.json() : null; })
          .then(function(j) { return (j && j.hypotheses) || null; })
          .catch(function() { return null; });
        return Promise.all([Promise.all(studyFetches), bibFetch,
                            Promise.all(chartFetches), genFetch,
                            ghRepoFetch, rigorFetch, fmFetch, hypFetch]).then(function(arr) {
          var chartsByStudy = {};
          arr[2].forEach(function(c) { chartsByStudy[c.name] = c.charts; });
          var generation = arr[3];
          var ghRepo = arr[4];
          var rigor = arr[5];
          var frameworkMetrics = arr[6];
          var hypotheses = arr[7];
          // Second pass: now that we have the specs, fetch each study's
          // embed_visualizations URLs so the downloaded report can inline
          // them as <iframe srcdoc="...">. This makes the file truly
          // self-contained — works offline because the full preview HTML
          // (incl. its Plotly CDN <script src>) is embedded inline.
          var specs = arr[0];
          var embedFetches = specs.map(function(spec) {
            var embeds = (spec && spec.embed_visualizations) || [];
            var perStudy = embeds.map(function(embed) {
              if (!embed || !embed.url) return Promise.resolve(null);
              return fetch(embed.url, {headers: {Accept: 'text/html'}})
                .then(function(r) { return r.ok ? r.text() : null; })
                .then(function(text) {
                  return text ? {
                    name: embed.name || '',
                    description: embed.description || '',
                    url: embed.url,
                    html: text,
                    stale: embed.stale === true,
                  } : null;
                })
                .catch(function() { return null; });
            });
            return Promise.all(perStudy).then(function(results) {
              return {name: spec && spec.name, embeds: results.filter(Boolean)};
            });
          });
          return Promise.all(embedFetches).then(function(embedResults) {
            var embedsByStudy = {};
            embedResults.forEach(function(e) {
              if (e && e.name) embedsByStudy[e.name] = e.embeds;
            });
            return {iset: iset, specs: specs, bibEntries: arr[1],
                    chartsByStudy: chartsByStudy, embedsByStudy: embedsByStudy,
                    generation: generation, ghRepo: ghRepo, rigor: rigor,
                    frameworkMetrics: frameworkMetrics, hypotheses: hypotheses};
          });
        });
      })
      .then(function(bundle) {
        var html = _buildInvestigationReportHtml(bundle.iset, bundle.specs,
                                                  bundle.bibEntries, bundle.chartsByStudy,
                                                  bundle.embedsByStudy, bundle.generation,
                                                  bundle.ghRepo, bundle.rigor,
                                                  bundle.frameworkMetrics, bundle.hypotheses);
        var dateStr = new Date().toISOString().slice(0, 10);
        var filename = 'investigation-' + name + '-' + dateStr + '.html';
        _triggerDownload(filename, html, 'text/html');
      })
      .catch(function(err) {
        console.error('report generation failed', err);
        alert('Report generation failed: ' + err);
      })
      .finally(function() {
        if (btn) { btn.textContent = orig; btn.disabled = false; }
      });
  }
  window._generateInvestigationReport = _generateInvestigationReport;

  function _triggerDownload(filename, content, mime) {
    var blob = new Blob([content], {type: mime || 'text/plain'});
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    setTimeout(function() {
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    }, 0);
  }
  window._triggerDownload = _triggerDownload;

  function _h(s) {
    if (s == null) return '';
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function _multiline(s) {
    if (s == null) return '';
    // YAML | block scalars carry hard newlines. Treat blank-line breaks as
    // paragraph spacing; single newlines as soft (space) so prose reflows
    // at the rendered column width instead of stuck at the YAML wrap.
    return _h(s).replace(/\n\s*\n/g, '<br><br>').replace(/\n/g, ' ');
  }

  // Small unobtrusive badge reflecting a chart's run→viz freshness. The
  // freshness field is computed server-side (lib/viz_freshness.chart_freshness)
  // and carried on each static chart object in the study-charts payload.
  //   fresh      → ✓ latest run   (green/muted)
  //   stale      → ⚠ stale        (amber; names the recorded source run when known)
  //   untracked  → ❓ untracked
  //   unrendered → ◌ not rendered
  function _freshnessBadge(c) {
    var f = c && c.freshness;
    if (!f) return '';
    var label, color, bg;
    if (f === 'fresh')        { label = '✓ latest run'; color = '#065f46'; bg = '#d1fae5'; }
    else if (f === 'stale')   {
      var src = (c.meta && (c.meta.source_run_id || c.meta.run_id)) || c.source_run_id;
      label = '⚠ stale' + (src ? ' (' + _h(src) + ')' : '');
      color = '#92400e'; bg = '#fef3c7';
    }
    else if (f === 'untracked')  { label = '❓ untracked';   color = '#475569'; bg = '#f1f5f9'; }
    else if (f === 'unrendered') { label = '◌ not rendered'; color = '#475569'; bg = '#f1f5f9'; }
    else return '';
    return '<span class="chart-freshness-badge" style="display:inline-block;'
      + 'margin-left:8px;padding:1px 7px;border-radius:10px;font-size:11px;'
      + 'font-weight:500;vertical-align:middle;color:' + color + ';background:' + bg + ';">'
      + label + '</span>';
  }

  // Build the inner HTML of a study's chart-card list (shared by the initial
  // report/card render and the live Refresh re-render). Each card carries a
  // title row with the freshness badge, the media (inline SVG or data-URI
  // <img>), and any caption/provenance text.
  function _renderChartCardsHtml(charts, slug) {
    return (charts || []).map(function(c, i) {
      // Per-figure annotation host: a "study-...-chart-..." id matches the
      // feedback ID_PATTERNS (/^study-/), so each figure gets its OWN 💬
      // comment affordance (keyed by this id), not just the section-level one.
      var cardId = 'study-' + (slug || 'x') + '-chart-'
        + String(c.key || ('fig' + i)).replace(/[^a-zA-Z0-9_-]/g, '-');
      var titleHtml = '';
      var badge = _freshnessBadge(c);
      var titleText = c.title || c.key || '';
      if (badge || titleText) {
        titleHtml = '<div class="chart-title" style="font-size:13px;font-weight:600;'
          + 'margin-bottom:4px;display:flex;align-items:center;flex-wrap:wrap;">'
          + '<span>' + _h(titleText) + '</span>' + badge + '</div>';
      }
      var capHtml = '';
      if (c.caption) capHtml += '<div class="chart-caption">' + _h(c.caption) + '</div>';
      if (c.simulations) capHtml +=
          '<div class="chart-simulations"><strong>Simulations behind this chart.</strong> '
          + _h(c.simulations) + '</div>';
      if (c.interpretation) capHtml +=
          '<div class="chart-interpretation"><strong>What it means.</strong> '
          + _h(c.interpretation) + '</div>';
      var media = c.img
        ? '<img class="chart-img" src="' + c.img + '" alt="' + _h(c.key || 'chart') + '" loading="lazy">'
        : (c.svg || '');
      return '<div class="chart-card" id="' + cardId + '">' + titleHtml + media + capHtml + '</div>';
    }).join('');
  }

  // POST /api/study-refresh-viz/<study> then re-fetch + re-render that study's
  // charts section in place. Resilient: shows a brief inline status/error and
  // never throws (a failed POST leaves the existing charts untouched).
  window._refreshStudyViz = function(btn) {
    var study = btn && btn.getAttribute('data-study');
    if (!study) return;
    var statusEl = btn.parentElement
      ? btn.parentElement.querySelector('.chart-refresh-status') : null;
    var setStatus = function(txt) { if (statusEl) statusEl.textContent = txt || ''; };
    btn.disabled = true;
    setStatus('refreshing…');
    fetch('/api/study-refresh-viz/' + encodeURIComponent(study), {method: 'POST'})
      .then(function(r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function(out) {
        var results = (out && out.results) || [];
        var errs = results.filter(function(x) { return x && x.status === 'error'; }).length;
        var ok = results.filter(function(x) { return x && x.status === 'rendered'; }).length;
        // Re-fetch the freshly-stamped charts and rebuild the section body.
        return fetch('/api/study-charts/' + encodeURIComponent(study))
          .then(function(r) { return r.ok ? r.json() : {charts: []}; })
          .then(function(j) {
            var container = document.getElementById('study-' + study + '-charts');
            if (container) {
              var cards = _renderChartCardsHtml(j.charts || [], study);
              // Replace everything after the <h3> heading (preserve the
              // heading + its Refresh button).
              var h3 = container.querySelector('h3');
              if (h3) {
                while (h3.nextSibling) container.removeChild(h3.nextSibling);
                h3.insertAdjacentHTML('afterend', cards);
                // Re-point the status element (it lives inside the preserved h3).
                statusEl = h3.querySelector('.chart-refresh-status');
              } else {
                container.innerHTML = cards;
              }
            }
            setStatus(ok + ' rendered' + (errs ? ', ' + errs + ' failed' : ''));
          });
      })
      .catch(function(e) {
        setStatus('refresh failed: ' + (e && e.message ? e.message : 'error'));
      })
      .then(function() { btn.disabled = false; });
  };

  // Construct the report's HTML body from the investigation + per-study specs.
  // Render the Evidence & rigor section from an /api/investigation-rigor payload
  // (deterministic skeptic-feedback). Returns '' when no payload (older server /
  // fetch failure) so the report degrades gracefully.
  // ── C2 — derived 3-track conclusion verdicts (read-only, computed) ─────
  // These three rules are kept IDENTICAL to single_study_report.py
  // (_derive_conclusion_verdicts) and study-detail.js so every surface
  // shows the same badge.
  var _GATE_RESULT_NORM = {
    pass: 'PASS', passed: 'PASS', ok: 'PASS',
    fail: 'FAIL', failed: 'FAIL',
    partial: 'PARTIAL', mixed: 'PARTIAL', needs_calibration: 'PARTIAL'
  };
  var _RUN_ERRORED = {error: 1, errored: 1, failed: 1, crashed: 1, fail: 1};
  var _RUN_COMPLETED = {completed: 1, complete: 1, success: 1, succeeded: 1, ok: 1, done: 1, finished: 1};
  var _TRACK_COLORS = {
    PASS: ['#dcfce7', '#166534'], PARTIAL: ['#fef3c7', '#92400e'],
    FAIL: ['#fee2e2', '#991b1b'], GAP: ['#f1f5f9', '#475569'], PENDING: ['#f1f5f9', '#475569']
  };
  function _normGateResult(v) {
    return _GATE_RESULT_NORM[String(v == null ? '' : v).trim().toLowerCase()] || 'PENDING';
  }
  // W8 — per-finding evidential-weight chip. The weight is COMPUTED SERVER-SIDE
  // (pbg_superpowers.rigor.finding_evidential_weight, carried on the finding as
  // `_evidential_weight` via the report-data path) so the SPA just renders it —
  // no JS recompute, no drift. Degrades to nothing when the field is absent.
  var _WEIGHT_CHIP_COLORS = {
    strong:   ['#dcfce7', '#166534'],
    moderate: ['#fef9c3', '#854d0e'],
    weak:     ['#fee2e2', '#991b1b']
  };
  function _findingWeightChip(w) {
    if (!w || !w.weight) return '';
    var c = _WEIGHT_CHIP_COLORS[w.weight] || ['#f1f5f9', '#475569'];
    var label = _h(w.weight) + (typeof w.n_supporting === 'number' ? ' · ' + w.n_supporting + '/5' : '');
    var title = '';
    if (w.dims) {
      var dims = []; for (var k in w.dims) { if (w.dims[k]) dims.push(k); }
      title = ' title="evidence dims: ' + _h(dims.join(', ') || 'none') + '"';
    }
    return '<span class="finding-weight"' + title + ' style="display:inline-block;'
      + 'padding:1px 8px;border-radius:9999px;background:' + c[0] + ';color:' + c[1] + ';'
      + 'font-weight:600;font-size:0.72em;margin-left:6px;vertical-align:middle">'
      + label + '</span>';
  }
  // Wave 3b — per-finding claim_scope (#21) / generality (#22) / lifecycle_state
  // (#25) chips, beside the finding's tier/weight badges. Authored on the finding;
  // the lifecycle FLOOR arrives via the report-data path as `_lifecycle_floor`
  // (server-computed by pbg_superpowers.study_verdict.lifecycle_floor). Enums
  // match the cross-repo contract + lib/single_study_report.py. Degrade to ''.
  var _CLAIM_SCOPE_COLORS = {
    'local-implementation': ['#f1f5f9', '#475569'],
    mechanism:   ['#dbeafe', '#1e40af'],
    behavioral:  ['#dcfce7', '#166534'],
    theoretical: ['#ede9fe', '#6d28d9'],
    generality:  ['#fef9c3', '#854d0e']
  };
  function _claimScopeChip(f) {
    if (!f || typeof f !== 'object') return '';
    var cs = f.claim_scope;
    if (typeof cs !== 'string' || !cs.trim()) return '';
    var v = cs.trim();
    var c = _CLAIM_SCOPE_COLORS[v] || ['#fef9c3', '#854d0e'];
    return '<span class="claim-scope" title="claim scope (critique #21)" style="display:inline-block;'
      + 'padding:1px 8px;border-radius:9999px;background:' + c[0] + ';color:' + c[1] + ';'
      + 'font-weight:600;font-size:0.72em;margin-left:6px;vertical-align:middle">scope: ' + _h(v) + '</span>';
  }
  var _GENERALITY_LEVEL_COLORS = {
    instance_specific: ['#fee2e2', '#991b1b'],
    mechanism:         ['#fef9c3', '#854d0e'],
    framework:         ['#dcfce7', '#166534']
  };
  function _generalityChip(f) {
    if (!f || typeof f !== 'object') return '';
    var g = f.generality;
    if (!g || typeof g !== 'object') return '';
    var level = (typeof g.level === 'string') ? g.level.trim() : '';
    var axes = g.axes_tested || [];
    if (typeof axes === 'string') axes = [axes];
    axes = axes.filter(Boolean).map(String);
    if (!level && !axes.length) return '';
    var c = _GENERALITY_LEVEL_COLORS[level] || ['#f1f5f9', '#475569'];
    var label = 'generality' + (level ? ': ' + level : '');
    if (axes.length) label += ' · ' + axes.length + ' ax' + (axes.length !== 1 ? 'es' : 'is');
    var title = 'generality (critique #22) — axes tested: ' + (axes.join(', ') || 'none');
    return '<span class="generality" title="' + _h(title) + '" style="display:inline-block;'
      + 'padding:1px 8px;border-radius:9999px;background:' + c[0] + ';color:' + c[1] + ';'
      + 'font-weight:600;font-size:0.72em;margin-left:6px;vertical-align:middle">' + _h(label) + '</span>';
  }
  var _LIFECYCLE_COLORS = {
    observation:              ['#f1f5f9', '#475569'],
    'candidate-explanation':  ['#e0e7ff', '#3730a3'],
    'tested-vs-alternatives': ['#dbeafe', '#1e40af'],
    'provisional-claim':      ['#fef9c3', '#854d0e'],
    generalized:              ['#dcfce7', '#166534'],
    retired:                  ['#fee2e2', '#991b1b'],
    superseded:               ['#fee2e2', '#991b1b']
  };
  function _lifecycleChip(f) {
    if (!f || typeof f !== 'object') return '';
    var authored = (typeof f.lifecycle_state === 'string' && f.lifecycle_state.trim())
      ? f.lifecycle_state.trim() : null;
    var floor = (typeof f._lifecycle_floor === 'string' && f._lifecycle_floor.trim())
      ? f._lifecycle_floor.trim() : null;
    var state = authored || floor;
    if (!state) return '';
    var c = _LIFECYCLE_COLORS[state] || ['#f1f5f9', '#475569'];
    var derived = !authored && !!floor;
    var label = state + (derived ? ' · floor' : '');
    var title = 'lifecycle state (critique #25)' + (derived ? ' — derived floor (no authored state)' : '');
    return '<span class="lifecycle-state" title="' + _h(title) + '" style="display:inline-block;'
      + 'padding:1px 8px;border-radius:9999px;background:' + c[0] + ';color:' + c[1] + ';'
      + 'font-weight:600;font-size:0.72em;margin-left:6px;vertical-align:middle">' + _h(label) + '</span>';
  }
  function _findingChips(f) {
    return _claimScopeChip(f) + _generalityChip(f) + _lifecycleChip(f);
  }
  // Wave 3b #9 — threshold provenance.kind chip (+ note in the tooltip) beside a
  // pass_if band. DISTINCT from cites/calibration_anchor. Enum matches the
  // cross-repo contract. Degrades to '' when no provenance is declared.
  var _THRESHOLD_PROV_COLORS = {
    theory:      ['#dbeafe', '#1e40af'],
    calibration: ['#dcfce7', '#166534'],
    literature:  ['#e0e7ff', '#3730a3'],
    expert:      ['#fef9c3', '#854d0e'],
    exploratory: ['#f1f5f9', '#475569'],
    post_hoc:    ['#fee2e2', '#991b1b']
  };
  function _thresholdProvenanceChip(passIf) {
    if (!passIf || typeof passIf !== 'object') return '';
    var prov = passIf.provenance;
    if (!prov || typeof prov !== 'object') return '';
    var kind = prov.kind;
    if (typeof kind !== 'string' || !kind.trim()) return '';
    var v = kind.trim();
    var c = _THRESHOLD_PROV_COLORS[v] || ['#fef9c3', '#854d0e'];
    var note = (typeof prov.note === 'string') ? prov.note.trim() : '';
    var title = 'threshold provenance (critique #9)' + (note ? ' — ' + note : '');
    return '<span class="threshold-provenance" title="' + _h(title) + '" style="display:inline-block;'
      + 'padding:1px 8px;border-radius:9999px;background:' + c[0] + ';color:' + c[1] + ';'
      + 'font-weight:600;font-size:0.72em;margin-left:6px;vertical-align:middle">provenance: ' + _h(v) + '</span>';
  }
  function _deriveConclusionVerdicts(s) {
    var authored = s.conclusion_verdicts || {};
    var ge = (s.pipeline_gate || {}).gate_evaluator || {};
    var bio = _normGateResult(ge.result || s.gate_status);

    var runs = (s.runs || []).filter(function(r) { return r && typeof r === 'object'; });
    var reg;
    if (!runs.length) { reg = 'PENDING'; }
    else {
      var statuses = runs.map(function(r) { return String(r.status == null ? '' : r.status).trim().toLowerCase(); });
      if (statuses.some(function(x) { return _RUN_ERRORED[x]; })) reg = 'FAIL';
      else if (statuses.every(function(x) { return _RUN_COMPLETED[x]; })) reg = 'PASS';
      else reg = 'PARTIAL';
    }

    var findings = (s.findings || []).filter(function(f) { return f && typeof f === 'object'; });
    var exp;
    if (!findings.length) exp = 'GAP';
    else if (findings.some(function(f) { return f.tier === 'interpretation' || f.mechanism_origin; })) exp = 'PASS';
    else exp = 'PARTIAL';

    function basis(t) { var x = authored[t]; return (x && typeof x === 'object') ? (x.basis || '') : ''; }
    return {
      biological_validation:    {result: bio, basis: basis('biological_validation')},
      regression_compatibility: {result: reg, basis: basis('regression_compatibility')},
      explanatory_gain:         {result: exp, basis: basis('explanatory_gain')}
    };
  }
  function _conclusionVerdictsHtml(s, slug) {
    var cv = _deriveConclusionVerdicts(s);
    var tracks = [
      ['biological_validation', 'Biological validation', 'from gate evaluator'],
      ['regression_compatibility', 'Regression compatibility', 'from run status'],
      ['explanatory_gain', 'Explanatory gain', 'from interpretation-tier findings']
    ];
    var rows = tracks.map(function(t) {
      var tr = cv[t[0]]; var res = tr.result;
      var col = _TRACK_COLORS[res] || ['#f1f5f9', '#475569'];
      var basisHtml = tr.basis
        ? '<div style="color:#475569;font-size:0.9em;margin-top:2px">' + _multiline(tr.basis) + '</div>' : '';
      return '<div style="padding:8px 0;border-top:1px solid #f1f5f9">'
        + '<div style="display:flex;gap:10px;align-items:baseline;flex-wrap:wrap">'
        + '<span style="display:inline-block;min-width:11em;font-weight:600;color:#1e293b">' + _h(t[1]) + '</span>'
        + '<span style="display:inline-block;padding:2px 10px;border-radius:9999px;background:' + col[0]
        + ';color:' + col[1] + ';font-weight:700;font-size:0.85em">' + _h(res) + '</span>'
        + '<span style="color:#94a3b8;font-size:0.82em">' + _h(t[2]) + ' · computed</span>'
        + '</div>' + basisHtml + '</div>';
    }).join('');
    return '<div class="conclusion-verdicts" id="study-' + slug + '-verdicts">'
      + '<h3>Conclusion verdicts</h3>'
      + '<p class="muted small" style="margin:0 0 8px 0">Three-track verdict — each result is '
      + '<strong>computed</strong> from canonical fields (gate evaluator, run status, finding tiers). '
      + 'The basis is the author\'s rationale.</p>'
      + rows + '</div>';
  }
  // C3 — read-only four-section synthesis sourced from canonical fields.
  function _conclusionSynthesisHtml(s, slug) {
    var findings = (s.findings || []).filter(function(f) { return f && typeof f === 'object'; });
    var claims = findings.map(function(f) { return f.statement || f.summary; }).filter(Boolean);
    var evidence = [];
    findings.forEach(function(f) {
      var ev = f.evidence;
      if (ev && typeof ev === 'object') ev = ev.observed || ev.summary || ev.detail;
      if (ev !== undefined && ev !== null && ev !== '') evidence.push(ev);
    });
    var limitations = s.limitations || [];
    if (typeof limitations === 'string') limitations = [limitations];
    var di = s.discovery_implications || {};
    var nextSteps = [];
    (di.followup_study_proposals || []).forEach(function(p) {
      if (p && typeof p === 'object') { var t = p.title || p.id; if (t) nextSteps.push(t); }
      else if (p) nextSteps.push(String(p));
    });
    var sections = [['Claims', claims], ['Evidence', evidence], ['Limitations', limitations], ['Next steps', nextSteps]];
    var blocks = sections.map(function(pair) {
      var items = (pair[1] || []).filter(Boolean);
      if (!items.length) return '';
      var lis = items.map(function(i) {
        return '<li>' + _multiline(typeof i === 'string' ? i : (i.text || JSON.stringify(i))) + '</li>';
      }).join('');
      return '<div style="margin:10px 0"><strong style="color:#1e293b">' + _h(pair[0]) + '</strong>'
        + '<ul style="margin:4px 0 0;padding-left:20px;color:#334155">' + lis + '</ul></div>';
    }).join('');
    if (!blocks) return '';
    return '<div class="conclusion-synthesis" id="study-' + slug + '-synthesis">'
      + '<h3>Conclusion synthesis</h3>'
      + '<p class="muted small" style="margin:0 0 8px 0">Read-only synthesis derived from the study\'s '
      + 'canonical fields (findings, limitations, follow-up proposals).</p>'
      + blocks + '</div>';
  }
  // Item 13 — controls table + falsifiability statement verbatim.
  function _controlsFalsifiabilityHtml(s, slug) {
    var controls = (s.controls || []).filter(function(c) { return c && typeof c === 'object'; });
    var fals = s.falsifiability;
    var bits = '';
    if (controls.length) {
      var rows = controls.map(function(c) {
        var res = String(c.result == null ? '' : c.result).toUpperCase();
        var col = _TRACK_COLORS[res] || ['#f1f5f9', '#475569'];
        var resHtml = res ? '<span style="padding:1px 8px;border-radius:9999px;background:' + col[0]
          + ';color:' + col[1] + ';font-weight:600;font-size:0.82em">' + _h(res) + '</span>' : '';
        return '<tr style="border-top:1px solid #f1f5f9;font-size:0.9em">'
          + '<td style="padding:4px 8px">' + _h(c.name || '') + '</td>'
          + '<td style="padding:4px 8px">' + _h(c.kind || '') + '</td>'
          + '<td style="padding:4px 8px">' + _h(c.hypothesis || '') + '</td>'
          + '<td style="padding:4px 8px">' + _h(c.expected || '') + '</td>'
          + '<td style="padding:4px 8px">' + _h(c.observed || '') + '</td>'
          + '<td style="padding:4px 8px">' + resHtml + '</td></tr>';
      }).join('');
      bits += '<div id="study-' + slug + '-controls" style="margin:10px 0">'
        + '<strong style="color:#1e293b">Controls</strong>'
        + '<table style="border-collapse:collapse;width:100%;margin-top:4px">'
        + '<tr style="text-align:left;color:#475569;font-size:0.82em">'
        + '<th style="padding:4px 8px">Name</th><th style="padding:4px 8px">Kind</th>'
        + '<th style="padding:4px 8px">Hypothesis</th><th style="padding:4px 8px">Expected</th>'
        + '<th style="padding:4px 8px">Observed</th><th style="padding:4px 8px">Result</th></tr>'
        + rows + '</table></div>';
    }
    if (fals) {
      bits += '<div id="study-' + slug + '-falsifiability" style="margin:10px 0;padding:8px 12px;'
        + 'background:#f8fafc;border-left:4px solid #64748b;border-radius:4px">'
        + '<strong style="color:#1e293b">Falsifiability:</strong> ' + _multiline(String(fals)) + '</div>';
    }
    return bits;
  }

  // ── Wave 2 — compositional causal discovery + semantic closure ─────────
  // All consume data the model WRITES into study.yaml (composition_commitment,
  // invariant_check, ablations, model_representation). Mirror the server-side
  // renderers in single_study_report.py. Each degrades to '' when absent.
  function _chipList(items, bg, fg) {
    bg = bg || '#f1f5f9'; fg = fg || '#0f172a';
    return (items || []).filter(function(i) { return i != null && i !== ''; })
      .map(function(i) {
        return '<span style="display:inline-block;padding:2px 9px;border-radius:9999px;background:'
          + bg + ';color:' + fg + ';margin:2px;font-size:0.82em">' + _h(String(i)) + '</span>';
      }).join('');
  }

  // C-COMMIT — "Theoretical commitment" panel. Invariants link to earlier
  // studies (#study-<slug>); new behaviors link to the study's own tests fold.
  function _compositionCommitmentHtml(s, slug) {
    var cc = s.composition_commitment;
    if (!cc || typeof cc !== 'object') return '';
    var rows = [];
    var added = cc.component_added;
    if (typeof added === 'string') added = [added];
    if (added && added.length) {
      rows.push('<div style="margin:8px 0"><strong style="color:#1e293b">Component added</strong> '
        + _chipList(added, '#e0e7ff', '#3730a3') + '</div>');
    }
    var deficit = cc.deficit_addressed;
    if (deficit && typeof deficit === 'object') {
      var note = deficit.note || '';
      var gaps = deficit.closure_gap_item; if (typeof gaps === 'string') gaps = [gaps];
      var gapHtml = (gaps && gaps.length)
        ? ' <span style="color:#475569;font-size:0.85em">closes:</span> ' + _chipList(gaps, '#fee2e2', '#991b1b')
        : '';
      if (note || gapHtml) {
        rows.push('<div style="margin:8px 0"><strong style="color:#1e293b">Deficit addressed</strong> '
          + (note ? _multiline(String(note)) : '') + gapHtml + '</div>');
      }
    } else if (typeof deficit === 'string' && deficit) {
      rows.push('<div style="margin:8px 0"><strong style="color:#1e293b">Deficit addressed</strong> '
        + _multiline(deficit) + '</div>');
    }
    var nb = cc.new_behavior; if (typeof nb === 'string') nb = [nb];
    if (nb && nb.length) {
      var nbHtml = nb.filter(Boolean).map(function(t) {
        return '<a href="#study-' + _h(slug) + '" style="display:inline-block;padding:2px 9px;'
          + 'border-radius:9999px;background:#dcfce7;color:#166534;margin:2px;font-size:0.82em;'
          + 'text-decoration:none">' + _h(String(t)) + '</a>';
      }).join('');
      rows.push('<div style="margin:8px 0"><strong style="color:#1e293b">New behavior</strong> ' + nbHtml + '</div>');
    }
    var inv = cc.invariants_required || [];
    var invBits = inv.map(function(iv) {
      if (iv && typeof iv === 'object') {
        var study = iv.study || ''; var test = iv.test || '';
        var label = study + (test ? ' · ' + test : '');
        if (!label) return '';
        return study
          ? '<li><a href="#study-' + _h(study) + '"><code>' + _h(label) + '</code></a></li>'
          : '<li><code>' + _h(label) + '</code></li>';
      }
      return iv ? '<li><code>' + _h(String(iv)) + '</code></li>' : '';
    }).filter(Boolean).join('');
    if (invBits) {
      rows.push('<div style="margin:8px 0"><strong style="color:#1e293b">Invariants required</strong>'
        + '<ul style="margin:4px 0 0;padding-left:20px;color:#334155;font-size:0.92em">' + invBits + '</ul></div>');
    }
    var ex = cc.alternatives_excluded; if (typeof ex === 'string') ex = [ex];
    if (ex && ex.length) {
      rows.push('<div style="margin:8px 0"><strong style="color:#1e293b">Alternatives excluded</strong> '
        + _chipList(ex, '#fef9c3', '#854d0e') + '</div>');
    }
    if (!rows.length) return '';
    return '<div class="composition-commitment" id="study-' + slug + '-commitment">'
      + '<h3>Theoretical commitment</h3>'
      + '<p class="muted small" style="margin:0 0 8px 0">What this study adds to its prerequisite — '
      + 'the component introduced, the deficit it closes, the new behavior it unlocks, the earlier '
      + 'invariants it must preserve, and the alternatives it excludes.</p>'
      + rows.join('') + '</div>';
  }

  // C-INVAR — "Invariant checks" sub-section (invalidated/weakened first).
  var _INVAR_STATUS_COLORS = {
    invalidated: ['#fee2e2', '#991b1b'], weakened: ['#fef9c3', '#854d0e'],
    preserved: ['#dcfce7', '#166534'], strengthened: ['#dbeafe', '#1e40af']
  };
  var _INVAR_STATUS_RANK = {invalidated: 0, weakened: 1, preserved: 2, strengthened: 3};
  function _invariantChecksHtml(s, slug) {
    var checks = (s.invariant_check || []).filter(function(c) { return c && typeof c === 'object'; });
    if (!checks.length) return '';
    checks = checks.slice().sort(function(a, b) {
      var ra = _INVAR_STATUS_RANK[String(a.status || '').toLowerCase()];
      var rb = _INVAR_STATUS_RANK[String(b.status || '').toLowerCase()];
      return (ra == null ? 9 : ra) - (rb == null ? 9 : rb);
    });
    var rows = checks.map(function(c) {
      var st = String(c.status || '').toLowerCase();
      var col = _INVAR_STATUS_COLORS[st] || ['#f1f5f9', '#475569'];
      var chip = '<span style="padding:1px 8px;border-radius:9999px;background:' + col[0] + ';color:'
        + col[1] + ';font-weight:600;font-size:0.82em">' + _h(st || '—') + '</span>';
      return '<tr style="border-top:1px solid #f1f5f9;font-size:0.9em">'
        + '<td style="padding:4px 8px"><code>' + _h(c.study || '') + '</code></td>'
        + '<td style="padding:4px 8px">' + _h(c.test || '') + '</td>'
        + '<td style="padding:4px 8px">' + _h(c.prior == null ? '' : c.prior) + '</td>'
        + '<td style="padding:4px 8px">' + _h(c.now == null ? '' : c.now) + '</td>'
        + '<td style="padding:4px 8px">' + chip + '</td></tr>';
    }).join('');
    return '<div class="invariant-checks" id="study-' + slug + '-invariants">'
      + '<h3>Invariant checks</h3>'
      + '<p class="muted small" style="margin:0 0 8px 0">Earlier guarantees re-checked in the current '
      + 'code state — prior vs current value and whether each was preserved. Invalidated / weakened first.</p>'
      + '<table style="border-collapse:collapse;width:100%">'
      + '<tr style="text-align:left;color:#475569;font-size:0.82em">'
      + '<th style="padding:4px 8px">Study</th><th style="padding:4px 8px">Test</th>'
      + '<th style="padding:4px 8px">Prior</th><th style="padding:4px 8px">Now</th>'
      + '<th style="padding:4px 8px">Status</th></tr>' + rows + '</table></div>';
  }

  // C-CF — "Causal necessity" table from study.ablations[].
  function _causalNecessityHtml(s, slug) {
    var abl = (s.ablations || []).filter(function(a) { return a && typeof a === 'object'; });
    if (!abl.length) return '';
    var roleColors = {
      necessary: ['#fee2e2', '#991b1b'], modulatory: ['#fef9c3', '#854d0e'],
      redundant: ['#f1f5f9', '#475569']
    };
    var rows = abl.map(function(a) {
      var target = a.target;
      if (Array.isArray(target)) target = target.join('.');
      var procTarget = _h(String(a.process == null ? '' : a.process))
        + (target ? ' <code style="font-size:0.82em">' + _h(String(target)) + '</code>' : '');
      var role = String(a.role || '').toLowerCase();
      var col = roleColors[role] || ['#f1f5f9', '#475569'];
      var roleHtml = '<span style="padding:1px 8px;border-radius:9999px;background:' + col[0]
        + ';color:' + col[1] + ';font-weight:600;font-size:0.82em">' + _h(role || '—') + '</span>';
      var nec = a.causally_necessary;
      var necHtml = nec === true ? '✓' : (nec === false ? '✗' : '—');
      return '<tr style="border-top:1px solid #f1f5f9;font-size:0.9em">'
        + '<td style="padding:4px 8px">' + procTarget + '</td>'
        + '<td style="padding:4px 8px"><code>' + _h(a.mode || '') + '</code></td>'
        + '<td style="padding:4px 8px">' + _h(a.behavior_test || '') + '</td>'
        + '<td style="padding:4px 8px">' + _h(String(a.baseline_result)) + ' → ' + _h(String(a.ablated_result)) + '</td>'
        + '<td style="padding:4px 8px">' + roleHtml + '</td>'
        + '<td style="padding:4px 8px;text-align:center;font-weight:700">' + necHtml + '</td></tr>';
    }).join('');
    return '<div class="causal-necessity" id="study-' + slug + '-causal">'
      + '<h3>Causal necessity</h3>'
      + '<p class="muted small" style="margin:0 0 8px 0">Counterfactual read of the ablation suite — '
      + 'each component removed or perturbed, whether a behavior test flipped, and so whether it is '
      + 'causally necessary (vs redundant or merely modulatory).</p>'
      + '<table style="border-collapse:collapse;width:100%">'
      + '<tr style="text-align:left;color:#475569;font-size:0.82em">'
      + '<th style="padding:4px 8px">Process / target</th><th style="padding:4px 8px">Mode</th>'
      + '<th style="padding:4px 8px">Behavior test</th><th style="padding:4px 8px">Baseline → ablated</th>'
      + '<th style="padding:4px 8px">Role</th><th style="padding:4px 8px">Necessary</th></tr>'
      + rows + '</table></div>';
  }

  // C-MODELCARD — "Representation claims" table from s.model_representation.
  // (The full static model card is rendered server-side in single_study_report.py
  // so it survives the static read-only bundle; here we surface the representation
  // labels + closure status, which need no composite fetch.)
  var _REPR_ROLE_COLORS = {
    'inside': ['#f1f5f9', '#475569'], 'boundary-crossing': ['#dbeafe', '#1e40af'],
    'derived': ['#ede9fe', '#6d28d9'], 'self-produced': ['#dcfce7', '#166534']
  };
  function _representationHtml(s, slug) {
    var mr = s.model_representation;
    if (!mr || typeof mr !== 'object') return '';
    var cats = [
      ['self-produced', mr.self_produced], ['derived', mr.derived],
      ['boundary-crossing', mr.boundary], ['boundary-crossing', mr.requires],
      ['inside', mr.provides], ['inside', mr.inside]
    ];
    var storeRole = {};
    cats.forEach(function(pair) {
      var lst = pair[1]; if (typeof lst === 'string') lst = [lst];
      (lst || []).forEach(function(st) {
        if (storeRole[String(st)] === undefined) storeRole[String(st)] = pair[0];
      });
    });
    var gap = mr.gap; if (typeof gap === 'string') gap = [gap];
    var gapSet = {}; (gap || []).forEach(function(g) { gapSet[String(g)] = 1; });
    var rows = Object.keys(storeRole).sort().map(function(store) {
      var role = storeRole[store];
      var col = _REPR_ROLE_COLORS[role] || ['#f1f5f9', '#475569'];
      var gapBadge = gapSet[store] ? ' <span style="padding:0 6px;border-radius:9999px;background:#fee2e2;'
        + 'color:#991b1b;font-size:0.72em">unclosed gap</span>' : '';
      return '<tr style="border-top:1px solid #f1f5f9;font-size:0.9em">'
        + '<td style="padding:4px 8px"><code>' + _h(store) + '</code>' + gapBadge + '</td>'
        + '<td style="padding:4px 8px"><span style="padding:1px 8px;border-radius:9999px;background:'
        + col[0] + ';color:' + col[1] + ';font-weight:600;font-size:0.82em">' + _h(role) + '</span></td></tr>';
    }).join('');
    function closureChip(label, closed) {
      var bg, fg, txt;
      if (closed === true) { bg = '#dcfce7'; fg = '#166534'; txt = 'CLOSED'; }
      else if (closed === false) { bg = '#fee2e2'; fg = '#991b1b'; txt = 'OPEN'; }
      else { bg = '#f1f5f9'; fg = '#475569'; txt = '—'; }
      return '<span style="margin-right:12px">' + _h(label) + ': <span style="padding:1px 8px;'
        + 'border-radius:9999px;background:' + bg + ';color:' + fg + ';font-weight:700;font-size:0.82em">'
        + txt + '</span></span>';
    }
    var semantic = (mr.semantic && typeof mr.semantic === 'object') ? mr.semantic : {};
    var closureHtml = '<div style="margin:10px 0">'
      + closureChip('Interface closure', mr.interface_closed)
      + closureChip('Semantic closure', semantic.semantically_closed) + '</div>';
    var tableHtml = rows ? ('<table style="border-collapse:collapse;width:100%;margin-top:4px">'
      + '<tr style="text-align:left;color:#475569;font-size:0.82em">'
      + '<th style="padding:4px 8px">Store</th><th style="padding:4px 8px">Representation</th></tr>'
      + rows + '</table>') : '';
    if (!rows && mr.interface_closed == null && semantic.semantically_closed == null) return '';
    return '<div class="representation-claims" id="study-' + slug + '-representation">'
      + '<h3>Representation claims</h3>'
      + '<p class="muted small" style="margin:0 0 8px 0">How each store is represented '
      + '(inside / boundary-crossing / derived / self-produced) and whether the model achieves '
      + 'interface closure (no missing inputs) and semantic closure (every self-produced store fluxes).</p>'
      + closureHtml + tableHtml + '</div>';
  }

  // Wave 3a #1 — what the investigation primarily evaluates. Renders a small
  // header chip; omitted when the field is unset / not a known enum value.
  var _OBJ_OF_EVAL = {method: 1, model: 1, hypothesis: 1, 'composition-protocol': 1};
  function _objectOfEvaluationChip(obj) {
    if (typeof obj !== 'string' || !obj.trim()) return '';
    var v = obj.trim().toLowerCase();
    if (!_OBJ_OF_EVAL[v]) return '';
    return ' <span class="badge" title="object of evaluation (critique #1) — what '
      + 'this investigation primarily evaluates" style="background:#e0e7ff;color:#3730a3;'
      + 'font-weight:600">evaluates: ' + _h(v) + '</span>';
  }

  // Wave 3a #26 — "Framework scorecard". Renders the deterministic framework-self
  // metrics computed by pbg_superpowers.rigor.framework_metrics (each entry is
  // {fraction, count, total}). The label is the dashboard's job; the math is
  // pbg's. Omitted when the payload carries no metrics (degrades gracefully).
  function _frameworkScorecardHtml(fm) {
    if (!fm || typeof fm !== 'object') return '';
    var metrics = fm.metrics || {};
    var keys = Object.keys(metrics).filter(function (k) {
      var m = metrics[k];
      return m && typeof m === 'object' && (typeof m.fraction === 'number'
        || typeof m.count === 'number' || typeof m.total === 'number');
    });
    if (!keys.length) return '';
    var nInv = (typeof fm.n_investigations === 'number') ? fm.n_investigations : 0;
    function humanize(k) {
      return String(k).replace(/_/g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });
    }
    var rows = keys.map(function (k) {
      var m = metrics[k];
      var frac = (typeof m.fraction === 'number') ? m.fraction : null;
      var pct = (frac == null) ? '—' : Math.round(frac * 100) + '%';
      var cnt = (typeof m.count === 'number' && typeof m.total === 'number')
        ? (m.count + ' / ' + m.total) : '';
      var w = (frac == null) ? 0 : Math.max(0, Math.min(100, Math.round(frac * 100)));
      var barColor = w >= 67 ? '#16a34a' : (w >= 34 ? '#d97706' : '#dc2626');
      return '<div style="display:flex;gap:10px;align-items:center;padding:6px 0;'
        + 'border-top:1px solid #f1f5f9">'
        + '<span style="flex:0 0 16em;color:#1e293b;font-weight:600">' + _h(humanize(k)) + '</span>'
        + '<span style="flex:1;display:flex;align-items:center;gap:8px">'
        +   '<span style="flex:1;height:8px;background:#f1f5f9;border-radius:9999px;overflow:hidden">'
        +     '<span style="display:block;height:100%;width:' + w + '%;background:' + barColor + '"></span>'
        +   '</span>'
        +   '<span style="flex:0 0 3.5em;text-align:right;font-weight:700;color:#1e293b">' + _h(pct) + '</span>'
        +   (cnt ? '<span style="flex:0 0 5em;text-align:right;color:#64748b;font-size:0.85em">' + _h(cnt) + '</span>' : '')
        + '</span>'
        + '</div>';
    }).join('');
    return '<details class="report-fold" id="framework-scorecard"><summary>📊 Framework scorecard'
      + ' <span class="rf-prev">framework-self metrics (n=' + nInv + ' investigation'
      + (nInv === 1 ? '' : 's') + ')</span></summary>'
      + '<p style="color:#475569;font-size:0.92em">Framework-self metrics aggregated across '
      + 'every study and investigation in the workspace — how consistently the framework itself '
      + 'applies its own rigor practices (discriminating controls, emergent-mechanism labelling, '
      + 'threshold provenance, replication, verdict divergence, falsification exposure). Computed '
      + 'deterministically from declared fields by pbg_superpowers.rigor.framework_metrics.</p>'
      + rows
      + '</details>';
  }

  // Wave 3b #6/#16 — "Competing hypotheses" panel. Each hypothesis carries its
  // AUTHORED predictions + status and a COMPUTED support trajectory (▲ supports /
  // ▼ weakens / ⊘ excludes) folded server-side by
  // pbg_superpowers.hypotheses.rollup_support and delivered via the report-data
  // path (GET /api/investigation-hypotheses). Omitted when no hypotheses are
  // declared (degrades gracefully).
  function _competingHypothesesHtml(hypotheses) {
    var hyps = (hypotheses || []).filter(function(h) { return h && typeof h === 'object'; });
    if (!hyps.length) return '';
    var STATUS_COLORS = {
      open:      ['#f1f5f9', '#475569'],
      supported: ['#dcfce7', '#166534'],
      weakened:  ['#fef9c3', '#854d0e'],
      excluded:  ['#fee2e2', '#991b1b']
    };
    var DELTA = {
      supports: ['▲', '#16a34a', 'supports'],
      weakens:  ['▼', '#d97706', 'weakens'],
      excludes: ['⊘', '#dc2626', 'excludes']
    };
    var cards = hyps.map(function(h) {
      var status = (typeof h.status === 'string' && h.status.trim()) ? h.status.trim() : 'open';
      var sc = STATUS_COLORS[status] || ['#f1f5f9', '#475569'];
      var preds = (h.predictions || []).filter(function(p) { return p && typeof p === 'object'; });
      var predHtml = preds.length
        ? '<div style="margin-top:4px"><span class="muted small">predicts:</span>'
          + '<ul style="margin:2px 0 0;padding-left:20px;color:#334155;font-size:0.9em">'
          + preds.map(function(p) {
              return '<li><code>' + _h(String(p.observable || '')) + '</code> '
                + (p.expected != null ? '<strong>' + _h(String(p.expected)) + '</strong>' : '') + '</li>';
            }).join('') + '</ul></div>'
        : '';
      var log = (h.support_log || []).filter(function(e) { return e && typeof e === 'object'; });
      var trajHtml;
      if (log.length) {
        var tally = {supports: 0, weakens: 0, excludes: 0};
        var steps = log.map(function(e) {
          var key = String(e.delta || '').toLowerCase();
          var d = DELTA[key] || ['·', '#94a3b8', String(e.delta || '')];
          if (tally[key] != null) tally[key]++;
          var tip = (e.study ? e.study + ': ' : '') + (e.observation || '') + ' (' + d[2] + ')';
          return '<span title="' + _h(tip) + '" style="color:' + d[1] + ';font-weight:700;margin-right:6px">'
            + d[0] + (e.study ? '<span style="color:#64748b;font-weight:400;font-size:0.82em"> '
            + _h(String(e.study)) + '</span>' : '') + '</span>';
        }).join('');
        trajHtml = '<div style="margin-top:6px"><span class="muted small">support trajectory:</span> '
          + '<span style="margin-left:4px;font-weight:600">▲' + tally.supports + ' ▼' + tally.weakens
          + ' ⊘' + tally.excludes + '</span>'
          + '<div style="margin-top:3px">' + steps + '</div></div>';
      } else {
        trajHtml = '<div class="muted small" style="margin-top:6px">no study evidence linked yet</div>';
      }
      return '<div style="padding:10px 0;border-top:1px solid #f1f5f9">'
        + '<div style="display:flex;gap:8px;align-items:baseline;flex-wrap:wrap">'
        +   (h.id ? '<code style="font-size:0.82em">' + _h(String(h.id)) + '</code>' : '')
        +   '<strong style="color:#1e293b">' + _h(String(h.statement || '(untitled hypothesis)')) + '</strong>'
        +   '<span style="padding:1px 8px;border-radius:9999px;background:' + sc[0] + ';color:' + sc[1]
        +     ';font-weight:600;font-size:0.78em">' + _h(status) + '</span>'
        + '</div>' + predHtml + trajHtml + '</div>';
    }).join('');
    return '<details class="report-fold" id="competing-hypotheses"><summary>⚖️ Competing hypotheses'
      + ' <span class="rf-prev">' + hyps.length + ' hypothes' + (hyps.length === 1 ? 'is' : 'es')
      + ' under test</span></summary>'
      + '<p style="color:#475569;font-size:0.92em">The rival explanations this investigation '
      + 'discriminates. Each carries its authored predictions and a <strong>computed</strong> support '
      + 'trajectory — ▲ supports / ▼ weakens / ⊘ excludes — folded from member studies\' findings + '
      + 'alternate_hypotheses by pbg_superpowers.hypotheses.rollup_support.</p>'
      + cards + '</details>';
  }

  function _rigorSectionHtml(rigor, specs) {
    if (!rigor || !((rigor.dimensions && rigor.dimensions.length) ||
                    (rigor.per_study && Object.keys(rigor.per_study).length))) return '';
    var color = {ok: '#16a34a', warn: '#d97706', gap: '#dc2626'};
    var glyph = {ok: '✓', warn: '⚠', gap: '✗'};
    function dimRows(dims) {
      return (dims || []).map(function(d) {
        var c = color[d.severity] || '#64748b';
        var cm = (d.comments && d.comments.length)
          ? ' <span style="color:#94a3b8;font-size:0.82em">' + _esc(d.comments.join(' ')) + '</span>' : '';
        return '<div style="display:flex;gap:10px;align-items:flex-start;padding:6px 0;border-top:1px solid #f1f5f9">' +
          '<span style="color:' + c + ';font-weight:700;min-width:1.2em">' + (glyph[d.severity] || '•') + '</span>' +
          '<div><strong style="color:#1e293b">' + _esc(d.label || '') + '</strong>' + cm +
          '<div style="color:#475569;font-size:0.9em;margin-top:1px">' + _esc(d.detail || '') + '</div></div></div>';
      }).join('');
    }
    var html = '<details class="report-fold" id="rigor"><summary>🔬 Evidence &amp; rigor — '
      + 'how well the method defends its claims'
      + (rigor.summary ? ' <span class="rf-prev">' + _esc(rigor.summary) + '</span>' : '')
      + '</summary>'
      + '<p style="color:#475569;font-size:0.92em">Deterministic feedback on how well the '
      + '<strong>method</strong> defends its claims against a skeptical reader — a method-level '
      + 'judgement, distinct from the per-study model verdicts above. Computed from declared '
      + 'fields, not judged. Gaps are an invitation to add negative controls, replicate across '
      + 'seeds, weigh alternative explanations, state falsifiability, or add an adversarial study.</p>';
    html += dimRows(rigor.dimensions);
    var per = rigor.per_study || {};
    var slugs = Object.keys(per);
    if (slugs.length) {
      // Item 13 — surface the scored-but-hidden controls[] table + the
      // falsifiability statement verbatim under each study's rigor fold.
      var specsBySlug = {};
      (specs || []).forEach(function(sp) { if (sp && sp.name) specsBySlug[sp.name] = sp; });
      html += '<h3 style="margin-top:16px">Per-study rigor</h3>';
      slugs.forEach(function(slug) {
        var sc = per[slug] || {};
        var detail = specsBySlug[slug] ? _controlsFalsifiabilityHtml(specsBySlug[slug], slug) : '';
        // Each member study folds into its own nested dropdown.
        html += '<details class="report-fold" style="margin:8px 0"><summary>' + _esc(slug)
          + ' <span style="font-weight:400;color:#64748b;font-size:0.88em">— ' + _esc(sc.summary || '') + '</span></summary>'
          + dimRows(sc.dimensions) + detail + '</details>';
      });
    }
    html += '</details>';
    return html;
  }

  function _buildInvestigationReportHtml(iset, specs, bibEntries, chartsByStudy, embedsByStudy, generation, ghRepo, rigor, frameworkMetrics, hypotheses) {
    bibEntries = bibEntries || [];
    chartsByStudy = chartsByStudy || {};
    embedsByStudy = embedsByStudy || {};
    generation = generation || null;
    ghRepo = ghRepo || null;
    // Wave 3b #6/#16 — prefer the report-data-path enriched hypotheses (with the
    // computed support_log); fall back to the authored iset.hypotheses so the
    // panel still renders (un-enriched) when the fetch is unavailable.
    hypotheses = hypotheses || (iset && iset.hypotheses) || [];
    var bibByKey = {};
    bibEntries.forEach(function(e) { bibByKey[e.key] = e; });
    var now = new Date().toISOString().replace('T', ' ').slice(0, 16) + ' UTC';

    // ── Coordinated-generation banner (expert-feedback A.3) ──────────────
    // One prominent provenance stamp so the reviewer knows every panel below
    // reflects a single (git_sha, params) state — and a loud warning when
    // displayed runs span more than one generation (the "results are mixed,
    // some 5/17 some 5/19" complaint). Built once, here, so live + exported
    // reports stamp identically.
    function _genBannerHtml() {
      // Gather the distinct generation ids actually present in displayed runs.
      var seen = {};
      specs.forEach(function(s) {
        (s.runs || []).forEach(function(r) {
          var g = r && r.generation_id;
          if (g) seen[g] = true;
        });
      });
      var distinct = Object.keys(seen);
      var curId = generation && generation.generation_id;
      var bits = [];
      if (curId) bits.push('<code>' + _h(curId) + '</code>');
      if (generation && generation.git_sha) bits.push('git <code>' + _h(generation.git_sha) + '</code>');
      if (generation && generation.param_set_hash) bits.push('params <code>' + _h(generation.param_set_hash) + '</code>');
      if (generation && generation.created_at) bits.push(_h(String(generation.created_at).replace('T', ' ').slice(0, 16)));
      // A report mixes generations if displayed runs carry >1 distinct id, or
      // any displayed run's generation differs from the current one.
      var mixes = distinct.length > 1
        || (curId && distinct.some(function(g) { return g !== curId; }));
      var head, body, bg, border, fg;
      if (!curId && !distinct.length) {
        return '';  // no generation model in play — say nothing
      }
      if (mixes) {
        bg = '#fffbeb'; border = '#f59e0b'; fg = '#92400e';
        head = '⚠ This report mixes results from more than one generation';
        body = 'Panels below do not all reflect the same code + parameter state. '
             + 'Re-run the whole investigation as one generation, then re-export, '
             + 'so every result is coordinated.'
             + (distinct.length ? ' Generations present: '
                 + distinct.map(function(g){return '<code>'+_h(g)+'</code>';}).join(', ') + '.' : '');
      } else {
        bg = '#f0fdf4'; border = '#16a34a'; fg = '#166534';
        head = 'Coordinated generation';
        body = 'Every result below reflects one snapshot: ' + bits.join(' · ') + '.';
      }
      return '<div class="generation-banner" id="generation-banner" '
        + 'style="margin:16px 0;padding:12px 16px;background:' + bg + ';border:1px solid '
        + border + ';border-left-width:5px;border-radius:6px;color:' + fg + '">'
        + '<strong>' + head + '</strong>'
        + '<div class="small" style="margin-top:4px">' + body + '</div>'
        + '</div>';
    }
    var generationBannerHtml = _genBannerHtml();

    // Topological depth ordering of the studies (same as the dashboard DAG).
    var depthMap = {};
    var children = {};
    specs.forEach(function(s) {
      depthMap[s.name] = 0;
      children[s.name] = [];
    });
    specs.forEach(function(s) {
      (s.parent_studies || []).forEach(function(p) {
        var pn = (typeof p === 'string') ? p : p.study;
        if (children[pn]) children[pn].push(s.name);
      });
    });
    var queue = [];
    specs.forEach(function(s) {
      if (!(s.parent_studies || []).length) queue.push(s.name);
    });
    var guard = specs.length * 4;
    while (queue.length && guard-- > 0) {
      var n = queue.shift();
      (children[n] || []).forEach(function(c) {
        if (depthMap[c] < (depthMap[n] || 0) + 1) {
          depthMap[c] = depthMap[n] + 1;
          queue.push(c);
        }
      });
    }
    var ordered = specs.slice().sort(function(a, b) {
      return (depthMap[a.name] || 0) - (depthMap[b.name] || 0)
          || a.name.localeCompare(b.name);
    });

    // ── Spine C2: investigation verdict DAG + acceptance narrative ──────────
    // A compact, dependency-true map of the member studies, each badged with
    // its code-computed gate verdict, plus a one-paragraph acceptance roll-up.
    // Reuses the `ordered` / `depthMap` topology above (no second sort) and the
    // spine-computed verdicts/acceptance (no recompute). Mirrors the
    // param-enforcement banner: surfaced, connected (nodes/criteria link to the
    // per-study sections), labeled code-computed.
    function _spineVerdictBadge(result) {
      var r = (result || '').toString().toLowerCase();
      if (r === 'passed' || r === 'pass') return { glyph: '✅', cls: 'pass', bd: '#16a34a' };
      if (r === 'failed' || r === 'fail') return { glyph: '⛔', cls: 'fail', bd: '#dc2626' };
      if (!r) return { glyph: '◽', cls: 'none', bd: '#cbd5e1' };
      // needs_calibration / blocked / stale → needs work
      return { glyph: '⚠', cls: 'warn', bd: '#f59e0b' };
    }
    function _verdictDagHtml() {
      if (!ordered.length) return '';
      var hasEdges = specs.some(function(s) { return (s.parent_studies || []).length; });
      var byDepth = {};
      ordered.forEach(function(s) {
        var d = depthMap[s.name] || 0;
        (byDepth[d] = byDepth[d] || []).push(s);
      });
      var depths = Object.keys(byDepth).map(Number).sort(function(a, b) { return a - b; });
      var ranks = depths.map(function(d) {
        var nodes = byDepth[d].map(function(s) {
          var b = _spineVerdictBadge((s.computed_gate_verdict || {}).result);
          var parents = (s.parent_studies || []).map(function(p) {
            return (typeof p === 'string') ? p : p.study;
          }).filter(Boolean);
          var dep = parents.length
            ? ' <span class="sdag-dep muted small">← ' + parents.map(function(p) {
                return '<a href="#study-' + _h(p) + '">' + _h(p) + '</a>';
              }).join(', ') + '</span>'
            : '';
          return '<li class="sdag-node sdag-' + b.cls + '" '
            + 'style="margin:3px 0;padding:2px 8px;border-left:3px solid ' + b.bd + '">'
            + '<span class="sdag-badge" title="code-computed gate verdict">' + b.glyph + '</span> '
            + '<a href="#study-' + _h(s.name) + '"><strong>' + _h(s.name) + '</strong></a>'
            + dep + '</li>';
        }).join('');
        return '<div class="sdag-rank" style="margin:4px 0">'
          + (hasEdges ? '<span class="sdag-rank-lbl muted small" style="display:inline-block;min-width:64px">depth ' + d + '</span>' : '')
          + '<ul class="sdag-list" style="list-style:none;margin:0;padding:0;display:inline-block;vertical-align:top">' + nodes + '</ul></div>';
      }).join('');
      return '<div class="study-verdict-dag" id="study-verdict-dag" '
        + 'style="margin:14px 0;padding:12px 16px;background:#f8fafc;border:1px solid #cbd5e1;border-left-width:5px;border-radius:6px">'
        + '<strong>Study verdict map</strong> '
        + '<span class="muted small">code-computed gate verdicts (✅ passed · ⚠ needs work · ⛔ blocked)'
        + (hasEdges ? '; edges = pipeline prerequisites (← depends on)' : '') + '</span>'
        + ranks + '</div>';
    }
    function _acceptanceNarrativeHtml() {
      var ca = iset.computed_acceptance;
      if (!ca || !ca.criteria || !ca.criteria.length) return '';
      var total = ca.criteria.length;
      function _is(r, set) { return set.indexOf((r || '').toString().toLowerCase()) >= 0; }
      var nPass = ca.criteria.filter(function(c) { return _is(c.result, ['passing', 'pass']); }).length;
      var blocked = ca.criteria.filter(function(c) {
        return _is(c.result, ['failing', 'fail', 'blocked']);
      }).map(function(c) { return c.study; }).filter(Boolean);
      var vs = ca.verdict_status || (nPass === total ? 'passing' : 'in-progress');
      return '<p class="acceptance-narrative" id="acceptance-narrative" '
        + 'style="margin:10px 0;padding:10px 16px;background:#f0f9ff;border-left:4px solid #3b82f6;border-radius:4px">'
        + '<strong>Investigation acceptance: ' + _h(vs) + '.</strong> '
        + nPass + ' of ' + total + ' acceptance criteria passing'
        + (blocked.length
            ? '; blocked by ' + blocked.map(function(n) {
                return '<a href="#study-' + _h(n) + '">' + _h(n) + '</a>';
              }).join(', ')
            : '')
        + '. <span class="muted small">code-computed from member-study verdicts</span></p>';
    }
    // ── SP4a: AC → study gating-matrix panel ───────────────────────────────
    // Rows = acceptance criteria; columns = the gating study (linked to its
    // section) + the computed result. Acceptance criteria with NO `study:` link
    // are FLAGGED red ("no study linked — gap") — this is what surfaces e.g.
    // chromosome-cycle-calibration's 5 unlinked criteria. Built synchronously
    // from iset.acceptance_criteria (so the gap is visible even in a static
    // snapshot), then ENRICHED from /api/linkage-index?investigation=<name>
    // when the live endpoint is reachable. Tolerates the endpoint failing.
    function _acResultBadge(result) {
      var r = (result || '').toString().toLowerCase();
      if (r === 'passing' || r === 'pass' || r === 'passed') return { glyph: '✅', bd: '#16a34a', bg: '#f0fdf4' };
      if (r === 'failing' || r === 'fail' || r === 'failed') return { glyph: '⛔', bd: '#dc2626', bg: '#fef2f2' };
      if (r === 'passing-with-caveats') return { glyph: '⚠', bd: '#f59e0b', bg: '#fffbeb' };
      return { glyph: '◐', bd: '#94a3b8', bg: '#f8fafc' };  // in-progress / pending
    }
    // What an acceptance criterion IS — shown once, above the acceptance tables,
    // so a reviewer knows these are computed metrics, not assertions.
    var _acceptanceExplainer =
      '<p class="muted small" style="margin:2px 0 10px 0;line-height:1.5;color:#475569">'
      + 'Each <strong>acceptance criterion</strong> is a <em>behaviour test</em> declared in a study: a '
      + 'measured field from the run (e.g. <code>closure_gap_size</code>) compared against an explicit '
      + '<code>pass_if</code> band (a numeric threshold/range). The per-criterion result, each study’s '
      + 'gate verdict, and this roll-up are <strong>computed in code from the run outcomes</strong> '
      + '(deterministic) — not human judgement. Expand a row to see the field, the passing band, and the '
      + 'observed value.</p>';
    // Map study slug -> spec, to look up each criterion's underlying behaviour
    // test (the actual metric) from the gating study.
    var _specBySlug = {};
    (specs || []).forEach(function(s) { if (s && s.name) _specBySlug[s.name] = s; });
    function _passIfText(p) {
      if (p == null || p === '') return '';
      if (typeof p !== 'object') return String(p);
      // {op, value} — the common shape (e.g. {op:">", value:0} -> "> 0").
      if (p.op !== undefined && p.value !== undefined) {
        var sym = {'>=': '≥', '<=': '≤', '>': '>', '<': '<', '==': '=', '!=': '≠',
                   'in_range': 'in range'}[p.op] || p.op;
        return sym + ' ' + p.value;
      }
      // {low, high} band.
      if (p.low !== undefined || p.high !== undefined) {
        return 'in [' + (p.low !== undefined ? p.low : '−∞') + ', '
          + (p.high !== undefined ? p.high : '∞') + ']';
      }
      var bits = [];
      if (p.min !== undefined || p.max !== undefined)
        bits.push((p.min !== undefined ? ('≥ ' + p.min) : '') +
                  (p.max !== undefined ? ((p.min !== undefined ? ' and ' : '') + '≤ ' + p.max) : ''));
      ['gte', 'lte', 'gt', 'lt', 'equals', 'eq', 'min_fraction', 'at_least', 'at_most'].forEach(function(k) {
        if (p[k] !== undefined) bits.push(k.replace(/_/g, ' ') + ' ' + p[k]);
      });
      return bits.length ? bits.join(', ') : JSON.stringify(p);
    }
    // A measure ({kind, field/path}) as readable text, e.g. "broken_network_gap (derived_scalar)".
    function _measureText(m) {
      if (!m) return '';
      if (typeof m !== 'object') return String(m);
      var f = m.field || m.path || '';
      var k = m.kind || '';
      if (f && k) return '<code>' + _h(f) + '</code> <span class="muted small">(' + _h(k) + ')</span>';
      return f ? '<code>' + _h(f) + '</code>' : (k ? '<span class="muted small">' + _h(k) + '</span>' : _h(JSON.stringify(m)));
    }
    // Returns {field, passIf, observed, description} for a (study, behavior),
    // or null if the gating study / test can't be resolved.
    function _critMetric(study, behavior) {
      var s = _specBySlug[study];
      if (!s) return null;
      var tests = s.behavior_tests || s.expected_behavior || [];
      var t = null;
      for (var i = 0; i < tests.length; i++) {
        if (tests[i] && tests[i].name === behavior) { t = tests[i]; break; }
      }
      if (!t) return null;
      var field = (t.measure && (t.measure.field || t.measure.kind)) || '';
      var observed = null;
      var runs = s.runs || [];
      if (runs.length) {
        var oc = (runs[runs.length - 1].outcomes || {})[behavior];
        if (oc && oc.observed !== undefined) observed = oc.observed;
      }
      return { field: field, passIf: _passIfText(t.pass_if), observed: observed,
               description: t.description || '' };
    }
    // A compact "field · pass-if · observed" detail line for a criterion row.
    function _critMetricDetail(study, behavior) {
      var m = _critMetric(study, behavior);
      if (!m) return '';
      var bits = [];
      if (m.field) bits.push('field <code>' + _h(m.field) + '</code>');
      if (m.passIf) bits.push('passes if <code>' + _h(m.passIf) + '</code>');
      if (m.observed !== null && m.observed !== undefined)
        bits.push('observed <strong>' + _h(typeof m.observed === 'number' ? (Math.round(m.observed * 1000) / 1000) : m.observed) + '</strong>');
      if (!bits.length && !m.description) return '';
      return '<div class="crit-metric muted small" style="margin:2px 0 0 0;color:#475569">'
        + bits.join(' &middot; ')
        + (m.description ? '<div style="margin-top:2px">' + _h(m.description.replace(/\s+/g, ' ').trim()) + '</div>' : '')
        + '</div>';
    }
    function _acGatingMatrixHtml() {
      var crits = (iset.acceptance_criteria || []).filter(function(c) {
        return c && typeof c === 'object';
      });
      if (!crits.length) return '';
      var computed = ((iset.computed_acceptance || {}).criteria) || [];
      var nGap = 0;
      var rows = crits.map(function(c, i) {
        var study = (c.study || '').toString().trim();
        var gap = !study;
        if (gap) nGap += 1;
        var result = (computed[i] && computed[i].result) || c.status || '';
        var b = _acResultBadge(result);
        var behavior = c.behavior || c.name || '(criterion ' + (i + 1) + ')';
        var studyCell = gap
          ? '<td style="color:#b91c1c;font-weight:600">⚠ no study linked — gap</td>'
          : '<td><a href="#study-' + _h(study) + '">' + _h(study) + '</a></td>';
        var resultCell = '<td id="acg-result-' + i + '" '
          + 'style="white-space:nowrap"><span class="acg-pill" '
          + 'style="display:inline-block;padding:1px 8px;border-radius:10px;border:1px solid '
          + b.bd + ';background:' + b.bg + '">' + b.glyph + ' ' + _h(result || 'pending') + '</span></td>';
        return '<tr id="acg-row-' + i + '" data-gap="' + (gap ? '1' : '0') + '" '
          + 'style="' + (gap ? 'background:#fef2f2' : '') + '">'
          + '<td style="padding-right:10px;vertical-align:top">' + _h(behavior)
            + _critMetricDetail(study, behavior) + '</td>'
          + studyCell + resultCell + '</tr>';
      }).join('');
      var gapNote = nGap
        ? '<p class="muted small" style="margin:6px 0 0 0;color:#b91c1c">'
          + nGap + ' of ' + crits.length + ' acceptance criteria have no study linked (gaps) — '
          + 'nothing gates them.</p>'
        : '<p class="muted small" style="margin:6px 0 0 0">All ' + crits.length
          + ' acceptance criteria are linked to a gating study.</p>';
      return '<div class="ac-gating-matrix" id="ac-gating-matrix" '
        + 'data-investigation="' + _h(iset.name || '') + '" '
        + 'style="margin:12px 0;padding:12px 16px;background:#f0f9ff;'
        + 'border:1px solid #bae6fd;border-left:4px solid #3b82f6;border-radius:6px">'
        + '<strong>AC → study gating matrix</strong> '
        + '<span class="muted small">which study gates each acceptance criterion · '
        + '⚠ = no study linked (gap)</span>'
        + _acceptanceExplainer
        + '<table class="acg-table" style="width:100%;border-collapse:collapse;margin-top:8px;font-size:0.92em">'
        + '<thead><tr style="text-align:left;border-bottom:1px solid #cbd5e1">'
        + '<th style="padding:2px 10px 4px 0">Acceptance criterion</th>'
        + '<th style="padding:2px 0 4px 0">Gating study</th>'
        + '<th style="padding:2px 0 4px 0">Result</th></tr></thead>'
        + '<tbody>' + rows + '</tbody></table>' + gapNote
        + '</div>'
        // Enrich from the live linkage-index endpoint when reachable. The panel
        // already renders the gaps synchronously, so a failed/absent endpoint
        // (e.g. a static snapshot) is harmless — we just keep the skeleton.
        + '<script>(function(){try{'
        + 'var panel=document.getElementById("ac-gating-matrix");'
        + 'if(!panel)return;var inv=panel.getAttribute("data-investigation");if(!inv)return;'
        + 'fetch("/api/linkage-index?investigation="+encodeURIComponent(inv))'
        + '.then(function(r){return r.ok?r.json():null;})'
        + '.then(function(d){if(!d||!d.ac_matrix||!d.ac_matrix.criteria)return;'
        + 'd.ac_matrix.criteria.forEach(function(c,i){'
        + 'var cell=document.getElementById("acg-result-"+i);if(!cell)return;'
        + 'var res=(c.result||"pending");'
        + 'var span=cell.querySelector(".acg-pill");if(span)span.textContent="• "+res;'
        + '});})'
        + '.catch(function(){});'
        + '}catch(e){}})();</script>';
    }

    // ── SP5: "Decisions needed" report section ─────────────────────────────
    // A compact list of the same needs-attention items shown in the live panel.
    // Built as a placeholder that an inline script fills from the deterministic
    // /api/needs-attention scan (mirrors the AC-gating-matrix enrich pattern):
    // the section hydrates in the live report and stays quiet/hidden in a static
    // snapshot where the endpoint is unreachable. The report computes nothing.
    function _needsAttentionReportHtml() {
      var inv = (iset.name || '').toString();
      if (!inv) return '';
      return '<section class="needs-attention-report" id="needs-attention-report" '
        + 'data-investigation="' + _h(inv) + '" style="display:none;margin:12px 0;'
        + 'padding:12px 16px;background:#fffbeb;border:1px solid #fcd34d;'
        + 'border-left:4px solid #f59e0b;border-radius:6px">'
        + '<strong>Decisions needed</strong> '
        + '<span class="muted small">items the deterministic scan flags for triage · code-computed</span>'
        + '<div id="needs-attention-report-body" style="margin-top:8px;font-size:0.92em"></div>'
        + '</section>'
        + '<script>(function(){try{'
        + 'var sec=document.getElementById("needs-attention-report");'
        + 'if(!sec)return;var inv=sec.getAttribute("data-investigation");if(!inv)return;'
        + 'fetch("/api/needs-attention?investigation="+encodeURIComponent(inv))'
        + '.then(function(r){return r.ok?r.json():null;})'
        + '.then(function(d){if(!d||!d.summary||!(d.summary.total>0))return;'
        + 'var sevcol={high:"#dc2626",medium:"#f59e0b",low:"#3b82f6"};'
        + 'var body=document.getElementById("needs-attention-report-body");if(!body)return;'
        + 'var esc=function(s){var e=document.createElement("span");e.textContent=(s==null?"":String(s));return e.innerHTML;};'
        + 'var rows=(d.items||[]).map(function(it){'
        + 'var c=sevcol[(it.severity||"low")]||"#3b82f6";'
        + 'var ref=esc(it.study||it.ref||"");'
        + 'var hint=it.action_hint?(" &middot; "+esc(it.action_hint)):"";'
        + 'return "<li style=\\"margin-top:5px;padding-left:9px;border-left:3px solid "+c+"\\">"'
        + '+"<code style=\\"font-size:0.85em\\">"+esc(it.kind||"")+"</code> &middot; <code>"+ref+"</code>"+hint+"</li>";'
        + '}).join("");'
        + 'body.innerHTML="<div class=\\"muted small\\">"+((d.summary.by_severity||{}).high||0)+" high &middot; "'
        + '+d.summary.total+" total</div><ul style=\\"margin:6px 0 0 0;padding:0 0 0 4px;list-style:none\\">"+rows+"</ul>";'
        + 'sec.style.display="";'
        + '}).catch(function(){});'
        + '}catch(e){}})();</script>';
    }

    var verdictDagHtml = _verdictDagHtml();
    var acceptanceNarrativeHtml = _acceptanceNarrativeHtml();
    var acGatingMatrixHtml = _acGatingMatrixHtml();
    var needsAttentionReportHtml = _needsAttentionReportHtml();
    var rigorSectionHtml = _rigorSectionHtml(rigor, specs);
    var frameworkScorecardHtml = _frameworkScorecardHtml(frameworkMetrics);  // #26
    var competingHypothesesHtml = _competingHypothesesHtml(hypotheses);      // #6/#16

    // Data-driven flags so the "How to read" guide describes only what this
    // investigation actually contains — no workspace-specific boilerplate.
    var hasDag = specs.some(function(s) {
      return (s.parent_studies || []).length > 0;
    });
    var hasAssumptions = specs.some(function(s) {
      return ((s.key_assumptions || s.assumptions) || []).length > 0;
    });

    // --- v3-shape per-study section ----------------------------------
    // Render a sweep table (e.g. {1x: {dnaA_median: 115}, ...}) as a small
    // inline-SVG bar chart. Used in finding cards when evidence.sweep or
    // evidence.sweep_table is present.
    function _renderSweepChart(sweep) {
      if (!sweep || typeof sweep !== 'object') return '';
      var keys = Object.keys(sweep);
      if (!keys.length) return '';
      var metrics = {};
      keys.forEach(function(k) {
        var v = sweep[k];
        if (v && typeof v === 'object') {
          Object.keys(v).forEach(function(m) {
            var n = v[m];
            if (typeof n === 'number') {
              (metrics[m] = metrics[m] || {})[k] = n;
            }
          });
        }
      });
      var metricNames = Object.keys(metrics);
      if (!metricNames.length) return '';
      // Render the most numeric-rich metric (max count of non-null values).
      var metric = metricNames.sort(function(a, b) {
        return Object.keys(metrics[b]).length - Object.keys(metrics[a]).length;
      })[0];
      var data = metrics[metric];
      var entries = keys.map(function(k){return [k, data[k]];}).filter(function(e){return e[1] != null;});
      if (!entries.length) return '';
      var maxV = Math.max.apply(null, entries.map(function(e){return Math.abs(e[1]);}));
      var minV = Math.min.apply(null, entries.map(function(e){return e[1];}));
      var W = 480, H = 160, barW = Math.max(40, (W - 80) / entries.length - 8);
      var x0 = 56, baseY = (minV < 0) ? H / 2 : H - 32;
      var bars = entries.map(function(e, i) {
        var x = x0 + i * (barW + 8);
        var pixels = maxV ? Math.abs(e[1]) / maxV * (H - 60) : 0;
        var y = e[1] >= 0 ? baseY - pixels : baseY;
        var color = e[1] >= 0 ? '#3b82f6' : '#dc2626';
        return '<rect x="' + x + '" y="' + y + '" width="' + barW + '" height="' + pixels + '" fill="' + color + '" rx="2"/>'
             + '<text x="' + (x + barW/2) + '" y="' + (y - 4) + '" font-size="10" text-anchor="middle" fill="#0f172a">' + e[1] + '</text>'
             + '<text x="' + (x + barW/2) + '" y="' + (H - 10) + '" font-size="10" text-anchor="middle" fill="#475569">' + _h(e[0]) + '</text>';
      }).join('');
      return '<div class="sweep-chart"><svg viewBox="0 0 ' + W + ' ' + H + '" style="display:block;width:100%;max-width:' + W + 'px;margin:8px 0">'
        + '<text x="' + W/2 + '" y="16" font-size="11" font-weight="600" text-anchor="middle" fill="#0f172a">Sweep comparison — ' + _h(metric) + '</text>'
        + '<line x1="' + x0 + '" y1="' + baseY + '" x2="' + (W - 16) + '" y2="' + baseY + '" stroke="#94a3b8" stroke-width="0.5"/>'
        + bars
        + '</svg></div>';
    }

    // Decision-status helper — returns the data the decision box renders.
    function _decideDecision(s) {
      var runs = s.runs || [];
      var latest = runs.length ? runs[runs.length - 1] : null;
      var followUps = s.follow_up_studies || [];
      var openFollowups = followUps.filter(function(f) {
        return f.status !== 'done' && f.kind !== 'existing';
      });
      var phase = s.phase || '';
      var status = s.status || 'planned';

      // No runs yet
      if (!latest) {
        if (phase === 'Design' || status === 'planned') {
          return {
            label: 'Not started',
            cls:   'dec-notstarted',
            passed: [], failed: [], blocks: [],
            next: 'Run the baseline simulation to begin evaluation.'
          };
        }
        return {
          label: 'Ready to run',
          cls:   'dec-ready',
          passed: [], failed: [], blocks: [],
          next: 'Execute the simulation_set to gather evidence.'
        };
      }

      // Decide from BOTH the authored outcomes AND the run/outcome-spine
      // computed_outcomes (authored wins) so the panel reflects the evaluator and
      // stays current — not just hand-recorded verdicts.
      var outcomes = Object.assign({}, latest.computed_outcomes || {}, latest.outcomes || {});
      var passed = [], failed = [], partial = [];
      Object.keys(outcomes).forEach(function(name) {
        var res = (outcomes[name] || {}).result;
        if (res === 'PASS') passed.push(name);
        if (res === 'FAIL') failed.push(name);
        if (res === 'PARTIAL') partial.push(name);
      });
      var calibration = openFollowups.filter(function(f){return f.kind === 'calibration_task';});
      var infra       = openFollowups.filter(function(f){return f.kind === 'infrastructure_fix';});
      var newWork     = openFollowups.filter(function(f){return f.kind === 'new';});

      if (failed.length === 0 && partial.length === 0 && passed.length > 0) {
        var enables = (s.pipeline_gate && s.pipeline_gate.enables) || [];
        return {
          label: 'Passed',
          cls:   'dec-passed',
          passed: passed, failed: [], partial: [], blocks: [],
          next: enables.length
            ? 'Gate cleared. Next: ' + enables.join(', ')
            : 'Gate cleared. No declared downstream studies — review pipeline_gate.enables.'
        };
      }
      if (failed.length > 0) {
        var label = calibration.length ? 'Needs calibration' : 'Blocked';
        var cls   = calibration.length ? 'dec-needscal'      : 'dec-blocked';
        var nextItem = calibration[0] || infra[0] || newWork[0] || null;
        var nextStr;
        if (nextItem) {
          nextStr = 'Resolve: ' + nextItem.title;
        } else {
          nextStr = 'Investigate why ' + failed.length + ' test(s) failed.';
        }
        return {
          label: label, cls: cls,
          passed: passed, failed: failed,
          blocks: openFollowups.map(function(f){return f.title;}),
          next: nextStr
        };
      }
      if (partial.length > 0) {
        return {
          label: 'Partial', cls: 'dec-inprogress',
          passed: passed, failed: failed, partial: partial, blocks: [],
          next: partial.length + ' test(s) ran but did not meet the gate threshold — see findings.'
        };
      }
      return {
        label: 'In progress', cls: 'dec-inprogress',
        passed: passed, failed: failed, partial: partial, blocks: [],
        next: 'Continue analysing run outcomes.'
      };
    }

    // Plain-English study summary — 2-4 sentences, no code identifiers.
    function _studySummary(s, dec) {
      var purpose = s.purpose || {};
      var question = (purpose.question || '').trim().split('\n')[0];
      var findings = s.findings || [];
      var sentences = [];

      if (question) {
        var q = question.charAt(0).toLowerCase() + question.slice(1);
        if (q.charAt(q.length - 1) === '.') q = q.slice(0, -1);
        sentences.push('This study asks whether ' + q + '.');
      } else if (s.objective) {
        // Minimal study (no purpose.question): lead with the objective.
        sentences.push(_firstSentence(s.objective));
      }
      if (findings.length) {
        var confirms     = findings.filter(function(f){return f.status === 'confirms';}).length;
        var contradicts  = findings.filter(function(f){return f.status === 'contradicts';}).length;
        var novel        = findings.filter(function(f){return f.status === 'novel';}).length;
        var parts = [];
        if (confirms)    parts.push(confirms + ' finding' + (confirms === 1 ? '' : 's') + ' confirm the expected biology');
        if (contradicts) parts.push(contradicts + ' contradict it');
        if (novel)       parts.push(novel + ' novel computational result' + (novel === 1 ? '' : 's'));
        if (parts.length) sentences.push('We recorded ' + parts.join(', ') + '.');
      } else if ((s.runs || []).length === 0) {
        sentences.push('No simulations have run yet — the study is still in its design phase.');
      }
      sentences.push('Gate decision: ' + dec.label + '. ' + dec.next);
      return sentences.join(' ');
    }

    // First sentence of a (possibly multi-line) prose blob — used to derive a
    // one-liner for the collapsed control panel when no explicit one-liner was
    // authored. Collapses whitespace/newlines first.
    function _firstSentence(text) {
      if (!text) return '';
      var t = String(text).replace(/\s+/g, ' ').trim();
      var m = /^(.*?[.!?])(\s|$)/.exec(t);
      return m ? m[1] : t;
    }

    // Word-boundary preview (collapse whitespace, cut at a space, ellipsis).
    // Unlike _firstSentence it never breaks mid-abbreviation ("E. coli").
    function _previewText(text, maxLen) {
      var t = String(text || '').replace(/\s+/g, ' ').trim();
      if (t.length <= maxLen) return t;
      var cut = t.slice(0, maxLen);
      var sp = cut.lastIndexOf(' ');
      if (sp > maxLen * 0.6) cut = cut.slice(0, sp);
      return cut + '\u2026';
    }

    // Verdict vocabulary for the collapsed control panel. An authored
    // `report.verdict` (one of the keys below) wins; otherwise we derive it
    // from the gate decision class so older studies still get a sensible badge.
    var VERDICT_MAP = {
      'passing':              {emoji: '✅', label: 'Passing',                       cls: 'v-pass'},
      'passing-with-caveats': {emoji: '⚠️', label: 'Passing with caveats',          cls: 'v-warn'},
      'blocked':              {emoji: '⛔', label: 'Blocked',                       cls: 'v-block'},
      'preliminary':          {emoji: '🧪', label: 'Preliminary',                   cls: 'v-prelim'},
      'failing-bio':          {emoji: '❌', label: 'Failing biological validation', cls: 'v-fail'},
      'calibrating':          {emoji: '🔄', label: 'Calibration in progress',       cls: 'v-cal'},
      'not-started':          {emoji: '📋', label: 'Not started',                   cls: 'v-none'}
    };
    function _verdictBadge(s, decision) {
      var key = ((s.report || {}).verdict || '').trim().toLowerCase();
      if (VERDICT_MAP[key]) return VERDICT_MAP[key];
      switch (decision.cls) {
        case 'dec-passed':     return VERDICT_MAP['passing'];
        case 'dec-needscal':   return VERDICT_MAP['calibrating'];
        case 'dec-blocked':    return VERDICT_MAP['blocked'];
        case 'dec-notstarted': return VERDICT_MAP['not-started'];
        default:               return VERDICT_MAP['preliminary'];
      }
    }

    // The reviewer-facing "Ran · Tests · Verdict" clarity strip. Prefers the
    // server-computed `s.clarity_summary` (single-sourced from
    // pbg_superpowers.study_status.study_clarity_summary) and falls back to an
    // equivalent client-side computation so the strip renders even against an
    // older server. Answers, at a glance: did this study run? were the tests
    // run (pass/fail)? did it pass? (dnaa-replication reviewer feedback.)
    function _clarityStrip(s) {
      var cs = (s || {}).clarity_summary;
      if (!cs) {
        var runs = (s && s.runs) || [];
        var done = function (r) {
          var st = ((r && r.status) || '').toLowerCase();
          return st === 'completed' || st === 'complete' || st === 'ran' || st === 'done';
        };
        var nC = runs.filter(done).length;
        var ranStatus = nC ? 'ran'
          : (runs.some(function (r) { return ((r && r.status) || '').toLowerCase() === 'running'; }) ? 'running' : 'not_run');
        var tests = (s && (s.tests || s.behavior_tests || s.expected_behavior)) || [];
        var latest = runs.length ? runs[runs.length - 1] : null;
        var outc = (latest && latest.outcomes) || {};
        var c = { pass: 0, fail: 0, skip: 0, pending: 0, total: tests.length };
        tests.forEach(function (t) {
          var o = outc[t.name];
          var r = (((o && o.result) != null ? o.result : o) || '').toString().toLowerCase();
          if (r === 'pass' || r === 'passed' || r === 'ok') c.pass++;
          else if (r === 'fail' || r === 'failed' || r === 'error') c.fail++;
          else if (r === 'skip' || r === 'skipped' || r === 'inconclusive' || r === 'partial') c.skip++;
          else c.pending++;
        });
        var gate = ((s && s.gate_status) || '').toLowerCase();
        var verd;
        if (gate === 'passed') verd = { label: 'Passed', glyph: '✅', cls: 'v-pass' };
        else if (gate === 'failed' || gate === 'failed_evaluation') verd = { label: 'Failing', glyph: '❌', cls: 'v-fail' };
        else if (gate === 'blocked') verd = { label: 'Blocked', glyph: '⛔', cls: 'v-block' };
        else if (gate === 'needs_calibration') verd = { label: 'Needs calibration', glyph: '🔄', cls: 'v-cal' };
        else if (gate === 'in_progress') verd = { label: 'In progress', glyph: '🔶', cls: 'v-warn' };
        else if (ranStatus !== 'ran') verd = { label: 'Not run', glyph: '○', cls: 'v-none' };
        else if (c.fail) verd = { label: 'Failing', glyph: '❌', cls: 'v-fail' };
        else if (c.pending && c.total) verd = { label: 'Tests pending', glyph: '⏳', cls: 'v-warn' };
        else if (c.pass) verd = { label: 'Passed', glyph: '✅', cls: 'v-pass' };
        else verd = { label: 'In progress', glyph: '🔶', cls: 'v-warn' };
        var parts = [];
        if (c.pass) parts.push(c.pass + '✓');
        if (c.fail) parts.push(c.fail + '✗');
        if (c.skip) parts.push(c.skip + '⏭');
        if (c.pending) parts.push(c.pending + '⏳');
        cs = {
          ran: { status: ranStatus, label: ranStatus === 'ran' ? ('Ran · ' + nC + ' run' + (nC !== 1 ? 's' : '')) : (ranStatus === 'running' ? 'Running…' : 'Not run') },
          tests: { label: c.total ? ('Tests: ' + parts.join(' · ')) : 'No tests declared', total: c.total, pending: c.pending },
          verdict: verd, ambiguities: []
        };
      }
      var ranOn = cs.ran.status === 'ran';
      var pill = 'display:inline-block;padding:2px 9px;border-radius:9999px;font-size:0.78em;font-weight:600;margin-right:6px;';
      var ranBg = ranOn ? 'background:#dbeafe;color:#1e40af' : (cs.ran.status === 'running' ? 'background:#fef3c7;color:#92400e' : 'background:#f1f5f9;color:#475569');
      var tBg = (cs.tests.pending && cs.tests.total) ? 'background:#fef3c7;color:#92400e' : 'background:#f1f5f9;color:#334155';
      var amb = (cs.ambiguities && cs.ambiguities.length)
        ? '<span style="' + pill + 'background:#fef3c7;color:#92400e" title="' + _h(cs.ambiguities.join(' | ')) + '">⚠ ' + cs.ambiguities.length + ' clarity note' + (cs.ambiguities.length > 1 ? 's' : '') + '</span>'
        : '';
      // Spine A2: code-vs-authored gate divergence chip. The spine's
      // study_verdict writes pipeline_gate.gate_evaluator (computed_gate_verdict)
      // carrying diverges_from_authored. Mirrors the param-enforcement banner:
      // surfaced beside the verdict, connected to its source (the coded
      // evaluator), and labeled code-computed vs authored. Only shown on divergence.
      var cgv = s && s.computed_gate_verdict;
      var divChip = (cgv && cgv.diverges_from_authored)
        ? '<span class="sp-gate-divergence" style="' + pill + 'background:#fffbeb;color:#92400e;border:1px solid #f59e0b" title="Code-computed verdict (spine study_verdict, not human-authored) disagrees with the authored gate_status.">⚠ code: ' + _h(cgv.result || '?') + ' · authored: ' + _h((s && s.gate_status) || '—') + '</span>'
        : '';
      return '<div class="sp-clarity" style="margin:6px 0 2px 0">'
        + '<span style="' + pill + ranBg + '">' + (ranOn ? '▶' : (cs.ran.status === 'running' ? '…' : '○')) + ' ' + _h(cs.ran.label) + '</span>'
        + '<span style="' + pill + tBg + '">' + _h(cs.tests.label) + '</span>'
        + '<span class="sp-verdict ' + cs.verdict.cls + '" style="' + pill + '">' + cs.verdict.glyph + ' ' + _h(cs.verdict.label) + '</span>'
        + divChip
        + amb
        + '</div>';
    }

    // The collapsed study header — a scannable "scientific control panel".
    // Ordering follows the spec: identity → verdict → confidence/evidence →
    // objective → conclusion → metrics → insight → caveat. Every field is
    // optional; an absent field simply doesn't render. Authored one-liners
    // (report.objective/conclusion/main_insight/caveat) win; otherwise we
    // derive from the longer report prose so nothing is silently blank.
    function _studyControlPanel(s, i, decision) {
      var rep = s.report || {};
      var v = _verdictBadge(s, decision);
      var title = s.title || rep.title || _humanizeStudyName(s.name).title;
      var objective  = rep.objective    || _firstSentence(rep.purpose)
                        || _firstSentence((s.purpose || {}).question);
      var conclusion = rep.conclusion   || _firstSentence(rep.result);
      var insight    = rep.main_insight || _firstSentence(rep.interpretation);
      var caveat = rep.caveat;
      if (!caveat && Array.isArray(s.limitations) && s.limitations.length) {
        var l0 = s.limitations[0];
        caveat = (typeof l0 === 'string') ? l0 : (l0 && (l0.text || l0.limitation)) || '';
      }

      // Metadata: keep machine ids visually secondary.
      var runs = s.runs || [];
      var latest = runs.length ? runs[runs.length - 1] : null;
      var updated = (latest && (latest.created_at || latest.timestamp)) || s.last_run || '';
      if (updated) updated = String(updated).replace('T', ' ').slice(0, 16);
      var sha = (generation && generation.git_sha) ? String(generation.git_sha).slice(0, 7) : '';
      var meta = ['<code>' + _h(s.name) + '</code>', 'depth ' + (depthMap[s.name] || 0)];
      if (updated) meta.push('updated ' + _h(updated));
      if (sha) meta.push('git <code>' + _h(sha) + '</code>');

      var conf = (rep.confidence || '').trim();
      var ev   = (rep.evidence_quality || '').trim();

      // Metrics strip: authored key_metrics (strings or {label,value,status})
      // plus an auto-derived test pass ratio and literature-match chip.
      var chips = [];
      (rep.key_metrics || []).forEach(function(m) {
        if (typeof m === 'string') {
          chips.push('<span class="sp-metric">' + _h(m) + '</span>');
        } else if (m && typeof m === 'object') {
          var st = (m.status || '').toLowerCase();
          var icon = st === 'pass' ? '✅ ' : st === 'warn' ? '⚠️ ' : st === 'fail' ? '❌ ' : '';
          var txt = (m.label || '') + (m.value != null ? ': ' + m.value : '');
          chips.push('<span class="sp-metric sp-metric-' + _h(st || 'plain') + '">' + icon + _h(txt) + '</span>');
        }
      });
      var nPass = (decision.passed || []).length, nFail = (decision.failed || []).length;
      if (nPass + nFail) {
        chips.push('<span class="sp-metric sp-metric-' + (nFail ? 'warn' : 'pass') + '">'
                   + nPass + '/' + (nPass + nFail) + ' tests passing</span>');
      }
      if (rep.lit_match) chips.push('<span class="sp-metric">Lit match: ' + _h(rep.lit_match) + '</span>');

      return ''
        + '<div class="sp-top">'
        +   '<span class="sp-num">' + (i + 1) + '.</span>'
        +   '<span class="sp-title">' + _h(title) + '</span>'
        +   '<span class="sp-verdict ' + v.cls + '">' + v.emoji + ' ' + _h(v.label) + '</span>'
        + '</div>'
        + _clarityStrip(s)
        + (objective ? '<div class="sp-objective">' + _h(objective) + '</div>' : '')
        + '<div class="sp-meta">' + meta.join(' · ') + '</div>'
        + ((conf || ev)
            ? '<div class="sp-quality">'
              + (conf ? '<span class="sp-conf sp-conf-' + _h(conf.toLowerCase()) + '">Confidence: ' + _h(conf) + '</span>' : '')
              + (ev   ? '<span class="sp-ev">Evidence: ' + _h(ev) + '</span>' : '')
              + '</div>'
            : '')
        + (conclusion ? '<div class="sp-conclusion"><span class="sp-lbl">Conclusion</span> ' + _h(conclusion) + '</div>' : '')
        + (chips.length ? '<div class="sp-metrics">' + chips.join('') + '</div>' : '')
        + (insight ? '<div class="sp-insight"><span class="sp-lbl">Insight</span> ' + _h(insight) + '</div>' : '')
        + (caveat  ? '<div class="sp-caveat"><span class="sp-lbl">Caveat</span> ' + _h(caveat) + '</div>' : '')
        + '<span class="sp-expand-hint">▸ click to expand full study</span>';
    }

    // Review-readiness gates — mechanical checks that catch the classes of
    // problem an expert reviewer keeps flagging, BEFORE the report reaches them.
    // Computed from already-declared fields (no run data needed), so they fire
    // at design time. Returns {warns:[html], oks:[text]}.
    //   Gate 1 (parameter vs reference): a model_setting's `default` is the
    //     literature/heuristic value; flag when `current` deviates materially.
    //   Gate 2 (duration vs doubling time): flag when the configured run length
    //     can't cover one doubling time τ (steadiness claims need ≥ 1 τ).
    function _reviewReadiness(s) {
      var cond = (s.conditions && typeof s.conditions === 'object') ? s.conditions : {};
      var settings = cond.model_settings || cond.expert_inputs || [];
      var warns = [], oks = [];

      settings.forEach(function(ms) {
        var def = ms.default, cur = ms.current;
        if (typeof def === 'number' && typeof cur === 'number' && def !== 0 && cur !== def) {
          var ratio = cur / def;
          if (ratio < 0.75 || ratio > 1.34) {
            var factor = ratio < 1 ? def / cur : ratio;
            warns.push('Parameter <code>' + _h(ms.name) + '</code> is set to <strong>' + _h(cur)
              + '</strong> but the heuristic/literature default is <strong>' + _h(def) + '</strong> ('
              + (factor >= 10 ? Math.round(factor) : factor.toFixed(1)) + '× off). '
              + 'Justify the deviation in the study or correct it.');
          }
        }
      });

      var tau = null, tauName = null;
      settings.forEach(function(ms) {
        var v = (ms.current != null) ? ms.current : ms.default;
        if (tau == null && typeof v === 'number'
            && /(^|_)(tau|doubling|generation[_ ]?time)/i.test(ms.name || '')) {
          tau = v; tauName = ms.name;
        }
      });
      if (tau != null) {
        var bp = (cond.baseline && cond.baseline.params) || {};
        var nSteps = bp.n_steps, ts = (typeof bp.time_step === 'number' && bp.time_step > 0) ? bp.time_step : 1;
        if (typeof nSteps === 'number') {
          var runMin = nSteps * ts / 60.0;
          if (runMin < tau) {
            warns.push('Configured run is ≈ <strong>' + runMin.toFixed(0) + ' min</strong> (n_steps '
              + nSteps + ' × ' + ts + ' s), shorter than one doubling time τ = <strong>' + _h(tau)
              + ' min</strong> (<code>' + _h(tauName) + '</code>). Steadiness / steady-state claims need ≥ 1 doubling time.');
          } else {
            oks.push('Run ≈ ' + runMin.toFixed(0) + ' min covers ≥ 1 doubling time (τ = ' + tau + ' min).');
          }
        }
      }
      return {warns: warns, oks: oks};
    }

    // SP3b: per-study feedback → action table for the report. Renders the
    // pbg-supplied s.feedback_actions items that carry a proposed action
    // (kind + proposed_text + open/applied status). Read-only — the report
    // never computes the action. Returns '' when there are none.
    function _renderReportFeedbackActions(s, slug) {
      var fa = s && s.feedback_actions;
      if (!fa || !fa.items || !fa.items.length) return '';
      var withActions = fa.items.filter(function(it) { return it && it.action; });
      if (!withActions.length) return '';
      var badge = {
        open:      'background:#fef3c7;color:#92400e;',
        applied:   'background:#d1fae5;color:#065f46;',
        dismissed: 'background:#f1f5f9;color:#64748b;',
      };
      var rows = withActions.map(function(it) {
        var a = it.action || {};
        var st = it.status || 'open';
        return '<tr>'
          + '<td><span style="' + (badge[st] || badge.open)
          +   'padding:1px 8px;border-radius:9999px;font-size:0.82em;'
          +   'font-family:ui-monospace,monospace">' + _h(st) + '</span></td>'
          + '<td><code>' + _h(a.kind || '') + '</code>'
          +   (a.target_finding ? ' <span class="muted small">→ ' + _h(a.target_finding) + '</span>' : '') + '</td>'
          + '<td>' + _h(a.proposed_text || '') + '</td>'
          + '<td class="muted small">' + _h((it.text || '').slice(0, 120)) + '</td>'
          + '</tr>';
      }).join('');
      return '<div class="feedback-actions-panel" id="study-' + slug + '-feedback-actions" '
        + 'style="margin:12px 0;padding:12px 16px;background:#f5f3ff;border:1px solid #8b5cf6;'
        + 'border-left-width:5px;border-radius:6px;color:#3730a3">'
        + '<strong>🔁 Feedback → action (' + withActions.length + ')</strong>'
        + '<table class="readout-table" style="margin-top:8px"><thead><tr>'
        + '<th>Status</th><th>Action</th><th>Proposed</th><th>From feedback</th>'
        + '</tr></thead><tbody>' + rows + '</tbody></table></div>';
    }

    function v3StudySection(s, i, statusBadge, phaseBadge, parents, kids) {
      var slug = _h(s.name);
      var sid = {
        summary:    'study-' + slug + '-summary',
        decision:   'study-' + slug + '-decision',
        takeaways:  'study-' + slug + '-takeaways',
        findings:   'study-' + slug + '-findings',
        sims:       'study-' + slug + '-sims',
        charts:     'study-' + slug + '-charts',
        readouts:   'study-' + slug + '-readouts',
        tests:      'study-' + slug + '-tests',
        conditions: 'study-' + slug + '-conditions',
        build:      'study-' + slug + '-build',
        reqs:       'study-' + slug + '-reqs',
        followups:  'study-' + slug + '-followups',
        discovery:  'study-' + slug + '-discovery',
        limits:     'study-' + slug + '-limitations',
        refs:       'study-' + slug + '-refs',
      };

      var purpose = s.purpose || {};
      var gate = s.pipeline_gate || {};
      var sims = s.simulation_set || [];
      var modelChange = s.model_change;
      var assumptions = s.key_assumptions || [];
      var reqs = s.implementation_requirements || [];
      var readouts = s.readouts || [];
      var tests = s.behavior_tests || s.expected_behavior || [];
      var decide = s.conclusion_logic || {};
      var limitations = s.limitations || [];
      // Tolerate a string (authors sometimes write limitations as prose, not a list).
      if (typeof limitations === 'string') limitations = limitations.trim() ? [limitations] : [];
      var followUps = s.follow_up_studies || [];
      // Discovery Implications — alternate hypotheses, mechanism-update
      // proposals, and the richer followup_study_proposals (successor to
      // follow_up_studies). All fields optional; section hidden when empty.
      var discImpl = (s.discovery_implications && typeof s.discovery_implications === 'object')
                      ? s.discovery_implications : {};
      var followupProposals = discImpl.followup_study_proposals || [];
      var findings = s.findings || [];
      var bib = (s.bibliography && s.bibliography.bib_keys) || [];
      var charts = (chartsByStudy && chartsByStudy[s.name]) || [];

      var hasBuild = !!modelChange || assumptions.length || reqs.length;
      var ifPass = decide.if_primary_tests_pass || decide.if_pass;
      var ifFail = decide.if_primary_tests_fail || decide.if_fail;
      var runs = s.runs || [];
      var latestRun = runs.length ? runs[runs.length - 1] : null;

      // Derive decision + plain-English summary FIRST so they can be linked
      // from the sub-nav and rendered at the top of the section.
      var decision = _decideDecision(s);
      var summaryText = _studySummary(s, decision);
      var controlPanelHtml = _studyControlPanel(s, i, decision);
      var verdictBadge = _verdictBadge(s, decision);
      var _review = _reviewReadiness(s);
      var reviewHtml = _review.warns.length
        ? '<div class="review-gate" id="study-' + slug + '-review">'
          + '<strong>⚠ Review-readiness checks (' + _review.warns.length + ')</strong>'
          + '<div class="review-gate-sub">Caught before expert review — fix or justify each.</div>'
          + '<ul>' + _review.warns.map(function(w) { return '<li>' + w + '</li>'; }).join('') + '</ul>'
          + '</div>'
        : '';
      var hasDecide = !!(ifPass || ifFail
                         || (decide.implementation_validation && decide.implementation_validation.length)
                         || (decide.biological_validation && decide.biological_validation.length)
                         || s.conclusion || latestRun);

      // Sub-nav links — order MUST mirror the report's section render order
      // (the post-execution assembly below): embeds → summary → findings →
      // conditions → ran → measured → charts → tests → model changes →
      // build/fix → next steps → limitations → refs → decision (last).
      var links = [];
      var nEmbedsForStudy = (embedsByStudy[s.name] || []).length;
      if (nEmbedsForStudy)
        links.push('<a href="#study-' + slug + '-embeds">Visualizations <span class="sn-count">' + nEmbedsForStudy + '</span></a>');
      links.push('<a href="#' + sid.summary + '">Summary</a>');
      if (findings.length)    links.push('<a href="#' + sid.findings + '">Findings <span class="sn-count">' + findings.length + '</span></a>');
      var _hasDiscovery = !!(
        (discImpl.alternate_hypotheses || []).length
        || (discImpl.mechanism_update_proposals || []).length
        || followupProposals.length
        || (discImpl.resolved_uncertainties || []).length
        || (discImpl.remaining_uncertainties || []).length);
      if (_hasDiscovery) {
        var _nDisc = (discImpl.alternate_hypotheses || []).length
                   + (discImpl.mechanism_update_proposals || []).length
                   + followupProposals.length;
        links.push('<a href="#' + sid.discovery + '">Discovery implications'
                   + (_nDisc ? ' <span class="sn-count">' + _nDisc + '</span>' : '') + '</a>');
      }
      // Conditions sub-nav link: rendered when v4 ``conditions:`` exists.
      var _cond = (s.conditions && typeof s.conditions === 'object') ? s.conditions : null;
      var _nVar = (_cond && _cond.variants || []).length;
      var _nEI  = (_cond && (_cond.model_settings || _cond.expert_inputs) || []).length;
      if (_cond) {
        var _condCount = _nVar + _nEI;
        links.push('<a href="#' + sid.conditions + '">Conditions ' +
                   (_condCount ? '<span class="sn-count">' + _condCount + '</span>' : '') + '</a>');
      }
      var _ranCount = sims.length || (s.baseline || []).length || (s.runs || []).length;
      links.push('<a href="#' + sid.sims + '">What we ran' + (_ranCount ? ' <span class="sn-count">' + _ranCount + '</span>' : '') + '</a>');
      if (readouts.length)    links.push('<a href="#' + sid.readouts + '">What we measured <span class="sn-count">' + readouts.length + '</span></a>');
      if (charts.length)      links.push('<a href="#' + sid.charts + '">Charts <span class="sn-count">' + charts.length + '</span></a>');
      if (tests.length)       links.push('<a href="#' + sid.tests + '">How we judge it <span class="sn-count">' + tests.length + '</span></a>');
      if (hasBuild)           links.push('<a href="#' + sid.build + '">Model changes</a>');
      if (reqs.length)        links.push('<a href="#' + sid.reqs + '">What to build / fix <span class="sn-count">' + reqs.length + '</span></a>');
      if (followUps.length)   links.push('<a href="#' + sid.followups + '">Next steps <span class="sn-count">' + followUps.length + '</span></a>');
      if (limitations.length) links.push('<a href="#' + sid.limits + '">Limitations <span class="sn-count">' + limitations.length + '</span></a>');
      if (bib.length)         links.push('<a href="#' + sid.refs + '">Cited refs <span class="sn-count">' + bib.length + '</span></a>');
      links.push('<a href="#' + sid.decision + '">Decision</a>');

      var dependsBrief = parents ? 'Depends on: ' + parents : '<em>Root study (no dependencies)</em>';

      var subNav = ''
        + '<div class="study-nav">'
        +   '<div class="study-nav-row1">'
        +     '<span class="study-nav-num">' + (i + 1) + '.</span>'
        +     '<strong class="study-nav-name">' + _h(s.name) + '</strong>'
        +     phaseBadge + statusBadge
        +     '<span class="study-nav-deps muted small">' + dependsBrief + '</span>'
        +   '</div>'
        +   '<nav class="study-nav-row2">' + links.join('') + '</nav>'
        +   '<span class="sn-collapse-hint" data-collapse="study">▴ click to collapse full study</span>'
        + '</div>';

      // ── COMPACT REPORT BLOCK (authored: Purpose·Setup·Result·… ) ──────
      // Leads each study with the uniform human-facing pattern. Reads
      // s.report; absent → just the plain-English summary below (fallback).
      var _rep = s.report || {};
      var reportHtml = '';
      (function() {
        var rows = [
          ['Purpose', _rep.purpose], ['Setup', _rep.setup],
          ['Result', _rep.result], ['Interpretation', _rep.interpretation],
          ['Decision', _rep.decision], ['Next action', _rep.next_action],
        ].filter(function(r) { return r[1]; });
        if (!rows.length) return;
        reportHtml = '<div class="study-report">' + rows.map(function(r) {
          return '<div class="study-report-row"><span class="srl">' + r[0] + '</span>'
               + '<span class="srv">' + _multiline(r[1]) + '</span></div>';
        }).join('') + '</div>';
      })();

      // ── PLAIN-ENGLISH SUMMARY ─────────────────────────────────────────
      var summaryHtml = reportHtml
        + '<div id="' + sid.summary + '" class="study-summary">'
        + '<h3>Overview</h3>'
        + '<p class="study-summary-text">' + _h(summaryText) + '</p>'
        + '<details class="tech-details"><summary>Purpose &amp; background (study design)</summary>'
        +   (purpose.question         ? '<div class="callout cl-blue"><strong>Question.</strong> ' + _multiline(purpose.question) + '</div>' : '')
        +   (purpose.mechanism        ? '<div class="callout cl-yellow"><strong>Mechanism / Model change.</strong> ' + _multiline(purpose.mechanism) + '</div>' : '')
        +   (purpose.expected_outcome ? '<div class="callout cl-green"><strong>Expected outcome.</strong> ' + _multiline(purpose.expected_outcome) + '</div>' : '')
        + '</details>'
        + '</div>';

      // ── DECISION BOX ──────────────────────────────────────────────────
      function _listAsBullets(arr, emptyText) {
        if (!arr || !arr.length) return '<em class="muted">' + emptyText + '</em>';
        return '<ul style="margin:4px 0 0 18px;padding:0">' + arr.map(function(x){return '<li>' + _h(x) + '</li>';}).join('') + '</ul>';
      }
      var decisionTechnical = '';
      if (gate.prerequisites || gate.enables || gate.proceed_condition || ifPass || ifFail) {
        var prereqStr = (gate.prerequisites && gate.prerequisites.length)
          ? gate.prerequisites.map(function(p){
              // prerequisites are {study, condition} objects (or bare slug strings).
              var slug = (p && typeof p === 'object') ? (p.study || p.slug || '') : p;
              var cond = (p && typeof p === 'object' && p.condition) ? ' <span class="muted small">(' + _h(p.condition) + ')</span>' : '';
              return '<a href="#study-' + _h(slug) + '"><code>' + _h(slug) + '</code></a>' + cond;
            }).join(' · ')
          : '<em class="muted">none (root study)</em>';
        var enablesStr = (gate.enables && gate.enables.length)
          ? gate.enables.map(function(p){
              var slug = (p && typeof p === 'object') ? (p.study || p.slug || '') : p;
              var cond = (p && typeof p === 'object' && p.condition) ? ' <span class="muted small">(' + _h(p.condition) + ')</span>' : '';
              return '<a href="#study-' + _h(slug) + '"><code>' + _h(slug) + '</code></a>' + cond;
            }).join(' · ')
          : '<em class="muted">—</em>';
        decisionTechnical = '<details class="tech-details"><summary>Pipeline gate &amp; conclusion logic (technical)</summary>'
          + '<p><strong>Prerequisites:</strong> ' + prereqStr + '</p>'
          + '<p><strong>Enables:</strong> ' + enablesStr + '</p>'
          + (gate.proceed_condition ? '<p><strong>Proceed when:</strong> ' + _multiline(gate.proceed_condition) + '</p>' : '')
          + (ifPass ? '<div class="callout cl-green"><strong>If primary tests pass:</strong> ' + (typeof ifPass === 'string' ? _multiline(ifPass) : _multiline((ifPass.implementation_status || '') + (ifPass.biological_validation ? ' ' + ifPass.biological_validation : ''))) + '</div>' : '')
          + (ifFail ? '<div class="callout cl-red"><strong>If primary tests fail:</strong> ' + (typeof ifFail === 'string' ? _multiline(ifFail) : _multiline((ifFail.block_downstream || JSON.stringify(ifFail.diagnose || '')))) + '</div>' : '')
          + '</details>';
      }
      var decisionHtml = '<div id="' + sid.decision + '" class="decision-box decision-' + decision.cls + '">'
        + '<div class="decision-header">'
        +   '<h3 class="decision-title">Can we move to the next study?</h3>'
        +   '<span class="decision-status">' + _h(decision.label) + '</span>'
        + '</div>'
        + '<div class="decision-grid">'
        +   '<div class="decision-cell decision-cell-pass"><strong>✓ Passed</strong>' + _listAsBullets(decision.passed, 'nothing yet') + '</div>'
        +   '<div class="decision-cell decision-cell-fail"><strong>✗ Failed</strong>' + _listAsBullets(decision.failed, 'nothing failing') + '</div>'
        +   '<div class="decision-cell decision-cell-block"><strong>⛔ Blocks the next study</strong>' + _listAsBullets(decision.blocks, 'nothing blocking') + '</div>'
        +   '<div class="decision-cell decision-cell-next"><strong>→ Immediate next action</strong><div style="margin-top:4px">' + _h(decision.next) + '</div></div>'
        + '</div>'
        + decisionTechnical
        + '</div>';

      // ── KEY TAKEAWAYS + GROUPED FINDINGS ─────────────────────────────
      var takeawaysHtml = '';
      if (findings.length) {
        var groups = {biological: [], computational: [], methodological: [], other: []};
        findings.forEach(function(f) {
          var k = f.kind || 'other';
          (groups[k] || groups.other).push(f);
        });

        // 1) Short takeaways list — one bullet per finding (statement first sentence).
        var takeawayItems = findings.map(function(f) {
          var status = f.status || 'novel';
          var glyph = ({confirms:'✓', partial:'◐', contradicts:'✗', novel:'◆'})[status] || '◆';
          var stmt = (f.statement || (f.id||'').replace(/[-_]/g,' ')).split('\n')[0].split('.')[0];
          if (stmt.length > 180) stmt = stmt.slice(0, 177) + '…';
          return '<li class="takeaway-' + status + '"><span class="takeaway-glyph">' + glyph + '</span> '
               + '<a href="#finding-' + _h(f.id || '') + '">' + _h(stmt) + '</a></li>';
        }).join('');

        // 2) Detailed cards grouped by kind, each with a heading.
        var kindHeader = {
          biological:     'Biological findings',
          computational:  'Infrastructure / computational findings',
          methodological: 'Methodological findings',
          other:          'Other findings',
        };
        function _renderFinding(f) {
          var status = f.status || 'novel';
          var glyph = ({confirms:'✓', partial:'◐', contradicts:'✗', novel:'◆'})[status] || '◆';
          var statusText = (f.kind === 'biological')
            ? (status + ' literature')
            : ({confirms:'confirmed', partial:'partial result', contradicts:'correction', novel:'new result'}[status] || status);
          var ev = f.evidence || {};
          var exp = f.expected || {};
          var ref = f.expert_reference || {};
          var prov = f.provenance || {};
          // Anchor a (possibly descriptive) test reference: the leading
          // identifier token becomes the #<prefix>-<id> target, the full text
          // stays as the link label so the reader can trace the value to its
          // source (the test card) instead of reading dead code.  Only used
          // for the TEST case: the report renders test cards (id="test-..."),
          // so #test- anchors resolve.  The report has NO per-run rows, so run
          // references are rendered as plain <code> (see _traceRun) — anchoring
          // them would produce dead run-row links.  (The STUDY page does emit
          // per-run rows and keeps its run anchors; that lives in study-detail.)
          function _traceLink(prefix, val) {
            var s = String(val);
            var tok = (s.match(/^[A-Za-z0-9_.\-]+/) || [s])[0];
            return '<a href="#' + prefix + '-' + _h(tok) + '"><code>' + _h(s) + '</code></a>';
          }
          // Run references in the report: plain <code>, no anchor (no target).
          function _traceRun(val) {
            return '<code>' + _h(String(val)) + '</code>';
          }
          var techParts = [];
          if (ev.from_test) techParts.push('test: ' + _traceLink('test', ev.from_test));
          if (ev.from_run)  techParts.push('run: ' + _traceRun(ev.from_run));
          if (ev.window)    techParts.push('window: ' + _h(ev.window));
          if (ev.smoking_gun) techParts.push('<details style="margin-top:4px"><summary>Smoking gun</summary><pre style="white-space:pre-wrap;font-size:0.85em;background:#fff;padding:6px;border-radius:3px">' + _h(ev.smoking_gun) + '</pre></details>');
          if (ev.discovered_during) techParts.push('discovered during: <code>' + _h(ev.discovered_during) + '</code>');

          var techDisclosure = techParts.length
            ? '<details class="tech-details"><summary>Technical details</summary>' + techParts.join('<br>') + '</details>'
            : '';

          var evMain = '';
          if (ev.observed != null) {
            evMain = '<div class="finding-evidence"><strong>What we saw:</strong> '
                   + _h(String(ev.observed)) + (ev.units ? ' ' + _h(ev.units) : '') + '</div>';
          }
          var expMain = '';
          if (exp.range != null || exp.threshold != null || exp.summary) {
            var rngStr = '';
            if (exp.range != null) {
              var rng = Array.isArray(exp.range) ? '[' + exp.range.join(', ') + ']' : String(exp.range);
              rngStr = '<strong>What the literature says:</strong> ' + _h(rng);
            } else if (exp.threshold != null) {
              rngStr = '<strong>Target threshold:</strong> ' + _h(String(exp.threshold));
            }
            expMain = '<div class="finding-expected">' + rngStr
                    + (exp.summary ? '<div style="margin-top:4px">' + _multiline(exp.summary) + '</div>' : '')
                    + (exp.cites && exp.cites.length ? '<div class="muted small" style="margin-top:4px">Cites: ' + exp.cites.map(function(c){return '<code>' + _h(c) + '</code>';}).join(', ') + '</div>' : '')
                    + '</div>';
          }

          // Traceability block (spine B1): surface the finding's computed
          // distance + provenance, connected to source. The headline computed
          // number `divergence_factor` (how far observed is from expected) was
          // previously dropped; render it prominently. Link the cited test +
          // run, list provenance.run_ids (linked), and inline the cited test's
          // pass_if band so the reader sees what "passing" meant without hunting.
          var traceBits = [];
          if (ev.divergence_factor != null) {
            traceBits.push('<span class="finding-divergence" style="font-weight:600">×'
              + _h(String(ev.divergence_factor)) + ' vs expected</span>');
          }
          if (ev.from_test) traceBits.push('test: ' + _traceLink('test', ev.from_test));
          if (ev.from_run)  traceBits.push('run: ' + _traceRun(ev.from_run));
          var runIds = prov.run_ids || [];
          if (Array.isArray(runIds) && runIds.length) {
            traceBits.push('runs: ' + runIds.map(function(rid) {
              return '<code>' + _h(String(rid)) + '</code>';
            }).join(', '));
          }
          // Inline the cited test's pass_if band (look it up by from_test).
          var citedBand = '';
          if (ev.from_test) {
            var citedTok = (String(ev.from_test).match(/^[A-Za-z0-9_.\-]+/) || [''])[0];
            var citedTest = (tests || []).filter(function(t){ return t && t.name === citedTok; })[0];
            if (citedTest && (citedTest.pass_if || citedTest.expect)) {
              citedBand = '<div class="pass_if-band muted small" style="margin-top:4px">passes if '
                + (citedTest.measure ? _measureText(citedTest.measure) + ' ' : '')
                + '<strong>' + _h(_passIfText(citedTest.pass_if || citedTest.expect)) + '</strong></div>';
            }
          }
          var traceBlock = (traceBits.length || citedBand)
            ? '<div class="finding-traceability" style="margin-top:6px;padding:6px 10px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:4px;font-size:0.88em">'
              + (traceBits.length ? '<span class="muted small">traceability:</span> ' + traceBits.join(' · ') : '')
              + citedBand
              + '</div>'
            : '';

          var refBlock = '';
          if (ref.doc || ref.quote || ref.note) {
            var refBody = '';
            if (ref.quote) refBody += '<blockquote class="finding-expert-quote">' + _multiline(ref.quote) + '</blockquote>';
            if (ref.note) refBody += '<div class="finding-expert-note">' + _multiline(ref.note) + '</div>';
            var refLabel = ref.doc ? 'Expert reference: <code>' + _h(ref.doc) + '</code>' : 'Expert reference';
            if (ref.section) refLabel += ' (' + _h(ref.section) + ')';
            refBlock = '<details class="finding-expert"><summary>' + refLabel + '</summary>' + refBody + '</details>';
          }

          // Optional sweep visualisation when evidence carries a sweep table
          // (e.g. F-08 / F-10 calibration sweeps). Renders an inline SVG
          // bar chart of the numeric value across multipliers.
          var sweepChart = '';
          var sweepData = ev.sweep_table || ev.sweep;
          if (sweepData && typeof sweepData === 'object') {
            sweepChart = _renderSweepChart(sweepData);
          }

          return '<div class="finding-card finding-kind-' + _h(f.kind || 'other') + ' finding-status-' + _h(status) + '" id="finding-' + _h(f.id || '') + '">'
               +   '<div class="finding-header">'
               +     '<span class="finding-status-glyph">' + glyph + '</span>'
               +     '<span class="finding-id">' + _h(f.id || '') + '</span>'
               +     '<span class="finding-status-text">' + _h(statusText) + '</span>'
               +     _findingWeightChip(f._evidential_weight)
               +     _findingChips(f)
               +   '</div>'
               +   '<div class="finding-statement">' + _multiline(f.statement || (f.id ? f.id.replace(/[-_]/g,' ') : '(no statement)')) + '</div>'
               +   evMain
               +   expMain
               +   traceBlock
               +   (f.explanation ? '<div class="finding-explanation"><em>Why:</em> ' + _multiline(f.explanation) + '</div>' : '')
               +   sweepChart
               +   refBlock
               +   (f.next_action ? '<div class="finding-next"><strong>→ Next:</strong> ' + _multiline(f.next_action) + '</div>' : '')
               +   (f.seeded_study ? '<div class="finding-seeded"><strong>→ seeded study:</strong> <a href="/studies/' + encodeURIComponent(f.seeded_study) + '">' + _h(f.seeded_study) + '</a></div>' : '')
               +   techDisclosure
               + '</div>';
        }

        takeawaysHtml = '<div id="' + sid.findings + '" class="findings-section">'
          + '<h3>Detailed findings</h3>'
          + Object.keys(groups).filter(function(k){return groups[k].length;}).map(function(k) {
              return '<h4 class="findings-group-header">' + kindHeader[k] + ' <span class="muted small">(' + groups[k].length + ')</span></h4>'
                   + groups[k].map(_renderFinding).join('');
            }).join('')
          + '</div>';
      }

      // ── WHAT DID/WILL WE RUN? (Simulations) ──────────────────────────
      var simsHtml = '';
      // What we ran — ENFORCED. The composite(s) + parameter settings actually
      // simulated. Prefer the v3 simulation_set; else derive from the dashboard-
      // managed baseline (composite + params) + recorded runs + robustness
      // (seeds) — which is how the autopoiesis studies record runs. Always
      // rendered; a study with neither gets an explicit gap notice. Each
      // composite links out to the bigraph-loom explorer (popped out, live only).
      function _short(model) {
        if (!model) return '';
        var p = String(model).split('.');
        return p[p.length - 1];
      }
      // A composite reference rendered as a one-click pop-out to the bigraph-loom
      // STATIC view (read-only): /bigraph-loom/?static=1&stateUrl=/api/composite-
      // state/<ref>.json. Works from any report on the live dashboard.
      function _loomStaticPopout(composite) {
        // Pop out the bigraph-loom STATIC (read-only) view of the composite. The
        // dashboard origin is captured at GENERATION time and baked in as an
        // ABSOLUTE URL, so the button works whether the report is viewed inline,
        // in an iframe/srcdoc, or downloaded (as long as that dashboard is up) —
        // the earlier relative URL + protocol guard failed in non-http contexts.
        // stateUrl hits /api/composite-state?ref=<id>; the loom unwraps {state}.
        var origin = (typeof location !== 'undefined' && location.origin
                      && /^https?:/.test(location.origin)) ? location.origin : '';
        return "var o='" + origin + "';"
          + "var s=o+'/api/composite-state?ref='+encodeURIComponent('" + _h(composite) + "');"
          + "var u=o+'/bigraph-loom/index.html?static=1&stateUrl='+encodeURIComponent(s);"
          + "window.open(u,'loom','width=1200,height=840');";
      }
      function _compositeCell(composite) {
        if (!composite) return '<span class="muted">—</span>';
        return '<a href="#" class="composite-loom-link" '
          + 'title="Open a static view of this composite in bigraph-loom" '
          + 'onclick="event.preventDefault(); ' + _loomStaticPopout(composite) + '">'
          + '<code>' + _h(_short(composite)) + '</code> <span aria-hidden="true">↗</span></a>';
      }
      function _paramsCell(params) {
        if (!params || typeof params !== 'object' || !Object.keys(params).length)
          return '<span class="muted">default parameters</span>';
        return Object.keys(params).map(function(k) {
          return '<code>' + _h(k) + ' = ' + _h(JSON.stringify(params[k])) + '</code>';
        }).join(' ');
      }
      if (sims.length) {
        // The first sim is the reference; describe each row as its diff from it.
        var baseSim = sims[0] || {};
        var baseParams = baseSim.params || {};
        var baseModel = baseSim.base_model;
        function _changes(sim) {
          var bits = [];
          if (sim === baseSim) return '<em class="muted">reference baseline</em>';
          if (sim.base_model && sim.base_model !== baseModel)
            bits.push('different model <code>' + _h(_short(sim.base_model)) + '</code>');
          // perturbation dict wins; else diff params vs the baseline sim
          var changed = sim.perturbation && Object.keys(sim.perturbation).length
            ? sim.perturbation
            : (function() {
                var d = {}, p = sim.params || {};
                Object.keys(p).forEach(function(k) {
                  if (k === 'seed' || k === 'cache_dir' || k === 'n_steps') return;
                  if (JSON.stringify(p[k]) !== JSON.stringify(baseParams[k])) d[k] = p[k];
                });
                return d;
              })();
          var keys = Object.keys(changed).filter(function(k){return changed[k] !== null;});
          if (keys.length) bits.push(keys.slice(0, 6).map(function(k) {
            return '<code>' + _h(k) + '=' + _h(JSON.stringify(changed[k])) + '</code>';
          }).join(' '));
          return bits.length ? bits.join('; ') : '<em class="muted">same params, longer/other</em>';
        }
        var rows = sims.map(function(sim) {
          var statusClass = sim.status === 'ready' ? 'sim-status-ready'
                          : sim.status === 'gated' ? 'sim-status-gated'
                          : sim.status === 'ran' ? 'sim-status-ran' : 'sim-status-unknown';
          var statusPill = sim.status ? '<span class="sim-status-pill ' + statusClass + '">' + _h(sim.status) + '</span>' : '<span class="muted small">—</span>';
          var runParts = [];
          if (sim.condition) runParts.push(_h(sim.condition));
          var ns = (sim.params && sim.params.n_steps);
          if (sim.duration_min != null) runParts.push(_h(sim.duration_min) + ' min');
          else if (ns != null) runParts.push(_h(ns) + ' steps');
          if (sim.seeds && sim.seeds.length) runParts.push(sim.seeds.length + ' seed' + (sim.seeds.length === 1 ? '' : 's'));
          var tests = sim.applies_tests || sim.tests || [];
          var feeds = (Array.isArray(tests) && tests.length)
            ? '<div class="sim-feeds muted small">feeds: ' + tests.map(function(t){return '<code>' + _h(t) + '</code>';}).join(' ') + '</div>' : '';
          return '<tr>'
            + '<td><strong>' + _h(sim.name || '(unnamed)') + '</strong>' + feeds + '</td>'
            + '<td>' + _compositeCell(sim.base_model) + '</td>'
            + '<td>' + _changes(sim) + '</td>'
            + '<td class="muted small">' + (runParts.join(' · ') || '—') + '</td>'
            + '<td>' + statusPill + '</td>'
            + '</tr>';
        }).join('');
        simsHtml = '<div id="' + sid.sims + '"><h3>What we ran <span class="muted small">(' + sims.length + ' simulation' + (sims.length === 1 ? '' : 's') + ')</span></h3>'
          + '<p class="muted small" style="margin:0 0 8px 0">One row per concrete run: the model composite (click ↗ to open it in the bigraph-loom explorer), what changes vs the reference baseline, the condition / length, and its status.</p>'
          + '<table class="sim-table"><thead><tr><th>Simulation</th><th>Composite</th><th>Changes vs baseline</th><th>Run</th><th>Status</th></tr></thead>'
          + '<tbody>' + rows + '</tbody></table>'
          + '</div>';
      } else {
        // No simulation_set — derive what was run from the dashboard-managed
        // baseline (composite + parameter settings), recorded runs, and
        // robustness (seeds). This is how the autopoiesis studies record runs.
        var baseline = s.baseline || [];
        var runsArr = s.runs || [];
        var rob = s.robustness || {};
        var runByName = {};
        runsArr.forEach(function(r) { if (r && r.name) runByName[r.name] = r; });
        var replCell = (rob && (rob.n_replicates || (rob.seeds && rob.seeds.length)))
          ? ((rob.n_replicates || rob.seeds.length) + ' seed'
             + ((rob.n_replicates || rob.seeds.length) === 1 ? '' : 's')
             + (rob.parameter_sweep ? ' + sweep' : ''))
          : (runsArr.length ? '1 run' : '—');
        var entries = baseline.length
          ? baseline
          : runsArr.map(function(r) { return {name: r.name, composite: r.composite, params: null}; });
        if (entries.length) {
          var brows = entries.map(function(b) {
            var run = runByName[b.name] || runsArr[0] || {};
            var status = run.status || 'recorded';
            return '<tr>'
              + '<td><strong>' + _h(b.name || 'baseline') + '</strong></td>'
              + '<td>' + _compositeCell(b.composite) + '</td>'
              + '<td>' + _paramsCell(b.params) + '</td>'
              + '<td class="muted small">' + _h(replCell) + '</td>'
              + '<td><span class="sim-status-pill sim-status-ran">' + _h(status) + '</span></td>'
              + '</tr>';
          }).join('');
          simsHtml = '<div id="' + sid.sims + '"><h3>What we ran <span class="muted small">(composite + parameters)</span></h3>'
            + '<p class="muted small" style="margin:0 0 8px 0">The composite(s) and parameter settings actually simulated for this study (from its baseline). Click a composite ↗ to open it in the bigraph-loom explorer.</p>'
            + '<table class="sim-table"><thead><tr><th>Run</th><th>Composite</th><th>Parameters</th><th>Replication</th><th>Status</th></tr></thead>'
            + '<tbody>' + brows + '</tbody></table>'
            + '</div>';
        } else {
          // ENFORCED: a study with no composite/params recorded is flagged.
          simsHtml = '<div id="' + sid.sims + '"><h3>What we ran</h3>'
            + '<p style="margin:0;color:#b45309">⚠ No composite or parameters recorded for this study — declare a baseline (composite + parameter settings) or a simulation_set so the report shows what was simulated.</p>'
            + '</div>';
        }
      }

      // ── PROMINENT MODEL BANNER ───────────────────────────────────────────
      // Every study runs at least one composite — surface it at the TOP of the
      // study with its key parameters and a one-click pop-out to the bigraph-loom
      // STATIC view. Enforced: a study with no composite is flagged in red.
      var modelBannerHtml = (function() {
        var entries = [];
        if (sims.length) {
          var seen = {};
          sims.forEach(function(sm) {
            var c = sm.base_model;
            if (c && !seen[c]) { seen[c] = 1; entries.push({composite: c, params: sm.params}); }
          });
        } else if ((s.baseline || []).length) {
          (s.baseline).forEach(function(b) { entries.push({composite: b.composite, params: b.params}); });
        } else {
          (s.runs || []).forEach(function(r) { if (r.composite) entries.push({composite: r.composite, params: null}); });
        }
        if (!entries.length) {
          return '<div class="study-model-banner study-model-missing" style="margin:10px 0;padding:12px 16px;'
            + 'background:#fef2f2;border:1px solid #fecaca;border-left:5px solid #dc2626;border-radius:8px;color:#991b1b">'
            + '<strong>⚠ No model declared.</strong> Every study must run at least one composite — declare a '
            + 'baseline (composite + parameters) so this study is reproducible.</div>';
        }
        var rows = entries.map(function(e) {
          var params = (e.params && typeof e.params === 'object' && Object.keys(e.params).length)
            ? Object.keys(e.params).map(function(k) { return '<code>' + _h(k) + '=' + _h(JSON.stringify(e.params[k])) + '</code>'; }).join(' ')
            : '<span class="muted">default parameters</span>';
          var btn = e.composite
            ? '<button class="model-explore-btn" onclick="' + _loomStaticPopout(e.composite) + '" '
              + 'style="font-size:0.92em;font-weight:600;padding:5px 12px;border:1px solid #2563eb;background:#eff6ff;'
              + 'color:#1e40af;border-radius:6px;cursor:pointer;white-space:nowrap">🧬 ' + _h(_short(e.composite))
              + ' — explore in bigraph-loom ↗</button>'
            : '<span class="muted">(no composite)</span>';
          return '<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:6px">'
            + btn + '<span style="font-size:0.88em;color:#475569">' + params + '</span></div>';
        }).join('');
        return '<div class="study-model-banner" style="margin:10px 0;padding:12px 16px;'
          + 'background:#f0f9ff;border:1px solid #bae6fd;border-left:5px solid #2563eb;border-radius:8px">'
          + '<div style="font-weight:700;color:#0c4a6e">Model</div>'
          + '<div class="muted small" style="margin-top:2px">The composite(s) this study runs and their parameters — '
          + 'click to open a static view in the bigraph-loom explorer.</div>'
          + rows + '</div>';
      })();

      // ── CHARTS (visualisations from runs.db) ─────────────────────────
      var chartsHtml = charts.length
        ? '<div id="' + sid.charts + '">'
          + '<h3 style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">'
          + '<span>Visualisations from the latest run</span>'
          + '<button type="button" class="chart-refresh-btn" data-study="' + _h(s.name) + '"'
          + ' onclick="window._refreshStudyViz(this)"'
          + ' style="font-size:12px;padding:2px 10px;border-radius:6px;border:1px solid #cbd5e1;'
          + 'background:#f8fafc;color:#334155;cursor:pointer;">↻ Refresh visualizations</button>'
          + '<span class="chart-refresh-status muted small" style="margin-left:4px;"></span>'
          + '</h3>'
          + _renderChartCardsHtml(charts, slug)
          + '</div>'
        : '';

      // ── WHAT DID/WILL WE MEASURE? (Readouts) ─────────────────────────
      var readoutsHtml = readouts.length
        ? '<div id="' + sid.readouts + '"><h3>What did/will we measure? <span class="muted small">(' + readouts.length + ' readouts)</span></h3>'
          + '<p class="muted small" style="margin:0 0 8px 0">Quantities we extract from each simulation run to evaluate the study\'s tests.</p>'
          + '<table class="readout-table"><thead><tr><th>Readout</th><th>Status</th><th>Path</th><th>Description</th></tr></thead><tbody>'
          + readouts.map(function(r) {
              var path = r.path || r.identifier || r.store_path;
              var blocked = (r.blocked_by_requirements && r.blocked_by_requirements.length)
                ? '<div class="muted small">⛔ blocked by ' + r.blocked_by_requirements.map(function(b){return '<code>' + _h(b) + '</code>';}).join(', ') + '</div>' : '';
              return '<tr>'
                + '<td><strong>' + _h(r.name || '') + '</strong></td>'
                + '<td class="muted small">' + (r.status ? _h(r.status) : '—') + '</td>'
                + '<td>' + (path ? '<code>' + _h(path) + '</code>' : '<span class="muted">—</span>')
                  + (r.units ? ' <span class="muted small">(' + _h(r.units) + ')</span>' : '') + '</td>'
                + '<td>' + _h(r.notes || r.description || '') + blocked + '</td>'
                + '</tr>';
            }).join('')
          + '</tbody></table>'
          + '</div>'
        : '';

      // ── HOW DO WE JUDGE SUCCESS? (Tests, claim-first) ────────────────
      var testsHtml = '';
      if (tests.length) {
        // Aggregate latest outcomes by test name so we can show PASS/FAIL pills.
        // Merge BOTH the authored outcomes AND the run/outcome-spine
        // evaluator-computed outcomes, so each test surfaces how it actually ran
        // (measured_value, evaluated_by code/agent) and whether the code verdict
        // agrees with the authored one (reconcile).
        var outcomeByTest = {};
        if (latestRun && latestRun.outcomes) {
          Object.keys(latestRun.outcomes).forEach(function(k) { outcomeByTest[k] = Object.assign({}, latestRun.outcomes[k]); });
        }
        if (latestRun && latestRun.computed_outcomes) {
          Object.keys(latestRun.computed_outcomes).forEach(function(k) {
            var c = latestRun.computed_outcomes[k] || {};
            var base = outcomeByTest[k] || {};
            if (base.result == null && c.result != null) base.result = c.result;   // code verdict when no authored one
            if (c.measured_value != null && base.measured_value == null) base.measured_value = c.measured_value;
            if (c.evaluated_by) base.evaluated_by = c.evaluated_by;   // code | agent | needs_rerun
            if (c.operator) base.operator = c.operator;
            if (c.reconcile) base.reconcile = c.reconcile;            // agree | divergent | no_authored
            if (base.detail == null && (c.detail || c.reason)) base.detail = c.detail || c.reason;
            outcomeByTest[k] = base;
          });
        }
        // At-a-glance summary so a reviewer doesn't have to count pills.
        var _tc = { PASS: 0, FAIL: 0, PARTIAL: 0, SKIP: 0, PENDING: 0 };
        tests.forEach(function(t) {
          var o = outcomeByTest[t.name];
          var r = (o && o.result) || t.result || (t.status === 'gated' ? 'GATED' : 'PENDING');
          if (r === 'PASS') _tc.PASS++; else if (r === 'FAIL') _tc.FAIL++;
          else if (r === 'PARTIAL') _tc.PARTIAL++;
          else if (r === 'SKIP') _tc.SKIP++; else _tc.PENDING++;
        });
        var _tcParts = [];
        if (_tc.PASS) _tcParts.push(_tc.PASS + ' ✓ passed');
        if (_tc.FAIL) _tcParts.push(_tc.FAIL + ' ✗ failed');
        if (_tc.PARTIAL) _tcParts.push(_tc.PARTIAL + ' ◐ partial');
        if (_tc.SKIP) _tcParts.push(_tc.SKIP + ' ⏭ skipped');
        if (_tc.PENDING) _tcParts.push(_tc.PENDING + ' ⏳ pending');
        var _tcSummary = _tcParts.length ? (' — ' + _tcParts.join(' · ')) : '';
        testsHtml = '<div id="' + sid.tests + '"><h3>How do we judge success? <span class="muted small">(' + tests.length + ' tests' + _tcSummary + ')</span></h3>'
          + '<p class="muted small" style="margin:0 0 8px 0">Each test makes a specific scientific claim with a machine-checkable criterion (<code>measure</code> + <code>pass_if</code>). Tests are now <strong>evaluated by code against the run</strong> (the run/outcome spine: RunReader → evaluator): the pill shows the result, and the evidence line shows the <em>measured value</em>, whether it was computed by <em>code</em> or routed to an <em>agent</em>, and whether the code verdict <em>agrees</em> with the authored one (reconcile). <span class="muted">⏳ pending = the study hasn\'t run yet.</span> Technical assertion + the exact evaluator are under "Technical details".</p>'
          + tests.map(function(t) {
              var name = t.name || '(unnamed)';
              var cls = t.classification || 'unclassified';
              var out = outcomeByTest[name];
              var result = (out && out.result) || t.result || (t.status === 'gated' ? 'GATED' : 'PENDING');
              var resBg = result === 'PASS' ? '#d1fae5' : (result === 'FAIL' ? '#fee2e2' : (result === 'SKIP' ? '#fef3c7' : (result === 'PARTIAL' ? '#fde68a' : '#f1f5f9')));
              var resFg = result === 'PASS' ? '#065f46' : (result === 'FAIL' ? '#991b1b' : (result === 'SKIP' ? '#92400e' : (result === 'PARTIAL' ? '#92400e' : '#475569')));
              var resGlyph = result === 'PASS' ? '✓' : (result === 'FAIL' ? '✗' : (result === 'PARTIAL' ? '◐' : '⏳'));
              // Claim: the English description, first sentence.
              var claim = (t.description || t.en || '').split('\n')[0].split('. ')[0];
              if (claim.length > 220) claim = claim.slice(0, 217) + '…';
              if (claim && claim.charAt(claim.length - 1) !== '.' && claim.charAt(claim.length - 1) !== '?') claim += '.';
              // Evidence (spine B3): render the code-computed outcome as a
              // styled row — measured_value + operator + evaluated_by in a
              // CODE-COMPUTED chip, kept visually SEPARATE from the
              // human-authored outcome (its own chip), with a prominent
              // reconcile:divergent badge and a link to the run that produced
              // the value + the pass_if band it was judged against. No more
              // raw merged k:v dump (which blended authored + computed).
              var authoredOut = (latestRun && latestRun.outcomes) ? latestRun.outcomes[name] : null;
              var computedOut = (latestRun && latestRun.computed_outcomes) ? latestRun.computed_outcomes[name] : null;
              var runIdent = latestRun ? (latestRun.run_id || latestRun.name || '') : '';
              var evidence = '';
              if (computedOut || authoredOut) {
                var co = computedOut || {};
                var mv = co.measured_value;
                var mvStr = (mv == null) ? '—' : (typeof mv === 'object' ? JSON.stringify(mv) : String(mv));
                if (mvStr.length > 220) mvStr = mvStr.slice(0, 217) + '…';
                var codeBits = [];
                if (co.result != null) codeBits.push('<strong>' + _h(String(co.result)) + '</strong>');
                if (co.operator) codeBits.push('op <code>' + _h(String(co.operator)) + '</code>');
                if (co.evaluated_by) codeBits.push('by <code>' + _h(String(co.evaluated_by)) + '</code>');
                var codeChip = computedOut
                  ? '<span class="outcome-chip outcome-chip-computed" style="display:inline-block;padding:3px 7px;border-radius:4px;background:#eef2ff;border:1px solid #c7d2fe;color:#3730a3;font-size:0.85em"><span class="muted">code computed</span> ' + codeBits.join(' · ') + '</span>'
                  : '';
                var authoredChip = (authoredOut && authoredOut.result != null)
                  ? ' <span class="outcome-chip outcome-chip-authored" style="display:inline-block;padding:3px 7px;border-radius:4px;background:#f8fafc;border:1px solid #e2e8f0;color:#475569;font-size:0.85em"><span class="muted">authored</span> <strong>' + _h(String(authoredOut.result)) + '</strong></span>'
                  : '';
                var divBadge = (co.reconcile === 'divergent')
                  ? ' <span class="reconcile-divergent" style="display:inline-block;padding:3px 7px;border-radius:4px;background:#fee2e2;border:1px solid #fca5a5;color:#991b1b;font-weight:600;font-size:0.85em">⚠ reconcile: divergent</span>'
                  : '';
                var runLink = runIdent
                  ? ' <span class="muted small">from run <code>' + _h(runIdent) + '</code></span>'
                  : '';
                var bandLine = t.pass_if
                  ? '<div class="pass_if-band muted small" style="margin-top:3px">passes if '
                    + (t.measure ? _measureText(t.measure) + ' ' : '')
                    + '<strong>' + _h(_passIfText(t.pass_if)) + '</strong>'
                    + _thresholdProvenanceChip(t.pass_if) + '</div>'   // #9
                  : '';
                var detailLine = (co.detail || co.reason)
                  ? '<div class="muted small" style="margin-top:3px">' + _h(String(co.detail || co.reason)) + '</div>'
                  : '';
                evidence = '<div class="computed-outcome-row">'
                  + (computedOut ? '<div><strong>measured_value:</strong> <code>' + _h(mvStr) + '</code></div>' : '')
                  + '<div style="margin-top:3px">' + codeChip + authoredChip + divBadge + runLink + '</div>'
                  + bandLine + detailLine
                  + '</div>';
              }
              var techBits = [];
              if (t.measure) techBits.push('Measure: ' + _measureText(t.measure));
              if (t.pass_if) techBits.push('Pass condition: ' + _h(_passIfText(t.pass_if)));
              else if (t.expect) techBits.push('Expect: <code>' + _h(JSON.stringify(t.expect)) + '</code>');
              // The Python that actually evaluates this test: the declarative
              // (kind, op) dispatch into the generic evaluator. There is no
              // per-test Python — evaluate() handles every test by kind + op.
              (function() {
                var kind = (t.measure && t.measure.kind) || null;
                var op = (t.pass_if && t.pass_if.op) || (t.expect && t.expect.op) || null;
                if (!kind && !op) return;
                var ref = 'Python: <code>vivarium_dashboard/lib/expected_behavior.py</code> → <code>evaluate()</code>';
                if (kind) ref += '; measure kind <code>' + _h(kind) + '</code> via <code>_series_for_simple_kind()</code>/<code>_measure()</code>';
                if (op) ref += '; op <code>' + _h(op) + '</code> via <code>_check()</code>';
                techBits.push(ref);
              })();
              if (t.requires_simulation) techBits.push('Requires sim: <code>' + _h(t.requires_simulation) + '</code>');
              if (t.cites && t.cites.length) techBits.push('Cites: ' + t.cites.map(function(c){return '<code>' + _h(c) + '</code>';}).join(', '));
              if (t.calibration_anchor) techBits.push('Calibration anchor: ⚠️ <code>' + _h(JSON.stringify(t.calibration_anchor)) + '</code>');
              var techDisc = techBits.length ? '<details class="tech-details"><summary>Technical details</summary>' + techBits.join('<br>') + '</details>' : '';

              return '<div class="test-card test-classification-' + _h(cls) + '" id="test-' + _h(name) + '">'
                   +   '<div class="test-header">'
                   +     '<span style="background:' + resBg + ';color:' + resFg + ';padding:2px 10px;border-radius:9999px;font-size:0.78em;font-weight:600">' + resGlyph + ' ' + _h(result) + '</span>'
                   +     '<span class="test-classification">' + _h(cls) + '</span>'
                   +     _thresholdProvenanceChip(t.pass_if)   // #9 — threshold provenance
                   +   '</div>'
                   +   '<div class="test-claim"><strong>Claim:</strong> ' + _h(claim) + '</div>'
                   +   (evidence ? '<div class="test-evidence"><strong>Evidence:</strong> ' + evidence + '</div>' : '')
                   +   '<div class="test-id muted small">Test id: <code>' + _h(name) + '</code></div>'
                   +   techDisc
                   + '</div>';
            }).join('')
          + '</div>';
      }

      // ── WHAT CHANGES IN THE MODEL? (Build / model_change) ────────────
      var buildHtml = '';
      if (modelChange || assumptions.length) {
        var mcHtml = '';
        if (modelChange) {
          if (typeof modelChange === 'string') {
            mcHtml = '<p>' + _multiline(modelChange) + '</p>';
          } else {
            var mcNotes = modelChange.notes || '';
            var hasNewWork = (modelChange.new_processes || []).length
                          || (modelChange.new_state_variables || []).length
                          || (modelChange.new_parameters || []).length
                          || (modelChange.modified_processes || []).length;
            mcHtml = mcNotes ? '<p>' + _multiline(mcNotes) + '</p>' : '';
            if (!hasNewWork && !mcNotes) mcHtml = '<p class="muted">No code-level model changes in this study.</p>';
            // Technical details with everything (processes, params, listeners).
            var mcBits = [];
            Object.keys(modelChange).forEach(function(k) {
              if (k === 'notes') return;
              var v = modelChange[k];
              if (Array.isArray(v) && !v.length) return;
              if (typeof v === 'string') mcBits.push(_h(k) + ': ' + _multiline(v));
              else mcBits.push(_h(k) + ': <code>' + _h(JSON.stringify(v)) + '</code>');
            });
            if (mcBits.length) mcHtml += '<details class="tech-details"><summary>Technical details</summary>' + mcBits.join('<br>') + '</details>';
          }
        }
        var asmHtml = assumptions.length
          ? '<h4 style="margin:12px 0 4px 0">Key assumptions</h4>'
          + '<ul>' + assumptions.map(function(a){return '<li>' + _multiline(typeof a === 'string' ? a : (a.text || JSON.stringify(a))) + '</li>';}).join('') + '</ul>'
          : '';
        buildHtml = '<div id="' + sid.build + '"><h3>What changes in the model?</h3>' + mcHtml + asmHtml + '</div>';
      }

      // ── WHAT NEEDS TO BE BUILT OR FIXED? (Implementation reqs) ───────
      var reqsHtml = '';
      if (reqs.length) {
        reqsHtml = '<div id="' + sid.reqs + '"><h3>What needs to be built or fixed? <span class="muted small">(' + reqs.length + ')</span></h3>'
          + '<p class="muted small" style="margin:0 0 8px 0">Concrete engineering work to fully exercise this study.</p>'
          + reqs.map(function(r) {
              var effortBadge = r.effort ? '<span class="req-effort">' + _h(r.effort) + '</span>' : '';
              var kindBadge   = r.kind   ? '<span class="req-kind">'   + _h(r.kind)   + '</span>' : '';
              var statusBadge = '';
              if (r.defer_until) statusBadge = '<span class="req-status req-status-deferred">deferred</span>';
              else if (r.status === 'done' || r.status === 'complete') statusBadge = '<span class="req-status req-status-done">done</span>';
              else statusBadge = '<span class="req-status req-status-open">open</span>';
              var keyLine = '';
              if (r.why) {
                keyLine = '<div class="req-key"><strong>Why it matters:</strong> ' + _multiline(r.why) + '</div>';
              } else if (r.description) {
                var teaser = String(r.description).split(/\n\s*\n/)[0].slice(0, 240);
                keyLine = '<div class="req-key">' + _multiline(teaser) + (r.description.length > 240 ? '…' : '') + '</div>';
              }
              var unblocks = '';
              if (r.unblocks) {
                var items = Array.isArray(r.unblocks) ? r.unblocks : [r.unblocks];
                unblocks = '<div class="req-unblocks"><strong>Unblocks:</strong><ul>' + items.map(function(u){return '<li>' + _h(u) + '</li>';}).join('') + '</ul></div>';
              }
              var deferredNote = r.defer_until
                ? '<div class="req-deferred">⏸ Deferred until <code>' + _h(r.defer_until) + '</code>.</div>'
                : '';
              var techBits = [];
              if (r.description && r.description.length > 240) techBits.push(_multiline(r.description));
              if (r.steps && r.steps.length) techBits.push('<ol>' + r.steps.map(function(st){return '<li>' + _h(st) + '</li>';}).join('') + '</ol>');
              if (r.files && r.files.length) techBits.push('Files: ' + r.files.map(function(f){return '<code>' + _h(f) + '</code>';}).join(', '));
              var techDisc = techBits.length ? '<details class="tech-details"><summary>Implementation detail</summary>' + techBits.join('<br>') + '</details>' : '';

              return '<div class="req-card">'
                   +   '<div class="req-header">'
                   +     '<code class="req-id">' + _h(r.id || '') + '</code>'
                   +     '<strong class="req-title">' + _h(r.title || '(untitled)') + '</strong>'
                   +     '<span class="req-badges">' + kindBadge + effortBadge + statusBadge + '</span>'
                   +   '</div>'
                   +   keyLine + deferredNote + unblocks + techDisc
                   + '</div>';
            }).join('')
          + '</div>';
      }

      // ── WHAT SHOULD HAPPEN NEXT? (Follow-ups) ────────────────────────
      var followUpsHtml = followUps.length
        ? '<div id="' + sid.followups + '"><h3>What should happen next? <span class="muted small">(' + followUps.length + ' follow-ups)</span></h3>'
          + '<p class="muted small">Concrete next steps. <em>Non-existing</em> entries can be seeded into child studies via the dashboard.</p>'
          + followUps.map(function(f) {
              var kind = f.kind || 'other';
              var techBits = [];
              if (f.hypothesized_mechanism) techBits.push('Hypothesised mechanism: ' + _multiline(f.hypothesized_mechanism));
              if (f.unblocks && f.unblocks.length) techBits.push('Unblocks: ' + f.unblocks.map(function(x){return '<code>' + _h(x) + '</code>';}).join(', '));
              if (f.acceptance && f.acceptance.length) techBits.push('Acceptance criteria: <ul>' + f.acceptance.map(function(a){return '<li>' + _h(a) + '</li>';}).join('') + '</ul>');
              var techDisc = techBits.length ? '<details class="tech-details"><summary>Technical details</summary>' + techBits.join('<br>') + '</details>' : '';
              var status = f.status ? '<span class="fu-status fu-status-' + _h(f.status) + '">' + _h(f.status) + '</span>' : '';
              var effort = f.effort ? '<span class="fu-effort">' + _h(f.effort) + '</span>' : '';
              return '<div class="fu-card fu-kind-' + _h(kind) + '">'
                   +   '<div class="fu-head"><span class="fu-kind">' + _h(kind) + '</span>' + effort + status + '<strong class="fu-title">' + _h(f.title || '(untitled)') + '</strong></div>'
                   +   (f.why ? '<div class="fu-why">' + _multiline(f.why) + '</div>' : '')
                   +   techDisc
                   + '</div>';
            }).join('')
          + '</div>'
        : '';

      // ── DISCOVERY IMPLICATIONS ───────────────────────────────────────
      // Turns the study's results into resolved/remaining uncertainties,
      // alternate hypotheses, mechanism-update proposals, and selectable
      // follow-up study proposals. Sits after the evidence/follow-ups and
      // before the Decide box. Each follow-up proposal carries an
      // "➕ Add to investigation" button that seeds a child study node.
      var discoveryHtml = '';
      if (_hasDiscovery) {
        var diBits = [];

        // Resolved / remaining uncertainties — two short lists.
        var resolved = discImpl.resolved_uncertainties || [];
        var remaining = discImpl.remaining_uncertainties || [];
        if (resolved.length || remaining.length) {
          var uncBits = [];
          if (resolved.length) {
            uncBits.push('<div class="di-unc di-unc-resolved"><h4>✓ Resolved uncertainties</h4><ul>'
              + resolved.map(function(u){ return '<li>' + _multiline(typeof u === 'string' ? u : JSON.stringify(u)) + '</li>'; }).join('')
              + '</ul></div>');
          }
          if (remaining.length) {
            uncBits.push('<div class="di-unc di-unc-remaining"><h4>● Remaining uncertainties</h4><ul>'
              + remaining.map(function(u){ return '<li>' + _multiline(typeof u === 'string' ? u : JSON.stringify(u)) + '</li>'; }).join('')
              + '</ul></div>');
          }
          diBits.push('<div class="di-uncertainties">' + uncBits.join('') + '</div>');
        }

        // Alternate hypotheses. Canonical source is
        // discovery_implications.alternate_hypotheses; C5 falls back to the
        // top-level alternative_hypotheses so authored prose anywhere still
        // surfaces (the top-level shape uses claim/discriminated_by/status).
        var altH = (discImpl.alternate_hypotheses && discImpl.alternate_hypotheses.length)
          ? discImpl.alternate_hypotheses
          : (s.alternative_hypotheses || []);
        if (altH.length) {
          diBits.push('<div class="di-group"><h4>Alternate hypotheses <span class="muted small">(' + altH.length + ')</span></h4>'
            + altH.map(function(h) {
                if (typeof h === 'string') h = {statement: h};
                var evFor = (h.evidence_for || []).length;
                var evAgainst = (h.evidence_against || []).length;
                var disc = h.discriminating_observables || [];
                var rows = [];
                if (h.why_plausible) rows.push('<div class="di-alt-why">' + _multiline(h.why_plausible) + '</div>');
                if (evFor || evAgainst) {
                  rows.push('<div class="di-alt-ev"><span class="di-ev di-ev-for">▲ ' + evFor + ' for</span>'
                    + '<span class="di-ev di-ev-against">▼ ' + evAgainst + ' against</span></div>');
                }
                if (disc.length) {
                  rows.push('<div class="di-alt-disc"><span class="di-lbl">Discriminating observables:</span> '
                    + disc.map(function(d){ return '<code>' + _h(d) + '</code>'; }).join(', ') + '</div>');
                }
                // Top-level alternative_hypotheses fields.
                if (h.discriminated_by) {
                  rows.push('<div class="di-alt-disc"><span class="di-lbl">Discriminated by:</span> ' + _multiline(h.discriminated_by) + '</div>');
                }
                if (h.status) {
                  rows.push('<div class="di-alt-status"><span class="di-lbl">Status:</span> ' + _h(h.status) + '</div>');
                }
                var elems = h.mechanism_elements_affected || [];
                if (elems.length) {
                  rows.push('<div class="di-alt-elems"><span class="di-lbl">Mechanism elements:</span> '
                    + elems.map(function(e){ return '<code>' + _h(e) + '</code>'; }).join(', ') + '</div>');
                }
                return '<div class="di-alt-card">'
                  + '<div class="di-alt-stmt"><strong>' + _h(h.statement || h.claim || h.hypothesis || '(untitled hypothesis)') + '</strong></div>'
                  + rows.join('')
                  + '</div>';
              }).join('')
            + '</div>');
        }

        // Mechanism update proposals.
        var mech = discImpl.mechanism_update_proposals || [];
        if (mech.length) {
          diBits.push('<div class="di-group"><h4>Mechanism update proposals <span class="muted small">(' + mech.length + ')</span></h4>'
            + mech.map(function(m) {
                var ut = (m.update_type || 'revise');
                var badge = m.requires_expert_approval
                  ? '<span class="di-approval-badge">needs expert approval</span>' : '';
                var cc = m.confidence_change
                  ? '<span class="di-conf-change">Δconfidence: ' + _h(String(m.confidence_change)) + '</span>' : '';
                return '<div class="di-mech-card">'
                  + '<div class="di-mech-head">'
                  +   '<code class="di-mech-target">' + _h(m.mechanism_node_or_edge || '(unspecified)') + '</code>'
                  +   '<span class="di-update-chip di-update-' + _h(ut) + '">' + _h(ut) + '</span>'
                  +   cc + badge
                  + '</div>'
                  + (m.rationale ? '<div class="di-mech-rationale">' + _multiline(m.rationale) + '</div>' : '')
                  + '</div>';
              }).join('')
            + '</div>');
        }

        // Follow-up study proposals — each a selectable card with an
        // "➕ Add to investigation" button (seeds a new child study node).
        if (followupProposals.length) {
          diBits.push('<div class="di-group"><h4>Follow-up study proposals <span class="muted small">(' + followupProposals.length + ')</span></h4>'
            + '<p class="muted small">Click <strong>➕ Add study</strong> to spawn a new study node in the investigation graph (seeds a child study.yaml from the proposal, with a leads-to edge back to this study).</p>'
            + followupProposals.map(function(p, pi) {
                var gain = (p.expected_information_gain || '').toLowerCase();
                var gainChip = gain ? '<span class="di-gain-chip di-gain-' + _h(gain) + '">gain: ' + _h(gain) + '</span>' : '';
                var typeChip = p.study_type ? '<span class="di-type-chip">' + _h(p.study_type) + '</span>' : '';
                var trigChip = p.source_trigger ? '<span class="di-trigger-chip">' + _h(p.source_trigger) + '</span>' : '';
                var targets = p.target_mechanism_elements || [];
                var prio = p.priority ? '<span class="di-prio-chip">priority: ' + _h(String(p.priority)) + '</span>' : '';
                // "➕ Add study" seeds a child study from this proposal via
                // _seedFollowupProposal (POST /api/study-seed-followup). Guarded
                // so a downloaded static report (no walkthrough.js) degrades to a
                // hint instead of a ReferenceError; the section is also an inline-
                // feedback host (💬) for reviewers.
                // Single-quoted args so they sit safely inside onclick="…" (a
                // JSON.stringify'd id would emit double quotes and break the attr).
                var seedArgs = "'" + _h(s.name) + "', '"
                  + _h(p.id != null ? String(p.id) : '') + "', " + pi + ", this";
                var seedBtn = '<div class="di-fup-actions" style="margin-top:8px">'
                  + '<button class="btn-seed-followup" '
                  + 'onclick="event.stopPropagation(); if(window._seedFollowupProposal){_seedFollowupProposal(' + seedArgs + ');}'
                  + 'else{alert(\'Open this investigation in the live dashboard to add the study.\');}" '
                  + 'style="font-size:0.82em;padding:3px 10px;border:1px solid #16a34a;background:#f0fdf4;'
                  + 'color:#166534;border-radius:6px;cursor:pointer;white-space:nowrap">➕ Add study</button></div>';
                return '<div class="di-fup-card">'
                  + '<div class="di-fup-head">'
                  +   '<strong class="di-fup-title">' + _h(p.title || '(untitled proposal)') + '</strong>'
                  +   typeChip + trigChip + gainChip + prio
                  + '</div>'
                  + (p.motivation ? '<div class="di-fup-motivation" style="margin-top:4px"><span class="di-lbl">Why:</span> ' + _multiline(p.motivation) + '</div>' : '')
                  + (p.proposed_experiment ? '<div class="di-fup-exp" style="margin-top:4px"><span class="di-lbl">Proposed experiment:</span> ' + _multiline(p.proposed_experiment) + '</div>' : '')
                  + (p.hypothesized_mechanism ? '<div class="di-fup-mech" style="margin-top:4px"><span class="di-lbl">Hypothesized mechanism:</span> ' + _multiline(p.hypothesized_mechanism) + '</div>' : '')
                  + (targets.length ? '<div class="di-fup-targets" style="margin-top:4px"><span class="di-lbl">Targets:</span> '
                      + targets.map(function(t){ return '<code>' + _h(t) + '</code>'; }).join(', ') + '</div>' : '')
                  + seedBtn
                  + '</div>';
              }).join('')
            + '</div>');
        }

        // Addressed mechanism uncertainty (provenance line, optional).
        var addressed = discImpl.mechanism_uncertainty_addressed || [];
        if (addressed.length) {
          diBits.push('<div class="di-addressed muted small"><span class="di-lbl">Mechanism uncertainty addressed:</span> '
            + addressed.map(function(a){ return _h(typeof a === 'string' ? a : JSON.stringify(a)); }).join('; ') + '</div>');
        }

        discoveryHtml = '<div id="' + sid.discovery + '" class="discovery-implications">'
          + '<h3>Discovery implications</h3>'
          + '<p class="muted small">Where this study\'s results leave the mechanism model — and what to investigate next.</p>'
          + diBits.join('')
          + '</div>';
      }

      // ── LIMITATIONS ──────────────────────────────────────────────────
      var limitsHtml = limitations.length
        ? '<div id="' + sid.limits + '"><h3>Limitations</h3><ul>'
          + limitations.map(function(l) { return '<li>' + _multiline(typeof l === 'string' ? l : (l.text || JSON.stringify(l))) + '</li>'; }).join('')
          + '</ul></div>'
        : '';

      // ── REFERENCES ───────────────────────────────────────────────────
      var refsHtml = bib.length
        ? '<div id="' + sid.refs + '"><h3>References cited by this study</h3><p>'
          + bib.map(function(k) { return '<code>' + _h(k) + '</code>'; }).join(', ')
          + '</p></div>'
        : '';

      // ── BIOLOGY-AT-A-GLANCE (planning-phase, biologist-first) ────────
      // Renders when study.yaml declares any of: biological_summary,
      // study_card, literature_anchors. Designed so a biologist reading
      // the report sees the biology before any code identifier.
      // ── MECHANISM NARRATIVE (framework: 7 first-class fields any study can
      // declare). Designed so the report reads as a cumulative mechanism
      // migration rather than a sequence of implementation tasks. Each
      // field is independently optional; only declared fields render.
      //   biological_role         — what mechanism this study introduces
      //   mechanism_replaced      — what heuristic / placeholder it replaces
      //   dependency_rationale    — why this study must run at this point in
      //                              the dependency chain
      //   primary_claim           — what observable would convince us the
      //                              mechanism is behaving correctly
      //   primary_visualization   — the explanatory figure for this claim
      //   scope_boundary          — what is explicitly in scope
      //   deferred_biology        — what biology is intentionally deferred to
      //                              later studies
      var narrativeFields = [
        ['biological_role',       'Biological role'],
        ['mechanism_replaced',    'Mechanism replaced'],
        ['dependency_rationale',  'Dependency rationale'],
        ['primary_claim',         'Primary claim'],
        ['primary_visualization', 'Primary visualization'],
        ['scope_boundary',        'Scope boundary'],
        ['deferred_biology',      'Deferred biology'],
      ];
      var narrativeRows = [];
      narrativeFields.forEach(function(pair) {
        var key = pair[0], label = pair[1];
        var v = s[key];
        if (typeof v === 'string' && v.trim()) {
          narrativeRows.push('<tr><th>' + label + '</th><td>' + _multiline(v) + '</td></tr>');
        }
      });
      var mechanismNarrativeHtml = '';
      if (narrativeRows.length) {
        mechanismNarrativeHtml =
          '<div class="mechanism-narrative">'
          + '<h3 class="biology-glance-label">Mechanism narrative</h3>'
          + '<table class="mechanism-narrative-table">' + narrativeRows.join('') + '</table>'
          + '</div>';
      }

      // C6 — biological_summary is the one optional override; derive the prose
      // from findings[].statement when it is absent so the Biology callout
      // still renders meaningful mechanism prose.
      var _bioProse = s.biological_summary;
      if (!_bioProse) {
        var _bioFindings = (s.findings || [])
          .filter(function(f) { return f && typeof f === 'object'; })
          .map(function(f) { return f.statement || f.summary; })
          .filter(Boolean);
        if (_bioFindings.length) _bioProse = _bioFindings.join('\n\n');
      }
      var biologyGlanceHtml = '';
      if (_bioProse || s.study_card || s.literature_anchors) {
        var bgsBits = [];
        if (_bioProse) {
          bgsBits.push(
            '<div class="biology-summary-callout">'
            + '<h3 class="biology-glance-label">Biology — what this study is about</h3>'
            + '<p class="biology-prose">' + _multiline(_bioProse) + '</p>'
            + '</div>'
          );
        }
        if (s.study_card) {
          var sc = s.study_card;
          var scRows = [];
          if (sc.goal) scRows.push('<tr><th>Goal</th><td>' + _multiline(sc.goal) + '</td></tr>');
          if (sc.mechanism) scRows.push('<tr><th>Mechanism</th><td>' + _multiline(sc.mechanism) + '</td></tr>');
          if (sc.why_before_next) scRows.push('<tr><th>Why before next</th><td>' + _multiline(sc.why_before_next) + '</td></tr>');
          if (sc.expected_result) scRows.push('<tr><th>Expected result</th><td>' + _multiline(sc.expected_result) + '</td></tr>');
          if (sc.main_expert_question) scRows.push('<tr><th>Main expert question</th><td>' + _multiline(sc.main_expert_question) + '</td></tr>');
          if (scRows.length) {
            bgsBits.push(
              '<div class="study-card">'
              + '<h3 class="biology-glance-label">Study card</h3>'
              + '<table class="study-card-table">' + scRows.join('') + '</table>'
              + '</div>'
            );
          }
        }
        if (Array.isArray(s.literature_anchors) && s.literature_anchors.length) {
          var anchorItems = s.literature_anchors.map(function(a) {
            var bits = ['<div class="anchor-expectation">' + _h(a.expectation || '') + '</div>'];
            if (a.model_observable) {
              bits.push('<div class="anchor-observable"><em>Model observable:</em> <code>'
                + _h(a.model_observable) + '</code></div>');
            }
            if (a.source) {
              bits.push('<div class="anchor-source"><em>Source:</em> ' + _h(a.source) + '</div>');
            }
            if (a.status_in_v2ecoli) {
              bits.push('<div class="anchor-status"><em>Current status:</em> '
                + _h(a.status_in_v2ecoli) + '</div>');
            }
            return '<li class="literature-anchor-card">' + bits.join('') + '</li>';
          }).join('');
          bgsBits.push(
            '<div class="literature-anchors">'
            + '<h3 class="biology-glance-label">Literature anchors</h3>'
            + '<p class="muted small" style="margin:0 0 8px 0">The biological '
            + 'expectations this study tests, mapped to the model observable that '
            + 'will measure each one. Full citations live in the test cards.</p>'
            + '<ul class="literature-anchor-list">' + anchorItems + '</ul>'
            + '</div>'
          );
        }
        if (bgsBits.length) {
          biologyGlanceHtml = '<div class="biology-glance">' + bgsBits.join('') + '</div>';
        }
      }

      // ── PRE-RUN EXPERT REVIEW ────────────────────────────────────────
      // Compiles expert_decisions_needed into a prominent panel so biologists
      // can answer them before the simulation is run.
      var expertReviewHtml = '';
      if (Array.isArray(s.expert_decisions_needed) && s.expert_decisions_needed.length) {
        var qCards = s.expert_decisions_needed.map(function(q) {
          var altHtml = '';
          if (Array.isArray(q.alternatives) && q.alternatives.length) {
            altHtml = '<div class="expert-question-alternatives"><em>Alternatives:</em><ul>'
              + q.alternatives.map(function(a){return '<li>' + _h(a) + '</li>';}).join('')
              + '</ul></div>';
          }
          var impactHtml = q.impact_if_wrong
            ? '<div class="expert-question-impact"><em>Impact if wrong:</em> '
              + _multiline(q.impact_if_wrong) + '</div>'
            : '';
          var blocksHtml = '';
          if (Array.isArray(q.blocks) && q.blocks.length) {
            blocksHtml = '<details class="expert-question-blocks"><summary>What this blocks ('
              + q.blocks.length + ' items)</summary><ul>'
              + q.blocks.map(function(b){return '<li>' + _h(b) + '</li>';}).join('')
              + '</ul></details>';
          }
          var requestedHtml = q.requested_response
            ? '<details class="expert-question-response"><summary>Requested response format</summary><p>'
              + _multiline(q.requested_response) + '</p></details>'
            : '';
          var askedToHtml = q.asked_to
            ? '<span class="expert-question-asked-to">asked to: ' + _h(q.asked_to) + '</span>'
            : '';
          return '<div class="expert-question-card status-' + _h(q.status || 'open') + '">'
            + '<div class="expert-question-header">'
            +   '<span class="expert-question-id">' + _h(q.id || '') + '</span>'
            +   '<span class="expert-question-status">' + _h(q.status || 'open') + '</span>'
            +   askedToHtml
            + '</div>'
            + '<div class="expert-question-text"><strong>Q.</strong> '
            +   _multiline(q.question || '') + '</div>'
            + altHtml + impactHtml + blocksHtml + requestedHtml
            + '</div>';
        }).join('');
        expertReviewHtml = '<div class="pre-run-expert-review" id="study-' + slug + '-expert">'
          + '<h3>Pre-run expert review</h3>'
          + '<p class="muted small" style="margin:0 0 8px 0">Open biological '
          + 'questions the planning is contingent on. A "wrong" answer here means '
          + 'a primary test threshold needs to change <em>before</em> the simulation '
          + 'is run, not after.</p>'
          + qCards
          + '</div>';
      }

      // ── EMBED VISUALIZATIONS ─────────────────────────────────────────
      // Pre-fetched HTML previews (study.yaml.embed_visualizations) inlined
      // as <iframe srcdoc> so the downloaded report works offline. The
      // preview's own <script src> CDN loads (Plotly) will still need
      // network access at *viewing* time, but the HTML structure + data
      // are baked in.
      var embedsHtml = '';
      var studyEmbeds = embedsByStudy[s.name] || [];
      if (studyEmbeds.length) {
        embedsHtml = '<div class="study-embeds" id="study-' + slug + '-embeds">'
          + '<h3>Visualizations</h3>'
          + studyEmbeds.map(function(emb) {
              // Escape double-quotes for srcdoc attribute.
              var escaped = (emb.html || '').replace(/&/g, '&amp;')
                                            .replace(/"/g, '&quot;');
              // A "prior / superseded" embed is one explicitly flagged stale, or
              // whose name/description marks it as a pre-execution, placeholder,
              // or older-dated preview. These are auto-collapsed (the expert's
              // "fold these previous results") so they don't dominate the page
              // with empty placeholder charts — but stay one click away.
              var meta = ((emb.name || '') + ' ' + (emb.description || '')).toLowerCase();
              var isStale = emb.stale === true
                || (typeof emb.description === 'string' && emb.description.indexOf('⚠') === 0)
                || /\b(prior|planning[- ]phase|placeholder|pending refresh|pre-execution|superseded|baseline rerun|will be populated|not yet run)\b/.test(meta);
              // If the inner doc declares a fixed CSS height clamp (e.g.
              // comparative_viz emits `html,body{height:540px;overflow:hidden}`
              // to bound Plotly's hover-layer scrollHeight inflation), set
              // the iframe height directly so _fitEmbed's measurements can't
              // over- or under-grow it. Unclamped embeds (e.g. the tall
              // chromosome figures) fall through to _fitEmbed's autosize.
              // Lenient regex: matches `html,body { ... height: NNNpx ... }`
              // regardless of property order inside the rule. Earlier strict
              // form `/html,body\{height:(\d+)px/` only matched when `height:`
              // was the FIRST property; viz authors who put `margin:0;padding:0;`
              // first lost the clamp and got auto-resized to scrollHeight
              // (which misreports for matplotlib-PNG bodies and for charts
              // whose legend overflows the chart div).
              var _hClamp = (emb.html || '').match(/html\s*,\s*body\s*\{[^}]*\bheight\s*:\s*(\d+)px/);
              var _hStyle = _hClamp ? (';height:' + (parseInt(_hClamp[1], 10) + 24) + 'px') : '';
              // Infrastructural no-scrollbar guarantee:
              //   scrolling="no"     — kills the browser iframe scrollbar
              //                        regardless of any size mismatch
              //                        between _fitEmbed's measurement
              //                        and the inner content's actual
              //                        rendered height. Plotly's legend-
              //                        overflow scrollbars previously
              //                        leaked through because the chart
              //                        div was sized for the chart but
              //                        not the wrapped legend rows.
              //   min-height:1200px  — under-measured iframes still show
              //                        enough vertical space for typical
              //                        multi-panel figures (e.g. 2×3 grid
              //                        cell_mass / growth_rate / RNA /
              //                        ribosome activity panels — these
              //                        rendered at ~1280 px tall and the
              //                        previous 720 px floor clipped them).
              //                        The _fitEmbed walk extends to svg/
              //                        img/canvas (see below) and uses
              //                        img.naturalHeight to pre-measure
              //                        before the browser has laid out
              //                        the data: URL, so iframes grow
              //                        correctly — this floor is the
              //                        safety net for first-paint before
              //                        any timers fire.
              var iframe = '<iframe srcdoc="' + escaped + '" '
                + 'class="embed-frame" onload="_wireEmbed(this)" '
                + 'scrolling="no" '
                + 'style="width:100%;min-height:1200px;border:0;display:block;overflow:hidden' + _hStyle + '" '
                + 'title="' + _h(emb.name) + '"></iframe>';
              if (isStale) {
                // Collapsed by default; re-fit on expand.
                return '<details class="study-embed-card stale-embed" ontoggle="_onEmbedToggle(this)" '
                  + 'style="margin:12px 0;border:1px solid #f59e0b;border-radius:6px;background:#fffdf6;overflow:hidden">'
                  + '<summary style="padding:8px 12px;cursor:pointer;background:#fffbeb;color:#92400e;font-weight:600;list-style:none">'
                  +   '⚠ ' + _h(emb.name) + ' <span style="font-weight:400">— prior / superseded result (click to view)</span>'
                  + '</summary>'
                  + (emb.description ? '<p class="small" style="margin:6px 12px;color:#92400e">' + _h(emb.description) + '</p>' : '')
                  + iframe
                  + '</details>';
              }
              return '<div class="study-embed-card" style="margin:12px 0;border:1px solid #e2e8f0;border-radius:6px;background:#fff;overflow:hidden">'
                + '<div style="padding:8px 12px;border-bottom:1px solid #e5e7eb;background:#f9fafb">'
                +   '<strong>' + _h(emb.name) + '</strong>'
                + '</div>'
                + (emb.description ? '<p class="muted small" style="margin:6px 12px">' + _h(emb.description) + '</p>' : '')
                + iframe
                + '</div>';
            }).join('')
          + '</div>';
      }

      // ── CONDITIONS (v4: baseline + variants + model_settings) ─────────
      // Renders the actual parameter table the evaluator wants: each
      // variant's overrides + every model_setting's current/default/range.
      var conditionsHtml = _renderConditionsBlock(s, sid.conditions);

      // C2 + C3 — derived 3-track verdicts + read-only four-section synthesis,
      // both computed from canonical fields (no longer write-only).
      var verdictsHtml = _conclusionVerdictsHtml(s, slug);
      var synthesisHtml = _conclusionSynthesisHtml(s, slug);

      // Wave 2 — compositional causal discovery + semantic closure renders.
      var commitmentHtml = _compositionCommitmentHtml(s, slug);   // C-COMMIT
      var invariantsHtml = _invariantChecksHtml(s, slug);         // C-INVAR
      var causalHtml = _causalNecessityHtml(s, slug);             // C-CF
      var representationHtml = _representationHtml(s, slug);       // C-MODELCARD

      // ── PLANNING-PHASE DETECTION ──
      // A study is "planning" when no runs have completed yet. In that
      // mode we strip decision / takeaways / findings (post-execution
      // sections) and lead with the spec the expert needs to comment on:
      // Question → Conditions → Tests → Baseline preview → Assumptions.
      // Once runs land, the full flow returns.
      var hasRuns = (s.runs || []).length > 0 || (s.findings || []).length > 0;
      var isPlanning = !hasRuns;

      // Param-enforcement banner (expert-feedback D.2). When the study
      // declares enforced_params and its latest run didn't apply them, show
      // the violations prominently so "declared but not implemented" is
      // visible — the exact thing the reviewer caught manually.
      var enforcementHtml = '';
      var pe = s.param_enforcement;
      if (pe && pe.violations && pe.violations.length) {
        enforcementHtml =
          '<div class="param-enforcement-banner" id="study-' + slug + '-enforcement" '
          + 'style="margin:12px 0;padding:12px 16px;background:#fffbeb;border:1px solid #f59e0b;'
          + 'border-left-width:5px;border-radius:6px;color:#92400e">'
          + '<strong>⚠ Declared parameters were not applied to the latest run</strong>'
          + '<div class="small" style="margin-top:4px">This study declares '
          + 'enforced parameters, but the most recent run did not apply '
          + (pe.violations.length === 1 ? 'one of them' : (pe.violations.length + ' of them'))
          + ' — results below may reflect composite defaults rather than the '
          + 'intended values. Re-run after wiring these in.</div>'
          + '<ul class="small" style="margin:8px 0 0 18px">'
          + pe.violations.map(function(v) {
              return '<li>' + _h(v.message || (v.param + ': declared ' + v.expected)) + '</li>';
            }).join('')
          + '</ul></div>';
      }

      // Spine A3: readiness panel placeholder. Populated after render by
      // _populateReadinessPanels(), which fetches /api/report-lint ONCE per
      // report and keys the deterministic linter findings by study. Mirrors
      // the param-enforcement banner: surfaced per study, connected to its
      // source (the linter), labeled code-computed. Empty until populated.
      var readinessHtml = '<div class="study-readiness-panel" id="study-' + slug + '-readiness" data-study="' + _h(slug) + '"></div>';

      // Imported expert feedback (expert-feedback B.1). Shows the reviewer's
      // own annotations back, in-context per study, so the loop closes: the
      // next report makes clear what was said and lets the team show it's
      // addressed. Newest-first; author + timestamp preserved.
      // Imported reviewer-feedback quotes are intentionally NOT rendered in the
      // report (per request — they cluttered the top of each study). Feedback is
      // still imported + tracked in investigations/<inv>/feedback/*.yaml, and how
      // it was addressed shows in the study conclusion/status.
      var feedbackHtml = '';

      // SP3b: feedback → action table. Read-only render of the pbg-supplied
      // s.feedback_actions (open feedback items that have a proposed action +
      // its kind / proposed_text / open-applied status). The report NEVER
      // computes the action — it renders what study_feedback_actions returns.
      feedbackHtml += _renderReportFeedbackActions(s, slug);

      // Status-drift banner (round-2 friction #2). When a stored status axis
      // (or a "planning" headline) contradicts what actually ran, say so — the
      // report should never show "planning" on an executed study.
      var statusDriftHtml = '';
      var sdis = s.status_disagreements;
      if (sdis && sdis.length) {
        statusDriftHtml =
          '<div class="status-drift-banner" id="study-' + slug + '-status-drift" '
          + 'style="margin:12px 0;padding:12px 16px;background:#fffbeb;border:1px solid #f59e0b;'
          + 'border-left-width:5px;border-radius:6px;color:#92400e">'
          + '<strong>⚠ Status is out of date relative to what ran</strong>'
          + '<ul class="small" style="margin:8px 0 0 18px">'
          + sdis.map(function(v) { return '<li>' + _h(v.message || (v.axis + ': ' + v.stored + ' → ' + v.derived)) + '</li>'; }).join('')
          + '</ul></div>';
      }

      // Charts come from runs.db when present, or fall back to the
      // workspace default-baseline. Wrap them with a BASELINE banner
      // so the expert knows the trace is pre-execution data, not a
      // study-specific run.
      var chartsWithBaselineNoticeHtml = chartsHtml;
      if (isPlanning && chartsHtml) {
        chartsWithBaselineNoticeHtml =
            '<div class="planning-baseline-strip" id="study-' + slug + '-baseline-strip">' +
              '<div class="planning-baseline-strip-banner">' +
                '<span class="planning-baseline-pill">BASELINE</span>' +
                '<span class="planning-baseline-text">' +
                  'Charts below show the <strong>workspace pre-execution baseline</strong>' +
                  ' — what the system looks like before any of this study\'s variants run.' +
                  ' Expert reviewers: comment on whether these traces look right for the' +
                  ' starting point.' +
                '</span>' +
              '</div>' +
              chartsHtml +
            '</div>';
      }

      if (isPlanning) {
        // Planning-phase layout — minimal, expert-comment-driven.
        // The <header class="study-header"> chrome (num + slug + phase
        // badge + status badge + Depends on + Blocks) was REMOVED because
        // every field is already in the sticky control panel above
        // (sp-top + sp-meta from _studyControlPanel). The v4 render path
        // dropped this same header at line ~6172 for the same reason;
        // this is the v3 sibling fix. Anchor (#study-<slug>) is on the
        // <details> element itself, not the h2, so URL hashes still
        // resolve. The "PLANNING — not yet run" pill is preserved as a
        // standalone callout because the sticky panel doesn't render it.
        return ''
          + '<details class="study-fold verdict-' + verdictBadge.cls + '" id="study-' + slug + '">'
          +   '<summary class="study-panel">' + controlPanelHtml + '</summary>'
          + '<section class="study study-planning">'
          +   subNav
          +   '<div class="study-planning-pill">PLANNING — not yet run</div>'
          +   modelBannerHtml     // 🧬 Model: composite(s) + params + loom static popout (PROMINENT)
          +   statusDriftHtml     // ⚠ status out of date vs runs (#2)
          +   enforcementHtml     // ⚠ declared params not applied (D.2)
          +   readinessHtml       // ✓/⚠ lint readiness panel (A3)
          +   reviewHtml          // ⚠ review-readiness gates (duration / param-vs-reference)
          +   feedbackHtml        // 💬 imported expert feedback (B.1)
          +   commitmentHtml      // Theoretical commitment (C-COMMIT)
          +   invariantsHtml      // Invariant checks (C-INVAR)
          +   summaryHtml         // Question / purpose
          +   conditionsHtml      // Conditions: variants + model settings (PROMINENT)
          +   testsHtml           // Expected behavior / tests (PROMINENT for comments)
          +   representationHtml   // Representation claims (C-MODELCARD)
          +   chartsWithBaselineNoticeHtml  // Baseline charts with BASELINE label
          +   embedsHtml          // Embedded preview HTMLs
          +   readoutsHtml        // What we'll measure
          +   buildHtml           // Model change (collapsed-ish, technical)
          +   '<details class="study-technical-fold"><summary>Technical context (model changes · implementation tasks · follow-ups · limitations · refs)</summary>'
          +     reqsHtml          // Implementation requirements
          +     followUpsHtml     // Follow-ups
          +     discoveryHtml     // Discovery implications
          +     limitsHtml        // Limitations
          +     refsHtml          // References
          +   '</details>'
          + '</section>'
          + '</details>';
      }

      // Post-execution layout — full v3 flow including decision + findings.
      // <header class="study-header"> dropped for the same reason as the
      // planning path + the v4 path: every field (num, slug, phase badge,
      // status badge, Depends on, Blocks) is already in the sticky control
      // panel's sp-top + sp-meta rows above.
      return ''
        + '<details class="study-fold verdict-' + verdictBadge.cls + '" id="study-' + slug + '">'
        +   '<summary class="study-panel">' + controlPanelHtml + '</summary>'
        + '<section class="study">'
        +   subNav
        +   modelBannerHtml     // 🧬 Model: composite(s) + params + loom static popout (PROMINENT)
        +   statusDriftHtml     // ⚠ status out of date vs runs (#2)
        +   enforcementHtml     // ⚠ declared params not applied (D.2)
        +   readinessHtml       // ✓/⚠ lint readiness panel (A3)
        +   reviewHtml          // ⚠ review-readiness gates (duration / param-vs-reference)
        +   feedbackHtml        // 💬 imported expert feedback (B.1)
        +   commitmentHtml      // Theoretical commitment (C-COMMIT)
        +   invariantsHtml      // Invariant checks (C-INVAR)
        +   biologyGlanceHtml   // 0. Biology-at-a-glance
        +   mechanismNarrativeHtml  // 0a. Mechanism narrative (7 framework fields)
        +   summaryHtml         // 1. Plain-English summary (explanation leads, before charts)
        +   embedsHtml          // 1a. Embedded visualizations (after the explanation)
        +   expertReviewHtml    // 2b. Pre-run expert review
        +   takeawaysHtml       // 3 + 4. Detailed findings
        +   verdictsHtml        // Derived 3-track conclusion verdicts (computed)
        +   causalHtml          // Causal necessity table (C-CF)
        +   discoveryHtml       // Discovery implications (directly under the findings)
        +   conditionsHtml      // Conditions (what we set up) — grouped with the runs
        +   simsHtml            // What did/will we run
        +   readoutsHtml        // What did/will we measure (above visualisations)
        +   chartsHtml          //    + Visualisations
        +   testsHtml           // 7. How we judge success
        +   buildHtml           // 8. Model changes
        +   representationHtml   // Representation claims (C-MODELCARD)
        +   reqsHtml            // 9. What to build/fix
        +   followUpsHtml       // 10. Next steps
        +   limitsHtml          // 11. Limitations
        +   synthesisHtml       // Read-only four-section conclusion synthesis (derived)
        +   refsHtml            // 12. References
        +   decisionHtml        // Decision: can we move to the next study?
        + '</section>'
        + '</details>';
    }

    // Render the per-study Conditions block (v4). Returns empty string for
    // studies without a ``conditions:`` mapping.
    //
    // Layout:
    //   - Baseline composite + params
    //   - Variants table (name, base_composite, parameter overrides)
    //   - Model settings table (name, type, default, current, range, gate)
    //
    // Why this lives next to Tests instead of inside Build: variants and
    // model_settings are the *experimental conditions* — what you change to
    // run the tests — distinct from the *code* changes captured in Build.
    function _renderConditionsBlock(s, anchorId) {
      var cond = (s.conditions && typeof s.conditions === 'object') ? s.conditions : null;
      // C4 — single canonical run-spec. When a study has no v4 ``conditions:``
      // mapping, derive the rich conditions table from the normalized
      // ``simulation_set`` (the server folds top-level baseline/variants and
      // parameter-override interventions into it), so there is one source.
      if (!cond && Array.isArray(s.simulation_set) && s.simulation_set.length) {
        var _derivedBaseline = {};
        var _derivedVariants = [];
        s.simulation_set.forEach(function(e) {
          if (!e || typeof e !== 'object') return;
          if (e.is_baseline) {
            _derivedBaseline = {composite: e.base_model, params: e.params || {}};
          } else {
            _derivedVariants.push({
              name: e.name,
              composite: e.base_model,
              parameter_overrides: e.params || {},
              description: e.description || ''
            });
          }
        });
        cond = {baseline: _derivedBaseline, variants: _derivedVariants, model_settings: []};
      }
      if (!cond) return '';
      var baseline = cond.baseline || {};
      var variants = cond.variants || [];
      var expertInputs = cond.model_settings || cond.expert_inputs || [];
      if (!baseline.composite && !variants.length && !expertInputs.length) return '';

      function _fmtVal(v) {
        if (v === null || v === undefined) return '<em class="muted">—</em>';
        if (typeof v === 'object') return '<code>' + _h(JSON.stringify(v)) + '</code>';
        return '<code>' + _h(String(v)) + '</code>';
      }
      function _kvList(obj) {
        var keys = Object.keys(obj || {});
        if (!keys.length) return '<em class="muted">(no overrides)</em>';
        return keys.map(function(k) {
          return '<div class="cond-kv"><span class="cond-kv-k">' + _h(k) + '</span>' +
                 '<span class="cond-kv-v">' + _fmtVal(obj[k]) + '</span></div>';
        }).join('');
      }

      // Baseline row
      var baselineHtml = '';
      if (baseline.composite || baseline.params) {
        baselineHtml =
            '<div class="cond-baseline">' +
              '<h4>Baseline</h4>' +
              '<div class="cond-baseline-composite">' +
                'Composite: <code>' + _h(baseline.composite || '?') + '</code>' +
              '</div>' +
              '<div class="cond-baseline-params">' +
                _kvList(baseline.params || {}) +
              '</div>' +
            '</div>';
      }

      // Variants table
      var variantsHtml = '';
      if (variants.length) {
        variantsHtml =
            '<div class="cond-variants">' +
              '<h4>Variants <span class="muted small">(' + variants.length + ')</span></h4>' +
              '<p class="muted small" style="margin:0 0 6px 0">Each variant is a perturbation of the baseline — typically a parameter override or a swapped composite. These define the runs that test the assumption.</p>' +
              '<table class="cond-table">' +
                '<thead><tr><th>Variant</th><th>Composite / base</th><th>Parameter overrides</th><th>Notes</th></tr></thead>' +
                '<tbody>' +
                  variants.map(function(v) {
                    var ovr = v.parameter_overrides || v.params || {};
                    var base = v.composite || v.base_composite || '<em class="muted">(inherits baseline)</em>';
                    var name = v.name || '?';
                    var notes = v.description || v.notes || '';
                    return '<tr>' +
                      '<td><code>' + _h(name) + '</code></td>' +
                      '<td>' + (typeof base === 'string' && base.indexOf('<em') === 0 ? base : '<code>' + _h(base) + '</code>') + '</td>' +
                      '<td>' + _kvList(ovr) + '</td>' +
                      '<td>' + (notes ? _multiline(notes) : '<em class="muted">—</em>') + '</td>' +
                    '</tr>';
                  }).join('') +
                '</tbody>' +
              '</table>' +
            '</div>';
      }

      // Model settings table
      var expertHtml = '';
      if (expertInputs.length) {
        var nRequired = expertInputs.filter(function(e){return e.gate === 'required-before-run';}).length;
        var requiredBadge = nRequired
          ? '<span class="cond-ei-required-badge" title="' + nRequired + ' input(s) must be set before this study can run">' + nRequired + ' required</span>'
          : '';
        expertHtml =
            '<div class="cond-expert-inputs">' +
              '<h4>Model settings <span class="muted small">(' + expertInputs.length + ')</span> ' + requiredBadge + '</h4>' +
              '<p class="muted small" style="margin:0 0 6px 0">Parameters that need human input before the study runs. Edit a value on the dashboard\'s study-detail page (Build tab) and the next <code>pbg_runner</code> invocation will pick it up.</p>' +
              '<table class="cond-table">' +
                '<thead><tr><th>Name</th><th>Type</th><th>Default</th><th>Current</th><th>Range</th><th>Gate</th><th>Description</th></tr></thead>' +
                '<tbody>' +
                  expertInputs.map(function(e) {
                    var name = e.name || '?';
                    var type = e.type || '';
                    var def  = e.default;
                    var cur  = (e.current === null || e.current === undefined) ? null : e.current;
                    var range = '';
                    if (Array.isArray(e.range) && e.range.length === 2)
                      range = '[' + e.range[0] + ', ' + e.range[1] + ']';
                    else if (Array.isArray(e.options))
                      range = e.options.join(' | ');
                    var gate = e.gate || 'optional';
                    var gateBadge = gate === 'required-before-run'
                      ? '<span class="cond-ei-gate-req">required</span>'
                      : '<span class="cond-ei-gate-opt">optional</span>';
                    var awaiting = (cur === null) ? '<em class="muted">awaiting expert</em>' : _fmtVal(cur);
                    return '<tr>' +
                      '<td><code>' + _h(name) + '</code></td>' +
                      '<td>' + _h(type) + '</td>' +
                      '<td>' + _fmtVal(def) + '</td>' +
                      '<td>' + awaiting + '</td>' +
                      '<td>' + (range ? '<code>' + _h(range) + '</code>' : '<em class="muted">—</em>') + '</td>' +
                      '<td>' + gateBadge + '</td>' +
                      '<td>' + (e.description ? _multiline(e.description) : '<em class="muted">—</em>') + '</td>' +
                    '</tr>';
                  }).join('') +
                '</tbody>' +
              '</table>' +
            '</div>';
      }

      return '<div id="' + anchorId + '" class="study-conditions">' +
               '<h3>Conditions <span class="muted small">— what we set up to test it</span></h3>' +
               baselineHtml + variantsHtml + expertHtml +
             '</div>';
    }

    // --- per-study section builder -----------------------------------
    function studySection(s, i) {
      var isV3 = !!(s.purpose || s.simulation_set || s.behavior_tests
                    || s.pipeline_gate || s.readouts || s.implementation_requirements);
      var statusBadge = '<span class="badge badge-' + _h(s.status || 'planned') + '">'
                      + _h(s.status || 'planned') + '</span>';
      var phaseBadge = s.phase
        ? ' <span class="phase-badge phase-' + _h((s.phase || '').toLowerCase()) + '">' + _h(s.phase) + '</span>'
        : '';

      // Parent + child chips.
      var parents = (s.parent_studies || []).map(function(p) {
        var pn = (typeof p === 'string') ? p : p.study;
        var cond = (typeof p === 'string') ? 'tests-passed' : (p.condition || 'tests-passed');
        return '<code>' + _h(pn) + '</code> <span class="muted">(' + _h(cond) + ')</span>';
      }).join(' · ');
      var kids = (children[s.name] || []).map(function(c) { return '<code>' + _h(c) + '</code>'; }).join(' · ');

      if (isV3) return v3StudySection(s, i, statusBadge, phaseBadge, parents, kids);

      // Variants list.
      var variants = (s.variants || []).map(function(v) {
        var paramRows = v.params ? Object.entries(v.params).map(function(kv) {
          return '<li><code>' + _h(kv[0]) + ' = ' + _h(JSON.stringify(kv[1])) + '</code></li>';
        }).join('') : '';
        return '<details class="variant"><summary><strong>' + _h(v.name) + '</strong>'
             + (v.status ? ' <span class="muted">[' + _h(v.status) + ']</span>' : '')
             + '</summary>'
             + '<p>' + _multiline(v.description || '') + '</p>'
             + (paramRows ? '<ul class="params">' + paramRows + '</ul>' : '')
             + '</details>';
      }).join('');

      // Interventions list.
      var interventions = (s.interventions || []).map(function(iv) {
        var tests = (iv.triggers_tests || []).map(function(t) { return '<code>' + _h(t) + '</code>'; }).join(', ');
        return '<details class="intervention"><summary><strong>' + _h(iv.name) + '</strong></summary>'
             + '<p>' + _multiline(iv.description || '') + '</p>'
             + (tests ? '<p class="muted">Triggers tests: ' + tests + '</p>' : '')
             + '</details>';
      }).join('');

      // Expected-behavior table (the assumptions / predictions block).
      var ebRows = (s.expected_behavior || []).map(function(b) {
        var cites = (b.cites || []).map(function(k) { return '<code>' + _h(k) + '</code>'; }).join(', ');
        return '<tr class="eb-row eb-' + _h(b.status || 'implemented') + '">'
             + '<td><code>' + _h(b.name) + '</code></td>'
             + '<td>' + _h(b.en || '') + '</td>'
             + '<td>' + _h(b.status || 'implemented') + '</td>'
             + '<td>' + cites + '</td>'
             + '</tr>';
      }).join('');

      // Gaps (assumptions / explicit deferrals).
      var gaps = (s.gaps || []).map(function(g) {
        return '<details class="gap"><summary><strong>' + _h(g.id || '') + '</strong> — ' + _h(g.title || '') + '</summary>'
             + (g.why ? '<p><strong>Why:</strong> ' + _multiline(g.why) + '</p>' : '')
             + (g.approach ? '<p><strong>Approach:</strong> ' + _multiline(g.approach) + '</p>' : '')
             + (g.defer_until ? '<p class="muted">Deferred until: <code>' + _h(g.defer_until) + '</code></p>' : '')
             + '</details>';
      }).join('');

      // Expert questions (the validate-this block).
      var expertQs = (s.expert_questions || []).map(function(q) {
        return '<li>' + _h(q) + '</li>';
      }).join('');

      // Bibliography keys for this study (so the expert can pull each).
      var bib = (s.bibliography && s.bibliography.bib_keys) || [];
      var bibList = bib.map(function(k) { return '<code>' + _h(k) + '</code>'; }).join(', ');

      // Sub-section ids — used by the per-study sticky sub-nav so each
      // section is clickable to scroll-to.
      var slug = _h(s.name);
      var sidQ  = 'study-' + slug + '-qh';
      var sidBg = 'study-' + slug + '-background';
      var sidPr = 'study-' + slug + '-predictions';
      var sidVa = 'study-' + slug + '-variants';
      var sidIn = 'study-' + slug + '-interventions';
      var sidGa = 'study-' + slug + '-gaps';
      var sidQu = 'study-' + slug + '-questions';
      var sidRe = 'study-' + slug + '-refs';

      // Per-study sub-nav. CSS makes it sticky inside the .study section,
      // so it sticks at the top of the viewport while you're in the study
      // and is naturally replaced by the next study's nav as you scroll
      // past.
      var subNav = '';
      var links = [];
      links.push('<a href="#' + sidQ + '">Question</a>');
      if (s.description)  links.push('<a href="#' + sidBg + '">Background</a>');
      if (ebRows)         links.push('<a href="#' + sidPr + '">Predictions <span class="sn-count">' + (s.expected_behavior||[]).length + '</span></a>');
      if (variants)       links.push('<a href="#' + sidVa + '">Variants <span class="sn-count">' + (s.variants||[]).length + '</span></a>');
      if (interventions)  links.push('<a href="#' + sidIn + '">Interventions <span class="sn-count">' + (s.interventions||[]).length + '</span></a>');
      if (gaps)           links.push('<a href="#' + sidGa + '">Gaps <span class="sn-count">' + (s.gaps||[]).length + '</span></a>');
      if (expertQs)       links.push('<a href="#' + sidQu + '">Expert questions <span class="sn-count">' + (s.expert_questions||[]).length + '</span></a>');
      if (bibList)        links.push('<a href="#' + sidRe + '">Cited refs <span class="sn-count">' + bib.length + '</span></a>');
      var dependsBrief = parents ? 'Depends on: ' + parents : '<em>Root study (no dependencies)</em>';

      subNav = ''
        + '<div class="study-nav">'
        +   '<div class="study-nav-row1">'
        +     '<span class="study-nav-num">' + (i + 1) + '.</span>'
        +     '<strong class="study-nav-name">' + _h(s.name) + '</strong>'
        +     statusBadge
        +     '<span class="study-nav-deps muted small">' + dependsBrief + '</span>'
        +   '</div>'
        +   '<nav class="study-nav-row2">' + links.join('') + '</nav>'
        +   '<span class="sn-collapse-hint" data-collapse="study">▴ click to collapse full study</span>'
        + '</div>';

      // Wrap the v4 narrative-spine section in a <details class="study-fold">
      // so the Expand all / Collapse all toolbar buttons (which target
      // .study-fold) actually have something to operate on. v3 studies got
      // this for free via v3StudySection's <details> wrapper; v4 sections
      // were left flat and the buttons did nothing on v4-only investigations.
      // Open by default so existing reader behaviour is unchanged.
      //
      // Reuses the v3 `.sp-*` CSS classes so the collapsed-card look matches
      // what v3 readers already see: num + title + verdict / one-line
      // objective / slug+depth meta / chips for predictions + variants +
      // refs / expand hint. Populated from v4 narrative-spine fields:
      // objective for the one-liner, expected_behavior for the chips, etc.
      var v4Title    = s.title || _humanizeStudyName(s.name).title;
      var v4Verdict  = (function() {
        var st = (s.status || 'planning').toLowerCase();
        if (st === 'planning' || st === 'planned') return {cls: 'v-prelim', emoji: '📋', label: 'Planned'};
        if (st === 'running' || st === 'in_progress') return {cls: 'v-cal', emoji: '🔬', label: 'Running'};
        if (st === 'complete' || st === 'ran' || st === 'passed') return {cls: 'v-pass', emoji: '✅', label: 'Complete'};
        if (st === 'failed' || st === 'invalid') return {cls: 'v-fail', emoji: '❌', label: 'Failed'};
        return {cls: 'v-none', emoji: '·', label: _h(s.status || 'planning')};
      })();
      var v4Objective = _firstSentence(s.objective || '');
      var v4Meta = ['<code>' + _h(s.name) + '</code>',
                    'depth ' + (depthMap[s.name] || 0)];
      if (s.phase) v4Meta.push('phase ' + _h(s.phase));
      if (s.topic) v4Meta.push('topic ' + _h(s.topic));

      // Rich-panel content: an optional `report:` block on the study
      // YAML (same shape as v3 _studyControlPanel reads) drives the
      // dnaa-style Confidence/Evidence chips + CONCLUSION/INSIGHT/CAVEAT
      // rows + status-colored key_metrics. Synthesises sensible
      // pre-execution scaffold values when the block is missing or
      // partial, so a fresh investigation lands with a populated card
      // instead of an empty one.
      var rep = s.report || {};
      var v4Conf = (rep.confidence || '').trim();
      var v4Ev   = (rep.evidence_quality || '').trim();
      if (!v4Conf && (v4Verdict.label === 'Planned')) v4Conf = 'design-stage';
      if (!v4Ev   && (v4Verdict.label === 'Planned')) v4Ev   = 'scaffold';
      var v4Conclusion = rep.conclusion   || _firstSentence(rep.result)
                       || (v4Verdict.label === 'Planned' && s.hypothesis
                            ? 'Predicted — ' + _firstSentence(s.hypothesis) : '');
      var v4Insight    = rep.main_insight || _firstSentence(rep.interpretation);
      var v4Caveat     = rep.caveat;
      if (!v4Caveat && Array.isArray(s.limitations) && s.limitations.length) {
        var l0 = s.limitations[0];
        v4Caveat = (typeof l0 === 'string') ? l0 : (l0 && (l0.text || l0.limitation)) || '';
      }
      var v4LitMatch = (rep.lit_match || '').trim();

      // Chip strip: rich key_metrics (label+value+status) when authored,
      // else auto-derived prediction-count + status breakdown + variants
      // + refs + deps.
      var v4Chips = [];
      (rep.key_metrics || []).forEach(function(m) {
        if (typeof m === 'string') {
          v4Chips.push('<span class="sp-metric">' + _h(m) + '</span>');
        } else if (m && typeof m === 'object') {
          var st = (m.status || '').toLowerCase();
          var icon = st === 'pass' ? '✅ ' : st === 'warn' ? '⚠️ ' : st === 'fail' ? '❌ ' : '';
          var txt = (m.label || '') + (m.value != null ? ': ' + m.value : '');
          v4Chips.push('<span class="sp-metric sp-metric-' + _h(st || 'plain') + '">' + icon + _h(txt) + '</span>');
        }
      });
      var ebList = s.expected_behavior || [];
      if (ebList.length) {
        var counts = {stub: 0, gated: 0, implemented: 0};
        ebList.forEach(function(b) {
          var st = (b && b.status) || 'implemented';
          if (counts[st] !== undefined) counts[st]++;
        });
        v4Chips.push('<span class="sp-metric">' + ebList.length + ' predictions</span>');
        if (counts.implemented) v4Chips.push('<span class="sp-metric sp-metric-pass">✅ ' + counts.implemented + ' implemented</span>');
        if (counts.gated)       v4Chips.push('<span class="sp-metric sp-metric-warn">⏳ ' + counts.gated + ' gated</span>');
        if (counts.stub)        v4Chips.push('<span class="sp-metric">🟡 ' + counts.stub + ' stub</span>');
      }
      var nVar = (s.variants || []).length;
      if (nVar) v4Chips.push('<span class="sp-metric">' + nVar + ' variants</span>');
      var v4Bib = (s.bibliography && s.bibliography.bib_keys) || [];
      if (v4Bib.length) v4Chips.push('<span class="sp-metric">' + v4Bib.length + ' refs</span>');
      var nParents = (s.parent_studies || []).length;
      if (nParents) v4Chips.push('<span class="sp-metric">depends on ' + nParents + '</span>');
      var nKids = (children[s.name] || []).length;
      if (nKids) v4Chips.push('<span class="sp-metric">blocks ' + nKids + '</span>');
      if (v4LitMatch) v4Chips.push('<span class="sp-metric">Lit match: ' + _h(v4LitMatch) + '</span>');

      // Section-nav chips inside the sticky panel. CSS hides this row
      // when the fold is COLLAPSED (it would just duplicate the
      // metric chips below); when OPEN, the rich rows are hidden and
      // this nav becomes the primary content of the sticky strip, so
      // the user can jump to Question / Background / Predictions / etc.
      // without scrolling back to the topbar.
      var spSectionNav = links.length
        ? '<nav class="sp-section-nav">' + links.join('') + '</nav>'
        : '';

      var foldSummary = ''
        + '<summary class="study-panel">'
        +   '<div class="sp-top">'
        +     '<span class="sp-num">' + (i + 1) + '.</span>'
        +     '<span class="sp-title">' + _h(v4Title) + '</span>'
        +     '<span class="sp-verdict ' + v4Verdict.cls + '">' + v4Verdict.emoji + ' ' + _h(v4Verdict.label) + '</span>'
        +   '</div>'
        +   spSectionNav
        +   (v4Objective ? '<div class="sp-objective">' + _h(v4Objective) + '</div>' : '')
        +   '<div class="sp-meta">' + v4Meta.join(' · ') + '</div>'
        +   ((v4Conf || v4Ev)
              ? '<div class="sp-quality">'
                + (v4Conf ? '<span class="sp-conf sp-conf-' + _h(v4Conf.toLowerCase()) + '">Confidence: ' + _h(v4Conf) + '</span>' : '')
                + (v4Ev   ? '<span class="sp-ev">Evidence: ' + _h(v4Ev) + '</span>' : '')
                + '</div>'
              : '')
        +   (v4Conclusion ? '<div class="sp-conclusion"><span class="sp-lbl">Conclusion</span> ' + _h(v4Conclusion) + '</div>' : '')
        +   (v4Chips.length ? '<div class="sp-metrics">' + v4Chips.join('') + '</div>' : '')
        +   (v4Insight ? '<div class="sp-insight"><span class="sp-lbl">Insight</span> ' + _h(v4Insight) + '</div>' : '')
        +   (v4Caveat  ? '<div class="sp-caveat"><span class="sp-lbl">Caveat</span> '   + _h(v4Caveat)  + '</div>' : '')
        +   '<span class="sp-expand-hint">▸ click to expand full study</span>'
        + '</summary>';

      // Dropped chrome on the v4 expanded section to remove three forms
      // of redundancy with the (now-rich) sp-* summary panel:
      //   1. subNav (sticky study-nav with chips like Question / Background
      //      / Predictions / Cited refs) — the sp-metrics chips in the
      //      summary panel already convey the same counts; the topbar nav
      //      handles cross-study navigation. Removing it also kills the
      //      double-sticky stack (topbar + study-fold panel + study-nav).
      //   2. <header class="study-header"><h2>num. slug status</h2></header>
      //      — every field is in sp-top + sp-meta of the panel above.
      //   3. The "Depends on / Blocks" paragraphs that lived in the
      //      header — these are now shown as the resolved dep list right
      //      below the summary so the dep slugs (not just counts) stay
      //      visible while the panel is sticky.
      var depsLine = '';
      if (parents || kids) {
        var bits = [];
        if (parents) bits.push('<span class="muted">Depends on:</span> ' + parents);
        if (kids)    bits.push('<span class="muted">Blocks:</span> '     + kids);
        depsLine = '<p class="study-deps muted small">' + bits.join(' &nbsp;·&nbsp; ') + '</p>';
      }

      return ''
        + '<details class="study-fold" id="study-fold-' + slug + '">'
        + foldSummary
        + '<section class="study" id="study-' + slug + '">'
        +   depsLine

        +   '<div class="qh" id="' + sidQ + '">'
        +     (s.question   ? '<p><strong>Question.</strong> '   + _multiline(s.question)   + '</p>' : '')
        +     (s.hypothesis ? '<p><strong>Hypothesis.</strong> ' + _multiline(s.hypothesis) + '</p>' : '')
        +     (s.objective  ? '<p><strong>Objective.</strong> '  + _multiline(s.objective)  + '</p>' : '')
        +   '</div>'

        +   (s.description ? '<div class="description" id="' + sidBg + '"><h3>Background</h3><p>' + _multiline(s.description) + '</p></div>' : '')

        +   (ebRows ? '<div id="' + sidPr + '"><h3>Predicted behavior (assumptions to validate)</h3>'
                    + '<p class="muted small">Each row is a precise, testable prediction. Status indicates whether the supporting code is in place today (implemented) or gated on upstream work (gated / stub).</p>'
                    + '<table class="eb"><thead><tr><th>Name</th><th>Prediction</th><th>Status</th><th>Citations</th></tr></thead>'
                    + '<tbody>' + ebRows + '</tbody></table></div>' : '')

        +   (variants ? '<div id="' + sidVa + '"><h3>Variants (perturbations to be tested)</h3>' + variants + '</div>' : '')

        +   (interventions ? '<div id="' + sidIn + '"><h3>Interventions (simulation plans)</h3>' + interventions + '</div>' : '')

        +   (gaps ? '<div id="' + sidGa + '"><h3>Open gaps / explicit deferrals</h3>'
                  + '<p class="muted small">Concrete pieces of code that need to land before this study can run end-to-end.</p>'
                  + gaps + '</div>' : '')

        +   (expertQs ? '<div id="' + sidQu + '"><h3>Questions for domain experts</h3><ul class="expert-qs">' + expertQs + '</ul></div>' : '')

        +   (bibList ? '<div id="' + sidRe + '"><h3>References cited by this study</h3><p>' + bibList + '</p></div>' : '')

        + '</section>'
        + '</details>';
    }

    // ── PARTS grouping (framework): investigation.yaml may declare a `parts`
    // field grouping studies into conceptual phases (Foundations / Nucleotide
    // cycle / Chromosome binding / Initiation trigger / Reset mechanisms / …).
    // When present, render a Part header before each group's studies so the
    // report reads as a coherent mechanism progression rather than a flat list.
    // Schema:
    //   parts:
    //     - name: "I. Foundations"
    //       overview: "Optional 1-2 sentence prose..."
    //       studies: ["dnaa-00-parameter-foundation", "dnaa-01-expression-dynamics"]
    var studiesHtml;
    var parts = (iset && Array.isArray(iset.parts)) ? iset.parts : null;
    if (parts && parts.length) {
      // Map slug → index in `ordered` so we render each study once even when
      // a part declares a study not in `ordered` (skip) or `ordered` has a
      // study not declared in any part (append as "Unassigned" group).
      var byNameIdx = {};
      ordered.forEach(function(s, i) { byNameIdx[s && s.name] = i; });
      var rendered = {};
      var groupHtmls = [];
      parts.forEach(function(part) {
        var partStudies = (part && Array.isArray(part.studies)) ? part.studies : [];
        var sections = [];
        partStudies.forEach(function(slug) {
          var i = byNameIdx[slug];
          if (i === undefined) return;
          sections.push(studySection(ordered[i], i));
          rendered[slug] = true;
        });
        if (!sections.length) return;
        var heading = '<header class="part-heading"><h2 class="part-title">' + _h(part.name || '') + '</h2>'
          + (part.overview ? '<p class="part-overview">' + _multiline(part.overview) + '</p>' : '')
          + '</header>';
        groupHtmls.push('<section class="investigation-part">' + heading + sections.join('\n') + '</section>');
      });
      // Catch any unassigned studies (so nothing silently disappears).
      var unassigned = ordered.filter(function(s) { return s && !rendered[s.name]; });
      if (unassigned.length) {
        var stub = '<header class="part-heading"><h2 class="part-title">Other studies</h2></header>';
        var sec = unassigned.map(function(s) { return studySection(s, byNameIdx[s.name]); }).join('\n');
        groupHtmls.push('<section class="investigation-part">' + stub + sec + '</section>');
      }
      studiesHtml = groupHtmls.join('\n');
    } else {
      studiesHtml = ordered.map(studySection).join('\n');
    }

    /* `acceptance` variable removed: it built an <ol> of acceptance_criteria
       entries that fed the top-of-report "Acceptance criteria" section
       (now removed). The acceptance_criteria field on investigation.yaml
       still exists in the schema; per-study behavior_tests +
       conclusion_verdicts carry the same signal more actionably.
       The defensive `_asList` coercion this fix added at the (now-deleted)
       render site is superseded; the durable guard lives server-side in
       `_coerce_list_field`. `_asList` is kept as a reusable helper. */
    var acceptance = '';

    // ── Collect the union of references across the investigation + studies ──
    // Sources: study expected_behavior[].cites + bibliography.bib_keys (bib keys),
    // the investigation's declared inputs.references (iset.references, bib keys),
    // and each study's `references:` (bib-key strings → looked up in papers.bib;
    // rich {name,url,role} entries → rendered as standalone sources).
    var citedKeys = new Set();
    var extraSources = [];
    function _collectRef(r) {
      if (typeof r === 'string') { if (r) citedKeys.add(r); return; }
      if (!r || typeof r !== 'object') return;
      if (r.key || r.bib_key) { citedKeys.add(r.key || r.bib_key); return; }
      if (r.name || r.url || r.path) extraSources.push(r);
    }
    (iset.references || []).forEach(_collectRef);
    specs.forEach(function(s) {
      (s.expected_behavior || []).forEach(function(b) {
        (b.cites || []).forEach(function(k) { citedKeys.add(k); });
      });
      var bib = (s.bibliography && s.bibliography.bib_keys) || [];
      bib.forEach(function(k) { citedKeys.add(k); });
      (s.references || []).forEach(_collectRef);
    });
    var orderedCited = Array.from(citedKeys).sort();
    var referencesHtml = orderedCited.map(function(key) {
      var e = bibByKey[key];
      if (!e) {
        return '<li class="ref-entry"><code>' + _h(key) + '</code> <span class="muted">— (not in papers.bib)</span></li>';
      }
      var citation = '';
      if (e.author)  citation += _h(e.author);
      if (e.year)    citation += (citation ? ' (' + _h(e.year) + ')' : _h(e.year));
      if (e.title)   citation += (citation ? '. ' : '') + '<em>' + _h(e.title) + '</em>';
      if (e.journal) citation += '. ' + _h(e.journal);
      if (e.volume) {
        citation += ' ' + _h(e.volume);
        if (e.number) citation += '(' + _h(e.number) + ')';
      }
      if (e.pages)   citation += ', pp. ' + _h(e.pages);
      var doiLink = e.doi ? ' · <a href="https://doi.org/' + encodeURIComponent(e.doi) + '" target="_blank">doi:' + _h(e.doi) + '</a>' : '';
      var urlLink = e.url ? ' · <a href="' + _h(e.url) + '" target="_blank">link ↗</a>' : '';
      return '<li class="ref-entry" id="ref-' + _h(key) + '">'
           + '<code>' + _h(key) + '</code> &middot; '
           + citation
           + doiLink + urlLink
           + (e.note ? '<div class="muted small">Note: ' + _h(e.note) + '</div>' : '')
           + '</li>';
    }).join('');
    // Rich study/investigation sources (name + online link + role) that aren't
    // papers.bib keys — de-duped by name+url, appended to the References list.
    var _seenSrc = {};
    referencesHtml += extraSources.filter(function (r) {
      var k = (r.name || '') + '|' + (r.url || r.path || '');
      if (_seenSrc[k]) return false; _seenSrc[k] = 1; return true;
    }).map(function (r) {
      var label = _h(r.name || r.url || r.path || 'source');
      var head = r.url
        ? '<a href="' + _h(r.url) + '" target="_blank" rel="noopener">' + label + '</a> <small class="muted">↗</small>'
        : '<strong>' + label + '</strong>';
      return '<li class="ref-entry">' + head
           + (r.role ? '<div class="muted small">' + _h(r.role) + '</div>' : '') + '</li>';
    }).join('');

    // ── Build the TOC (sidebar nav) entries from the ordered studies ────
    // Display name is human-readable; the kebab-slug appears in small
    // muted text below as a stable identifier reference. Counts surface
    // the v3-shape quantities a reader actually cares about.
    function _humanizeStudyName(slug) {
      // strip a leading "<prefix>-NN[a-z]?-" so dnaa-01-expression-dynamics
      // becomes just "expression-dynamics". Keep the numbered prefix for
      // display as a chip ("dnaa-01").
      var m = /^([a-z]+-\d+[a-z]*)-(.+)$/.exec(slug);
      if (!m) return {chip: '', title: slug.replace(/-/g, ' ')};
      var rest = m[2].replace(/-/g, ' ');
      // Title-case the first letter of the first word; leave the rest in
      // lowercase so identifiers (rna_synth_prob etc.) read naturally.
      rest = rest.charAt(0).toUpperCase() + rest.slice(1);
      // Truncate aggressively for very long follow-up names.
      if (rest.length > 60) rest = rest.slice(0, 57) + '…';
      return {chip: m[1], title: rest};
    }

    var nameClean = _h(iset.name);

    return ''
      + '<!doctype html>\n<html><head><meta charset="utf-8">'
      + '<title>Investigation: ' + _h(iset.title || iset.name) + '</title>'
      + '<style>'
      // ── reset + base ──
      + '*{box-sizing:border-box}'
      + 'html,body{margin:0;padding:0}'
      + 'body{font-family:-apple-system,system-ui,"Segoe UI",Roboto,sans-serif;color:#0f172a;line-height:1.55;background:#fff}'
      // ── layout: sticky top nav + single centered column ──
      + '.topbar{position:sticky;top:0;z-index:100;display:flex;flex-wrap:wrap;align-items:center;gap:6px;'
      +     'padding:9px 20px;background:rgba(255,255,255,0.95);backdrop-filter:saturate(140%) blur(6px);'
      +     'border-bottom:1px solid #e2e8f0}'
      + '.topbar .tb-title{font-weight:700;font-size:0.92em;color:#0f172a;margin-right:10px;white-space:nowrap}'
      + '.topbar a{font-size:0.83em;color:#334155;text-decoration:none;padding:4px 12px;border-radius:9999px;background:#f1f5f9;white-space:nowrap}'
      + '.topbar a:hover{background:#e2e8f0;color:#0f172a}'
      + '.topbar a.active{background:#dbeafe;color:#1e40af;font-weight:600}'
      /* iset switcher dropdown at the right end of the topbar (margin-left:auto
         pushes it past the section links). Calls /api/investigation-registry
         to list peer dashboards; click a peer row to navigate. Trigger styled
         like the section-anchor chips but with a subtle distinguishing border
         so it doesn't look like just another anchor. */
      + '.tb-iset-switcher{margin-left:auto;display:inline-flex;align-items:center;gap:5px;'
      +     'font:inherit;font-size:0.83em;color:#334155;'
      +     'padding:4px 12px;border-radius:9999px;background:#fff;border:1px solid #cbd5e1;cursor:pointer;'
      +     'white-space:nowrap}'
      + '.tb-iset-switcher:hover{background:#f1f5f9;border-color:#94a3b8}'
      + '.tb-iset-switcher[aria-expanded="true"]{background:#dbeafe;border-color:#3b82f6;color:#1e40af}'
      + '.tb-iset-switcher-icon{font-size:1.05em;line-height:1}'
      + '.tb-iset-switcher-arrow{font-size:0.7em;color:#94a3b8;margin-left:1px}'
      + '.tb-iset-menu{position:fixed;z-index:200;min-width:320px;max-width:480px;max-height:70vh;overflow-y:auto;'
      +     'background:#fff;border:1px solid #cbd5e1;border-radius:8px;'
      +     'box-shadow:0 8px 24px rgba(0,0,0,0.12);padding:6px 0}'
      + '.tb-iset-menu[hidden]{display:none}'
      + '.tb-iset-menu-section{padding:6px 14px 4px;font-size:0.7em;font-weight:700;letter-spacing:0.05em;'
      +     'text-transform:uppercase;color:#94a3b8}'
      + '.tb-iset-menu-row{display:flex;align-items:center;gap:8px;padding:7px 14px;cursor:pointer;border:0;background:none;'
      +     'width:100%;text-align:left;font:inherit;color:#0f172a;font-size:0.86em}'
      + '.tb-iset-menu-row:hover{background:#f1f5f9}'
      + '.tb-iset-menu-row-current{background:#dbeafe;color:#1e40af;cursor:default}'
      + '.tb-iset-menu-row-current:hover{background:#dbeafe}'
      + '.tb-iset-menu-slug{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}'
      + '.tb-iset-menu-pill{font-size:0.7em;font-weight:600;padding:2px 7px;border-radius:9999px;'
      +     'background:#f1f5f9;color:#64748b;white-space:nowrap}'
      + '.tb-iset-menu-pill-here{background:#dcfce7;color:#166534}'
      + '.tb-iset-menu-pill-running{background:#fef9c3;color:#854d0e}'
      + '.tb-iset-menu-pill-dormant{background:#f1f5f9;color:#64748b}'
      + '.tb-iset-menu-empty,.tb-iset-menu-error{padding:10px 14px;font-size:0.82em;color:#64748b}'
      + '.tb-iset-menu-error{color:#991b1b}'
      + '.content{max-width:none;margin:0;padding:24px 40px}'
      // Anchor targets clear the sticky bar when jumped to.
      + '.content [id]{scroll-margin-top:60px}'
      // Cap prose paragraphs only (≈75 chars) so wide-screen lines stay
      // readable, but keep tables, code blocks, and callouts full-width.
      // Text spans the full content width — no separate prose cap (which used
      // to stop paragraphs short of the page while headings/rules ran wider).
      + '.content p, .content li, .content .description p, .qh p{max-width:none}'
      // ── typography ──
      + 'h1{margin:0 0 8px 0;font-size:2em;line-height:1.2}'
      + 'h2{margin:32px 0 12px 0;font-size:1.4em;border-bottom:1px solid #e2e8f0;padding-bottom:6px;scroll-margin-top:16px}'
      + 'h3{margin:22px 0 8px 0;font-size:1.08em;color:#1e293b}'
      + 'p{margin:8px 0}'
      + 'code{background:#f1f5f9;padding:1px 5px;border-radius:3px;font-size:0.88em;font-family:ui-monospace,monospace}'
      + 'pre{background:#f1f5f9;padding:10px 12px;border-radius:4px;font-size:0.85em;overflow-x:auto;white-space:pre-wrap;word-wrap:break-word}'
      // ── tables ──
      + 'table{border-collapse:collapse;width:100%;font-size:0.92em;margin:8px 0}'
      + 'th,td{border-bottom:1px solid #e2e8f0;padding:7px 10px;text-align:left;vertical-align:top}'
      + 'th{background:#f8fafc;font-weight:600}'
      + 'table.eb td{vertical-align:top}'
      + 'table.eb td:first-child{font-family:ui-monospace,monospace;font-size:0.85em;color:#475569;white-space:nowrap}'
      // ── badges + status pills ──
      + '.muted{color:#64748b}'
      + '.small{font-size:0.85em}'
      + '.badge{display:inline-block;font-size:0.72em;padding:2px 9px;border-radius:9999px;background:#e2e8f0;color:#1e293b;text-transform:lowercase;vertical-align:middle;margin-left:8px;font-weight:500}'
      + '.badge-planned{background:#f1f5f9;color:#475569}'
      + '.badge-running{background:#dbeafe;color:#1e40af}'
      + '.badge-ran{background:#d1fae5;color:#065f46}'
      + '.badge-complete{background:#d1fae5;color:#064e3b}'
      + '.badge-failed{background:#fee2e2;color:#991b1b}'
      + '.badge-invalid{background:#fee2e2;color:#991b1b}'
      + '.badge-planning{background:#fef3c7;color:#92400e}'
      + '.phase-badge{display:inline-block;font-size:0.72em;padding:2px 9px;border-radius:9999px;margin-right:4px;font-weight:500;background:#e0e7ff;color:#3730a3;vertical-align:middle}'
      + '.phase-design{background:#e0e7ff;color:#3730a3}'
      + '.phase-build{background:#fef3c7;color:#92400e}'
      + '.phase-simulate{background:#dbeafe;color:#1e40af}'
      + '.phase-evaluate{background:#fce7f3;color:#9d174d}'
      + '.phase-decide{background:#d1fae5;color:#065f46}'
      + '.callout{margin:8px 0;padding:10px 14px;border-radius:4px;line-height:1.55}'
      + '.callout.cl-blue{background:#eff6ff;border-left:4px solid #3b82f6}'
      + '.callout.cl-yellow{background:#fefce8;border-left:4px solid #facc15}'
      + '.callout.cl-green{background:#f0fdf4;border-left:4px solid #10b981}'
      + '.callout strong{margin-right:6px}'
      // follow-up cards
      + '.fu-card{padding:10px 14px;margin:8px 0;border:1px solid #e2e8f0;border-left:4px solid #94a3b8;border-radius:4px;background:#f8fafc;font-size:0.93em}'
      + '.fu-kind-existing{border-left-color:#3b82f6;background:#eff6ff}'
      + '.fu-kind-infrastructure_fix{border-left-color:#dc2626;background:#fef2f2}'
      + '.fu-kind-calibration_task{border-left-color:#f59e0b;background:#fefce8}'
      + '.fu-kind-expert_question{border-left-color:#a855f7;background:#faf5ff}'
      + '.fu-kind-new{border-left-color:#10b981;background:#f0fdf4}'
      + '.fu-head{display:flex;align-items:center;gap:8px;margin-bottom:6px;flex-wrap:wrap}'
      + '.fu-kind{font-size:0.7em;text-transform:uppercase;letter-spacing:0.05em;padding:1px 8px;border-radius:9999px;background:#e2e8f0;color:#475569}'
      + '.fu-effort{font-size:0.7em;padding:1px 8px;border-radius:9999px;background:#e0e7ff;color:#3730a3;font-family:ui-monospace,monospace}'
      + '.fu-status{font-size:0.7em;padding:1px 8px;border-radius:9999px;background:#e2e8f0;color:#475569}'
      + '.fu-status-blocked{background:#fef3c7;color:#92400e}'
      + '.fu-status-done{background:#d1fae5;color:#065f46}'
      + '.fu-title{flex:1}'
      + '.fu-why,.fu-unblocks,.fu-acc,.fu-hyp{margin:4px 0 0 0;font-size:0.92em;line-height:1.45}'
      + '.fu-hyp{padding:6px 10px;background:#fff;border-radius:3px;border:1px dashed #cbd5e1}'
      + '.fu-acc ul{margin:2px 0 0 18px;padding:0}'
      // discovery implications — alternate hypotheses, mechanism updates,
      // selectable follow-up proposals.
      + '.discovery-implications{margin:0 0 24px 0;padding:14px 16px;background:#fdfcff;border:1px solid #ddd6fe;border-radius:8px}'
      + '.discovery-implications>h3{margin-top:0}'
      + '.di-group{margin:14px 0 0 0}'
      + '.di-group>h4{margin:0 0 6px 0;font-size:0.95em}'
      + '.di-uncertainties{display:flex;gap:14px;flex-wrap:wrap;margin-top:6px}'
      + '.di-unc{flex:1;min-width:220px;padding:8px 12px;border-radius:6px;font-size:0.9em}'
      + '.di-unc>h4{margin:0 0 4px 0;font-size:0.85em}'
      + '.di-unc ul{margin:0 0 0 18px;padding:0}'
      + '.di-unc-resolved{background:#ecfdf5;border:1px solid #a7f3d0}'
      + '.di-unc-remaining{background:#fffbeb;border:1px solid #fde68a}'
      + '.di-alt-card,.di-mech-card,.di-fup-card{padding:10px 14px;margin:8px 0;border:1px solid #e2e8f0;border-left:4px solid #a78bfa;border-radius:4px;background:#fff;font-size:0.93em}'
      + '.di-alt-stmt{margin-bottom:4px}'
      + '.di-alt-why{color:#475569;margin:4px 0;line-height:1.45}'
      + '.di-alt-ev{display:flex;gap:8px;margin:4px 0}'
      + '.di-ev{font-size:0.78em;padding:1px 8px;border-radius:9999px}'
      + '.di-ev-for{background:#dcfce7;color:#166534}'
      + '.di-ev-against{background:#fee2e2;color:#991b1b}'
      + '.di-alt-disc,.di-alt-elems,.di-fup-targets{font-size:0.85em;color:#475569;margin-top:4px}'
      + '.di-lbl{color:#64748b;font-weight:600}'
      + '.di-mech-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:4px}'
      + '.di-mech-target{background:#f1f5f9;padding:1px 6px;border-radius:3px}'
      + '.di-mech-rationale{color:#475569;line-height:1.45}'
      + '.di-update-chip{font-size:0.7em;text-transform:uppercase;letter-spacing:0.05em;padding:1px 8px;border-radius:9999px;background:#e2e8f0;color:#475569}'
      + '.di-update-strengthen{background:#dcfce7;color:#166534}'
      + '.di-update-weaken{background:#fef3c7;color:#92400e}'
      + '.di-update-reject{background:#fee2e2;color:#991b1b}'
      + '.di-update-revise,.di-update-split,.di-update-merge{background:#e0e7ff;color:#3730a3}'
      + '.di-conf-change{font-size:0.72em;padding:1px 8px;border-radius:9999px;background:#eef2ff;color:#4338ca;font-family:ui-monospace,monospace}'
      + '.di-approval-badge{font-size:0.7em;padding:1px 8px;border-radius:9999px;background:#fef3c7;color:#92400e}'
      + '.di-fup-card{border-left-color:#10b981}'
      + '.di-fup-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:4px}'
      + '.di-fup-title{flex:1;min-width:160px}'
      + '.di-fup-exp{color:#475569;margin:4px 0;line-height:1.45}'
      + '.di-type-chip,.di-trigger-chip,.di-prio-chip{font-size:0.7em;padding:1px 8px;border-radius:9999px;background:#e2e8f0;color:#475569}'
      + '.di-trigger-chip{background:#f3e8ff;color:#6b21a8}'
      + '.di-gain-chip{font-size:0.7em;padding:1px 8px;border-radius:9999px;background:#e2e8f0;color:#475569}'
      + '.di-gain-high{background:#dcfce7;color:#166534}'
      + '.di-gain-medium{background:#fef9c3;color:#854d0e}'
      + '.di-gain-low{background:#f1f5f9;color:#64748b}'
      + '.di-add-btn{margin-top:8px;font-size:0.82em;padding:4px 12px;border:1px solid #10b981;background:#f0fdf4;color:#065f46;border-radius:4px;cursor:pointer}'
      + '.di-add-btn:hover{background:#dcfce7}'
      + '.di-add-btn:disabled{opacity:0.6;cursor:default}'
      + '.di-addressed{margin-top:12px}'
      // charts — SVGs scale to fit their card container; preserves aspect
      // ratio so a 1400×484 chart shrinks to (e.g.) 800×276 instead of
      // overflowing horizontally + clipping content.
      + '.chart-card{background:#fff;border:1px solid #e2e8f0;border-radius:6px;padding:8px 12px 12px 12px;margin:10px 0}'
      + '.chart-card svg,.chart-card img.chart-img{display:block;width:100%;max-width:100%;height:auto}'
      + '.chart-caption{font-size:0.83em;color:#475569;margin-top:4px;line-height:1.4}'
      + '.chart-simulations{font-size:0.9em;color:#1e3a8a;background:#dbeafe;border-left:3px solid #2563eb;padding:6px 10px;margin-top:8px;border-radius:0 3px 3px 0;line-height:1.5}'
      + '.chart-simulations strong{color:#1e40af}'
      + '.chart-interpretation{font-size:0.9em;color:#14532d;background:#dcfce7;border-left:3px solid #16a34a;padding:6px 10px;margin-top:6px;border-radius:0 3px 3px 0;line-height:1.5}'
      + '.chart-interpretation strong{color:#15803d}'
      // implementation-requirement cards (biologist-friendly layout)
      + '.req-card{padding:12px 14px;margin:10px 0;border:1px solid #e2e8f0;border-radius:6px;background:#fff;box-shadow:0 1px 1px rgba(0,0,0,0.02)}'
      + '.req-header{display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap}'
      + '.req-id{font-size:0.78em;color:#475569;background:#f1f5f9;padding:1px 6px;border-radius:3px;font-family:ui-monospace,monospace}'
      + '.req-title{font-size:1.02em;flex:1;line-height:1.3}'
      + '.req-badges{display:flex;gap:4px;flex-wrap:wrap}'
      + '.req-kind{font-size:0.7em;text-transform:lowercase;padding:1px 8px;border-radius:9999px;background:#e0e7ff;color:#3730a3}'
      + '.req-effort{font-size:0.72em;font-family:ui-monospace,monospace;padding:1px 8px;border-radius:9999px;background:#fef3c7;color:#92400e;font-weight:600}'
      + '.req-status{font-size:0.7em;padding:1px 8px;border-radius:9999px;font-weight:500}'
      + '.req-status-open{background:#fee2e2;color:#991b1b}'
      + '.req-status-deferred{background:#fef3c7;color:#92400e}'
      + '.req-status-done{background:#d1fae5;color:#065f46}'
      + '.req-key{padding:8px 12px;background:#f8fafc;border-left:3px solid #3b82f6;border-radius:3px;font-size:0.94em;line-height:1.5;margin:6px 0}'
      + '.req-deferred{padding:6px 10px;background:#fffbeb;border-left:3px solid #f59e0b;border-radius:3px;font-size:0.86em;color:#78350f;margin:6px 0}'
      + '.req-unblocks{padding:6px 10px;background:#f0fdf4;border-left:3px solid #10b981;border-radius:3px;font-size:0.88em;margin:6px 0}'
      + '.req-unblocks ul{margin:4px 0 0 20px;padding:0}'
      + '.req-unblocks li{margin:2px 0}'
      + '.req-detail{margin-top:8px;padding:6px 10px;background:#fafafa;border:1px solid #e2e8f0;border-radius:4px}'
      + '.req-detail summary{cursor:pointer;font-size:0.85em;color:#475569;font-weight:500}'
      + '.req-detail summary:hover{color:#0f172a}'
      + '.req-detail-section{margin-top:8px}'
      + '.req-detail-section h5{margin:6px 0 4px 0;font-size:0.85em;color:#475569;text-transform:uppercase;letter-spacing:0.04em}'
      + '.req-detail-section ol,.req-detail-section ul{margin:4px 0 0 22px;padding:0;font-size:0.93em}'
      // simulation cards (biologist-friendly layout)
      + '.sim-card{padding:12px 14px;margin:10px 0;border:1px solid #e2e8f0;border-radius:6px;background:#fff;box-shadow:0 1px 1px rgba(0,0,0,0.02)}'
      + '.sim-card.sim-sim-status-gated{border-left:4px solid #f59e0b;background:#fffbeb}'
      + '.sim-card.sim-sim-status-ready{border-left:4px solid #10b981}'
      + '.sim-card.sim-sim-status-ran{border-left:4px solid #3b82f6;background:#eff6ff}'
      + '.sim-header{display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap}'
      + '.sim-name{font-size:1.02em;flex:1}'
      + '.sim-status-pill{font-size:0.7em;text-transform:lowercase;padding:1px 8px;border-radius:9999px;font-weight:500}'
      + '.sim-status-pill.sim-status-ready{background:#d1fae5;color:#065f46}'
      + '.sim-status-pill.sim-status-gated{background:#fef3c7;color:#92400e}'
      + '.sim-status-pill.sim-status-ran{background:#dbeafe;color:#1e40af}'
      + '.sim-pert{padding:8px 12px;margin:6px 0;background:#f8fafc;border-left:3px solid #3b82f6;border-radius:3px;font-size:0.92em}'
      + '.sim-pert ul{margin:4px 0 0 20px;padding:0}'
      + '.sim-pert li{margin:2px 0;line-height:1.4}'
      + '.sim-pert-none{color:#64748b;font-style:italic;border-left-color:#cbd5e1}'
      + '.sim-meta{margin:6px 0;font-size:0.85em;color:#475569}'
      + '.sim-meta span{margin-right:2px}'
      + '.sim-meta em{font-style:normal;color:#94a3b8;font-size:0.92em}'
      + '.sim-readouts,.sim-tests{margin:6px 0;font-size:0.88em;line-height:1.5}'
      + '.sim-blocked{padding:6px 10px;margin:6px 0;background:#fef2f2;border-left:3px solid #dc2626;border-radius:3px;font-size:0.88em;color:#7f1d1d}'
      + '.sim-blocked code{background:rgba(220,38,38,0.08);padding:1px 4px;border-radius:2px}'
      + '.sim-detail{margin-top:8px;padding:6px 10px;background:#fafafa;border:1px solid #e2e8f0;border-radius:4px}'
      + '.sim-detail summary{cursor:pointer;font-size:0.85em;color:#475569;font-weight:500}'
      + '.sim-detail summary:hover{color:#0f172a}'
      + '.sim-extra{margin-top:6px;font-size:0.92em;line-height:1.5}'
      // findings (top-of-section "what we learned" cards)
      + '.findings-section{margin:0 0 24px 0;padding:14px 16px;background:#fafbff;border:1px solid #c7d2fe;border-radius:8px}'
      + '.findings-section h3{margin:0 0 6px 0;color:#3730a3}'
      // study summary (plain-English block at top of each study)
      + '.study-summary{padding:14px 16px;margin:12px 0 16px 0;background:#f8fafc;border-left:4px solid #6366f1;border-radius:6px}'
      + '.study-summary-text{margin:0;font-size:1.02em;line-height:1.55;color:#1e293b}'
      // Compact authored report block (leads each study).
      + '.study-report{margin:12px 0 14px 0;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden}'
      + '.study-report-row{display:flex;gap:0;border-bottom:1px solid #eef2f7}'
      + '.study-report-row:last-child{border-bottom:none}'
      + '.study-report .srl{flex:0 0 130px;padding:9px 12px;background:#f8fafc;font-weight:600;font-size:0.82em;'
      +    'text-transform:uppercase;letter-spacing:0.03em;color:#475569}'
      + '.study-report .srv{flex:1 1 auto;padding:9px 14px;color:#1e293b;min-width:0}'
      + '.tech-details{margin-top:10px;padding:6px 10px;background:#ffffff;border:1px solid #e2e8f0;border-radius:4px;font-size:0.88em}'
      + '.tech-details summary{cursor:pointer;color:#475569;font-weight:500}'
      + '.tech-details summary:hover{color:#0f172a}'
      // decision box
      + '.decision-box{margin:0 0 20px 0;padding:14px 16px;border-radius:8px;border:2px solid #cbd5e1;background:#fff}'
      + '.decision-box.dec-passed{border-color:#10b981;background:#f0fdf4}'
      + '.decision-box.dec-blocked{border-color:#dc2626;background:#fef2f2}'
      + '.decision-box.dec-needscal{border-color:#f59e0b;background:#fffbeb}'
      + '.decision-box.dec-ready{border-color:#3b82f6;background:#eff6ff}'
      + '.decision-box.dec-notstarted{border-color:#94a3b8;background:#f8fafc}'
      + '.decision-box.dec-inprogress{border-color:#8b5cf6;background:#faf5ff}'
      + '.decision-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px}'
      + '.decision-title{margin:0;font-size:1.1em;color:#0f172a}'
      + '.decision-status{font-size:0.9em;font-weight:600;padding:4px 12px;border-radius:9999px;background:#fff;border:1px solid currentColor}'
      + '.dec-passed .decision-status{color:#065f46}'
      + '.dec-blocked .decision-status{color:#991b1b}'
      + '.dec-needscal .decision-status{color:#92400e}'
      + '.dec-ready .decision-status{color:#1e40af}'
      + '.dec-notstarted .decision-status{color:#475569}'
      + '.dec-inprogress .decision-status{color:#6b21a8}'
      + '.decision-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin-bottom:6px}'
      + '.decision-cell{padding:8px 10px;background:#fff;border-radius:4px;font-size:0.9em;border:1px solid #e2e8f0}'
      + '.decision-cell strong{display:block;margin-bottom:4px;font-size:0.88em;color:#475569}'
      + '.decision-cell-pass{border-left:3px solid #10b981}'
      + '.decision-cell-fail{border-left:3px solid #dc2626}'
      + '.decision-cell-block{border-left:3px solid #f59e0b}'
      + '.decision-cell-next{border-left:3px solid #3b82f6}'
      // key takeaways list
      + '.takeaways-section{margin:0 0 20px 0;padding:14px 16px;background:#fafbff;border-left:4px solid #6366f1;border-radius:6px}'
      + '.takeaways-section h3{margin:0 0 8px 0;color:#3730a3}'
      + '.takeaway-list{list-style:none;padding:0;margin:0}'
      + '.takeaway-list li{padding:5px 0;line-height:1.45;font-size:0.95em}'
      + '.takeaway-list li a{color:#1e293b;text-decoration:none}'
      + '.takeaway-list li a:hover{text-decoration:underline}'
      + '.takeaway-glyph{display:inline-block;width:20px;text-align:center;margin-right:4px}'
      + '.takeaway-confirms .takeaway-glyph{color:#10b981}'
      + '.takeaway-contradicts .takeaway-glyph{color:#dc2626}'
      + '.takeaway-partial .takeaway-glyph{color:#f59e0b}'
      + '.takeaway-novel .takeaway-glyph{color:#8b5cf6}'
      + '.findings-group-header{margin:14px 0 4px 0;font-size:1em;color:#3730a3}'
      // test cards (claim-first)
      + '.test-card{padding:10px 14px;margin:8px 0;background:#fff;border:1px solid #e2e8f0;border-radius:6px}'
      + '.test-card.test-classification-primary{border-left:4px solid #10b981}'
      + '.test-card.test-classification-supporting{border-left:4px solid #3b82f6}'
      + '.test-card.test-classification-diagnostic{border-left:4px solid #f59e0b}'
      + '.test-card.test-classification-regression{border-left:4px solid #94a3b8}'
      + '.test-header{display:flex;gap:8px;align-items:center;margin-bottom:6px;flex-wrap:wrap}'
      + '.test-classification{font-size:0.7em;text-transform:uppercase;letter-spacing:0.05em;padding:1px 8px;border-radius:9999px;background:#e0e7ff;color:#3730a3}'
      + '.test-claim{font-size:0.95em;line-height:1.5;margin:4px 0}'
      + '.test-evidence{font-size:0.86em;color:#475569;padding:6px 10px;background:#f8fafc;border-left:3px solid #94a3b8;border-radius:3px;margin:6px 0}'
      + '.test-id{margin-top:4px;font-family:ui-monospace,monospace}'
      // readout cards
      + '.readout-card{padding:8px 12px;margin:6px 0;background:#fff;border:1px solid #e2e8f0;border-radius:4px}'
      + '.readout-desc{font-size:0.9em;color:#475569;margin-top:4px}'
      // sweep chart
      + '.sweep-chart{margin:8px 0;padding:6px;background:#fafbff;border:1px solid #e0e7ff;border-radius:4px}'
      + '.finding-card{padding:12px 14px;margin:10px 0;border:1px solid #e2e8f0;border-left:5px solid #6366f1;border-radius:6px;background:#fff;box-shadow:0 1px 1px rgba(0,0,0,0.02)}'
      + '.finding-card.finding-status-confirms{border-left-color:#10b981}'
      + '.finding-card.finding-status-partial{border-left-color:#f59e0b}'
      + '.finding-card.finding-status-contradicts{border-left-color:#dc2626}'
      + '.finding-card.finding-status-novel{border-left-color:#8b5cf6}'
      + '.finding-header{display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap}'
      + '.finding-status-glyph{font-size:1.2em;width:24px;text-align:center}'
      + '.finding-status-confirms .finding-status-glyph{color:#10b981}'
      + '.finding-status-partial   .finding-status-glyph{color:#f59e0b}'
      + '.finding-status-contradicts .finding-status-glyph{color:#dc2626}'
      + '.finding-status-novel     .finding-status-glyph{color:#8b5cf6}'
      + '.finding-id{font-family:ui-monospace,monospace;font-size:0.78em;color:#475569;background:#f1f5f9;padding:1px 6px;border-radius:3px}'
      + '.finding-kind{font-size:0.7em;text-transform:uppercase;letter-spacing:0.05em;padding:1px 8px;border-radius:9999px;background:#e0e7ff;color:#3730a3;font-weight:500}'
      + '.finding-status-text{font-size:0.78em;color:#64748b;margin-left:auto;font-style:italic}'
      + '.finding-statement{font-size:1.0em;line-height:1.5;font-weight:500;color:#0f172a;margin:4px 0 8px 0}'
      + '.finding-evidence{font-size:0.86em;color:#475569;padding:6px 10px;background:#f8fafc;border-left:3px solid #94a3b8;border-radius:3px;margin:6px 0;line-height:1.5}'
      + '.finding-expected{font-size:0.86em;color:#475569;padding:6px 10px;background:#f8fafc;border-left:3px solid #94a3b8;border-radius:3px;margin:6px 0;line-height:1.5}'
      + '.finding-exp-summary{font-size:0.9em;color:#475569;padding:6px 10px;background:#fafbff;border-left:3px solid #6366f1;border-radius:3px;margin:6px 0;line-height:1.5}'
      + '.finding-explanation{font-size:0.92em;color:#1e293b;margin:6px 0;line-height:1.5}'
      + '.finding-explanation em{color:#475569;font-style:normal;font-weight:600}'
      + '.finding-expert{margin:6px 0;padding:6px 10px;background:#fafafa;border:1px solid #e2e8f0;border-radius:4px}'
      + '.finding-expert summary{cursor:pointer;font-size:0.85em;color:#475569;font-weight:500}'
      + '.finding-expert-quote{border-left:3px solid #6366f1;margin:6px 0 4px 0;padding:6px 10px;background:#fafbff;font-style:italic;color:#1e1b4b;font-size:0.92em;line-height:1.5}'
      + '.finding-expert-note{font-size:0.88em;color:#475569;margin-top:4px;font-style:italic}'
      + '.finding-next{padding:6px 10px;background:#f0fdf4;border-left:3px solid #10b981;border-radius:3px;font-size:0.9em;margin-top:8px;line-height:1.5}'
      + '.finding-next strong{color:#065f46}'
      // ── eb table row coloring ──
      // ── Conditions block (Variants + Model settings) ──
      + '.study-conditions{margin:18px 0 10px 0;padding:12px 14px;background:#fef3c7;border:1px solid #fcd34d;border-radius:6px}'
      // Planning-phase banner at the top of the report
      + '.planning-phase-banner{display:flex;gap:16px;align-items:flex-start;background:linear-gradient(135deg,#fef9c3 0%,#fde68a 100%);border:1px solid #f59e0b;border-radius:8px;padding:18px 22px;margin:16px 0 24px 0;box-shadow:0 1px 3px rgba(0,0,0,0.05)}'
      + '.planning-phase-banner-icon{font-size:1.8em;line-height:1;flex:0 0 auto;width:32px}'
      + '.planning-phase-banner-content{flex:1 1 auto;min-width:0;color:#78350f;line-height:1.55}'
      + '.planning-phase-banner-body{color:#78350f;line-height:1.55}'
      + '.planning-phase-banner-body strong{color:#451a03}'
      + '.planning-phase-banner-list{margin:8px 0 0 20px;padding:0;color:#78350f}'
      + '.planning-phase-banner-list li{margin:6px 0;line-height:1.5}'
      + '.planning-phase-banner-foot{margin:10px 0 0 0;color:#92400e;font-size:0.9em;font-style:italic;padding-top:8px;border-top:1px solid rgba(217,119,6,0.25)}'
      // Per-study planning pill in the header
      + '.study-planning-pill{display:inline-block;background:#fbbf24;color:#451a03;font-weight:700;font-size:0.78em;letter-spacing:0.06em;padding:3px 10px;border-radius:4px;margin-top:8px}'
      // Baseline strip wrapping charts in planning mode
      + '.planning-baseline-strip{border:1px solid #93c5fd;border-radius:8px;padding:0;margin:18px 0;background:#fff;overflow:hidden}'
      + '.planning-baseline-strip-banner{display:flex;gap:10px;align-items:flex-start;background:#dbeafe;padding:8px 14px;border-bottom:1px solid #93c5fd}'
      + '.planning-baseline-pill{display:inline-block;background:#1e40af;color:#fff;font-weight:700;font-size:0.72em;letter-spacing:0.08em;padding:3px 9px;border-radius:3px;flex-shrink:0;margin-top:2px}'
      + '.planning-baseline-text{color:#1e40af;font-size:0.92em;line-height:1.5}'
      + '.planning-baseline-text strong{color:#1e3a8a}'
      + '.planning-baseline-strip .charts{padding:12px 14px}'
      // Collapsed technical fold at the end of a planning study
      + '.study-technical-fold{margin:18px 0 0 0;padding:8px 12px;background:#f1f5f9;border:1px solid #cbd5e1;border-radius:6px}'
      + '.study-technical-fold>summary{cursor:pointer;color:#475569;font-size:0.9em;font-weight:600}'
      + '.study-technical-fold[open]{background:#fff;border-color:#94a3b8}'
      + '.study-technical-fold[open]>summary{margin-bottom:8px;color:#0f172a}'
      + '.study-conditions h3{margin:0 0 8px 0;font-size:1.05em;color:#0f172a}'
      + '.study-conditions h4{margin:14px 0 6px 0;font-size:0.95em;color:#334155;text-transform:uppercase;letter-spacing:0.04em}'
      + '.cond-baseline{background:#fff;border:1px solid #e2e8f0;border-radius:4px;padding:8px 10px;margin:0 0 12px 0}'
      + '.cond-baseline-composite{font-size:0.92em;color:#334155;margin-bottom:6px}'
      + '.cond-baseline-params{display:flex;flex-wrap:wrap;gap:6px}'
      + '.cond-kv{display:inline-flex;align-items:center;gap:6px;background:#eef2ff;border-radius:3px;padding:2px 6px;font-size:0.85em}'
      + '.cond-kv-k{color:#3730a3;font-weight:600;font-family:ui-monospace,monospace}'
      + '.cond-kv-v code{background:transparent;padding:0;color:#1f2937}'
      + '.cond-table{width:100%;border-collapse:collapse;font-size:0.9em;margin:6px 0}'
      + '.cond-table th{text-align:left;padding:6px 8px;border-bottom:1px solid #cbd5e1;color:#334155;font-weight:600;background:#fff}'
      + '.cond-table td{padding:6px 8px;border-bottom:1px solid #e5e7eb;vertical-align:top}'
      + '.cond-table tr:last-child td{border-bottom:none}'
      + '.cond-table td .cond-kv{display:block;margin:2px 0;background:#f3f4f6}'
      + '.cond-ei-required-badge{display:inline-block;background:#fde68a;color:#78350f;font-size:0.75em;padding:1px 8px;border-radius:9px;margin-left:6px;font-weight:600}'
      + '.cond-ei-gate-req{display:inline-block;background:#fde68a;color:#78350f;font-size:0.78em;padding:1px 6px;border-radius:3px;font-weight:600}'
      + '.cond-ei-gate-opt{display:inline-block;background:#e0e7ff;color:#3730a3;font-size:0.78em;padding:1px 6px;border-radius:3px}'
      + 'tr.eb-stub td{background:#fefce8}'
      + 'tr.eb-gated td{background:#fff7ed}'
      + 'tr.eb-implemented td{background:#f0fdf4}'
      // ── details / collapsibles ──
      + 'details{margin:8px 0;padding:8px 12px;background:#f8fafc;border-radius:4px;border-left:3px solid #cbd5e1}'
      + 'details > summary{cursor:pointer;font-size:0.95em}'
      + 'details[open]{background:#fff;border-left-color:#3b82f6}'
      + 'details details{margin-left:0;background:#fff}'
      // ── per-study sections ──
      // Each .study is a sticky container for its own .study-nav. As the
      // user scrolls past a study, its .study-nav exits its bounding
      // .study div and the next study's nav takes over.
      + '.study{margin-top:40px;padding-top:8px;scroll-margin-top:16px;position:relative}'
      + '.study-nav{position:sticky;top:44px;z-index:20;background:rgba(255,255,255,0.96);backdrop-filter:saturate(120%) blur(2px);'
      +     '-webkit-backdrop-filter:saturate(120%) blur(2px);'
      +     'border-bottom:1px solid #e2e8f0;padding:8px 12px 6px 12px;margin:0 -12px 12px -12px;border-radius:4px}'
      + '.study-nav .study-nav-row1{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:4px}'
      + '.study-nav .study-nav-num{color:#94a3b8;font-family:ui-monospace,monospace;font-size:0.85em}'
      + '.study-nav .study-nav-name{font-size:1.02em}'
      + '.study-nav .study-nav-deps{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}'
      + '.study-nav-row2{display:flex;flex-wrap:wrap;gap:4px}'
      + '.study-nav-row2 a{display:inline-block;padding:2px 10px;border-radius:9999px;font-size:0.83em;color:#3b82f6;text-decoration:none;background:#eff6ff;border:1px solid transparent}'
      + '.study-nav-row2 a:hover{background:#dbeafe;border-color:#bfdbfe}'
      + '.sn-count{display:inline-block;margin-left:4px;font-size:0.8em;color:#64748b;background:#fff;padding:0 5px;border-radius:9999px;border:1px solid #e2e8f0}'
      /* sn-collapse-hint: the click-to-collapse affordance ON the visible
         sticky strip (study-nav). Previous attempt put it inside the
         per-study panel (which is the OFFICIAL <summary> click target for
         the <details>), but study-nav has higher z-index than the panel at
         top:44px, so the panel is covered and the in-panel hint is
         invisible during scroll. Putting the hint here means the click
         handler has to manually toggle the parent <details>.open — see
         the DOMContentLoaded handler near the bottom of this file. Styled
         to match sp-expand-hint (small grey, left-aligned, same font-size)
         so it reads as the same control in two states. */
      + '.sn-collapse-hint{display:none}'
      + '.study-fold[open] .sn-collapse-hint{display:block;font-size:0.73em;color:#94a3b8;margin-top:6px;cursor:pointer}'
      + '.study-fold[open] .sn-collapse-hint:hover{color:#334155}'
      // Scroll-margin so links to sub-sections don't get hidden under the
      // sticky study-nav.
      + '.study [id^="study-"]{scroll-margin-top:96px}'
      + '.study-header h2{border:0;padding:0;margin:0 0 4px 0}'
      + '.study-num{color:#94a3b8;font-weight:normal;font-size:0.85em;margin-right:4px}'
      + '.qh{padding:12px 16px;background:#f8fafc;border-left:4px solid #3b82f6;border-radius:4px;margin:12px 0}'
      + '.qh p{margin:6px 0}'
      + '.description p{white-space:pre-wrap}'
      + 'ul.params{font-size:0.85em;font-family:ui-monospace,monospace;margin:6px 0;padding-left:20px}'
      // ── footer ──
      + 'footer{margin-top:56px;padding-top:16px;border-top:1px solid #e2e8f0;font-size:0.82em;color:#64748b}'
      // ── responsive ──
      + '@media (max-width:900px){'
      +   '.content{padding:20px;max-width:none}'
      +   '.topbar{padding:8px 14px}'
      + '}'
      // ── print ──
      + '@media print{'
      +   '.topbar{display:none}'
      +   '.content{padding:0;max-width:none}'
      +   'details[open]{margin:4px 0}'
      +   'h1,h2,h3{break-after:avoid}'
      +   '.study{break-inside:avoid-page}'
      + '}'

      // ── biology-at-a-glance + investigation biology-story + expert-review
      //    (added so the shareable report mirrors the live dashboard
      //    biologist-first planning view; styled inline so the standalone
      //    HTML renders with no external assets) ─────────────────────────
      + '.investigation-biology-story{padding:16px 20px;background:#f0f9ff;border:1px solid #bae6fd;border-left:5px solid #0284c7;border-radius:8px;margin:14px 0 18px 0;max-width:none}'
      + '.investigation-biology-story p.biology-prose{margin:0;font-size:1em;line-height:1.6;color:#0c4a6e;white-space:pre-line;max-width:none}'
      + 'details.report-fold{margin:10px 0;border-left:3px solid #cbd5e1;padding-left:10px}'
      + 'details.report-fold>summary{cursor:pointer;font-weight:600;color:#1e3a8a;margin-bottom:6px}'
      + '.rf-prev{color:#64748b;font-weight:400;font-size:0.9em}'
      + '.rf-chip{display:inline-block;background:#eef2ff;color:#3730a3;border-radius:10px;padding:1px 8px;font-size:0.72em;font-weight:600;margin:0 3px;vertical-align:middle}'
      + '.rf-pill{display:inline-block;border-radius:10px;padding:1px 8px;font-size:0.72em;font-weight:700;margin:0 3px;vertical-align:middle;background:#e2e8f0;color:#334155;text-transform:uppercase;letter-spacing:0.03em}'
      + '.rf-pill-pass,.rf-pill-passed,.rf-pill-complete{background:#dcfce7;color:#166534}'
      + '.rf-pill-in-progress,.rf-pill-active,.rf-pill-running{background:#fef9c3;color:#854d0e}'
      + '.rf-pill-fail,.rf-pill-failed,.rf-pill-invalid{background:#fee2e2;color:#991b1b}'
      + '.biology-glance{margin:0 0 18px 0;padding:14px 18px;background:#f0fdf4;border:1px solid #bbf7d0;border-left:5px solid #16a34a;border-radius:8px}'
      + '.biology-glance .biology-glance-label{font-size:0.85em;text-transform:uppercase;letter-spacing:0.05em;color:#166534;margin:0 0 8px 0;font-weight:600;border:none;padding:0}'
      + '.biology-summary-callout{margin-bottom:14px}'
      + '.biology-summary-callout .biology-prose{margin:0;font-size:1.02em;line-height:1.55;color:#14532d;white-space:pre-line;max-width:none}'
      + '.biology-glance .study-card{margin-bottom:14px;background:#fff;border-radius:6px;padding:10px 14px;border:1px solid #d1fae5}'
      + '.study-card-table{width:100%;border-collapse:collapse;font-size:0.93em;margin:0}'
      + '.study-card-table th{text-align:left;font-weight:600;color:#166534;background:#f0fdf4;padding:6px 10px;white-space:nowrap;vertical-align:top;width:180px;border-bottom:1px solid #bbf7d0}'
      + '.study-card-table td{padding:6px 10px;vertical-align:top;color:#14532d;border-bottom:1px solid #f0fdf4;line-height:1.5}'
      + '.study-card-table tr:last-child th,.study-card-table tr:last-child td{border-bottom:none}'
      // Investigation Parts grouping: section headings before each study group.
      + '.investigation-part{margin-bottom:30px}'
      + '.part-heading{margin:34px 0 10px 0;padding:14px 18px;background:linear-gradient(90deg,#eef2ff 0%,#fff 100%);border-left:4px solid #6366f1;border-radius:4px}'
      + '.part-title{margin:0;font-size:1.4em;color:#3730a3;font-weight:700}'
      + '.part-overview{margin:6px 0 0 0;color:#475569;font-size:0.95em;line-height:1.5;white-space:pre-line}'
      // Mechanism narrative: 7 framework fields any study can declare.
      + '.mechanism-narrative{margin:18px 0 14px 0;background:#fff;border-radius:6px;padding:10px 14px;border:1px solid #c7d2fe}'
      + '.mechanism-narrative-table{width:100%;border-collapse:collapse;font-size:0.93em;margin:0}'
      + '.mechanism-narrative-table th{text-align:left;font-weight:600;color:#3730a3;background:#eef2ff;padding:6px 10px;white-space:nowrap;vertical-align:top;width:190px;border-bottom:1px solid #c7d2fe}'
      + '.mechanism-narrative-table td{padding:6px 10px;vertical-align:top;color:#1e1b4b;border-bottom:1px solid #eef2ff;line-height:1.55;white-space:pre-line}'
      + '.mechanism-narrative-table tr:last-child th,.mechanism-narrative-table tr:last-child td{border-bottom:none}'
      + '.literature-anchors{background:#fff;border-radius:6px;padding:10px 14px;border:1px solid #d1fae5}'
      + '.literature-anchor-list{list-style:none;margin:0;padding:0;display:grid;gap:8px}'
      + '.literature-anchor-card{padding:8px 12px;background:#f8fefa;border-left:3px solid #16a34a;border-radius:4px;font-size:0.92em}'
      + '.literature-anchor-card .anchor-expectation{font-weight:500;color:#064e3b;margin-bottom:4px;line-height:1.45}'
      + '.literature-anchor-card .anchor-observable,.literature-anchor-card .anchor-source,.literature-anchor-card .anchor-status{font-size:0.88em;color:#475569;margin:2px 0;line-height:1.45}'
      + '.literature-anchor-card .anchor-observable code{font-size:0.92em;background:#fff;padding:1px 5px;border-radius:3px;border:1px solid #d1fae5}'
      + '.literature-anchor-card .anchor-status{font-style:italic}'
      + '.pre-run-expert-review{margin:18px 0;padding:14px 16px;background:#faf5ff;border:1px solid #e9d5ff;border-left:5px solid #a855f7;border-radius:8px}'
      + '.pre-run-expert-review h3{color:#6b21a8;margin:0 0 6px 0}'
      + '.expert-question-card{padding:10px 14px;background:#fff;border:1px solid #e9d5ff;border-left:4px solid #a855f7;border-radius:6px;margin:10px 0}'
      + '.expert-question-card.status-resolved{border-left-color:#10b981}'
      + '.expert-question-header{display:flex;align-items:center;gap:8px;font-size:0.85em;color:#6b21a8;margin-bottom:6px;flex-wrap:wrap}'
      + '.expert-question-id{font-family:ui-monospace,monospace;font-size:0.85em;background:#ede9fe;padding:1px 6px;border-radius:3px}'
      + '.expert-question-status{font-size:0.78em;padding:1px 6px;border-radius:9999px;background:#fef3c7;color:#92400e}'
      + '.expert-question-card.status-resolved .expert-question-status{background:#d1fae5;color:#065f46}'
      + '.expert-question-asked-to{font-size:0.78em;color:#6b7280;margin-left:auto}'
      + '.expert-question-text{font-size:0.95em;line-height:1.55;color:#1e1b4b;margin-bottom:6px}'
      + '.expert-question-alternatives,.expert-question-impact{font-size:0.9em;color:#475569;line-height:1.5;margin:4px 0}'
      + '.expert-question-alternatives ul{margin:4px 0 0 18px;padding:0}'
      + '.expert-question-alternatives li{margin:2px 0}'
      + '.expert-question-impact em,.expert-question-alternatives em{color:#6b21a8;font-style:normal;font-weight:600}'
      + '.expert-question-blocks,.expert-question-response{font-size:0.88em;margin:6px 0 0 0;color:#475569}'
      + '.expert-question-blocks summary,.expert-question-response summary{cursor:pointer;padding:3px 0;color:#6b7280}'
      + '.expert-question-blocks ul{margin:4px 0 0 18px;padding:0}'
      + '.expert-question-blocks li{margin:2px 0;font-size:0.92em}'
      + '.expert-question-response p{margin:4px 0;padding:6px 10px;background:#faf5ff;border-radius:4px}'

      // ── collapsible study fold + control-panel summary ──
      + '.study-fold{border:1px solid #e2e8f0;border-radius:10px;margin:10px 0;background:#fff;scroll-margin-top:16px}'
      + '.study-fold[open]{box-shadow:0 1px 3px rgba(0,0,0,.07)}'
      + '.study-fold>.study-panel{cursor:pointer;list-style:none;padding:12px 16px;border-left:4px solid #cbd5e1;border-radius:9px}'
      + '.study-fold>.study-panel::-webkit-details-marker{display:none}'
      + '.study-fold>.study-panel:hover{background:#f8fafc}'
      // When a study is open: make its header sticky so the collapse arrow
      // stays in view while scrolling inside the study. One click collapses
      // and the next study floats into view — no scrolling back to the top.
      // Stick BELOW the topbar (which sits at top:0, z:100), not at top:0.
      // Otherwise the topbar (higher z-index) visually covers the panel and
      // every interactive element inside it — including the collapse hint —
      // becomes invisible the moment the user scrolls. The 44px offset
      // matches the existing `.study-nav{top:44px}` convention (topbar is
      // ~44px tall after its 9px padding + ~26px line content). Friction
      // report 2026-05-28: "click to collapse goes out of view when we
      // scroll" — was actually the entire sticky panel disappearing
      // behind the topbar, not just the hint.
      + '.study-fold[open]>.study-panel{position:sticky;top:44px;z-index:10;padding:8px 16px;border-bottom:1px solid #e2e8f0;border-radius:9px 9px 0 0;background:#f8fafc;box-shadow:0 1px 4px rgba(0,0,0,.06)}'
      // Sticky-when-open: hide the rich content rows (still visible
      // below in the expanded body — no information lost, just no
      // longer duplicated). The section-nav row stays visible to
      // serve as the in-study jump-target navigation.
      + '.study-fold[open]>.study-panel .sp-objective,'
      + '.study-fold[open]>.study-panel .sp-meta,'
      + '.study-fold[open]>.study-panel .sp-quality,'
      + '.study-fold[open]>.study-panel .sp-conclusion,'
      + '.study-fold[open]>.study-panel .sp-metrics,'
      + '.study-fold[open]>.study-panel .sp-insight,'
      + '.study-fold[open]>.study-panel .sp-caveat{display:none}'
      // Section-nav chips: hidden in the collapsed card (would
      // duplicate the metric chips); shown ONLY when the fold is
      // open so the sticky strip provides in-study navigation.
      + '.sp-section-nav{display:none}'
      + '.study-fold[open]>.study-panel .sp-section-nav{'
      +   'display:flex;flex-wrap:wrap;gap:4px;margin:6px 0 0;width:100%'
      + '}'
      + '.sp-section-nav a{'
      +   'display:inline-block;padding:2px 10px;border-radius:9999px;'
      +   'font-size:0.83em;color:#3b82f6;text-decoration:none;'
      +   'background:#eff6ff;border:1px solid transparent'
      + '}'
      + '.sp-section-nav a:hover{background:#dbeafe;border-color:#bfdbfe}'
      + '.sp-section-nav .sn-count{'
      +   'display:inline-block;margin-left:5px;padding:0 6px;border-radius:9999px;'
      +   'background:rgba(59,130,246,0.13);color:#3b82f6;font-size:0.85em;'
      + '}'
      // Prominent collapse affordance (open state). Replaces the small
      // float:right hint with a button-style chip in the top-right of
      // the sticky strip; visible at a glance + obvious click target.
      + '.study-fold[open]>.study-panel{display:flex;flex-wrap:wrap;align-items:center;gap:6px 12px}'
      + '.study-fold[open]>.study-panel>.sp-top{flex:1 1 auto;min-width:0;margin:0}'
      + '.study-fold.verdict-v-pass>.study-panel{border-left-color:#16a34a}'
      + '.study-fold.verdict-v-warn>.study-panel{border-left-color:#d97706}'
      + '.study-fold.verdict-v-block>.study-panel{border-left-color:#dc2626}'
      + '.study-fold.verdict-v-fail>.study-panel{border-left-color:#dc2626}'
      + '.study-fold.verdict-v-prelim>.study-panel{border-left-color:#6366f1}'
      + '.study-fold.verdict-v-cal>.study-panel{border-left-color:#0891b2}'
      + '.study-fold.verdict-v-none>.study-panel{border-left-color:#94a3b8}'
      + '.study-fold .study{margin-top:0;padding:8px 16px 4px}'
      + '.sp-top{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}'
      + '.sp-num{color:#94a3b8;font-family:ui-monospace,monospace;font-size:0.95em}'
      + '.sp-title{font-size:1.13em;font-weight:700;color:#0f172a;flex:1;min-width:200px}'
      + '.sp-verdict{font-size:0.88em;font-weight:700;padding:3px 11px;border-radius:9999px;white-space:nowrap}'
      + '.sp-verdict.v-pass{background:#dcfce7;color:#166534}'
      + '.sp-verdict.v-warn{background:#fef9c3;color:#854d0e}'
      + '.sp-verdict.v-block{background:#fee2e2;color:#991b1b}'
      + '.sp-verdict.v-prelim{background:#e0e7ff;color:#3730a3}'
      + '.sp-verdict.v-fail{background:#fee2e2;color:#991b1b}'
      + '.sp-verdict.v-cal{background:#cffafe;color:#155e75}'
      + '.sp-verdict.v-none{background:#f1f5f9;color:#475569}'
      + '.sp-objective{margin:6px 0 2px;color:#334155;font-size:0.97em}'
      + '.sp-meta{font-size:0.77em;color:#94a3b8;margin:2px 0 6px}'
      + '.sp-meta code{background:#f1f5f9;padding:0 4px;border-radius:3px;font-size:0.95em;color:#64748b}'
      + '.sp-quality{display:flex;gap:8px;flex-wrap:wrap;margin:4px 0}'
      + '.sp-conf,.sp-ev{font-size:0.78em;font-weight:600;padding:2px 9px;border-radius:6px;background:#f1f5f9;color:#475569}'
      + '.sp-conf-high{background:#dcfce7;color:#166534}'
      + '.sp-conf-medium{background:#fef9c3;color:#854d0e}'
      + '.sp-conf-low{background:#fee2e2;color:#991b1b}'
      + '.sp-conclusion{margin:6px 0;color:#0f172a;font-size:0.95em}'
      + '.sp-insight{margin:4px 0;color:#0f172a;font-size:0.92em}'
      + '.sp-caveat{margin:4px 0;color:#7c2d12;font-size:0.92em}'
      + '.sp-lbl{display:inline-block;font-size:0.7em;font-weight:700;text-transform:uppercase;letter-spacing:0.04em;color:#64748b;margin-right:5px;vertical-align:1px}'
      + '.sp-caveat .sp-lbl{color:#b45309}'
      + '.sp-metrics{display:flex;gap:6px;flex-wrap:wrap;margin:7px 0 3px}'
      + '.sp-metric{font-size:0.77em;background:#eef2ff;color:#3730a3;padding:2px 9px;border-radius:9999px}'
      + '.sp-metric-pass{background:#dcfce7;color:#166534}'
      + '.sp-metric-warn{background:#fef9c3;color:#854d0e}'
      + '.sp-metric-fail{background:#fee2e2;color:#991b1b}'
      + '.sp-expand-hint{display:inline-block;font-size:0.73em;color:#94a3b8;margin-top:6px}'
      + '.study-fold[open] .sp-expand-hint{display:none}'
      /* sp-collapse-hint: the OPEN-state partner of sp-expand-hint.
         Styled identically (same font-size, grey, margin) so the two
         affordances feel like the same control in two states.

         `flex: 0 0 100% + order: 100` guarantees it always lands on
         its own row at the very bottom of the sticky panel, regardless
         of whether sp-section-nav rendered (some studies have no nav
         links — without the flex-basis trick the hint would float onto
         the title row next to the verdict, which is what triggered the
         2026-05-28 "click to collapse is in the wrong menu bar" report).
         Default block text-align is left, matching where the expand-hint
         sits on collapsed cards. */
      + '.sp-collapse-hint{display:none}'
      + '.study-fold[open] .sp-collapse-hint{'
      +   'display:block;flex:0 0 100%;order:100;'
      +   'font-size:0.73em;color:#94a3b8;margin-top:6px'
      + '}'
      + '.studies-toolbar{display:flex;gap:8px;margin:8px 0 14px}'
      + '.studies-toolbar button{font:inherit;font-size:0.85em;padding:5px 12px;border:1px solid #cbd5e1;background:#f8fafc;border-radius:6px;cursor:pointer;color:#334155}'
      + '.studies-toolbar button:hover{background:#e2e8f0}'
      + '@media print{.sp-expand-hint,.sp-collapse-hint,.studies-toolbar{display:none}}'
      // ── review-readiness gate panel ──
      + '.review-gate{margin:10px 0;padding:10px 14px;background:#fffbeb;border:1px solid #f59e0b;border-left-width:5px;border-radius:6px;color:#92400e}'
      + '.review-gate>strong{color:#b45309}'
      + '.review-gate-sub{font-size:0.82em;color:#a16207;margin:2px 0 4px}'
      + '.review-gate ul{margin:6px 0 0 18px;padding:0}'
      + '.review-gate li{margin:3px 0}'
      + '.review-gate code{background:#fef3c7;padding:0 4px;border-radius:3px;font-size:0.92em}'
      // ── compact sim / readout tables ──
      + '.sim-table,.readout-table{width:100%;border-collapse:collapse;font-size:0.9em;margin:4px 0 8px}'
      + '.sim-table th,.readout-table th{text-align:left;padding:5px 8px;border-bottom:2px solid #e2e8f0;color:#475569;font-size:0.86em;font-weight:600}'
      + '.sim-table td,.readout-table td{padding:5px 8px;border-bottom:1px solid #f1f5f9;vertical-align:top}'
      + '.sim-table tr:hover,.readout-table tr:hover{background:#f8fafc}'
      + '.sim-table code,.readout-table code{background:#f1f5f9;padding:0 4px;border-radius:3px;font-size:0.92em}'
      + '.sim-feeds,.sim-table .sim-feeds code{font-size:0.82em}'
      + '.sim-status-pill{display:inline-block;font-size:0.8em;padding:1px 8px;border-radius:9999px;background:#e2e8f0;color:#1e293b}'
      + '.sim-status-ready,.sim-status-pill.sim-status-ready{background:#dcfce7;color:#166534}'
      + '.sim-status-ran,.sim-status-pill.sim-status-ran{background:#dbeafe;color:#1e40af}'
      + '.sim-status-gated,.sim-status-pill.sim-status-gated{background:#fef9c3;color:#854d0e}'
      + '</style></head><body>'

      // ── Embed autosize (self-reporting child pattern) ──────────────
      //
      // Each embed-frame iframe runs its own ResizeObserver +
      // MutationObserver inside its document and posts the measured
      // height to this parent via postMessage. The parent maps
      // event.source → iframe element and sets iframe.style.height.
      //
      // Why self-reporting instead of parent-side measurement: prior
      // _fitEmbed used a selector walk
      // (.plotly-graph-div / [data-plotly] / div[id] / svg / img / canvas)
      // to find tall children and sum their bounding rects. Every new
      // content type the walk didn't recognize (`<table>` from fetch,
      // `<video>`, etc.) under-measured → iframe clipped. The
      // MutationObserver here catches DOM changes that don't immediately
      // trigger a size change on body (e.g. table populated from a
      // fetch); the ResizeObserver catches everything else. body
      // .scrollHeight is ground truth in the iframe's own document, no
      // selector needed.
      //
      // The child function is defined here in the parent context only so
      // its source can be extracted via .toString() and injected into
      // each iframe's <head>. It does NOT execute in the parent.
      + '<script>'
      + 'window.__embedReg=window.__embedReg||new Map();'
      // Parent receiver — install once.
      + 'if(!window.__embedRecv){window.__embedRecv=true;'
      +   'window.addEventListener("message",function(ev){'
      +     'if(!ev.data||ev.data.type!=="embed-autosize:height")return;'
      +     'var f=window.__embedReg.get(ev.source);if(!f)return;'
      +     'var h=Math.max(0,+ev.data.height||0);'
      +     'if(h>0)f.style.height=(h+24)+"px";'
      +   '});'
      + '}'
      // Child function — its .toString() is what runs inside each iframe.
      //
      // Height measurement uses a SENTINEL: an invisible 0×0 div appended
      // as the last child of body. Its top position (relative to body's
      // top) IS the content height — independent of body's laid-out
      // height, html element height, or `height: 100%` style inheritance.
      // Avoids the feedback loop where html.scrollHeight grows with the
      // iframe's own viewport size, which then makes us report a larger
      // height, which grows the iframe again, ad infinitum.
      + 'window.__embedChildFn=function(){'
      +   'if(window.__ec)return;window.__ec=1;'
      +   'var sentinel=null;'
      +   'function ensureSentinel(){'
      +     'if(sentinel&&sentinel.parentNode===document.body)return;'
      +     'sentinel=document.createElement("div");'
      +     'sentinel.setAttribute("data-ec-sentinel","1");'
      +     'sentinel.style.cssText="height:0;width:0;visibility:hidden;clear:both;margin:0;padding:0";'
      +     'document.body.appendChild(sentinel);'
      +   '}'
      +   'function m(){var d=document,b=d.body;if(!b)return;'
      // Honor explicit pinned height (height + overflow:hidden in inner CSS).
      +     'var p=0;if(window.getComputedStyle){var bs=getComputedStyle(b);'
      +       'if(bs&&(bs.overflow||"").indexOf("hidden")>=0){'
      +         'var hm=(bs.height||"").match(/^(\\d+(?:\\.\\d+)?)px$/);'
      +         'if(hm)p=Math.round(parseFloat(hm[1]));}}'
      +     'var h;'
      +     'if(p>0){h=p;}else{'
      +       'ensureSentinel();'
      +       'var bRect=b.getBoundingClientRect();'
      +       'var sRect=sentinel.getBoundingClientRect();'
      +       'h=Math.max(0,Math.ceil(sRect.top-bRect.top));'
      // Fallback if sentinel reads 0 (e.g. body itself has display:none).
      +       'if(h===0)h=b.scrollHeight||0;'
      +     '}'
      +     'try{window.parent.postMessage({type:"embed-autosize:height",height:h},"*");}catch(_){}'
      +   '}'
      +   'function init(){ensureSentinel();m();'
      // Only observe body. Observing documentElement caused the runaway
      // feedback loop (html scrollHeight grows with iframe viewport).
      +     'if(window.ResizeObserver&&document.body){'
      +       'var ro=new ResizeObserver(m);ro.observe(document.body);}'
      +     'if(window.MutationObserver&&document.body){var mo=new MutationObserver(function(muts){'
      // Skip mutations triggered by our own sentinel insertion to avoid
      // a measurement immediately re-firing measurement.
      +       'for(var i=0;i<muts.length;i++){'
      +         'var t=muts[i].target;'
      +         'if(t&&t.getAttribute&&t.getAttribute("data-ec-sentinel"))return;}'
      +       'm();'
      +     '});'
      +       'mo.observe(document.body,{childList:1,subtree:1,attributes:1,'
      +         'attributeFilter:["style","class","src","open","hidden"]});}'
      // Per-image load handlers catch late-decoded images that don't
      // trigger body resize until they finish decoding.
      +     'if(document.body){var ii=document.body.querySelectorAll("img");'
      +       'for(var i=0;i<ii.length;i++){'
      +         'if(!ii[i].complete)ii[i].addEventListener("load",m);}}'
      +     'window.addEventListener("load",m);'
      // Belt-and-suspenders timed safety net for content loaded by
      // async scripts that don't mutate body's observable surface.
      +     '[100,500,2000].forEach(function(t){setTimeout(m,t);});'
      +   '}'
      +   'if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",init);'
      +   'else init();'
      + '};'
      // Stash the child source for injection.
      + 'window.__embedChildJs="("+window.__embedChildFn.toString()+")();";'
      // _wireEmbed: register the iframe in the parent map, inject the
      // child autosize script into its head. Idempotent — re-wiring
      // an already-wired iframe is a no-op.
      + 'window._wireEmbed=function(f){try{'
      +   'var cw=f.contentWindow;if(cw)window.__embedReg.set(cw,f);'
      +   'var inj=function(){try{var d=f.contentDocument;if(!d||!d.head)return;'
      +     'if(d.head.querySelector("script[data-ec]"))return;'
      +     'var s=d.createElement("script");s.setAttribute("data-ec","1");'
      +     's.textContent=window.__embedChildJs;'
      +     'd.head.appendChild(s);}catch(_e){}};'
      +   'if(f.contentDocument&&f.contentDocument.readyState!=="loading")inj();'
      +   'else f.addEventListener("load",inj,{once:true});'
      + '}catch(e){}};'
      // _fitEmbed: back-compat shim. A few call sites elsewhere still
      // invoke _fitEmbed directly; forward to _wireEmbed so they pick
      // up the new child-injection path without code changes.
      + 'window._fitEmbed=function(f){if(window._wireEmbed)window._wireEmbed(f);};'
      // Collapsed-embed toggle: when a <details> opens an embed, kick
      // the iframe to re-measure (in case it was paused while hidden).
      + 'window._onEmbedToggle=function(d){if(!d.open)return;var f=d.querySelector(".embed-frame");if(!f)return;'
      +   'try{f.contentWindow&&f.contentWindow.dispatchEvent(new Event("resize"));}catch(e){}'
      +   'if(window._wireEmbed)window._wireEmbed(f);};'
      + '</script>'

      // ── Sticky top nav — section-level tags only (per-study nav now lives
      //    in the collapsed control panels). Conditional tags render only when
      //    the section exists, keeping the bar uncluttered. Trailing switcher
      //    dropdown lists peer investigations from /api/investigation-registry
      //    so the user can jump between live dashboards without leaving the
      //    page — see _wireIsetSwitcher below for the click + render logic.
      + '<nav class="topbar">'
      +   '<span class="tb-title">' + _h(iset.title || iset.name) + '</span>'
      +   '<a href="#" onclick="window.scrollTo({top:0,behavior:\'smooth\'});return false;">Top</a>'
      /* "Acceptance" nav link removed alongside the section it pointed to */
      /* "Suggested additions" nav link removed per request; the section itself
         (id="proposed-inputs") stays in the body. */
      +   '<a href="#studies-heading">Studies</a>'
      +   '<a href="#references">References</a>'
      + '</nav>'

      // ── Main content ──
      + '<main class="content" id="top">'

      +   '<h1>' + _h(iset.title || iset.name) + ' <span class="badge badge-' + _h(iset.status || 'planning') + '">' + _h(iset.status || 'planning') + '</span>'
      +     _objectOfEvaluationChip(iset.object_of_evaluation) + '</h1>'
      +   '<p class="muted small">Investigation report · <code>' + nameClean + '</code> · generated ' + _h(now) + ' · '
      +     ((specs || []).some(function(s) { return (s.runs || []).length || (s.findings || []).length; })
          ? 'for expert review — results below reflect completed runs.'
          : 'for expert review prior to execution.') + '</p>'

      // Coordinated-generation provenance banner (expert-feedback A.3).
      +   generationBannerHtml

      // Spine C2: the one-line acceptance headline stays inline; the detailed
      // tables (gating matrix, study verdict map, needs-attention) fold into a
      // collapsed section so the top of the report isn't a wall of tables.
      +   acceptanceNarrativeHtml
      +   (function() {
            var inner = acGatingMatrixHtml + verdictDagHtml + needsAttentionReportHtml;
            if (!inner || !inner.trim()) return '';
            return '<details class="report-fold" id="acceptance-detail">'
              + '<summary>How the verdict is computed — acceptance criteria, gating matrix &amp; study verdicts</summary>'
              + inner + '</details>';
          })()
      // Competing hypotheses (#6/#16) — the rival explanations + their computed
      // support trajectory, just above the rigor roll-up that grades the method.
      +   competingHypothesesHtml

      // Evidence & rigor roll-up — deterministic skeptic-feedback (controls,
      // replication, alternatives, falsifiability, adversarial coverage).
      +   rigorSectionHtml

      // Framework scorecard — framework-self metrics across the workspace (#26).
      +   frameworkScorecardHtml

      // ── Execution-status banner (LEADS the report, before the folds) ────
      // Accurate to the actual run state: a pre-execution review notice when
      // nothing has run yet, otherwise a concise post-execution lead that names
      // the still-planned studies. Placed at the very top so it never wedges
      // between the collapsible sections.
      +   (function() {
            var alls = specs || [];
            var planning = alls.filter(function(s) { return !(s.runs || []).length && !(s.findings || []).length; });
            var total = alls.length, n = planning.length;
            if (!n) return '';
            var names = planning.map(function(s) { return s.name || s.slug || ''; }).filter(Boolean).join(', ');
            var pre = (n === total);   // genuinely pre-execution — nothing has run
            var body = pre
              ? '<strong>Planning phase — pre-execution review.</strong> None of the ' + total
                + ' studies have run yet; the charts are the <strong>workspace pre-execution baseline</strong>.'
                + ' For each study the key review surfaces are:'
              : '<strong>' + (total - n) + ' of ' + total + ' studies have completed runs</strong> — their'
                + ' verdicts + evaluator-computed test outcomes are below. ' + n + ' still in planning'
                + (names ? ' (<code>' + _h(names) + '</code>)' : '')
                + ': their charts are pre-execution baselines and their tests are pending those runs.'
                + ' For the planned studies the key review surfaces are:';
            return '<div class="planning-phase-banner" id="planning-phase-banner">'
              + '<div class="planning-phase-banner-icon">📝</div>'
              + '<div class="planning-phase-banner-content">'
              +   '<div class="planning-phase-banner-body">' + body + '</div>'
              +   '<ul class="planning-phase-banner-list">'
              +     '<li><strong>Conditions</strong> — variants and their parameter overrides, plus the model settings awaiting your call.</li>'
              +     '<li><strong>Expected behavior</strong> — what each test claims will pass / fail and the criterion it uses (flag any under- or over-specified).</li>'
              +     '<li><strong>Baseline visualizations</strong> — what the system looks like before the study\'s mechanism lands.</li>'
              +   '</ul>'
              +   '<div class="planning-phase-banner-foot">Click the <strong>💬</strong> icon next to any section to leave inline feedback. "Generate feedback report" (bottom-right) packages everything into a single yaml file.</div>'
              + '</div>'
              + '</div>';
          })()

      // ── LAYER 1: EXECUTIVE ─────────────────────────────────────────────
      // Authored narrative + conclusions for a human reviewer, at the very
      // top. Reads iset.executive; renders nothing if the field is absent
      // (older investigations fall back to Overview below).
      +   (function() {
            var ex = iset.executive || {};
            var dn = ex.decisions_needed || [];
            if (!ex.what_is_this && !ex.verdict && !iset.question && !iset.hypothesis) return '';
            var vs = ex.verdict_status || 'in-progress';
            var h = '<details id="executive" class="report-fold" style="margin-top:12px"><summary>📋 Executive summary' + ' <span class="rf-pill rf-pill-' + _h(String(vs).toLowerCase().replace(/[^a-z0-9]+/g,'-')) + '">' + _h(vs) + '</span>' + ((ex.verdict || ex.what_is_this) ? ' <span class="rf-prev">' + _h(_previewText(ex.verdict || ex.what_is_this, 150)) + '</span>' : '') + '</summary>';
            if (ex.what_is_this)
              h += '<p>' + _multiline(ex.what_is_this) + '</p>';
            if (ex.verdict)
              h += '<div class="callout" style="background:#f8fafc;border-left:5px solid #64748b;border-radius:8px;padding:12px 16px;margin:10px 0">'
                 + '<span class="badge badge-' + _h(vs) + '">' + _h(vs) + '</span> '
                 + '<strong>Current verdict.</strong> ' + _multiline(ex.verdict) + '</div>';
            if (iset.question)
              h += '<p><strong>Question.</strong> ' + _multiline(iset.question) + '</p>';
            if (iset.hypothesis)
              h += '<p><strong>Hypothesis.</strong> ' + _multiline(iset.hypothesis) + '</p>';
            // ── Spine A1: computed acceptance roll-up ──────────────────────
            // Restores the acceptance visibility removed earlier, now COMPUTED
            // by the spine (investigation_status.roll_up_acceptance) and
            // connected to the member studies' verdicts. Mirrors the
            // param-enforcement banner: surfaced, connected (each criterion
            // links to its study section), and labeled code-computed vs
            // authored, with a divergence badge when the two disagree.
            var ca = iset.computed_acceptance;
            if (ca && ca.criteria && ca.criteria.length) {
              var authoredVs = (ex.verdict_status || '').toString().toLowerCase().trim();
              var computedVs = (ca.verdict_status || '').toString().toLowerCase().trim();
              // NOTE: the per-criterion `result`s below are LIVE-ROLLED at render
              // time (from each member study's current verdict), whereas
              // `ca.diverges_from_authored` is the spine-PERSISTED divergence flag
              // — the two can momentarily differ; this is acceptable per the plan.
              // Prefer the spine-persisted divergence flag; fall back to the
              // computed-vs-authored verdict_status comparison the plan allows.
              var caDiverges = (ca.diverges_from_authored === true)
                || (!!authoredVs && !!computedVs && authoredVs !== computedVs);
              var critRows = ca.criteria.map(function(c) {
                var r = (c.result || '').toString().toLowerCase();
                var rcls = (r === 'passing' || r === 'pass') ? '#16a34a'
                         : (r === 'failing' || r === 'fail') ? '#dc2626'
                         : '#92400e';
                var m = _critMetric(c.study, c.behavior);
                var metricCell = m
                  ? (m.field ? '<code>' + _h(m.field) + '</code>' : '')
                    + (m.passIf ? ' <span class="muted small">pass if ' + _h(m.passIf) + '</span>' : '')
                    + (m.observed !== null && m.observed !== undefined
                        ? ' → <strong>' + _h(typeof m.observed === 'number' ? (Math.round(m.observed * 1000) / 1000) : m.observed) + '</strong>' : '')
                  : '<span class="muted small">—</span>';
                return '<tr>'
                  + '<td style="padding:3px 8px"><a href="#study-' + _h(c.study) + '">' + _h(c.study) + '</a></td>'
                  + '<td style="padding:3px 8px">' + _h(c.behavior || '') + '</td>'
                  + '<td style="padding:3px 8px;font-size:0.9em">' + metricCell + '</td>'
                  + '<td style="padding:3px 8px;font-weight:600;color:' + rcls + '">' + _h(c.result || '—') + '</td>'
                  + '</tr>';
              }).join('');
              var caBadge = caDiverges
                ? '<span class="acceptance-divergence" title="The code-computed acceptance disagrees with the authored verdict_status — computed by the spine (investigation_status), not human-authored." style="display:inline-block;margin-left:8px;padding:2px 9px;border-radius:9999px;font-size:0.8em;font-weight:600;background:#fffbeb;border:1px solid #f59e0b;color:#92400e">⚠ code: ' + _h(ca.verdict_status || computedVs || '?') + ' · authored: ' + _h(ex.verdict_status || '—') + '</span>'
                : '';
              h += '<div class="acceptance-rollup" id="' + _h(iset.name || 'inv') + '-acceptance-rollup" '
                + 'style="margin:12px 0;padding:12px 16px;background:#f8fafc;border:1px solid #cbd5e1;border-left-width:5px;border-radius:6px">'
                + '<strong>Acceptance roll-up</strong> '
                + '<span class="muted small" style="color:#64748b">code-computed from member-study verdicts</span>'
                + caBadge
                + _acceptanceExplainer
                + '<table class="small" style="margin-top:8px;border-collapse:collapse">'
                + '<thead><tr>'
                + '<th style="padding:3px 8px;text-align:left">Study</th>'
                + '<th style="padding:3px 8px;text-align:left">Behavior</th>'
                + '<th style="padding:3px 8px;text-align:left">Metric (field · pass-if → observed)</th>'
                + '<th style="padding:3px 8px;text-align:left">Result</th>'
                + '</tr></thead><tbody>' + critRows + '</tbody></table></div>';
            }
            return h + '</details>';
          })()

      // ── Decisions needed (top-level fold, pulled out of Executive) ──
      +   (function() {
            var dn = (iset.executive || {}).decisions_needed || [];
            if (!dn.length) return '';
            return '<details id="decisions-needed" class="report-fold"><summary>✋ Decisions needed from reviewers' + ' <span class="rf-chip">' + dn.length + ' item' + (dn.length===1?'':'s') + '</span>' + (dn[0] && dn[0].question ? ' <span class="rf-prev">next: ' + _h(_previewText(dn[0].question, 130)) + '</span>' : '') + '</summary><ol>'
              + dn.map(function(d) {
                  return '<li><strong>' + _h(d.question || '') + '</strong>'
                    + (d.context ? '<div class="muted small">' + _multiline(d.context) + '</div>' : '')
                    + '</li>';
                }).join('') + '</ol></details>';
          })()

      // ── PROPOSED INPUTS (pending expert approval) ──────────────────────
      // Agent-suggested references / mechanisms the expert did NOT provide.
      // They are NOT silently integrated: each is surfaced here for the
      // expert to Accept (→ promoted to a real provided input) or Decline.
      // Reads iset.proposed_inputs.items; renders nothing if absent/empty.
      // Mirrors the follow-up-proposal Accept/Decline button pattern via
      // _decideProposedInput → POST /api/proposed-input-decision.
      +   (function() {
            var pi = iset.proposed_inputs || {};
            var items = pi.items || [];
            if (!items.length) return '';
            var pending = items.filter(function(it){ return (it.status||'pending')==='pending'; }).length;
            function _kindBadge(kind) {
              var k = (kind||'reference');
              var bg = k === 'mechanism' ? '#faf5ff' : '#eff6ff';
              var fg = k === 'mechanism' ? '#6b21a8' : '#1e40af';
              return '<span style="font-size:0.7em;text-transform:uppercase;letter-spacing:0.05em;'
                + 'padding:1px 8px;border-radius:9999px;background:' + bg + ';color:' + fg + '">' + _h(k) + '</span>';
            }
            function _statusPill(status) {
              var s = (status||'pending');
              var c = s === 'accepted' ? {bg:'#dcfce7',fg:'#166534'}
                    : s === 'declined' ? {bg:'#fee2e2',fg:'#991b1b'}
                    : {bg:'#fef3c7',fg:'#92400e'};
              return '<span style="font-size:0.7em;padding:1px 8px;border-radius:9999px;background:'
                + c.bg + ';color:' + c.fg + ';margin-left:6px">' + _h(s) + '</span>';
            }
            var cards = items.map(function(it) {
              var status = it.status || 'pending';
              var headline = (it.kind === 'mechanism') ? (it.summary || '(mechanism)') : (it.citation || '(reference)');
              var rows = [];
              if (it.related_study) rows.push('<div class="muted small"><strong>Related study:</strong> <code>' + _h(it.related_study) + '</code></div>');
              if (it.rationale) rows.push('<div class="small" style="margin-top:4px"><strong>Rationale.</strong> ' + _multiline(it.rationale) + '</div>');
              if (it.provenance) rows.push('<div class="muted small" style="margin-top:4px"><strong>Provenance.</strong> ' + _multiline(it.provenance) + '</div>');
              if (it.proposed_by || it.proposed_at) {
                rows.push('<div class="muted small" style="margin-top:4px">proposed by ' + _h(it.proposed_by || 'agent')
                  + (it.proposed_at ? ' · ' + _h(String(it.proposed_at)) : '') + '</div>');
              }
              var actions;
              if (status === 'pending') {
                // No custom Accept/Decline buttons: reviewers annotate this
                // section with the standard inline-feedback 💬 affordance
                // (the section id="proposed-inputs" is an annotatable host),
                // which round-trips reliably in the downloaded file:// report.
                actions = '';
              } else {
                actions = '<div class="proposed-input-resolved muted small" style="margin-top:10px;font-style:italic">'
                  + (status === 'accepted'
                      ? '✓ Accepted by the expert' + (it.kind === 'reference' ? ' — added to the investigation\'s provided references.' : ' — a human integrates the mechanism.')
                      : '✗ Declined by the expert — not integrated.')
                  + '</div>';
              }
              var borderColor = status === 'accepted' ? '#16a34a' : status === 'declined' ? '#dc2626' : '#f59e0b';
              return '<div class="proposed-input-card" data-item-id="' + _h(String(it.id||'')) + '" '
                + 'style="padding:12px 14px;border:1px solid #e2e8f0;border-left:4px solid ' + borderColor
                + ';border-radius:6px;background:#fff;margin-bottom:10px">'
                + '<div style="display:flex;align-items:flex-start;gap:8px">'
                +   '<div style="flex:1;min-width:0">'
                +     _kindBadge(it.kind) + _statusPill(status)
                +     '<div style="font-weight:600;margin-top:6px">' + _h(headline) + '</div>'
                +     rows.join('')
                +   '</div>'
                + '</div>'
                + actions
                + '</div>';
            }).join('');
            var note = pi._note
              ? '<p class="muted small" style="margin:0 0 10px 0">' + _multiline(pi._note) + '</p>'
              : '<p class="muted small" style="margin:0 0 10px 0">These references / mechanisms were proposed by the agent and were '
                + '<strong>not</strong> provided by the expert. Nothing here is integrated until you <strong>Accept</strong> it.</p>';
            return '<details id="proposed-inputs" class="report-fold"><summary>🧩 Suggested additions — pending your approval'
              + ' <span class="rf-chip">' + items.length + ' item' + (items.length===1?'':'s')
              + (pending ? ' · ' + pending + ' pending' : '') + '</span></summary>'
              + note + cards + '</details>';
          })()

      // ── LAYER 2: SCIENTIFIC ARGUMENT ───────────────────────────────────
      // The claim and the evidence, for the reviewer. Reads
      // iset.scientific_argument; renders nothing if absent.
      +   (function() {
            var sa = iset.scientific_argument || {};
            var ef = sa.evidence_for || [], ea = sa.evidence_against || [],
                kf = sa.key_figures || [], cav = sa.caveats || [];
            if (!sa.main_claim && !ef.length && !ea.length) return '';
            function _li(x) { return '<li>' + _multiline(typeof x === 'string' ? x : (x.text || JSON.stringify(x))) + '</li>'; }
            var h = '<details id="scientific-argument" class="report-fold"><summary>🔬 Scientific argument' + ((ef.length || ea.length) ? ' <span class="rf-chip">' + ef.length + ' for \u00b7 ' + ea.length + ' against</span>' : '') + (sa.main_claim ? ' <span class="rf-prev">' + _h(_previewText(sa.main_claim, 150)) + '</span>' : '') + '</summary>';
            if (sa.main_claim)
              h += '<p><strong>Main claim.</strong> ' + _multiline(sa.main_claim) + '</p>';
            if (ef.length || ea.length) {
              h += '<div style="display:flex;gap:24px;flex-wrap:wrap">';
              if (ef.length) h += '<div style="flex:1 1 280px"><h3 style="color:#065f46">Evidence for</h3><ul>' + ef.map(_li).join('') + '</ul></div>';
              if (ea.length) h += '<div style="flex:1 1 280px"><h3 style="color:#9a3412">Evidence against</h3><ul>' + ea.map(_li).join('') + '</ul></div>';
              h += '</div>';
            }
            if (kf.length)
              h += '<h3>Key figures</h3><ul>' + kf.map(function(k) {
                return '<li><code>' + _h(k.study || '') + '</code> · <code>' + _h(k.viz || '') + '</code> — ' + _h(k.caption || '') + '</li>';
              }).join('') + '</ul>';
            if (cav.length)
              h += '<h3>Caveats</h3><ul>' + cav.map(_li).join('') + '</ul>';
            return h + '</details>';
          })()


      +   ((iset.biological_story || '').trim()
          ? '<details id="biology" class="report-fold">'
            + '<summary>🧬 Biology — the mechanism this investigation models' + ' <span class="rf-prev">' + _h(_previewText(iset.biological_story || '', 175)) + '</span>' + '</summary>'
            + '<p style="margin:0">' + _multiline(iset.biological_story) + '</p>'
            + '</details>'
          : '')

      +   ''

      /* Removed: top-of-report "Acceptance criteria" section.
         Per-study behavior_tests + conclusion_verdicts (the v4 way
         studies signal pass/fail) already convey "what must pass for
         this investigation to be considered complete." The top-of-
         report ordered list of acceptance criteria duplicated that
         signal in a less-actionable form. */

      +   '<h2 id="studies-heading">Studies' + (hasDag ? ' (dependency order)' : '') + '</h2>'
      +   '<p class="muted small">Each study is collapsed to a one-glance control panel — scan top to bottom, then click any panel to expand its full detail.</p>'
      +   '<div class="studies-toolbar">'
      +     '<button type="button" id="studies-expand-all">Expand all</button>'
      +     '<button type="button" id="studies-collapse-all">Collapse all</button>'
      +     '<script>(function(){'
      +       'function findFolds(){return Array.from(document.querySelectorAll(".study-fold"));}'
      +       'function setAll(open){'
      +         'var folds=findFolds();'
      +         'console.log("[studies-toolbar] "+(open?"expand":"collapse")+" "+folds.length+" .study-fold elements");'
      +         'folds.forEach(function(d){d.open=open;});'
      +         'if(open&&folds.length){folds[0].scrollIntoView({behavior:"smooth",block:"start"});}'
      +       '}'
      +       'function wire(){'
      +         'var ex=document.getElementById("studies-expand-all");'
      +         'var co=document.getElementById("studies-collapse-all");'
      +         'if(ex)ex.addEventListener("click",function(e){e.preventDefault();e.stopPropagation();setAll(true);});'
      +         'if(co)co.addEventListener("click",function(e){e.preventDefault();e.stopPropagation();setAll(false);});'
      +       '}'
      +       'document.addEventListener("click",function(e){'
      +         'var t=e.target;var h=t&&t.closest?t.closest(".sn-collapse-hint,.sp-collapse-hint"):null;'
      +         'if(h){var d=h.closest("details.study-fold");if(d){d.open=false;d.scrollIntoView({behavior:"smooth",block:"start"});}e.preventDefault();e.stopPropagation();return;}'
      +         'var na=t&&t.closest?t.closest(".study-nav a"):null;'
      +         'if(na&&na.getAttribute("href")&&na.getAttribute("href").charAt(0)==="#"){var tg=document.getElementById(na.getAttribute("href").slice(1));if(tg){var fd=tg.closest("details.study-fold");if(fd&&!fd.open)fd.open=true;e.preventDefault();setTimeout(function(){tg.scrollIntoView({behavior:"smooth",block:"start"});},0);}}'
      +       '});'
      +       'if(document.readyState==="loading"){document.addEventListener("DOMContentLoaded",wire);}else{wire();}'
      +     '})();</script>'
      +   '</div>'
      +   studiesHtml

      +   '<h2 id="references">References <span class="muted small">(' + orderedCited.length + ' cited across this investigation)</span></h2>'
      +   '<p class="muted small">Union of <code>bibliography.bib_keys</code> and per-behavior <code>cites:</code> across all studies in this investigation. Click DOI or link to open the source.</p>'
      +   '<ol class="references-list" style="line-height:1.6;font-size:0.93em">'
      +     referencesHtml
      +   '</ol>'

      +   '<footer id="footer">'
      +     '<p>Generated by vivarium-dashboard. Source of truth: <code>investigations/' + nameClean + '/investigation.yaml</code> and the per-study <code>studies/&lt;name&gt;/study.yaml</code> files.</p>'
      +     '<p>Open the live DAG: in the dashboard, click <strong>Investigations</strong> → <em>' + _h(iset.title || iset.name) + '</em>.</p>'
      +   '</footer>'

      + '</main>'

      // ── Active-section tracking for top-nav links ──
      + '<script>'
      + '(function(){'
      +   'var links=Array.from(document.querySelectorAll(".topbar a"));'
      +   'var targets=links.map(function(a){return document.getElementById(a.getAttribute("href").slice(1));})'
      +     '.filter(Boolean);'
      +   'function onScroll(){'
      +     'var y=window.scrollY+80;'
      +     'var current=null;'
      +     'for(var i=0;i<targets.length;i++){if(targets[i].offsetTop<=y)current=targets[i];}'
      +     'links.forEach(function(a){a.classList.toggle("active",current&&("#"+current.id)===a.getAttribute("href"));});'
      +   '}'
      +   'window.addEventListener("scroll",onScroll,{passive:true});'
      +   'onScroll();'
      // Studies are collapsed by default. When the URL targets a study (or any
      // anchor inside one), open all ancestor <details> so the target is
      // actually visible, then scroll to it.
      +   'function openToHash(){'
      +     'var h=location.hash;if(!h)return;'
      +     'var el=document.getElementById(decodeURIComponent(h.slice(1)));if(!el)return;'
      +     'if(el.tagName==="DETAILS")el.open=true;'
      +     'var d=el.closest?el.closest("details"):null;'
      +     'while(d){d.open=true;d=d.parentElement?d.parentElement.closest("details"):null;}'
      +     'try{el.scrollIntoView();}catch(e){}'
      +   '}'
      +   'window.addEventListener("hashchange",openToHash);'
      +   'openToHash();'
      // Printing / save-as-PDF must show everything — a closed <details> can't
      // be forced open by CSS, so open them all before print.
      +   'window.addEventListener("beforeprint",function(){'
      +     'document.querySelectorAll(".study-fold").forEach(function(d){d.open=true;});'
      +   '});'
      // When a fold opens, re-fit its embeds and nudge Plotly to recompute
      // width (charts drawn while the fold was collapsed render at 0 width).
      +   'document.querySelectorAll(".study-fold").forEach(function(d){'
      +     'd.addEventListener("toggle",function(){if(!d.open)return;'
      +       'd.querySelectorAll(".embed-frame").forEach(function(f){'
      +         'try{f.contentWindow&&f.contentWindow.dispatchEvent(new Event("resize"));}catch(e){}'
      +         'if(window._fitEmbed){window._fitEmbed(f);[120,400,1000].forEach(function(t){setTimeout(function(){window._fitEmbed(f);},t);});}'
      +       '});'
      +     '});'
      +   '});'
      + '})();'
      + '</script>'

      // ── Spine A3: readiness-panel populate (report-render completion) ──
      // The `.study-readiness-panel` placeholders above are emitted per study
      // but filled by JS. This report is self-contained (no walkthrough.js),
      // so bake an EXACT copy of _populateReadinessPanels (+ _readinessPanelHtml
      // + _h) via `.toString()` and invoke it once the study sections have
      // rendered. Reuses the same deterministic linter fetch — no duplicated
      // logic, no AI. Idempotent. fetch('/api/report-lint') resolves when the
      // report is SERVED by the dashboard; offline (file://) it no-ops cleanly.
      + '<script>'
      +   '(function(){'
      +     'var _h=' + _h.toString() + ';'
      +     'var _readinessPanelHtml=' + _readinessPanelHtml.toString() + ';'
      +     'var _populateReadinessPanels=' + _populateReadinessPanels.toString() + ';'
      +     'window._populateReadinessPanels=_populateReadinessPanels;'
      +     'if(document.readyState==="loading"){document.addEventListener("DOMContentLoaded",_populateReadinessPanels);}else{_populateReadinessPanels();}'
      +   '})();'
      + '</script>'

      // ── Proposed-input Accept/Decline wiring (in-report) ──────────────
      // Self-contained handler baked into the downloaded report so the
      // Accept/Decline buttons work both in the live dashboard report and
      // when the report is served by the dashboard. POSTs the decision to
      // /api/proposed-input-decision and, on success, rewrites the card's
      // status in place (no full reload needed). The investigation name is
      // baked in so the standalone report knows which investigation to PATCH.
      + '<script>'
      + '(function(){'
      +   'var INV=' + JSON.stringify(iset.name || '') + ';'
      // Accept / Decline / "+ Add to investigation" all record the reviewer's
      // decision as a NORMAL ANNOTATION — the same channel the manual 💬
      // highlight-comments use (window._fbAddAnnotation, defined by the inline-
      // feedback widget below). No server POST: the agent applies the decision
      // when it reads the exported feedback YAML, like all other feedback. This
      // works identically whether the report is served (http/https) or opened
      // offline (file://).
      +   'function _annotate(sid,text){'
      +     'if(window._fbAddAnnotation){window._fbAddAnnotation(sid,text);return true;}'
      +     'return false;'
      +   '}'
      +   'window._decideProposedInput=function(itemId,decision,btn){'
      +     'if(!itemId){alert("Missing item id");return;}'
      +     'var card=btn&&btn.closest?btn.closest(".proposed-input-card"):null;'
      +     'var actions=card?card.querySelector(".proposed-input-actions"):null;'
      +     'var accepted=decision==="accept";'
      +     'var titleEl=card?card.querySelector("div[style*=\\"font-weight:600\\"]"):null;'
      +     'var title=titleEl?titleEl.textContent.trim():"";'
      +     'var text=(accepted?"Accept":"Decline")+" \\u2014 "+(title||"proposed input")+" [id: "+itemId+"]";'
      +     'if(!_annotate("proposed-inputs",text)){alert("Could not record the decision (feedback widget unavailable).");return;}'
      +     'if(actions){actions.querySelectorAll("button").forEach(function(b){b.disabled=true;});}'
      +     'if(card){card.style.borderLeftColor=accepted?"#16a34a":"#dc2626";}'
      +     'var resolved=document.createElement("div");'
      +     'resolved.className="proposed-input-resolved muted small";'
      +     'resolved.style.cssText="margin-top:10px;font-style:italic";'
      +     'resolved.textContent=(accepted?"\\u2713":"\\u2717")+" recorded \\u2014 exports with your feedback";'
      +     'if(actions){actions.replaceWith(resolved);}else if(card){card.appendChild(resolved);}'
      +   '};'
      // ── "+ Add to investigation" seed handler (report-scoped) ──────────
      // Records a "Add study" annotation keyed to the proposed-inputs section.
      // The SPA defines its own richer _seedFollowupProposal (live graph
      // refresh); this report-scoped version just annotates so the offline /
      // served reviewer click is captured in the feedback export.
      +   'window._seedFollowupProposal=function(parentName,proposalId,proposalIdx,btn){'
      +     'var card=btn&&btn.closest?btn.closest(".di-fup-card"):null;'
      +     'var title=card?(function(){var t=card.querySelector(".di-fup-title");return t?t.textContent.trim():"";})():"";'
      +     'var targets=card?Array.prototype.map.call(card.querySelectorAll(".di-fup-targets code"),function(c){return c.textContent;}):[];'
      +     'var text="Add study \\u2014 "+(title||"new study")+(targets.length?" (targets: "+targets.join(", ")+")":"")+(parentName?" [parent: "+parentName+"]":"");'
      +     'if(!_annotate("proposed-inputs",text)){alert("Could not record the request (feedback widget unavailable).");return;}'
      +     'if(btn){btn.disabled=true;btn.textContent="\\u2713 recorded \\u2014 exports with your feedback";btn.style.borderColor="#16a34a";btn.style.color="#166534";}'
      +   '};'
      + '})();'
      + '</script>'

      // ── Inline feedback widget (fully detached: localStorage only) ──
      //
      // Per-report annotation key: each download gets a unique reportId
      // (millisecond-precision generation timestamp). Annotations are
      // keyed by INV + REPORT_ID, so opening an older report doesn't
      // see comments left on a newer one and vice versa. The yaml export
      // tags meta.report_id so pbg-feedback-import can attribute it
      // back to a specific report file.
      + _feedbackWidgetCss()
      + _feedbackWidgetJs(iset.name || 'investigation',
                          'rpt-' + new Date().toISOString()
                                     .slice(0, 19).replace(/[-:T]/g, ''),
                          ghRepo)

      + '</body></html>';
  }

  // Inline CSS for the inline-feedback widget. Self-contained so the
  // downloaded report works with no external dependencies.
  function _feedbackWidgetCss() {
    return '<style>'
      + '.fb-host{position:relative}'
      + '.fb-add{position:absolute;top:4px;right:4px;background:#f1f5f9;border:1px solid #cbd5e1;border-radius:999px;width:26px;height:26px;font-size:13px;line-height:1;cursor:pointer;opacity:.55;transition:opacity .15s;z-index:5;padding:0;display:inline-flex;align-items:center;justify-content:center}'
      + '.fb-host:hover .fb-add{opacity:1}'
      + '.fb-add:hover{background:#fde68a;border-color:#f59e0b}'
      + '.fb-add.has-fb{opacity:1;background:#fde68a;border-color:#f59e0b}'
      // Editor is a body-level FIXED overlay so it can never be clipped
      // by parent overflow:hidden / flex / transform. Positioned at click
      // time via getBoundingClientRect against the trigger button.
      + '.fb-editor{position:fixed;width:360px;max-width:calc(100vw - 24px);padding:12px;background:#fffbeb;border:1px solid #f59e0b;border-radius:6px;box-shadow:0 8px 24px rgba(0,0,0,.18);z-index:1000}'
      + '.fb-editor textarea{width:100%;box-sizing:border-box;padding:6px;font:inherit;border:1px solid #cbd5e1;border-radius:3px;min-height:80px;resize:vertical}'
      + '.fb-editor-row{display:flex;gap:6px;margin-top:8px;align-items:center}'
      + '.fb-editor-row input{flex:1;min-width:0;padding:4px 8px;border:1px solid #cbd5e1;border-radius:3px;font:inherit}'
      + '.fb-editor-row button{padding:5px 12px;cursor:pointer;border-radius:3px;font:inherit}'
      + '.fb-save{background:#2563eb;color:#fff;border:1px solid #1e40af}'
      + '.fb-cancel{background:#f3f4f6;border:1px solid #d1d5db;color:#1f2937}'
      + '.fb-entries{margin:6px 0 0 0}'
      + '.fb-entry{background:#fefce8;border-left:3px solid #f59e0b;padding:6px 10px;margin:4px 0;border-radius:0 4px 4px 0;position:relative}'
      + '.fb-meta{font-size:11px;color:#78716c}'
      + '.fb-text{margin-top:2px;white-space:pre-wrap}'
      + '.fb-del{position:absolute;top:4px;right:6px;background:none;border:none;color:#a8a29e;cursor:pointer;font-size:14px;padding:0;line-height:1}'
      + '.fb-del:hover{color:#dc2626}'
      + '.fb-gh-entry{margin-top:6px;background:#1f883d;color:#fff;border:1px solid #1a7f37;border-radius:4px;padding:3px 8px;font-size:11px;font-weight:600;cursor:pointer;line-height:1.2}'
      + '.fb-gh-entry:hover{background:#1a7f37}'
      + '.fb-bar{position:fixed;bottom:16px;right:16px;z-index:10;box-shadow:0 4px 12px rgba(0,0,0,.15);border-radius:6px;background:#fff}'
      + '.fb-bar-btn{background:#f59e0b;color:#1f2937;border:1px solid #d97706;padding:10px 14px;font-weight:600;border-radius:6px;cursor:pointer;font-size:14px}'
      + '.fb-bar-btn:hover{background:#fde68a}'
      + '.fb-bar-btn[disabled]{opacity:.5;cursor:not-allowed}'
      + '.fb-count{font-weight:400;opacity:.75;margin-left:4px}'
      + '@media print{.fb-add,.fb-editor,.fb-bar,.fb-gh-entry{display:none}}'
      + '</style>';
  }

  // Inline JS for the inline-feedback widget. Persists to localStorage
  // keyed per-investigation; renders 💬 buttons on every element whose
  // id matches the section taxonomy (study-*, finding-*, acceptance,
  // references, how-to-read, studies-heading) and offers a "Generate
  // feedback report" YAML download. No server contact — works offline.
  //
  // The editor is a body-level FIXED overlay anchored at click time to
  // the trigger button's viewport coords. Two reasons for the overlay
  // pattern instead of an in-host child:
  //   1. <button> defaults to type="submit" — appending a child editor
  //      inside arbitrary report sections can land in unexpected layout
  //      contexts (overflow:hidden parents, flex containers, <details>
  //      blocks) that clip or hide the editor entirely.
  //   2. A single global editor means clicking a different 💬 swaps the
  //      anchor cleanly instead of opening N stacked editors.
  function _feedbackWidgetJs(invName, reportId, ghRepo) {
    return '<script>'
      + '(function(){'
      +   'var INV=' + JSON.stringify(invName) + ';'
      +   'var REPORT_ID=' + JSON.stringify(reportId || '') + ';'
      // Repo (owner/name) resolved server-side at generation time from the
      // workspace git remote (fallback workspace.yaml dashboard.github_repo).
      // null when the workspace has no GitHub origin — the widget then
      // host-detects or prompts once.
      +   'var GH_REPO=' + JSON.stringify(ghRepo || '') + ';'
      +   'var KEY="v2ecoli_feedback_"+INV+(REPORT_ID?("_"+REPORT_ID):"");'
      +   'var ID_PATTERNS=[/^study-/,/^finding-/,/^acceptance$/,/^references$/,/^studies-heading$/,/^executive$/,/^decisions-needed$/,/^scientific-argument$/,/^biology$/,/^proposed-inputs$/];'
      +   'var openEd=null;'
      +   'var memStore={};'
      +   'function safeGet(k){try{var v=(typeof localStorage!=="undefined")?localStorage.getItem(k):null;return (v==null?memStore[k]:v)||"";}catch(e){return memStore[k]||"";}}'
      +   'function safeSet(k,v){memStore[k]=v;try{if(typeof localStorage!=="undefined")localStorage.setItem(k,v);}catch(e){}}'
      +   'function load(){try{var s=safeGet(KEY);return s?JSON.parse(s):{};}catch(e){return {};}}'
      +   'function save(d){safeSet(KEY,JSON.stringify(d));}'
      +   'function esc(s){return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");}'
      +   'function shouldAttach(el){if(!el.id)return false;return ID_PATTERNS.some(function(re){return re.test(el.id);});}'
      +   'function attachAll(){'
      +     'document.querySelectorAll("[id]").forEach(function(el){'
      +       'if(!shouldAttach(el)||el.dataset.fbAttached)return;'
      +       'el.dataset.fbAttached="1";el.classList.add("fb-host");'
      +       'var btn=document.createElement("button");'
      +       'btn.type="button";'  // explicit: never a form submit
      +       'btn.className="fb-add";btn.title="Add feedback to this section";btn.textContent="💬";'
      +       'btn.addEventListener("click",function(e){e.preventDefault();e.stopPropagation();openEditor(el,el.id,btn);});'
      +       'el.appendChild(btn);renderExisting(el,el.id);'
      +     '});'
      +     'updateBadges();updateBarCount();'
      +   '}'
      +   'function renderExisting(host,sid){'
      +     'var data=load();var entries=data[sid]||[];'
      +     'var box=host.querySelector(":scope>.fb-entries");'
      +     'if(!box){box=document.createElement("div");box.className="fb-entries";host.appendChild(box);}'
      +     'box.innerHTML=entries.map(function(e,i){'
      +       'return "<div class=\\"fb-entry\\">'
      +              '<button type=\\"button\\" class=\\"fb-del\\" data-i=\\""+i+"\\" title=\\"Delete\\">×</button>'
      +              '<div class=\\"fb-meta\\">"+esc(e.author||"evaluator")+" · "+esc(e.ts)+"</div>'
      +              '<div class=\\"fb-text\\">"+esc(e.text)+"</div>'
      // Per-annotation one-click GitHub issue (label=feedback, titled+bodied
      // with this section + this annotation). Hidden when no repo is known.
      +              '<button type=\\"button\\" class=\\"fb-gh-entry\\" data-i=\\""+i+"\\" title=\\"File this comment as a GitHub issue\\">\\u2197 Open GitHub issue</button>'
      +              '</div>";'
      +     '}).join("");'
      +     'box.querySelectorAll(".fb-del").forEach(function(b){'
      +       'b.addEventListener("click",function(ev){ev.preventDefault();ev.stopPropagation();var i=parseInt(b.dataset.i,10);var d=load();(d[sid]||[]).splice(i,1);if(!(d[sid]||[]).length)delete d[sid];save(d);renderExisting(host,sid);updateBadges();updateBarCount();});'
      +     '});'
      +     'box.querySelectorAll(".fb-gh-entry").forEach(function(b){'
      +       'b.addEventListener("click",function(ev){ev.preventDefault();ev.stopPropagation();var i=parseInt(b.dataset.i,10);var e=(load()[sid]||[])[i];if(e)openGhIssueForSection(sid,e.text,e.author);});'
      +     '});'
      +   '}'
      +   'function closeEditor(){if(openEd){openEd.remove();openEd=null;}}'
      +   'function positionEditor(ed,anchorBtn){'
      +     'var r=anchorBtn.getBoundingClientRect();'
      +     'var edW=Math.min(360,window.innerWidth-24);'
      +     'var top=r.bottom+8;'
      +     'var left=Math.max(12,Math.min(window.innerWidth-edW-12,r.right-edW));'
      +     'ed.style.top=top+"px";'
      +     'ed.style.left=left+"px";'
      +     'var edH=ed.offsetHeight||220;'
      +     'if(top+edH>window.innerHeight-12){ed.style.top=Math.max(12,r.top-edH-8)+"px";}'
      +   '}'
      +   'function openEditor(host,sid,anchorBtn){'
      +     'closeEditor();'  // singleton: only one editor at a time
      +     'var ed=document.createElement("div");ed.className="fb-editor";'
      +     'ed.setAttribute("data-fb-sid",sid);'
      +     'ed.innerHTML="<div style=\\"font-size:12px;color:#78716c;margin-bottom:6px\\">Feedback on §<code>"+esc(sid)+"</code></div>'
      +       '<textarea placeholder=\\"What feedback do you have on this section? (assumption, parameter, evidence, missing detail, etc.)\\"></textarea>'
      +       '<div class=\\"fb-editor-row\\">'
      +         '<input class=\\"fb-author\\" placeholder=\\"Your name (optional)\\" value=\\""+esc(safeGet("fb_author"))+"\\">'
      +         '<button type=\\"button\\" class=\\"fb-cancel\\">Cancel</button>'
      +         '<button type=\\"button\\" class=\\"fb-save\\">Save</button>'
      +       '</div>";'
      +     'document.body.appendChild(ed);'
      +     'openEd=ed;'
      +     'ed.addEventListener("click",function(e){e.stopPropagation();});'
      +     'positionEditor(ed,anchorBtn);'
      +     'window.requestAnimationFrame(function(){positionEditor(ed,anchorBtn);});'  // refine after layout
      +     'setTimeout(function(){var ta=ed.querySelector("textarea");if(ta)ta.focus();},0);'
      +     'ed.querySelector(".fb-cancel").addEventListener("click",function(e){e.preventDefault();e.stopPropagation();closeEditor();});'
      +     'ed.querySelector(".fb-save").addEventListener("click",function(e){'
      +       'e.preventDefault();e.stopPropagation();'
      +       'var text=ed.querySelector("textarea").value.trim();if(!text)return;'
      +       'var author=ed.querySelector(".fb-author").value.trim();'
      +       'if(author)safeSet("fb_author",author);'
      +       'var d=load();d[sid]=d[sid]||[];'
      +       'd[sid].push({ts:new Date().toISOString(),author:author,text:text});'
      +       'save(d);closeEditor();renderExisting(host,sid);updateBadges();updateBarCount();'
      +     '});'
      +   '}'
      +   'document.addEventListener("click",function(e){'
      +     'if(!openEd)return;'
      +     'if(openEd.contains(e.target))return;'
      +     'if(e.target.classList&&e.target.classList.contains("fb-add"))return;'
      +     'closeEditor();'
      +   '});'
      +   'document.addEventListener("keydown",function(e){if(e.key==="Escape")closeEditor();});'
      +   'window.addEventListener("resize",function(){if(openEd){var sid=openEd.getAttribute("data-fb-sid");var host=sid&&document.getElementById(sid);var btn=host&&host.querySelector(":scope>.fb-add");if(btn)positionEditor(openEd,btn);}});'
      +   'window.addEventListener("scroll",function(){if(openEd){var sid=openEd.getAttribute("data-fb-sid");var host=sid&&document.getElementById(sid);var btn=host&&host.querySelector(":scope>.fb-add");if(btn)positionEditor(openEd,btn);}},{passive:true});'
      +   'function countAll(){var d=load();var n=0;Object.keys(d).forEach(function(k){n+=(d[k]||[]).length;});return n;}'
      +   'function updateBadges(){var d=load();document.querySelectorAll(".fb-add").forEach(function(b){var sid=b.parentElement&&b.parentElement.id;if(!sid)return;b.classList.toggle("has-fb",((d[sid]||[]).length>0));});}'
      +   'function updateBarCount(){var c=countAll();var nt="("+c+")";document.querySelectorAll(".fb-count").forEach(function(s){if(s.textContent!==nt)s.textContent=nt;});document.querySelectorAll(".fb-bar-btn").forEach(function(btn){btn.disabled=c===0;});}'
      +   'function ensureBar(){'
      +     'if(document.querySelector(".fb-bar"))return;'
      +     'var bar=document.createElement("div");bar.className="fb-bar";'
      +     'var html="";'
      +     'html+="<button type=\\"button\\" class=\\"fb-bar-btn fb-dl-btn\\" title=\\"Download all your annotations as a yaml file\\">Download feedback (.yaml) <span class=\\"fb-count\\">(0)</span></button>";'
      // One-click submit to GitHub — works on any host (no dashboard server
      // needed), so an emailed/Pages-hosted report can file feedback directly.
      +     'html+="<button type=\\"button\\" class=\\"fb-bar-btn fb-gh-issue\\" style=\\"margin-left:6px;background:#1f883d\\" title=\\"Open a prefilled GitHub issue with your feedback\\">→ GitHub issue</button>";'
      +     'bar.innerHTML=html;'
      +     'document.body.appendChild(bar);'
      +     'var db=bar.querySelector(".fb-dl-btn");if(db)db.addEventListener("click",function(e){e.preventDefault();e.stopPropagation();downloadFeedback();});'
      +     'var gi=bar.querySelector(".fb-gh-issue");if(gi)gi.addEventListener("click",function(e){e.preventDefault();e.stopPropagation();openGhIssue();});'
      +   '}'
      // Proposed-input Accept/Decline and "+ Add to investigation" record
      // their decision through this — a NORMAL annotation, the same channel
      // the manual 💬 comments use. Author defaults to the reviewer's saved
      // name (fb_author) so decisions are attributed like every other comment.
      +   'window._fbAddAnnotation=function(sid,text,author){'
      +     'if(!sid||!text)return;'
      +     'author=author||safeGet("fb_author")||"reviewer";'
      +     'var d=load();d[sid]=d[sid]||[];'
      +     'd[sid].push({ts:new Date().toISOString(),author:author,text:text});'
      +     'save(d);var host=document.getElementById(sid);if(host&&host.dataset&&host.dataset.fbAttached)renderExisting(host,sid);updateBadges();updateBarCount();'
      +   '};'
      +   'function serialiseYaml(meta,data){'
      +     'var L=["# Inline feedback report","# Generated from the v2ecoli inline-feedback widget.","# Import with: pbg-feedback-import <this-file>"];'
      +     'L.push("meta:");'
      +     'Object.keys(meta).forEach(function(k){L.push("  "+k+": "+JSON.stringify(meta[k]));});'
      +     'var keys=Object.keys(data).sort();'
      +     'if(!keys.length){L.push("annotations: {}");}'
      +     'else{L.push("annotations:");keys.forEach(function(sid){'
      +       '(data[sid]||[]).forEach(function(e,i){if(i===0)L.push("  "+JSON.stringify(sid)+":");L.push("    - ts: "+JSON.stringify(e.ts));if(e.author)L.push("      author: "+JSON.stringify(e.author));L.push("      text: "+JSON.stringify(e.text));});'
      +     '});}'
      +     'return L.join("\\n")+"\\n";'
      +   '}'
      +   'function downloadFeedback(){'
      +     'var d=load();if(!countAll()){alert("No feedback yet — click 💬 next to any section first.");return;}'
      +     'var ts=new Date().toISOString();'
      +     'var meta={investigation:INV,report_id:REPORT_ID,generated_at:ts,page_title:document.title,source_url:location.href};'
      +     'var blob=new Blob([serialiseYaml(meta,d)],{type:"application/yaml"});'
      +     'var url=URL.createObjectURL(blob);var a=document.createElement("a");'
      +     'a.href=url;a.download="feedback-"+INV+"-"+ts.slice(0,19).replace(/[:T]/g,"-")+".yaml";'
      +     'document.body.appendChild(a);a.click();a.remove();setTimeout(function(){URL.revokeObjectURL(url);},0);'
      +   '}'
      // GitHub submit helpers. Resolve owner/repo from a github.io host
      // (vivarium-collective.github.io/<repo>/…), else ask once and remember.
      +   'function ghRepo(){'
      // 1) repo injected at generation time from the workspace git remote.
      +     'if(GH_REPO)return GH_REPO;'
      // 2) github.io host detection (vivarium-collective.github.io/<repo>/…).
      +     'try{var h=location.hostname,p=location.pathname.split("/").filter(Boolean);'
      +       'if(/\\.github\\.io$/.test(h)&&p.length)return h.split(".")[0]+"/"+p[0];}catch(e){}'
      // 3) remembered prompt answer, then a one-time prompt.
      +     'var v=safeGet("fb_gh_repo");if(v)return v;'
      +     'var ans=prompt("GitHub repo for this feedback (owner/repo):","");'
      +     'if(ans){ans=ans.replace(/^https?:\\/\\/github.com\\//,"").replace(/\\.git$/,"").replace(/\\/+$/,"");safeSet("fb_gh_repo",ans);}'
      +     'return ans||"";'
      +   '}'
      // Per-section GitHub issue: file ONE annotation as a focused issue,
      // titled with the section id and bodied with the annotation text, a
      // short quote of the section, and a deep-link anchor back to it.
      +   'function sectionQuote(sid){try{var el=document.getElementById(sid);if(!el)return "";'
      +     'var clone=el.cloneNode(true);clone.querySelectorAll(".fb-add,.fb-editor,.fb-entries,.fb-bar,script,style").forEach(function(n){n.remove();});'
      +     'var t=(clone.textContent||"").replace(/\\s+/g," ").trim();return t.slice(0,280)+(t.length>280?"\\u2026":"");}catch(e){return "";}}'
      +   'function openGhIssueForSection(sid,text,author){'
      +     'var repo=ghRepo();if(!repo)return;'
      +     'var anchor=location.origin+location.pathname+"#"+encodeURIComponent(sid);'
      +     'var quote=sectionQuote(sid);'
      +     'var body="Reviewer feedback on the **"+INV+"** investigation report.\\n\\n"'
      +       '+"**Section:** `"+sid+"`\\n"'
      +       '+(author?("**Reviewer:** "+author+"\\n"):"")'
      +       '+"\\n**Feedback:**\\n> "+String(text||"").replace(/\\n/g,"\\n> ")+"\\n"'
      +       '+(quote?("\\n**Section context:**\\n> "+quote+"\\n"):"")'
      +       '+"\\n[Open this section in the report]("+anchor+")\\n";'
      +     'var title="Reviewer feedback ["+INV+"]: "+sid;'
      +     'var url="https://github.com/"+repo+"/issues/new?labels=feedback&title="+encodeURIComponent(title)+"&body="+encodeURIComponent(body);'
      +     'var w=window.open(url,"_blank","noopener");if(!w)location.href=url;'
      +   '}'
      +   'function fbYaml(){var meta={investigation:INV,report_id:REPORT_ID,generated_at:new Date().toISOString(),page_title:document.title,source_url:location.href};return serialiseYaml(meta,load());}'
      +   'function openGhIssue(){if(!countAll()){alert("No feedback yet — click the 💬 icons first.");return;}var repo=ghRepo();if(!repo)return;'
      +     'var body="Inline feedback from the investigation report.\\n\\n```yaml\\n"+fbYaml()+"```\\n";'
      +     'var url="https://github.com/"+repo+"/issues/new?labels=feedback&title="+encodeURIComponent("Reviewer feedback: "+INV)+"&body="+encodeURIComponent(body);'
      +     'var w=window.open(url,"_blank","noopener");if(!w)location.href=url;'
      +   '}'
      +   'function init(){attachAll();ensureBar();var mo=new MutationObserver(function(){attachAll();});mo.observe(document.body,{childList:true,subtree:true});}'
      +   'if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",init);else init();'
      + '})();'
      + '</script>';
  }

  // Pop-out the investigation itself in a detached window. URL carries
  // both ?investigation=<name> AND #investigations so detection is robust
  // regardless of when the param is read on the receiving side.
  function _popoutInvestigation() {
    var name = window._currentIset;
    if (!name) {
      console.warn('_popoutInvestigation: no current investigation set');
      return;
    }
    // focus=investigations strips the sidebar + topbar (CSS rules in
    // style.css). investigation=<name> tells _loadInvestigationSets which
    // iset to auto-open. The hash anchors the right page.
    var url = window.location.origin + window.location.pathname +
              '?focus=investigations&investigation=' + encodeURIComponent(name) +
              '#investigations';
    var w = _openDetachedWindow(url, 1400, 900);
    if (!w) {
      console.warn('_popoutInvestigation: popup blocked, navigating in-place');
      alert('Popup blocked. Allow popups from this site to pop out the investigation.');
    }
  }
  window._popoutInvestigation = _popoutInvestigation;

  // Back-compat shim for any old callers (sidebar groups still use this).
  function _openStudyEmbeddedNewTab(name) {
    // If we're inside the Investigations tab, use the in-place embed.
    if (window._currentIset) {
      _openStudyInsideInvestigation(name);
      return;
    }
    _switchPage('studies');
    setTimeout(function() {
      if (typeof _openStudyEmbedded === 'function') _openStudyEmbedded(name);
    }, 80);
  }
  window._openStudyEmbeddedNewTab = _openStudyEmbeddedNewTab;

  // Sidebar grouping: studies-by-investigation, collapsible.
  // Replaces the existing flat-list render in #viv-rail-investigations.
  // Map a study's free-form status string to a small colored dot. Keeps the
  // rail rows readable: the study NAME gets the full row width, the dot is a
  // glanceable status, the full status text is shown in the title tooltip.
  function _railStatusColor(status) {
    var s = String(status || '').toLowerCase();
    if (s.indexOf('fail') !== -1 || s.indexOf('invalid') !== -1) return '#ef4444';   // red
    if (s.indexOf('pending') !== -1 || s.indexOf('refresh') !== -1) return '#f59e0b';// amber
    if (s.indexOf('inconclusive') !== -1 || s.indexOf('partial') !== -1) return '#d97706'; // dark amber
    if (s.indexOf('running') === 0) return '#3b82f6';                                // blue
    if (s.indexOf('done') === 0 || s.indexOf('ran') === 0 || s.indexOf('complete') !== -1
        || s.indexOf('evaluated') !== -1 || s.indexOf('confirmed') !== -1 || s.indexOf('passing') !== -1
        || s.indexOf('-wins') !== -1 || s.indexOf('in-band') !== -1) return '#16a34a'; // green
    if (s.indexOf('evaluate') === 0) return '#6366f1';                               // indigo (mid-pass action)
    return '#9ca3af';                                                                // gray (planned/unknown)
  }

  // Single-row per study: [dot] name [🔒?]. Full status string in tooltip.
  // Used by both the flat-list and grouped rail layouts.
  function _railStudyItem(s, opts) {
    opts = opts || {};
    var status = s.status || 'planned';
    var color = _railStatusColor(status);
    var indent = opts.indent ? '28px' : '12px';
    var fontSize = opts.indent ? '0.85em' : '0.86em';
    var nameColor = opts.indent ? '#64748b' : '#374151';
    var tip = _esc(s.name) + ' — ' + _esc(status) + (s.blocked ? ' (blocked)' : '');
    return '<a class="viv-rail-sublink" ' +
           'onclick="event.preventDefault();_openStudyEmbeddedNewTab(\'' + _esc(s.name) + '\');return false;" ' +
           'href="#" title="' + tip + '" ' +
           'style="display:flex;align-items:center;gap:8px;padding:4px 14px 4px ' + indent + ';color:' + nameColor + ';text-decoration:none;font-size:' + fontSize + ';">' +
             '<span aria-hidden="true" style="flex:none;width:8px;height:8px;border-radius:50%;background:' + color + ';display:inline-block"></span>' +
             '<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1">' + _esc(s.name) + '</span>' +
             (s.blocked ? '<span title="blocked" style="font-size:0.85em;flex:none">🔒</span>' : '') +
           '</a>';
  }

  function _renderRailInvestigationGroups() {
    var host = document.getElementById('viv-rail-investigations');
    if (!host) return;
    // Need both: window._investigations (all studies) AND window._isetIndex (groups).
    // If either isn't loaded yet, fall back to a loading message + kick the missing one.
    if (!Array.isArray(window._isetIndex)) window._isetIndex = [];
    if (!Array.isArray(window._investigations) || !window._investigations.length) {
      // No studies in memory yet → fall back to the legacy render until they arrive.
      if (typeof _renderRailInvestigationsLegacy === 'function') return _renderRailInvestigationsLegacy();
      host.innerHTML = '<p class="viv-rail-empty" style="font-size:0.85em;color:#9ca3af;padding:4px 12px">Loading…</p>';
      if (typeof _loadInvestigations === 'function') _loadInvestigations();
      return;
    }

    var memberSet = {};         // studySlug -> [isetName, ...]
    window._isetIndex.forEach(function(iset) {
      (iset.studies || []).forEach(function(slug) {
        (memberSet[slug] = memberSet[slug] || []).push(iset.name);
      });
    });

    // Group studies: each iset gets its members; leftovers go to "Ungrouped".
    var groups = [];   // [{name, title, studies: [study, ...]}]
    var seen = {};
    window._isetIndex.forEach(function(iset) {
      var members = (iset.studies || [])
        .map(function(slug) { return window._investigations.find(function(s) { return s.name === slug; }); })
        .filter(Boolean);
      members.forEach(function(s) { seen[s.name] = true; });
      // Sort within group by topological depth (the same map computed in
      // _renderInvestigations); if unavailable, fall back to alpha.
      var depthMap = window._investigationsDepth || {};
      members.sort(function(a, b) {
        var da = depthMap[a.name] || 0, db = depthMap[b.name] || 0;
        return da - db || a.name.localeCompare(b.name);
      });
      groups.push({name: iset.name, title: iset.title || iset.name, studies: members});
    });
    var ungrouped = window._investigations.filter(function(s) { return !seen[s.name]; });
    if (ungrouped.length) groups.push({name: '__ungrouped__', title: 'Ungrouped', studies: ungrouped});

    // Scope to current investigation: when the cross-worktree registry
    // has identified a current iset (window._currentIsetSlug, set by
    // investigation-switcher.js after fetching /api/investigation-registry)
    // AND that iset has a group here, drop every other group so the rail
    // reflects only the studies the user is actively working on. The
    // iset dropdown at the top of the rail is the way to switch isets;
    // listing every iset's studies in the rail itself was just noise.
    // Falls back to the full all-groups render if no current slug is
    // known yet (registry still loading) or if the current slug doesn't
    // match any group (defensive).
    var currentSlug = window._currentIsetSlug || '';
    if (currentSlug) {
      var hasCurrent = groups.some(function(g) { return g.name === currentSlug; });
      if (hasCurrent) {
        groups = groups.filter(function(g) { return g.name === currentSlug; });
      }
    }

    // List-first (State A): no investigation selected yet + several available ->
    // prompt the user to pick one rather than dumping every investigation's
    // studies into the rail.
    if (!currentSlug && groups.length > 1) {
      host.innerHTML = '<div style="padding:6px 14px;color:#94a3b8;font-style:italic">'
        + 'Select an investigation &rarr;</div>';
      return;
    }

    // Flat-list mode: when there's exactly one investigation (no
    // ungrouped studies), render its studies as a flat list directly
    // under the "Studies" rail-section label — no redundant group header.
    if (groups.length === 1 && groups[0].name !== '__ungrouped__') {
      var g = groups[0];
      var _iset = (window._isetIndex || []).filter(function(i){ return i.name === g.name; })[0] || {};
      host.innerHTML = '<div class="rail-iset-name" title="' + _esc(_iset.title || g.name) + '">'
        + _esc(_iset.title || g.name) + '</div>'
        + g.studies.map(function(s) { return _railStudyItem(s); }).join('');
      return;
    }

    var collapsedState = window._isetRailCollapsed || {};
    host.innerHTML = groups.map(function(g) {
      var isCollapsed = !!collapsedState[g.name];
      var children = isCollapsed ? '' : g.studies.map(function(s) {
        return _railStudyItem(s, { indent: true });
      }).join('');
      var headerClick = "event.preventDefault(); window._isetRailCollapsed = window._isetRailCollapsed || {}; window._isetRailCollapsed['" + _esc(g.name) + "'] = !window._isetRailCollapsed['" + _esc(g.name) + "']; _renderRailInvestigationGroups();";
      var groupClick = g.name === '__ungrouped__' ? '' :
        ' <a onclick="event.stopPropagation();event.preventDefault();_switchPage(\'investigations\');_openInvestigationDetail(\'' + _esc(g.name) + '\');return false;" ' +
        'href="#" style="font-size:0.7em;color:#3b82f6;margin-left:auto;">[DAG]</a>';
      return '<div class="viv-rail-iset-group" data-iset="' + _esc(g.name) + '">' +
        '<div onclick="' + headerClick + '" ' +
             'style="display:flex;align-items:center;gap:4px;padding:4px 12px;cursor:pointer;user-select:none;font-size:0.85em;color:#374151;font-weight:600;">' +
          '<span style="display:inline-block;width:10px;text-align:center;color:#94a3b8;">' + (isCollapsed ? '▸' : '▾') + '</span>' +
          '<span style="flex:1">' + _esc(g.title) + '</span>' +
          '<span class="muted" style="font-size:0.72em;font-weight:normal;">(' + g.studies.length + ')</span>' +
          groupClick +
        '</div>' +
        children +
      '</div>';
    }).join('');
  }
  window._renderRailInvestigationGroups = _renderRailInvestigationGroups;

  function _buildInvestigationTagChips() {
    var container = document.getElementById('investigations-tag-chips');
    if (!container) return;
    var tags = new Set();
    window._investigations.forEach(function(inv) {
      (inv.tags || []).forEach(function(t) { tags.add(t); });
    });
    var chips = Array.from(tags).sort().map(function(t) {
      var active = window._investigationsFilter.tags.has(t) ? ' active' : '';
      return '<button class="card-browse-chip' + active + '"' +
             ' onclick="_toggleInvestigationChip(\'' + _esc(t) + '\', this)">' +
             _esc(t) + '</button>';
    }).join('');
    container.innerHTML = chips;
  }

  function _toggleInvestigationChip(tag, btn) {
    var s = window._investigationsFilter.tags;
    if (s.has(tag)) { s.delete(tag); btn.classList.remove('active'); }
    else { s.add(tag); btn.classList.add('active'); }
    _renderInvestigations();
  }
  window._toggleInvestigationChip = _toggleInvestigationChip;

  // ── DAG helpers ─────────────────────────────────────────────────────
  // Build a children map (reverse of parent_studies) and a depth map
  // (BFS from roots) for the topological sort + Depends-on/Blocks chips.
  function _buildInvestigationDag(all) {
    var childrenMap = {};
    all.forEach(function(inv) { childrenMap[inv.name] = []; });
    function _parentName(p) { return (typeof p === 'string') ? p : (p && p.study); }
    all.forEach(function(inv) {
      (inv.parent_studies || []).forEach(function(p) {
        var pn = _parentName(p);
        if (pn && childrenMap[pn]) childrenMap[pn].push(inv.name);
      });
    });
    // BFS depth from roots.
    var depthMap = {};
    var queue = [];
    all.forEach(function(inv) {
      if (!(inv.parent_studies || []).length) {
        depthMap[inv.name] = 0;
        queue.push(inv.name);
      }
    });
    var guard = all.length * 4;   // cycle guard
    while (queue.length && guard-- > 0) {
      var name = queue.shift();
      var d = depthMap[name];
      (childrenMap[name] || []).forEach(function(child) {
        if (depthMap[child] === undefined || depthMap[child] < d + 1) {
          depthMap[child] = d + 1;
          queue.push(child);
        }
      });
    }
    all.forEach(function(inv) {
      if (depthMap[inv.name] === undefined) depthMap[inv.name] = 99;
    });
    return {children: childrenMap, depth: depthMap};
  }

  function _renderInvestigations() {
    var grid = document.getElementById('investigations-grid');
    if (!grid) return;
    var f = window._investigationsFilter;
    var q = f.search.toLowerCase();
    var dag = _buildInvestigationDag(window._investigations);
    window._investigationsChildren = dag.children;
    window._investigationsDepth = dag.depth;
    var filtered = window._investigations.filter(function(inv) {
      if (q) {
        var hay = (inv.name + ' ' + (inv.description || '') + ' ' +
                    (inv.tags || []).join(' ')).toLowerCase();
        if (hay.indexOf(q) < 0) return false;
      }
      if (f.tags.size > 0) {
        var match = (inv.tags || []).some(function(t) { return f.tags.has(t); });
        if (!match) return false;
      }
      return true;
    });
    if (!filtered.length) {
      grid.innerHTML = '<p class="empty-state">No studies match the filter. ' +
                       'Click <em>+ New study</em> to create one.</p>';
      grid.classList.remove('list-view');
      return;
    }
    var sort = window._investigationsSort || 'dependencies';   // topology default
    filtered.sort(function(a, b) {
      if (sort === 'last_run') {
        return (b.last_run || '').localeCompare(a.last_run || '');
      }
      if (sort === 'status') {
        return (a.status || '').localeCompare(b.status || '') || a.name.localeCompare(b.name);
      }
      if (sort === 'phase') {
        var phaseOrder = { Design: 0, Build: 1, Simulate: 2, Evaluate: 3, Decide: 4 };
        var pa = phaseOrder[a.phase];
        var pb = phaseOrder[b.phase];
        if (pa == null) pa = 99;
        if (pb == null) pb = 99;
        return pa - pb || a.name.localeCompare(b.name);
      }
      if (sort === 'topic') {
        return (a.topic || 'zzz').localeCompare(b.topic || 'zzz') || a.name.localeCompare(b.name);
      }
      if (sort === 'n_runs') {
        return (b.n_runs || 0) - (a.n_runs || 0);
      }
      if (sort === 'name') {
        return a.name.localeCompare(b.name);
      }
      // Default: topological depth (roots first), then alphabetical within depth.
      var depthMap = window._investigationsDepth || {};
      var da = depthMap[a.name] || 0, db = depthMap[b.name] || 0;
      return da - db || a.name.localeCompare(b.name);
    });
    grid.classList.toggle('list-view', window._investigationsView === 'list');
    grid.innerHTML = filtered.map(_renderInvestigationCard).join('');
  }

  function _setInvestigationsSort(value) {
    window._investigationsSort = value;
    _renderInvestigations();
  }
  window._setInvestigationsSort = _setInvestigationsSort;

  function _renderInvestigationCard(inv) {
    var status = inv.status || 'planned';
    var statusClass = ({planned:'planned', running:'in_progress', ran:'complete',
                        complete:'complete', failed:'gate_pending',
                        invalid:'gate_pending'})[status] || 'planned';
    var lastRun = inv.last_run ? new Date(inv.last_run + 'Z').toLocaleString() : '—';

    // Pretty baseline source comes from the server's v2 projection
    // (``pkg_short:name``). Fall back to the raw baseline name or the legacy
    // ``composite`` summary so old payloads still render something useful.
    var hasV2 = (inv.n_variants !== undefined) || (inv.baseline !== undefined);
    var baselineDisplay;
    if (inv.baseline_source) {
      baselineDisplay = inv.baseline_source;
    } else if (inv.baseline) {
      baselineDisplay = inv.baseline;
    } else if (!hasV2 && inv.composite) {
      baselineDisplay = inv.composite;
    } else {
      baselineDisplay = 'unknown';
    }

    var nVariants = (inv.n_variants !== undefined) ? inv.n_variants : 0;
    var nGroups = (inv.n_groups !== undefined) ? inv.n_groups : 0;
    var nRuns = (inv.n_runs !== undefined) ? inv.n_runs
              : (inv.n_simulations !== undefined ? inv.n_simulations : 0);
    var excerpt = inv.conclusions_excerpt || '';

    var conclusionsHtml = excerpt
      ? '<div class="ic-conclusions"><em>“' + _esc(excerpt) + '”</em></div>'
      : '';

    var runLabel = (status === 'planned') ? 'Run' : 'Re-run';

    // ── Dependency chips ──
    var parents = inv.parent_studies || [];
    var children = (window._investigationsChildren || {})[inv.name] || [];

    function _depLink(name, suffix, color) {
      return '<a onclick="event.stopPropagation(); _openStudyEmbedded(\'' + _esc(name) + '\')" ' +
             'style="color:' + color + ';cursor:pointer;text-decoration:underline;">' +
             _esc(name) + '</a>' + (suffix ? ' <small class="muted">(' + _esc(suffix) + ')</small>' : '');
    }
    var dependsHtml = '';
    if (parents.length) {
      dependsHtml = '<div class="ic-deps" style="margin-top:6px;font-size:0.78em;">' +
        '<span class="muted">Depends on:</span> ' +
        parents.map(function(p) {
          var name = (typeof p === 'string') ? p : p.study;
          var cond = (typeof p === 'string') ? 'tests-passed' : (p.condition || 'tests-passed');
          return _depLink(name, cond, '#3b82f6');
        }).join(' · ') +
      '</div>';
    }
    var blocksHtml = '';
    if (children.length) {
      blocksHtml = '<div class="ic-deps" style="font-size:0.78em;">' +
        '<span class="muted">Blocks:</span> ' +
        children.map(function(name) { return _depLink(name, '', '#94a3b8'); }).join(' · ') +
      '</div>';
    }

    // 🔒 Blocked badge (parents haven't satisfied their condition yet).
    var blockedBadge = '';
    if (inv.blocked) {
      var reasons = (inv.blocked_by || []).map(function(b) {
        return b.study + ' (' + b.condition + (b.missing ? ' — ' + b.missing : '') + ')';
      }).join('\n');
      blockedBadge = ' <span class="status-pill" ' +
                     'style="background:#fef3c7;color:#92400e;font-size:0.7em;padding:1px 6px;" ' +
                     'title="Blocked by:\n' + _esc(reasons) + '">🔒 blocked</span>';
    }

    var phaseColors = {
      Design:   {bg: '#e0e7ff', fg: '#3730a3'},
      Build:    {bg: '#fef3c7', fg: '#92400e'},
      Simulate: {bg: '#dbeafe', fg: '#1e40af'},
      Evaluate: {bg: '#fce7f3', fg: '#9d174d'},
      Decide:   {bg: '#d1fae5', fg: '#065f46'},
    };
    var pc = phaseColors[inv.phase] || null;
    var phaseChip = (inv.phase && pc)
      ? ' <span class="status-pill" style="background:' + pc.bg +
        ';color:' + pc.fg + ';font-size:0.7em;padding:1px 8px;border-radius:9999px;">' +
        _esc(inv.phase) + '</span>'
      : '';

    return '<div class="investigation-card" onclick="_openStudyEmbedded(\'' + _esc(inv.name) + '\')">' +
      '<div class="ic-header">' +
        '<div class="ic-title">' + _esc(inv.name) + '</div>' +
        '<span class="ic-status status-pill ' + statusClass + '">' + _esc(status) + '</span>' +
        phaseChip +
        blockedBadge +
      '</div>' +
      '<div class="ic-baseline"><small>Baseline:</small> <code>' + _esc(baselineDisplay) + '</code></div>' +
      dependsHtml +
      blocksHtml +
      conclusionsHtml +
      '<div class="ic-meta">' +
        '<span>' + nVariants + ' variant' + (nVariants === 1 ? '' : 's') + '</span>' +
        '<span>' + nGroups + ' group' + (nGroups === 1 ? '' : 's') + '</span>' +
        '<span>' + nRuns + ' run' + (nRuns === 1 ? '' : 's') + '</span>' +
        '<span class="ic-lastrun">last run: ' + _esc(lastRun) + '</span>' +
      '</div>' +
      '<div class="ic-actions">' +
        '<button class="btn-mini" onclick="event.stopPropagation(); event.preventDefault(); _runInvestigation(\'' + _esc(inv.name) + '\')">' + runLabel + '</button>' +
        '<button class="btn-mini" onclick="event.stopPropagation(); event.preventDefault(); _deleteInvestigation(\'' + _esc(inv.name) + '\')" style="color:#c00">Delete</button>' +
      '</div>' +
    '</div>';
  }

  function _setInvestigationsView(view) {
    window._investigationsView = view;
    document.querySelectorAll('#investigations-toolbar .view-btn').forEach(function(b) {
      b.classList.toggle('active', b.dataset.view === view);
    });
    _renderInvestigations();
  }
  window._setInvestigationsView = _setInvestigationsView;

  // Search input live-filter
  document.addEventListener('input', function(e) {
    if (e.target && e.target.id === 'investigations-search') {
      window._investigationsFilter.search = e.target.value;
      _renderInvestigations();
    }
  });

  function _createInvestigation() {
    var srcSel = document.getElementById('create-inv-source');
    if (srcSel) srcSel.innerHTML = '<option value="">— blank composites list, add later —</option>';
    fetch('/api/composites').then(function(r) { return r.json(); }).then(function(data) {
      (data.composites || []).forEach(function(c) {
        if (srcSel) {
          var sopt = document.createElement('option');
          sopt.value = c.id;
          sopt.textContent = c.name + '  —  ' + (c.description || c.id);
          srcSel.appendChild(sopt);
        }
      });
      openModal('modal-investigation-create');
    }).catch(function() {
      openModal('modal-investigation-create');
    });
  }
  window._createInvestigation = _createInvestigation;

  function _submitInvestigationCreate(form) {
    var data = new FormData(form);
    var payload = { name: data.get('name'), composite: data.get('composite'), source: data.get('source') || '' };
    fetch('/api/investigation-create', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    }).then(function(r) { return r.json().then(function(j) { return [r.ok, j]; }); })
      .then(function(parts) {
        var ok = parts[0], j = parts[1];
        if (!ok) {
          var err = form.querySelector('.form-error');
          if (err) err.textContent = j.error || 'create failed';
          return;
        }
        closeModal('modal-investigation-create');
        window._investigationsLoaded = false;
        _switchPage('studies');
        _vivRefreshInvestigationsRail();
      });
  }
  window._submitInvestigationCreate = _submitInvestigationCreate;

  function _openInvestigation(name) {
    window._currentInvestigation = name;
    var detail = document.getElementById('investigation-detail');
    if (detail) {
      detail.style.display = '';
      detail.innerHTML = '<p class="empty-state">Loading…</p>';
    }
    // Switch the Investigations page into single-study focus mode: hide the
    // grid + toolbar + chips and let the detail panel take the full width.
    _setInvestigationsFocusMode(true);
    fetch('/api/investigation/' + encodeURIComponent(name))
      .then(function(r) { return r.json(); })
      .then(function(data) { _renderInvestigationDetail(name, data); })
      .catch(function(err) {
        if (detail) {
          detail.innerHTML = '<p style="color:#c00">Failed: ' + _esc(String(err)) + '</p>';
        }
        console.error('Failed to open investigation:', err);
        _setInvestigationsFocusMode(false);
      });
  }
  window._openInvestigation = _openInvestigation;

  function _setInvestigationsFocusMode(on) {
    var page = document.getElementById('page-studies');
    if (!page) return;
    page.classList.toggle('inv-focus-mode', !!on);
    // Rail mirrors focus state: shows just the active study while focused,
    // restores the grouped sub-list when we're back on the index.
    if (typeof _vivRefreshInvestigationsRail === 'function') {
      _vivRefreshInvestigationsRail();
    }
  }
  window._setInvestigationsFocusMode = _setInvestigationsFocusMode;

  function _closeInvestigationFocus() {
    window._currentInvestigation = null;
    _setInvestigationsFocusMode(false);
    var detail = document.getElementById('investigation-detail');
    if (detail) {
      detail.style.display = 'none';
      detail.innerHTML = '';
    }
  }
  window._closeInvestigationFocus = _closeInvestigationFocus;

  function _renderInvestigationDetail(name, data) {
    var detail = document.getElementById('investigation-detail');
    if (data.error) {
      detail.innerHTML = '<p style="color:#c00">' + _esc(data.error) + '</p>';
      return;
    }
    var spec = data.spec || {};
    // Cache the spec so per-tab handlers (Comparisons, Add-Viz modal, etc.) can
    // read variants/observables/comparisons without re-fetching.
    window._invSpecCache = spec;
    var vizFiles = data.viz_files || [];
    var runs = data.runs_summary || [];
    var lastRun = spec.last_run ? new Date(spec.last_run + 'Z').toLocaleString() : '—';
    var status = spec.status || 'planned';
    var statusClass = ({planned:'planned', running:'in_progress', complete:'complete',
                        failed:'gate_pending'})[status] || 'planned';

    // ── Overview-tab data (B2) ────────────────────────────────────────────────
    var ovTopic      = (typeof spec.topic === 'string') ? spec.topic : '';
    var ovQuestion   = (typeof spec.question === 'string') ? spec.question : '';
    var ovHypothesis = (typeof spec.hypothesis === 'string') ? spec.hypothesis : '';
    var ovStatus     = spec.status || 'draft';
    var variants     = Array.isArray(spec.variants) ? spec.variants : [];
    var baseline     = spec.baseline || '';
    window._invBaselineCache = baseline;
    var baselineEntry = null;
    for (var bi = 0; bi < variants.length; bi++) {
      if (variants[bi] && variants[bi].name === baseline) { baselineEntry = variants[bi]; break; }
    }
    var baselineSource = (baselineEntry && baselineEntry.source) ? baselineEntry.source : '—';
    var variantNames = variants.map(function(v) { return v && v.name ? v.name : ''; }).filter(Boolean);
    var comparisons  = Array.isArray(spec.comparisons) ? spec.comparisons : [];
    var comparisonNames = comparisons.map(function(c) { return c && c.name ? c.name : ''; }).filter(Boolean);
    var concText = (typeof spec.conclusions === 'string') ? spec.conclusions : '';
    var concExcerpt = concText.length > 200 ? concText.slice(0, 200) + '…' : concText;
    var statusOptions = ['draft','in-progress','completed','archived'].map(function(opt) {
      var sel = (opt === ovStatus) ? ' selected' : '';
      return '<option value="' + opt + '"' + sel + '>' + opt + '</option>';
    }).join('');
    // Per-variant run breakdown (only show if there's a meaningful breakdown)
    var runsByVariant = {};
    runs.forEach(function(r) {
      var v = (r && (r.variant || r.variant_name)) || '';
      if (v) runsByVariant[v] = (runsByVariant[v] || 0) + 1;
    });
    var breakdownKeys = Object.keys(runsByVariant);
    var runsBreakdown = '';
    if (breakdownKeys.length > 1) {
      runsBreakdown = ' <small>(' + breakdownKeys.map(function(k) {
        return _esc(k) + ': ' + runsByVariant[k];
      }).join(', ') + ')</small>';
    }
    var overviewHtml =
      '<section class="ws-overview-meta">' +
        '<label>Topic' +
          '<input type="text" id="ov-topic" value="' + _esc(ovTopic) + '" ' +
                 'placeholder="e.g., Antibiotic response (optional)">' +
        '</label>' +
        '<label>Question' +
          '<textarea id="ov-question" rows="2">' + _esc(ovQuestion) + '</textarea>' +
        '</label>' +
        '<label>Hypothesis' +
          '<textarea id="ov-hypothesis" rows="2">' + _esc(ovHypothesis) + '</textarea>' +
        '</label>' +
        '<label>Status' +
          '<select id="ov-status">' + statusOptions + '</select>' +
        '</label>' +
      '</section>' +
      '<dl class="ws-overview-list">' +
        '<dt>Baseline</dt>' +
        '<dd>' + _esc(baseline || '—') + ' <small>(' + _esc(baselineSource) + ')</small></dd>' +
        '<dt>Variants</dt>' +
        '<dd>' + variants.length + (variantNames.length ? ' — ' + _esc(variantNames.join(', ')) : '') + '</dd>' +
        '<dt>Runs</dt>' +
        '<dd>' + runs.length + ' total' + runsBreakdown + '</dd>' +
        '<dt>Comparisons</dt>' +
        '<dd>' + comparisons.length + (comparisonNames.length ? ' — ' + _esc(comparisonNames.join(', ')) : '') + '</dd>' +
        '<dt>Visualizations</dt>' +
        '<dd>' + vizFiles.length + '</dd>' +
      '</dl>' +
      '<section class="ws-overview-conclusions">' +
        '<h3>Conclusions excerpt</h3>' +
        (concText.trim()
          ? '<p>' + _esc(concExcerpt) + '</p>'
          : '<p><em>No conclusions yet.</em></p>') +
        '<a href="#" onclick="_invDetailTab(\'conclusions\'); return false;">Read more →</a>' +
      '</section>';

    // Derive a pretty baseline source for the header summary, mirroring
    // server-side `_format_baseline_source`. If the payload already includes
    // `baseline_source` (from the index projection) we reuse it.
    var headerBaseline = data.baseline_source || '';
    if (!headerBaseline && baseline) {
      if (baselineEntry && baselineEntry.source) {
        var rawSrc = baselineEntry.source;
        var idx = rawSrc.indexOf('.composites.');
        if (idx >= 0) {
          headerBaseline = rawSrc.slice(0, idx) + ':' + rawSrc.slice(idx + '.composites.'.length);
        } else {
          headerBaseline = rawSrc;
        }
      } else {
        headerBaseline = baseline;
      }
    }
    if (!headerBaseline) headerBaseline = '—';

    var descHtml = (spec.description && String(spec.description).trim())
      ? '<p class="study-subtitle">' + _esc(spec.description) + '</p>'
      : '';

    // Heroicons outline SVGs reused for tab labels.
    var iconOverview =
      '<svg class="tab-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M3.75 6A2.25 2.25 0 0 1 6 3.75h2.25A2.25 2.25 0 0 1 10.5 6v2.25a2.25 2.25 0 0 1-2.25 2.25H6A2.25 2.25 0 0 1 3.75 8.25V6Zm10 0A2.25 2.25 0 0 1 16 3.75h2.25A2.25 2.25 0 0 1 20.5 6v2.25a2.25 2.25 0 0 1-2.25 2.25H16A2.25 2.25 0 0 1 13.75 8.25V6Zm-10 10A2.25 2.25 0 0 1 6 13.75h2.25a2.25 2.25 0 0 1 2.25 2.25v2.25a2.25 2.25 0 0 1-2.25 2.25H6a2.25 2.25 0 0 1-2.25-2.25V16Zm10 0A2.25 2.25 0 0 1 16 13.75h2.25a2.25 2.25 0 0 1 2.25 2.25v2.25a2.25 2.25 0 0 1-2.25 2.25H16a2.25 2.25 0 0 1-2.25-2.25V16Z"/>' +
      '</svg>';
    var iconBaseline =
      '<svg class="tab-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M9.75 3.104v5.714a2.25 2.25 0 0 1-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 0 1 4.5 0m0 0v5.714c0 .597.237 1.17.659 1.591L19.8 15.3M14.25 3.104c.251.023.501.05.75.082M19.8 15.3l-1.57.393A9.065 9.065 0 0 1 12 15a9.065 9.065 0 0 0-6.23-.693L5 14.5m14.8.8 1.402 1.402c1.232 1.232.65 3.318-1.067 3.611A48.309 48.309 0 0 1 12 21c-2.773 0-5.491-.235-8.135-.687-1.718-.293-2.3-2.379-1.067-3.61L5 14.5"/>' +
      '</svg>';
    var iconGroups =
      '<svg class="tab-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M18 18.72a9.094 9.094 0 0 0 3.741-.479 3 3 0 0 0-4.682-2.72m.94 3.198.001.031c0 .225-.012.447-.037.666A11.944 11.944 0 0 1 12 21c-2.17 0-4.207-.576-5.963-1.584A6.062 6.062 0 0 1 6 18.719m12 0a5.971 5.971 0 0 0-.941-3.197m0 0A5.995 5.995 0 0 0 12 12.75a5.995 5.995 0 0 0-5.058 2.772m0 0a3 3 0 0 0-4.681 2.72 8.986 8.986 0 0 0 3.74.477m.94-3.197a5.971 5.971 0 0 0-.94 3.197M15 6.75a3 3 0 1 1-6 0 3 3 0 0 1 6 0Zm6 3a2.25 2.25 0 1 1-4.5 0 2.25 2.25 0 0 1 4.5 0Zm-13.5 0a2.25 2.25 0 1 1-4.5 0 2.25 2.25 0 0 1 4.5 0Z"/>' +
      '</svg>';
    var iconInterventions =
      '<svg class="tab-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3"/>' +
      '</svg>';
    var iconRuns =
      '<svg class="tab-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M8.25 6.75h12m-12 5.25h12m-12 5.25h12M3.75 6.75h.007v.008H3.75V6.75Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0ZM3.75 12h.007v.008H3.75V12Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm-.375 5.25h.007v.008H3.75v-.008Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Z"/>' +
      '</svg>';
    var iconObservables =
      '<svg class="tab-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M2.036 12.322a1.012 1.012 0 0 1 0-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178Z"/>' +
      '<path d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z"/>' +
      '</svg>';
    var iconViz =
      '<svg class="tab-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 0 1 3 19.875v-6.75ZM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V8.625ZM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V4.125Z"/>' +
      '</svg>';
    var iconConclusions =
      '<svg class="tab-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M2.25 12.76c0 1.6 1.123 2.994 2.707 3.227 1.087.16 2.185.283 3.293.369V21l4.184-4.183a1.14 1.14 0 0 1 .778-.332 48.294 48.294 0 0 0 5.83-.498c1.585-.233 2.708-1.626 2.708-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0 0 12 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018Z"/>' +
      '</svg>';

    detail.innerHTML =
      '<div class="inv-detail-back" style="margin-bottom:12px">' +
        '<a href="#" onclick="_closeInvestigationFocus(); return false;" ' +
           'style="color:#3b82f6; text-decoration:none; font-size:0.9em">' +
          '← Back to all studies' +
        '</a>' +
      '</div>' +
      '<header class="study-header">' +
        '<h2 class="study-title">Study: <span class="study-name">' + _esc(name) + '</span></h2>' +
        descHtml +
        '<dl class="study-summary">' +
          '<div><dt>Baseline</dt><dd><code>' + _esc(headerBaseline) + '</code></dd></div>' +
          '<div><dt>Status</dt><dd class="status-pill ' + statusClass + '">' + _esc(status) + '</dd></div>' +
          '<div><dt>Runs</dt><dd>' + runs.length + '</dd></div>' +
          '<div><dt>Last run</dt><dd>' + _esc(lastRun) + '</dd></div>' +
        '</dl>' +
      '</header>' +
      '<div class="investigation-detail-tabs">' +
        '<button class="investigation-detail-tab active" data-tab="overview" onclick="_invDetailTab(\'overview\')">' +
          iconOverview + '<span class="tab-label">Overview</span></button>' +
        '<button class="investigation-detail-tab" data-tab="composites" onclick="_invDetailTab(\'composites\')">' +
          iconBaseline + '<span class="tab-label">Baseline Composite</span></button>' +
        '<button class="investigation-detail-tab" data-tab="groups" onclick="_invDetailTab(\'groups\')">' +
          iconGroups + '<span class="tab-label">Groups</span></button>' +
        '<button class="investigation-detail-tab" data-tab="interventions" onclick="_invDetailTab(\'interventions\')">' +
          iconInterventions + '<span class="tab-label">Interventions</span></button>' +
        '<button class="investigation-detail-tab" data-tab="runs" onclick="_invDetailTab(\'runs\')">' +
          iconRuns + '<span class="tab-label">Runs</span>' +
          '<span class="tab-count-badge">' + runs.length + '</span></button>' +
        '<button class="investigation-detail-tab" data-tab="observables" onclick="_invDetailTab(\'observables\')">' +
          iconObservables + '<span class="tab-label">Observables</span></button>' +
        '<button class="investigation-detail-tab" data-tab="viz" onclick="_invDetailTab(\'viz\')">' +
          iconViz + '<span class="tab-label">Visualizations</span>' +
          '<span class="tab-count-badge">' + vizFiles.length + '</span></button>' +
        '<button class="investigation-detail-tab" data-tab="conclusions" onclick="_invDetailTab(\'conclusions\')">' +
          iconConclusions + '<span class="tab-label">Conclusions</span></button>' +
      '</div>' +
      '<div class="investigation-detail-panel active" data-tab="overview">' +
        overviewHtml +
      '</div>' +
      '<div class="investigation-detail-panel" data-tab="composites">' +
        '<div style="margin-bottom:8px">' +
          '<button class="action-btn js-authoring" onclick="_openAddCompositeModal()">+ Add composite</button>' +
        '</div>' +
        '<div id="inv-composites-list" style="display:grid;grid-template-columns:220px 1fr;gap:16px">' +
          '<div id="inv-composites-sidebar"></div>' +
          '<div id="inv-composite-detail" style="border-left:1px solid #eee;padding-left:14px">' +
            '<div class="loom-frame-toolbar" style="display:flex;justify-content:flex-end;margin-bottom:6px">' +
              '<button class="btn-mini" onclick="_popoutLoom(\'inv-composite-explore-frame\')" title="Open this wiring view in a separate window">' +
                'Pop out ↗' +
              '</button>' +
            '</div>' +
            '<iframe id="inv-composite-explore-frame"' +
                    ' src="/bigraph-loom/index.html"' +
                    ' title="Composite wiring"' +
                    ' style="width:100%;height:520px;border:1px solid #ddd;background:#fff;display:none">' +
            '</iframe>' +
            '<div id="inv-composite-intervention" style="margin-top:12px;padding:10px;border:1px solid #eee;border-radius:4px;display:none"></div>' +
          '</div>' +
        '</div>' +
      '</div>' +
      '<div class="investigation-detail-panel" data-tab="groups">' +
        '<section class="ws-groups" style="padding:10px">' +
          '<button class="btn-mini js-authoring" style="margin-bottom:8px" onclick="_openAddGroupModal()">+ Add group</button>' +
          '<div id="ws-groups-list"></div>' +
        '</section>' +
      '</div>' +
      '<div class="investigation-detail-panel" data-tab="interventions">' +
        '<div id="inv-interventions-host">' +
          '<p class="empty-state">Loading interventions…</p>' +
        '</div>' +
      '</div>' +
      '<div class="investigation-detail-panel" data-tab="runs">' +
        (runs.length ? _renderInvestigationRunsTable(runs, name) : '<p class="empty-state">No runs yet — click Run to generate them.</p>') +
      '</div>' +
      '<div class="investigation-detail-panel" data-tab="observables">' +
        '<p class="panel-lead">Tick which state paths the simulation should record. Paths missing in a given composite are skipped for that run with a warning.</p>' +
        '<label style="display:block;margin-bottom:10px">' +
          '<input type="checkbox" id="inv-emit-all" onchange="_setEmitAll(this.checked)">' +
          ' Emit entire state (root)' +
        '</label>' +
        '<div id="inv-observables-tree" style="font-family:monospace;font-size:0.9em"></div>' +
        '<button class="action-btn js-authoring" onclick="_saveObservables()">Save observables</button>' +
        '<div id="inv-observables-status" style="margin-top:8px;font-size:0.9em;color:#555"></div>' +
      '</div>' +
      '<div class="investigation-detail-panel" data-tab="viz">' +
        '<section class="ws-comparisons" style="margin-bottom:16px;padding:10px;border:1px solid #eee">' +
          '<h3 style="margin-top:0">Comparisons</h3>' +
          '<div id="ws-comparisons-list"></div>' +
          '<button class="btn-mini js-authoring" onclick="_openAddComparisonModal()">+ Add comparison</button>' +
        '</section>' +
        (vizFiles.length ?
          '<button class="btn-mini js-authoring" style="margin-bottom:8px" onclick="_openAddVizModal(\'' + _esc(name) + '\')">+ Add visualization</button>' +
          vizFiles.map(function(v) {
            return '<h4 style="margin-bottom:4px">' + _esc(v.name) + '</h4>' +
                   '<iframe class="viz-frame" src="/' + _esc(v.path) + '?ts=' + Date.now() + '"></iframe>';
          }).join('') :
          '<p class="empty-state">No visualizations declared in <code>spec.yaml</code> yet. ' +
            'Click <em>Add visualization</em> to scaffold one, or edit ' +
            '<code>investigations/' + _esc(name) + '/spec.yaml</code> directly and click <em>Run</em>.</p>' +
          '<button class="action-btn js-authoring" onclick="_openAddVizModal(\'' + _esc(name) + '\')">+ Add visualization</button>') +
      '</div>' +
      '<div class="investigation-detail-panel" data-tab="conclusions">' +
        '<div class="ws-conclusions" style="padding:10px">' +
          '<label style="display:block;margin-bottom:8px">' +
            '<strong>Claims</strong>' +
            '<textarea id="cn-claims" rows="6" style="width:100%;font-family:monospace"></textarea>' +
          '</label>' +
          '<label style="display:block;margin-bottom:8px">' +
            '<strong>Evidence</strong>' +
            '<textarea id="cn-evidence" rows="6" style="width:100%;font-family:monospace"></textarea>' +
          '</label>' +
          '<label style="display:block;margin-bottom:8px">' +
            '<strong>Limitations</strong>' +
            '<textarea id="cn-limitations" rows="6" style="width:100%;font-family:monospace"></textarea>' +
          '</label>' +
          '<label style="display:block;margin-bottom:8px">' +
            '<strong>Next steps</strong>' +
            '<textarea id="cn-next-steps" rows="6" style="width:100%;font-family:monospace"></textarea>' +
          '</label>' +
          '<button class="btn-primary js-authoring" onclick="_saveConclusions()">Save</button>' +
          '<h4 style="margin-top:16px">Raw markdown (combined)</h4>' +
          '<pre id="conclusions-preview" style="background:#f5f5f5;padding:10px;white-space:pre-wrap;font-family:monospace"></pre>' +
        '</div>' +
      '</div>';

    // ── Overview-tab auto-save wiring (B2) ────────────────────────────────────
    var tEl = document.getElementById('ov-topic');
    if (tEl) {
      tEl.addEventListener('blur', function() {
        _saveOverviewField(name, 'topic', tEl.value);
        // Topic change can re-group the Investigations rail, so refresh it.
        if (typeof _vivRefreshInvestigationsRail === 'function') {
          _vivRefreshInvestigationsRail();
        }
      });
    }
    var qEl = document.getElementById('ov-question');
    if (qEl) {
      qEl.addEventListener('blur', function() {
        _saveOverviewField(name, 'question', qEl.value);
      });
    }
    var hEl = document.getElementById('ov-hypothesis');
    if (hEl) {
      hEl.addEventListener('blur', function() {
        _saveOverviewField(name, 'hypothesis', hEl.value);
      });
    }
    var sEl = document.getElementById('ov-status');
    if (sEl) {
      sEl.value = (spec.status || 'draft');
      sEl.addEventListener('change', function() {
        _saveOverviewField(name, 'status', sEl.value);
      });
    }
    // Render the Comparisons sub-panel (Visualizations tab).
    _renderComparisonsTable(name, data);
    // Render the Groups tab (B7).
    _renderGroupsTab(name, data);

    // ── Conclusions-tab wiring (B6) ───────────────────────────────────────────
    _loadConclusionsIntoTextareas(spec.conclusions || '');
    for (var k = 0; k < _CONCL_IDS.length; k++) {
      var ta = document.getElementById(_CONCL_IDS[k]);
      if (ta) ta.addEventListener('input', _updateConclusionsPreview);
    }
  }

  function _saveOverviewField(invName, key, value) {
    var body = { investigation: invName, fields: {} };
    body.fields[key] = value;
    fetch('/api/investigation-set-overview', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    })
      .then(function(r) {
        if (!r.ok) {
          return r.json().then(function(j) { alert(j.error || 'save failed'); });
        }
        if (typeof _showToast === 'function') _showToast('Saved ' + key);
      })
      .catch(function(e) { alert('Network error: ' + e); });
  }
  window._saveOverviewField = _saveOverviewField;

  // ── Conclusions tab (B6): 4-section textareas + Save ─────────────────────

  var _CONCL_SECTIONS = ['Claims', 'Evidence', 'Limitations', 'Next steps'];
  var _CONCL_IDS      = ['cn-claims', 'cn-evidence', 'cn-limitations', 'cn-next-steps'];

  function _loadConclusionsIntoTextareas(blob) {
    var map = { Claims: '', Evidence: '', Limitations: '', 'Next steps': '' };
    var current = 'Claims';   // free-form fallback
    var lines = (blob || '').split('\n');
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      var m = line.match(/^##\s+(Claims|Evidence|Limitations|Next steps)\s*$/i);
      if (m) {
        var canon = _CONCL_SECTIONS.find(function(s) { return s.toLowerCase() === m[1].toLowerCase(); });
        current = canon || 'Claims';
        continue;
      }
      map[current] += line + '\n';
    }
    for (var j = 0; j < _CONCL_SECTIONS.length; j++) {
      var el = document.getElementById(_CONCL_IDS[j]);
      if (el) el.value = (map[_CONCL_SECTIONS[j]] || '').replace(/\s+$/, '');
    }
    _updateConclusionsPreview();
  }

  function _emitConclusionsBlob() {
    return _CONCL_SECTIONS.map(function(s, i) {
      var body = (document.getElementById(_CONCL_IDS[i]) || {}).value || '';
      return '## ' + s + '\n\n' + body.trim();
    }).join('\n\n');
  }

  function _updateConclusionsPreview() {
    var pre = document.getElementById('conclusions-preview');
    if (pre) pre.textContent = _emitConclusionsBlob();
  }

  function _saveConclusions() {
    var invName = window._currentInvestigation;
    if (!invName) return;
    var blob = _emitConclusionsBlob();
    fetch('/api/investigation-set-conclusions', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({investigation: invName, markdown: blob}),
    })
      .then(function(r) {
        if (!r.ok) return r.json().then(function(j) { alert(j.error || 'save failed'); });
        if (typeof _showToast === 'function') _showToast('Saved conclusions');
      })
      .catch(function(e) { alert('Network error: ' + e); });
  }
  window._saveConclusions = _saveConclusions;

  // ── Comparisons sub-panel (Visualizations tab, Task B5) ──────────────────

  function _obsPath(o) {
    // Tolerate both v2 dict-shape ({path:[...]}) and legacy bare-string entries.
    if (o && typeof o === 'object' && Array.isArray(o.path)) {
      return o.path.join('/');
    }
    return String(o == null ? '' : o);
  }

  function _renderComparisonsTable(invName, data) {
    var listEl = document.getElementById('ws-comparisons-list');
    if (!listEl) return;
    var spec = (data && data.spec) || window._invSpecCache || {};
    var comparisons = Array.isArray(spec.comparisons) ? spec.comparisons : [];
    if (!comparisons.length) {
      listEl.innerHTML = '<p class="empty-state">No comparisons yet.</p>';
      return;
    }
    listEl.innerHTML = comparisons.map(function(c) {
      var cname = c && c.name ? String(c.name) : '';
      var vCsv = (c.variants || []).map(function(v) { return String(v); }).join(', ');
      var oCsv = (c.observables || []).map(function(o) { return _obsPath(o); }).join(', ');
      var nameAttr = cname.replace(/'/g, "\\'");
      return (
        '<div class="ws-comparison-row" data-name="' + _esc(cname) + '"' +
            ' style="padding:6px 0;border-bottom:1px solid #f0f0f0">' +
          '<strong>' + _esc(cname) + '</strong> ' +
          '<small class="muted">variants: ' + _esc(vCsv || '—') +
            ' · observables: ' + _esc(oCsv || '—') + '</small> ' +
          '<button class="btn-mini" onclick="_openEditComparisonModal(\'' + _esc(nameAttr) + '\')">Edit</button> ' +
          '<button class="btn-mini" style="color:#c00"' +
            ' onclick="_deleteComparison(\'' + _esc(nameAttr) + '\')">Remove</button>' +
        '</div>'
      );
    }).join('');
  }
  window._renderComparisonsTable = _renderComparisonsTable;

  function _closeComparisonModal() {
    var el = document.getElementById('modal-comparison-edit');
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }
  window._closeComparisonModal = _closeComparisonModal;

  function _openAddComparisonModal() {
    _openComparisonModal(null);
  }
  window._openAddComparisonModal = _openAddComparisonModal;

  function _openEditComparisonModal(cmpName) {
    var spec = window._invSpecCache || {};
    var comparisons = Array.isArray(spec.comparisons) ? spec.comparisons : [];
    var existing = null;
    for (var i = 0; i < comparisons.length; i++) {
      if (comparisons[i] && comparisons[i].name === cmpName) {
        existing = comparisons[i];
        break;
      }
    }
    _openComparisonModal(existing);
  }
  window._openEditComparisonModal = _openEditComparisonModal;

  function _openComparisonModal(existing) {
    _closeComparisonModal();
    var spec = window._invSpecCache || {};
    var variants = Array.isArray(spec.variants) ? spec.variants : [];
    var observables = Array.isArray(spec.observables) ? spec.observables : [];
    var isEdit = !!existing;
    var initName = isEdit ? String(existing.name || '') : '';
    var initDesc = isEdit ? String(existing.description || '') : '';
    var pickedVariants = {};
    (isEdit ? (existing.variants || []) : []).forEach(function(v) {
      pickedVariants[String(v)] = true;
    });
    var pickedObs = {};
    (isEdit ? (existing.observables || []) : []).forEach(function(o) {
      pickedObs[_obsPath(o)] = true;
    });

    var variantBoxes = variants.length
      ? variants.map(function(v, i) {
          var vname = (v && v.name) ? String(v.name) : '';
          var checked = pickedVariants[vname] ? ' checked' : '';
          var id = 'cmp-variant-' + i;
          return (
            '<label style="display:block;font-weight:normal">' +
              '<input type="checkbox" class="cmp-variant-cb" value="' + _esc(vname) +
                '" id="' + _esc(id) + '"' + checked + '> ' +
              _esc(vname) +
            '</label>'
          );
        }).join('')
      : '<p class="muted" style="margin:4px 0">No variants in the study yet.</p>';

    var obsEmpty = (observables.length === 0);
    var obsBoxes = obsEmpty
      ? '<p class="muted" style="margin:4px 0">No observables in the study yet — add some via the ' +
        'Composites tab or by editing the spec.yaml directly.</p>'
      : observables.map(function(o, i) {
          var path = _obsPath(o);
          var checked = pickedObs[path] ? ' checked' : '';
          var id = 'cmp-obs-' + i;
          return (
            '<label style="display:block;font-weight:normal">' +
              '<input type="checkbox" class="cmp-obs-cb" value="' + _esc(path) +
                '" id="' + _esc(id) + '"' + checked + '> ' +
              '<code>' + _esc(path) + '</code>' +
            '</label>'
          );
        }).join('');

    var modal = document.createElement('div');
    modal.id = 'modal-comparison-edit';
    modal.className = 'modal-overlay';
    modal.style.display = 'flex';
    modal.innerHTML =
      '<div class="modal-box">' +
        '<button class="modal-close" onclick="_closeComparisonModal()">&times;</button>' +
        '<h3>' + (isEdit ? 'Edit comparison' : 'Add comparison') + '</h3>' +
        '<label>Name' +
          '<input type="text" id="cmp-name" value="' + _esc(initName) + '"' +
            (isEdit ? ' disabled' : ' required pattern="[a-zA-Z0-9_-]+"') + '>' +
        '</label>' +
        '<label>Description' +
          '<input type="text" id="cmp-description" value="' + _esc(initDesc) + '">' +
        '</label>' +
        '<label>Variants</label>' +
        '<div id="cmp-variants-list" style="max-height:160px;overflow:auto;padding:4px;border:1px solid #eee;margin-bottom:6px">' +
          variantBoxes +
        '</div>' +
        '<label>Observables</label>' +
        '<div id="cmp-observables-list" style="max-height:160px;overflow:auto;padding:4px;border:1px solid #eee;margin-bottom:6px">' +
          obsBoxes +
        '</div>' +
        '<div class="form-error" id="cmp-form-error" style="color:#c00;min-height:1em"></div>' +
        '<div style="margin-top:8px">' +
          '<button type="button" class="action-btn" id="cmp-save-btn"' +
            (obsEmpty ? ' disabled' : '') + '>Save</button> ' +
          '<button type="button" class="btn-mini" onclick="_closeComparisonModal()">Cancel</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(modal);

    var saveBtn = document.getElementById('cmp-save-btn');
    if (saveBtn) {
      saveBtn.addEventListener('click', function() {
        _submitComparisonModal(isEdit, initName);
      });
    }
  }

  function _submitComparisonModal(isEdit, lockedName) {
    var errEl = document.getElementById('cmp-form-error');
    if (errEl) errEl.textContent = '';
    var nameEl = document.getElementById('cmp-name');
    var descEl = document.getElementById('cmp-description');
    var cmpName = isEdit ? lockedName : (nameEl ? nameEl.value.trim() : '');
    if (!cmpName) {
      if (errEl) errEl.textContent = 'Name is required.';
      return;
    }
    if (!isEdit && !/^[a-zA-Z0-9_-]+$/.test(cmpName)) {
      if (errEl) errEl.textContent = 'Name must match [a-zA-Z0-9_-]+';
      return;
    }
    var variants = Array.prototype.map.call(
      document.querySelectorAll('.cmp-variant-cb:checked'),
      function(cb) { return cb.value; }
    );
    var observables = Array.prototype.map.call(
      document.querySelectorAll('.cmp-obs-cb:checked'),
      function(cb) { return cb.value; }
    );
    if (!variants.length) {
      if (errEl) errEl.textContent = 'Select at least one variant.';
      return;
    }
    if (!observables.length) {
      if (errEl) errEl.textContent = 'Select at least one observable.';
      return;
    }
    var description = descEl ? descEl.value : '';
    _saveComparison(cmpName, {
      description: description,
      variants: variants,
      observables: observables,
    }, isEdit);
  }

  function _saveComparison(cmpName, fields, isEdit) {
    var invName = window._currentInvestigation;
    if (!invName) {
      var errEl0 = document.getElementById('cmp-form-error');
      if (errEl0) errEl0.textContent = 'No active investigation.';
      return;
    }
    var url, body;
    if (isEdit) {
      url = '/api/investigation-comparison-update';
      body = {
        investigation: invName,
        name: cmpName,
        fields_to_update: {
          description: fields.description,
          variants: fields.variants,
          observables: fields.observables,
        },
      };
    } else {
      url = '/api/investigation-comparison-add';
      body = {
        investigation: invName,
        name: cmpName,
        description: fields.description,
        variants: fields.variants,
        observables: fields.observables,
      };
    }
    fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    })
      .then(function(r) {
        return r.json().then(function(j) { return {ok: r.ok, body: j}; });
      })
      .then(function(res) {
        var errEl = document.getElementById('cmp-form-error');
        if (!res.ok) {
          if (errEl) errEl.textContent = (res.body && res.body.error) || 'save failed';
          return;
        }
        _closeComparisonModal();
        if (typeof _showToast === 'function') {
          _showToast((isEdit ? 'Updated' : 'Added') + ' comparison "' + cmpName + '"');
        }
        _openInvestigation(invName);  // re-fetch + re-render
      })
      .catch(function(err) {
        var errEl = document.getElementById('cmp-form-error');
        if (errEl) errEl.textContent = 'Network error: ' + err;
      });
  }
  window._saveComparison = _saveComparison;

  function _deleteComparison(cmpName) {
    var invName = window._currentInvestigation;
    if (!invName) return;
    if (!confirm('Remove comparison "' + cmpName + '"?')) return;
    fetch('/api/investigation-comparison', {
      method: 'DELETE',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({investigation: invName, name: cmpName}),
    })
      .then(function(r) {
        return r.json().then(function(j) { return {ok: r.ok, status: r.status, body: j}; });
      })
      .then(function(res) {
        if (!res.ok) {
          var msg = (res.body && res.body.error) || ('delete failed (' + res.status + ')');
          // 409 → dependent visualizations; surface the message inline at the
          // top of the comparisons list so the user sees which vizzes block it.
          var listEl = document.getElementById('ws-comparisons-list');
          if (listEl) {
            var banner = document.createElement('div');
            banner.style.cssText = 'color:#c00;padding:6px;margin-bottom:6px;border:1px solid #fbb;background:#fff5f5';
            banner.textContent = msg;
            listEl.insertBefore(banner, listEl.firstChild);
            setTimeout(function() {
              if (banner.parentNode) banner.parentNode.removeChild(banner);
            }, 8000);
          } else {
            alert(msg);
          }
          return;
        }
        if (typeof _showToast === 'function') {
          _showToast('Removed comparison "' + cmpName + '"');
        }
        _openInvestigation(invName);  // re-fetch + re-render
      })
      .catch(function(err) { alert('Network error: ' + err); });
  }
  window._deleteComparison = _deleteComparison;

  // ── Groups tab (B7) ──────────────────────────────────────────────────────

  function _renderGroupsTab(invName, data) {
    var listEl = document.getElementById('ws-groups-list');
    if (!listEl) return;
    var spec = (data && data.spec) || window._invSpecCache || {};
    var groups = Array.isArray(spec.groups) ? spec.groups : [];
    if (!groups.length) {
      listEl.innerHTML = '<p class="empty-state">No groups yet. ' +
        'Add a group to label your experimental conditions.</p>';
      return;
    }
    listEl.innerHTML = groups.map(function(g) {
      var gname = g && g.name ? String(g.name) : '';
      var gvariants = Array.isArray(g.variants) ? g.variants.map(String) : [];
      var vCsv = gvariants.join(', ');
      var desc = (g && g.description) ? String(g.description) : '';
      var nameAttr = gname.replace(/'/g, "\\'");
      return (
        '<div class="ws-group-row" data-name="' + _esc(gname) + '"' +
            ' style="padding:6px;border-bottom:1px solid #eee">' +
          '<strong>' + _esc(gname) + '</strong> ' +
          '<small class="muted">' + gvariants.length + ' variant(s): ' +
            _esc(vCsv || '—') + '</small>' +
          '<div>' + _esc(desc) + '</div>' +
          '<button class="btn-mini" onclick="_openEditGroupModal(\'' + _esc(nameAttr) + '\')">Edit</button> ' +
          '<button class="btn-mini" style="color:#c00"' +
            ' onclick="_deleteGroup(\'' + _esc(nameAttr) + '\')">Remove</button>' +
        '</div>'
      );
    }).join('');
  }
  window._renderGroupsTab = _renderGroupsTab;

  function _closeGroupModal() {
    var el = document.getElementById('modal-group-edit');
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }
  window._closeGroupModal = _closeGroupModal;

  function _openAddGroupModal() {
    _openGroupModal(null);
  }
  window._openAddGroupModal = _openAddGroupModal;

  function _openEditGroupModal(grpName) {
    var spec = window._invSpecCache || {};
    var groups = Array.isArray(spec.groups) ? spec.groups : [];
    var existing = null;
    for (var i = 0; i < groups.length; i++) {
      if (groups[i] && groups[i].name === grpName) {
        existing = groups[i];
        break;
      }
    }
    _openGroupModal(existing);
  }
  window._openEditGroupModal = _openEditGroupModal;

  function _openGroupModal(existing) {
    _closeGroupModal();
    var spec = window._invSpecCache || {};
    var variants = Array.isArray(spec.variants) ? spec.variants : [];
    var isEdit = !!existing;
    var initName = isEdit ? String(existing.name || '') : '';
    var initDesc = isEdit ? String(existing.description || '') : '';
    var pickedVariants = {};
    (isEdit ? (existing.variants || []) : []).forEach(function(v) {
      pickedVariants[String(v)] = true;
    });

    var variantBoxes = variants.length
      ? variants.map(function(v, i) {
          var vname = (v && v.name) ? String(v.name) : '';
          var checked = pickedVariants[vname] ? ' checked' : '';
          var id = 'grp-variant-' + i;
          return (
            '<label style="display:block;font-weight:normal">' +
              '<input type="checkbox" class="grp-variant-cb" value="' + _esc(vname) +
                '" id="' + _esc(id) + '"' + checked + '> ' +
              _esc(vname) +
            '</label>'
          );
        }).join('')
      : '<p class="muted" style="margin:4px 0">No variants in the study yet.</p>';

    var modal = document.createElement('div');
    modal.id = 'modal-group-edit';
    modal.className = 'modal-overlay';
    modal.style.display = 'flex';
    modal.innerHTML =
      '<div class="modal-box">' +
        '<button class="modal-close" onclick="_closeGroupModal()">&times;</button>' +
        '<h3>' + (isEdit ? 'Edit group' : 'Add group') + '</h3>' +
        '<label>Name' +
          '<input type="text" id="grp-name" value="' + _esc(initName) + '"' +
            (isEdit ? ' disabled' : ' required pattern="[a-zA-Z0-9_-]+"') + '>' +
        '</label>' +
        '<label>Description' +
          '<input type="text" id="grp-description" value="' + _esc(initDesc) + '">' +
        '</label>' +
        '<label>Variants</label>' +
        '<div id="grp-variants-list" style="max-height:160px;overflow:auto;padding:4px;border:1px solid #eee;margin-bottom:6px">' +
          variantBoxes +
        '</div>' +
        '<div class="form-error" id="grp-form-error" style="color:#c00;min-height:1em"></div>' +
        '<div style="margin-top:8px">' +
          '<button type="button" class="action-btn" id="grp-save-btn"' +
            (variants.length ? '' : ' disabled') + '>Save</button> ' +
          '<button type="button" class="btn-mini" onclick="_closeGroupModal()">Cancel</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(modal);

    var saveBtn = document.getElementById('grp-save-btn');
    if (saveBtn) {
      saveBtn.addEventListener('click', function() {
        _submitGroupModal(isEdit, initName);
      });
    }
  }

  function _submitGroupModal(isEdit, lockedName) {
    var errEl = document.getElementById('grp-form-error');
    if (errEl) errEl.textContent = '';
    var nameEl = document.getElementById('grp-name');
    var descEl = document.getElementById('grp-description');
    var grpName = isEdit ? lockedName : (nameEl ? nameEl.value.trim() : '');
    if (!grpName) {
      if (errEl) errEl.textContent = 'Name is required.';
      return;
    }
    if (!isEdit && !/^[a-zA-Z0-9_-]+$/.test(grpName)) {
      if (errEl) errEl.textContent = 'Name must match [a-zA-Z0-9_-]+';
      return;
    }
    var variants = Array.prototype.map.call(
      document.querySelectorAll('.grp-variant-cb:checked'),
      function(cb) { return cb.value; }
    );
    if (!variants.length) {
      if (errEl) errEl.textContent = 'Select at least one variant.';
      return;
    }
    var description = descEl ? descEl.value : '';
    _saveGroup(grpName, {
      description: description,
      variants: variants,
    }, isEdit);
  }

  function _saveGroup(grpName, fields, isEdit) {
    var invName = window._currentInvestigation;
    if (!invName) {
      var errEl0 = document.getElementById('grp-form-error');
      if (errEl0) errEl0.textContent = 'No active investigation.';
      return;
    }
    var url, body;
    if (isEdit) {
      url = '/api/investigation-group-update';
      body = {
        investigation: invName,
        name: grpName,
        fields_to_update: {
          description: fields.description,
          variants: fields.variants,
        },
      };
    } else {
      url = '/api/investigation-group-add';
      body = {
        investigation: invName,
        name: grpName,
        description: fields.description,
        variants: fields.variants,
      };
    }
    fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    })
      .then(function(r) {
        return r.json().then(function(j) { return {ok: r.ok, body: j}; });
      })
      .then(function(res) {
        var errEl = document.getElementById('grp-form-error');
        if (!res.ok) {
          if (errEl) errEl.textContent = (res.body && res.body.error) || 'save failed';
          return;
        }
        _closeGroupModal();
        if (typeof _showToast === 'function') {
          _showToast((isEdit ? 'Updated' : 'Added') + ' group "' + grpName + '"');
        }
        _openInvestigation(invName);  // re-fetch + re-render
      })
      .catch(function(err) {
        var errEl = document.getElementById('grp-form-error');
        if (errEl) errEl.textContent = 'Network error: ' + err;
      });
  }
  window._saveGroup = _saveGroup;

  function _deleteGroup(grpName) {
    var invName = window._currentInvestigation;
    if (!invName) return;
    if (!confirm('Remove group "' + grpName + '"?')) return;
    fetch('/api/investigation-group', {
      method: 'DELETE',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({investigation: invName, name: grpName}),
    })
      .then(function(r) {
        return r.json().then(function(j) { return {ok: r.ok, status: r.status, body: j}; });
      })
      .then(function(res) {
        if (!res.ok) {
          var msg = (res.body && res.body.error) || ('delete failed (' + res.status + ')');
          alert(msg);
          return;
        }
        if (typeof _showToast === 'function') {
          _showToast('Removed group "' + grpName + '"');
        }
        _openInvestigation(invName);  // re-fetch + re-render
      })
      .catch(function(err) { alert('Network error: ' + err); });
  }
  window._deleteGroup = _deleteGroup;

  function _invDetailTab(tab) {
    document.querySelectorAll('.investigation-detail-tab').forEach(function(b) {
      b.classList.toggle('active', b.dataset.tab === tab);
    });
    document.querySelectorAll('.investigation-detail-panel').forEach(function(p) {
      p.classList.toggle('active', p.dataset.tab === tab);
    });
    if (tab === 'composites' && window._currentInvestigation) {
      _loadInvComposites(window._currentInvestigation);
    }
    if (tab === 'observables' && window._currentInvestigation) {
      _loadInvObservables(window._currentInvestigation);
    }
    if (tab === 'interventions' && window._currentInvestigation) {
      _loadInterventionsTab(window._currentInvestigation);
    }
    if (tab === 'groups' && window._currentInvestigation) {
      // Re-render from the cached spec so no re-fetch is needed.
      _renderGroupsTab(window._currentInvestigation, {spec: window._invSpecCache || {}});
    }
  }
  window._invDetailTab = _invDetailTab;

  // ── Investigation Composites tab handlers ─────────────────────────────────

  function _loadInvComposites(invName) {
    fetch('/api/investigation-composites?investigation=' + encodeURIComponent(invName))
      .then(function(r) { return r.json(); })
      .then(function(data) {
        var sidebar = document.getElementById('inv-composites-sidebar');
        if (!sidebar) return;
        var entries = data.composites || [];
        window._invCompositesCache = entries;
        if (entries.length === 0) {
          sidebar.innerHTML = '<p class="empty-state">No composites yet — click + Add composite.</p>';
          var frame = document.getElementById('inv-composite-explore-frame');
          if (frame) frame.style.display = 'none';
          var panel0 = document.getElementById('inv-composite-intervention');
          if (panel0) panel0.style.display = 'none';
          return;
        }
        sidebar.innerHTML = entries.map(function(c) {
          var subtitle = c.extends
            ? '<small>extends <code>' + _esc(c.extends) + '</code></small>'
            : '<small>' + _esc(c.source || '') + '</small>';
          var isBaseline = (c.name === (window._invBaselineCache || ''));
          var alreadyPromoted = c.promoted === true;
          var promoteBtn = (!isBaseline && !alreadyPromoted)
            ? '<button class="btn-mini" onclick="event.stopPropagation();_openPromoteModal(\'' +
                _esc(invName) + '\',\'' + _esc(c.name) + '\')">Promote</button>'
            : (alreadyPromoted
                ? '<span class="badge" style="color:#080;margin-left:4px">&#10003; Promoted</span>'
                : '');
          return '<div class="inv-composite-row" style="padding:6px;border-bottom:1px solid #eee;cursor:pointer"' +
                 ' onclick="_loadInvCompositeDetail(\'' + _esc(invName) + '\',\'' + _esc(c.name) + '\')">' +
                 '<strong>' + _esc(c.name) + '</strong><br>' + subtitle +
                 '<div style="margin-top:4px">' +
                 '<button class="btn-mini" onclick="event.stopPropagation();_openPerturbModal(\'' +
                   _esc(invName) + '\',\'' + _esc(c.name) + '\')">Perturb</button>' +
                 (c.extends
                   ? '<button class="btn-mini" onclick="event.stopPropagation();_rebuildComposite(\'' +
                     _esc(invName) + '\',\'' + _esc(c.name) + '\')">Rebuild</button>'
                   : '') +
                 promoteBtn +
                 '<button class="btn-mini" style="color:#c00" onclick="event.stopPropagation();_removeComposite(\'' +
                   _esc(invName) + '\',\'' + _esc(c.name) + '\')">Remove</button>' +
                 '</div></div>';
        }).join('');
        // Auto-load first composite's detail
        _loadInvCompositeDetail(invName, entries[0].name);
      });
  }
  window._loadInvComposites = _loadInvComposites;

  function _loadInvCompositeDetail(invName, compName) {
    _renderInvCompositeIntervention(compName);
    fetch('/api/investigation-composite-doc?investigation=' + encodeURIComponent(invName) +
          '&composite=' + encodeURIComponent(compName))
      .then(function(r) { return r.json(); })
      .then(function(data) {
        var iframe = document.getElementById('inv-composite-explore-frame');
        if (!iframe) return;
        if (data.error) {
          console.error('investigation-composite-doc error:', data.error);
          return;
        }
        // Show the iframe before posting so it has a layout.
        iframe.style.display = '';
        var payload = {
          type: 'composite:load',
          state: data.state,
          metadata: { name: compName, id: compName, context: 'investigation:' + invName },
        };
        window._loomLastState = window._loomLastState || {};
        window._loomLastState[iframe.id] = payload;
        var post = function() {
          iframe.contentWindow.postMessage(payload, '*');
        };
        if (window._loomExploreReady && window._loomExploreReady[iframe.id]) {
          post();
        } else {
          var listener = function(ev) {
            if (ev.source === iframe.contentWindow && ev.data && ev.data.type === 'explore:ready') {
              window._loomExploreReady = window._loomExploreReady || {};
              window._loomExploreReady[iframe.id] = true;
              window.removeEventListener('message', listener);
              post();
            }
          };
          window.addEventListener('message', listener);
        }
      })
      .catch(function(err) { console.error('inv composite load failed:', err); });
  }
  window._loadInvCompositeDetail = _loadInvCompositeDetail;

  function _renderInvCompositeIntervention(compName) {
    var panel = document.getElementById('inv-composite-intervention');
    if (!panel) return;
    var entries = window._invCompositesCache || [];
    var entry = null;
    for (var ei = 0; ei < entries.length; ei++) {
      if (entries[ei] && entries[ei].name === compName) { entry = entries[ei]; break; }
    }
    var baseline = window._invBaselineCache || '';
    panel.style.display = '';
    if (compName === baseline) {
      panel.innerHTML = '<strong>Intervention:</strong> <em>(none — this is the baseline)</em>';
      return;
    }
    var iv = entry && entry.intervention;
    if (!iv) {
      panel.innerHTML = '<strong>Intervention:</strong> <em>(no intervention recipe stored)</em>';
      return;
    }
    var rows = [];
    rows.push('<strong>Intervention:</strong> ' +
      (iv.description ? '"' + _esc(iv.description) + '"' : '<em>(no description)</em>'));
    var params = iv.parameter_overrides || {};
    var paramKeys = Object.keys(params);
    if (paramKeys.length) {
      rows.push('<div style="margin-left:12px"><em>parameter_overrides:</em><br>' +
        paramKeys.map(function(k) {
          return '&nbsp;&nbsp;<code>' + _esc(k) + '</code>: ' + _esc(JSON.stringify(params[k]));
        }).join('<br>') + '</div>');
    }
    var procs = iv.process_overrides || {};
    var procKeys = Object.keys(procs);
    if (procKeys.length) {
      rows.push('<div style="margin-left:12px"><em>process_overrides:</em><br>' +
        procKeys.map(function(k) {
          var v = procs[k] === null ? '<em>(remove)</em>' : _esc(JSON.stringify(procs[k]));
          return '&nbsp;&nbsp;<code>' + _esc(k) + '</code>: ' + v;
        }).join('<br>') + '</div>');
    }
    rows.push('<div style="margin-top:8px"><button class="btn-mini" onclick="window._interventionsJumpTo=\'' +
      _esc(compName) + '\'; _invDetailTab(\'interventions\');">Edit in Interventions tab →</button></div>');
    panel.innerHTML = rows.join('<br>');
  }
  window._renderInvCompositeIntervention = _renderInvCompositeIntervention;

  // ── Interventions tab (B4) ────────────────────────────────────────────────
  // Reads from `window._invCompositesCache` (populated by `_loadInvComposites`)
  // and `window._invBaselineCache` to know which variant is baseline.
  // Renders a table of non-baseline variants; row click expands an inline
  // editor; Save POSTs to `/api/investigation-composite-perturb` which
  // replaces the existing variant in v2 spec shape.

  function _loadInterventionsTab(invName) {
    var entries = window._invCompositesCache || null;
    if (entries && entries.length) {
      _renderInterventionsTab(invName, entries);
      return;
    }
    // Cache miss — fetch and then render.
    fetch('/api/investigation-composites?investigation=' + encodeURIComponent(invName))
      .then(function(r) { return r.json(); })
      .then(function(data) {
        var list = (data && data.composites) || [];
        window._invCompositesCache = list;
        _renderInterventionsTab(invName, list);
      })
      .catch(function(err) {
        var host = document.getElementById('inv-interventions-host');
        if (host) host.innerHTML = '<p style="color:#c00">Failed to load: ' + _esc(err) + '</p>';
      });
  }
  window._loadInterventionsTab = _loadInterventionsTab;

  function _renderInterventionsTab(invName, entries) {
    var host = document.getElementById('inv-interventions-host');
    if (!host) return;
    var baseline = window._invBaselineCache || '';
    var nonBaseline = entries.filter(function(e) {
      return e && e.name && e.name !== baseline;
    });
    if (nonBaseline.length === 0) {
      host.innerHTML =
        '<p class="empty-state">No interventions yet. ' +
        'Add a variant by clicking <em>Perturb</em> on the baseline in the Composites tab.</p>';
      return;
    }
    var rows = nonBaseline.map(function(v) {
      var iv = v.intervention || {};
      var pCount = Object.keys(iv.parameter_overrides || {}).length;
      var prCount = Object.keys(iv.process_overrides || {}).length;
      var nameJs = _esc(v.name).replace(/'/g, '&#39;');
      return (
        '<tr class="inv-iv-row" data-name="' + _esc(v.name) + '" style="cursor:pointer">' +
          '<td><strong>' + _esc(v.name) + '</strong></td>' +
          '<td><code>' + _esc(v.extends || '—') + '</code></td>' +
          '<td>' + (iv.description ? _esc(iv.description) : '<em class="muted">—</em>') + '</td>' +
          '<td>' + (pCount ? (pCount + ' key' + (pCount === 1 ? '' : 's')) : '<em class="muted">—</em>') + '</td>' +
          '<td>' + (prCount ? (prCount + ' key' + (prCount === 1 ? '' : 's')) : '<em class="muted">—</em>') + '</td>' +
        '</tr>' +
        '<tr class="inv-iv-edit" data-name="' + _esc(v.name) + '" style="display:none">' +
          '<td colspan="5" id="inv-iv-edit-' + _esc(v.name) + '"></td>' +
        '</tr>'
      );
    }).join('');
    host.innerHTML =
      '<table class="inv-interventions" style="width:100%;border-collapse:collapse">' +
        '<thead>' +
          '<tr style="border-bottom:1px solid #ccc;text-align:left">' +
            '<th style="padding:6px">Variant</th>' +
            '<th style="padding:6px">Parent</th>' +
            '<th style="padding:6px">Description</th>' +
            '<th style="padding:6px">Param overrides</th>' +
            '<th style="padding:6px">Process overrides</th>' +
          '</tr>' +
        '</thead>' +
        '<tbody>' + rows + '</tbody>' +
      '</table>';
    // Wire row click → expand editor
    Array.prototype.forEach.call(host.querySelectorAll('.inv-iv-row'), function(tr) {
      tr.addEventListener('click', function() {
        var nm = tr.getAttribute('data-name');
        _toggleInterventionEditor(invName, nm);
      });
    });
    // Auto-expand if requested via the Composites-tab jump button
    var jumpTo = window._interventionsJumpTo;
    if (jumpTo) {
      window._interventionsJumpTo = null;
      // Defer to next tick so the DOM is settled before we click-toggle.
      setTimeout(function() {
        _toggleInterventionEditor(invName, jumpTo, /*forceOpen=*/true);
      }, 0);
    }
  }
  window._renderInterventionsTab = _renderInterventionsTab;

  function _toggleInterventionEditor(invName, name, forceOpen) {
    var hostRow = document.querySelector(
      '.inv-iv-edit[data-name="' + name.replace(/"/g, '\\"') + '"]');
    if (!hostRow) return;
    var cell = hostRow.querySelector('td');
    var isOpen = hostRow.style.display !== 'none';
    if (isOpen && !forceOpen) {
      hostRow.style.display = 'none';
      if (cell) cell.innerHTML = '';
      return;
    }
    // Close all other editors first (single-edit-at-a-time UX).
    Array.prototype.forEach.call(
      document.querySelectorAll('.inv-iv-edit'),
      function(tr) {
        if (tr !== hostRow) {
          tr.style.display = 'none';
          var c = tr.querySelector('td');
          if (c) c.innerHTML = '';
        }
      }
    );
    hostRow.style.display = '';
    var entries = window._invCompositesCache || [];
    var entry = null;
    for (var i = 0; i < entries.length; i++) {
      if (entries[i] && entries[i].name === name) { entry = entries[i]; break; }
    }
    if (!entry) {
      if (cell) cell.innerHTML = '<p style="color:#c00">Variant not found in cache.</p>';
      return;
    }
    var iv = entry.intervention || {};
    var desc = iv.description || '';
    var paramJson = JSON.stringify(iv.parameter_overrides || {}, null, 2);
    var procJson = JSON.stringify(iv.process_overrides || {}, null, 2);
    var inputId = 'inv-iv-desc-' + name;
    var paramId = 'inv-iv-param-' + name;
    var procId = 'inv-iv-proc-' + name;
    var errId = 'inv-iv-err-' + name;
    cell.innerHTML =
      '<div style="padding:10px;background:#fafafa;border:1px solid #eee">' +
        '<div style="margin-bottom:8px">' +
          '<label style="display:block;font-weight:600;margin-bottom:2px">Description</label>' +
          '<input type="text" id="' + _esc(inputId) + '" value="' + _esc(desc) +
            '" style="width:100%;padding:4px">' +
        '</div>' +
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">' +
          '<div>' +
            '<label style="display:block;font-weight:600;margin-bottom:2px">Parameter overrides (JSON)</label>' +
            '<textarea id="' + _esc(paramId) + '" rows="8"' +
              ' style="width:100%;font-family:monospace;font-size:12px">' +
              _esc(paramJson) +
            '</textarea>' +
          '</div>' +
          '<div>' +
            '<label style="display:block;font-weight:600;margin-bottom:2px">Process overrides (JSON)</label>' +
            '<textarea id="' + _esc(procId) + '" rows="8"' +
              ' style="width:100%;font-family:monospace;font-size:12px">' +
              _esc(procJson) +
            '</textarea>' +
          '</div>' +
        '</div>' +
        '<div id="' + _esc(errId) + '" style="color:#c00;margin-top:6px;min-height:1em"></div>' +
        '<div style="margin-top:8px">' +
          '<button class="action-btn" data-iv-save="' + _esc(name) + '">Save</button> ' +
          '<button class="btn-mini" data-iv-cancel="' + _esc(name) + '">Cancel</button>' +
        '</div>' +
      '</div>';
    var saveBtn = cell.querySelector('[data-iv-save]');
    if (saveBtn) {
      saveBtn.addEventListener('click', function() {
        _saveIntervention(invName, name, entry.extends || '');
      });
    }
    var cancelBtn = cell.querySelector('[data-iv-cancel]');
    if (cancelBtn) {
      cancelBtn.addEventListener('click', function() {
        hostRow.style.display = 'none';
        cell.innerHTML = '';
      });
    }
  }
  window._toggleInterventionEditor = _toggleInterventionEditor;

  function _saveIntervention(invName, name, extendsName) {
    var descEl = document.getElementById('inv-iv-desc-' + name);
    var paramEl = document.getElementById('inv-iv-param-' + name);
    var procEl = document.getElementById('inv-iv-proc-' + name);
    var errEl = document.getElementById('inv-iv-err-' + name);
    if (!descEl || !paramEl || !procEl) return;
    if (errEl) errEl.textContent = '';
    var paramObj, procObj;
    try {
      paramObj = paramEl.value.trim() ? JSON.parse(paramEl.value) : {};
      if (paramObj === null || typeof paramObj !== 'object' || Array.isArray(paramObj)) {
        throw new Error('parameter_overrides must be a JSON object');
      }
    } catch (e) {
      if (errEl) errEl.textContent = 'Parameter overrides JSON error: ' + (e.message || e);
      return;
    }
    try {
      procObj = procEl.value.trim() ? JSON.parse(procEl.value) : {};
      if (procObj === null || typeof procObj !== 'object' || Array.isArray(procObj)) {
        throw new Error('process_overrides must be a JSON object');
      }
    } catch (e) {
      if (errEl) errEl.textContent = 'Process overrides JSON error: ' + (e.message || e);
      return;
    }
    var body = {
      investigation: invName,
      name: name,
      extends: extendsName,
      description: descEl.value || '',
      parameter_overrides: paramObj,
      process_overrides: procObj,
    };
    fetch('/api/investigation-composite-perturb', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    })
      .then(function(r) {
        return r.json().then(function(j) { return {ok: r.ok, body: j}; });
      })
      .then(function(res) {
        if (!res.ok) {
          if (errEl) errEl.textContent = (res.body && res.body.error) || 'save failed';
          return;
        }
        if (typeof _showToast === 'function') _showToast('Saved intervention "' + name + '"');
        // Re-fetch composites so the cache and table reflect the new state.
        fetch('/api/investigation-composites?investigation=' + encodeURIComponent(invName))
          .then(function(r) { return r.json(); })
          .then(function(data) {
            var list = (data && data.composites) || [];
            window._invCompositesCache = list;
            _renderInterventionsTab(invName, list);
          });
      })
      .catch(function(err) {
        if (errEl) errEl.textContent = 'Network error: ' + err;
      });
  }
  window._saveIntervention = _saveIntervention;

  // ── Investigation Observables tab handlers ────────────────────────────────

  function _loadInvObservables(invName) {
    // 1. Get composites list, 2. fetch each one's state tree, 3. union store paths,
    // 4. pre-check based on spec.observables.
    fetch('/api/investigation-composites?investigation=' + encodeURIComponent(invName))
      .then(function(r) { return r.json(); })
      .then(function(data) {
        var composites = data.composites || [];
        if (composites.length === 0) {
          var el = document.getElementById('inv-observables-tree');
          if (el) el.innerHTML = '<p class="empty-state">Add a composite first.</p>';
          return;
        }
        Promise.all(composites.map(function(c) {
          return fetch('/api/investigation-state-tree?investigation=' + encodeURIComponent(invName) +
                       '&composite=' + encodeURIComponent(c.name))
            .then(function(r) { return r.json(); })
            .then(function(tree) { return {composite: c.name, nodes: tree.nodes || []}; });
        })).then(function(trees) {
          // Union of store paths across composites
          var union = {};
          trees.forEach(function(t) {
            t.nodes.forEach(function(n) {
              if (n.kind !== 'store') return;
              var key = (n.path || []).join('.');
              if (!union[key]) {
                union[key] = {path: n.path, types: [], composites: []};
              }
              var typ = n.type || 'any';
              if (union[key].types.indexOf(typ) === -1) union[key].types.push(typ);
              if (union[key].composites.indexOf(t.composite) === -1) union[key].composites.push(t.composite);
            });
          });
          var pathKeys = Object.keys(union).sort();

          // Load current spec.yaml.observables to pre-check checkboxes
          fetch('/investigations/' + encodeURIComponent(invName) + '/spec.yaml').then(function(r) {
            return r.ok ? r.text() : '';
          }).then(function(specText) {
            var existing = [];
            var emitAll = false;
            // Naive YAML scrape — find observables: block and parse {path: [...]} entries.
            var m = specText.match(/^observables:\s*\n([\s\S]*?)(?=^[a-zA-Z_]|\s*$)/m);
            if (m) {
              var block = m[1];
              var lines = block.split(/\r?\n/);
              lines.forEach(function(line) {
                // - {path: [a, b]} OR - path: [a, b]
                var p = line.match(/path:\s*\[(.*?)\]/);
                if (p) {
                  var inner = p[1].trim();
                  if (!inner) emitAll = true;
                  else existing.push(inner.split(',').map(function(s) {
                    return s.trim().replace(/^["']|["']$/g, '');
                  }).join('.'));
                }
              });
            }

            var emitAllEl = document.getElementById('inv-emit-all');
            if (emitAllEl) emitAllEl.checked = emitAll;
            var el = document.getElementById('inv-observables-tree');
            if (!el) return;
            el.innerHTML = pathKeys.map(function(k) {
              var u = union[k];
              var checked = existing.indexOf(k) !== -1 ? ' checked' : '';
              var disabled = emitAll ? ' disabled' : '';
              return '<div style="padding:3px 0"><label>' +
                     '<input type="checkbox" data-path="' + _esc(k) + '"' + checked + disabled + '> ' +
                     '<code>' + _esc(k) + '</code> ' +
                     '<small style="color:#888"> ' + u.types.join(',') +
                     '  ·  in: ' + u.composites.join(', ') + '</small>' +
                     '</label></div>';
            }).join('');
            if (!pathKeys.length) {
              el.innerHTML = '<p class="empty-state">No store paths found in this study\'s composites.</p>';
            }
          });
        });
      });
  }
  window._loadInvObservables = _loadInvObservables;

  function _setEmitAll(on) {
    var tree = document.getElementById('inv-observables-tree');
    if (!tree) return;
    tree.querySelectorAll('input[type=checkbox][data-path]').forEach(function(cb) {
      cb.disabled = on;
    });
  }
  window._setEmitAll = _setEmitAll;

  function _saveObservables() {
    var invName = window._currentInvestigation || '';
    var emitAllEl = document.getElementById('inv-emit-all');
    var emitAll = !!(emitAllEl && emitAllEl.checked);
    var paths = [];
    if (!emitAll) {
      document.querySelectorAll('#inv-observables-tree input[type=checkbox][data-path]:checked')
        .forEach(function(cb) { paths.push(cb.dataset.path.split('.')); });
    }
    fetch('/api/investigation-set-observables', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({investigation: invName, paths: paths, emit_all: emitAll}),
    }).then(function(r) { return r.json().then(function(j) { return [r.ok, j]; }); })
      .then(function(parts) {
        var status = document.getElementById('inv-observables-status');
        if (!status) return;
        if (parts[0]) {
          status.textContent = 'Saved ' + (emitAll ? '(emit entire state)' : (paths.length + ' observable(s)'));
        } else {
          status.textContent = 'Save failed: ' + ((parts[1] || {}).error || '');
        }
      });
  }
  window._saveObservables = _saveObservables;

  function _openAddCompositeModal() {
    var sel = document.getElementById('inv-add-composite-source');
    if (!sel) return;
    sel.innerHTML = '<option value="">— pick a workspace composite —</option>';
    fetch('/api/composites').then(function(r) { return r.json(); })
      .then(function(data) {
        (data.composites || []).forEach(function(c) {
          var opt = document.createElement('option');
          opt.value = c.id;
          opt.textContent = c.name + '  —  ' + (c.description || c.id);
          sel.appendChild(opt);
        });
        openModal('modal-inv-add-composite');
      })
      .catch(function() {
        // Fallback: open modal anyway
        openModal('modal-inv-add-composite');
      });
  }
  window._openAddCompositeModal = _openAddCompositeModal;

  function _submitAddComposite(form) {
    var data = new FormData(form);
    var invName = window._currentInvestigation || '';
    var errEl = form.querySelector('.form-error');
    if (errEl) errEl.textContent = '';
    var payload = {
      investigation: invName,
      name: data.get('name'),
      source: data.get('source'),
    };
    fetch('/api/investigation-composite-add', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    }).then(function(r) { return r.json().then(function(j) { return [r.ok, j]; }); })
      .then(function(parts) {
        var ok = parts[0], j = parts[1];
        if (!ok) {
          if (errEl) errEl.textContent = j.error || 'add failed';
          return;
        }
        closeModal('modal-inv-add-composite');
        _loadInvComposites(invName);
      });
  }
  window._submitAddComposite = _submitAddComposite;

  function _openPerturbModal(invName, parentName) {
    window._currentInvestigation = invName;
    var form = document.getElementById('form-inv-perturb');
    if (!form) return;
    form.elements['extends'].value = parentName;
    form.elements['name'].value = '';
    form.elements['parameter_overrides'].value = '';
    form.elements['process_overrides'].value = '';
    var errEl = form.querySelector('.form-error');
    if (errEl) errEl.textContent = '';
    openModal('modal-inv-perturb');
  }
  window._openPerturbModal = _openPerturbModal;

  function _submitPerturb(form) {
    var data = new FormData(form);
    var errEl = form.querySelector('.form-error');
    if (errEl) errEl.textContent = '';
    var parseOpt = function(raw, fieldName) {
      raw = (raw || '').trim();
      if (!raw) return null;
      try { return JSON.parse(raw); }
      catch (e) {
        if (errEl) errEl.textContent = 'Invalid JSON in ' + fieldName + ': ' + String(e);
        return undefined;
      }
    };
    var po = parseOpt(data.get('parameter_overrides'), 'parameter_overrides');
    if (po === undefined) return;
    var procO = parseOpt(data.get('process_overrides'), 'process_overrides');
    if (procO === undefined) return;
    var payload = {
      investigation: window._currentInvestigation || '',
      name: data.get('name'),
      extends: data.get('extends'),
    };
    if (po) payload.parameter_overrides = po;
    if (procO) payload.process_overrides = procO;
    fetch('/api/investigation-composite-perturb', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    }).then(function(r) { return r.json().then(function(j) { return [r.ok, j]; }); })
      .then(function(parts) {
        var ok = parts[0], j = parts[1];
        if (!ok) {
          if (errEl) errEl.textContent = j.error || 'perturb failed';
          return;
        }
        closeModal('modal-inv-perturb');
        _loadInvComposites(payload.investigation);
      });
  }
  window._submitPerturb = _submitPerturb;

  function _rebuildComposite(invName, compName) {
    fetch('/api/investigation-composite-rebuild', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({investigation: invName, name: compName}),
    }).then(function() {
      _loadInvComposites(invName);
      _loadInvCompositeDetail(invName, compName);
    });
  }
  window._rebuildComposite = _rebuildComposite;

  function _removeComposite(invName, compName) {
    if (!confirm('Remove composite ' + compName + '?')) return;
    fetch('/api/investigation-composite', {
      method: 'DELETE', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({investigation: invName, name: compName}),
    }).then(function(r) { return r.json().then(function(j) { return [r.ok, j]; }); })
      .then(function(parts) {
        var ok = parts[0], j = parts[1];
        if (!ok) {
          if (j.dependents) {
            alert('Cannot remove — has dependents:\n - ' + j.dependents.join('\n - '));
          } else {
            alert(j.error || 'remove failed');
          }
          return;
        }
        _loadInvComposites(invName);
      });
  }
  window._removeComposite = _removeComposite;

  // ── Promote-to-catalog modal (C1) ─────────────────────────────────────────

  function _closePromoteModal() {
    var el = document.getElementById('modal-promote-edit');
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }
  window._closePromoteModal = _closePromoteModal;

  function _openPromoteModal(invName, variantName) {
    _closePromoteModal();
    window._promoteModalCtx = {investigation: invName, variant: variantName};
    var defaultTarget = String(variantName || '')
      .toLowerCase()
      .replace(/[^a-z0-9_-]+/g, '-')
      .replace(/^-+|-+$/g, '') || 'composite';
    var modal = document.createElement('div');
    modal.id = 'modal-promote-edit';
    modal.className = 'modal-overlay';
    modal.style.display = 'flex';
    modal.innerHTML =
      '<div class="modal-box">' +
        '<button class="modal-close" onclick="_closePromoteModal()">&times;</button>' +
        '<h3>Promote variant to workspace catalog</h3>' +
        '<p class="muted" style="margin:4px 0">Promoting <code>' + _esc(variantName) +
          '</code> from investigation <code>' + _esc(invName) +
          '</code> into the workspace composite catalog.</p>' +
        '<label>Target name' +
          '<input type="text" id="promote-target-name" value="' + _esc(defaultTarget) +
            '" pattern="[a-z0-9_-]+" required>' +
        '</label>' +
        '<label>Description' +
          '<input type="text" id="promote-description" placeholder="Short description (optional)">' +
        '</label>' +
        '<div class="form-error" id="promote-error" style="color:#c00;min-height:1em"></div>' +
        '<div style="margin-top:8px">' +
          '<button type="button" class="action-btn" id="promote-save-btn">Promote</button> ' +
          '<button type="button" class="btn-mini" onclick="_closePromoteModal()">Cancel</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(modal);
    var saveBtn = document.getElementById('promote-save-btn');
    if (saveBtn) {
      saveBtn.addEventListener('click', function() { _submitPromoteModal(); });
    }
  }
  window._openPromoteModal = _openPromoteModal;

  function _submitPromoteModal() {
    var ctx = window._promoteModalCtx || {};
    var invName = ctx.investigation;
    var variant = ctx.variant;
    var targetEl = document.getElementById('promote-target-name');
    var descEl = document.getElementById('promote-description');
    var errEl = document.getElementById('promote-error');
    if (errEl) errEl.textContent = '';
    var target = targetEl ? targetEl.value.trim() : '';
    var desc = descEl ? descEl.value.trim() : '';
    if (!target) {
      if (errEl) errEl.textContent = 'Target name required';
      return;
    }
    if (!/^[a-z0-9_-]+$/.test(target)) {
      if (errEl) errEl.textContent = 'Target name must match [a-z0-9_-]+';
      return;
    }
    fetch('/api/composite-promote-to-catalog', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        investigation: invName,
        variant: variant,
        target_name: target,
        description: desc,
      }),
    })
      .then(function(r) {
        return r.json().then(function(j) { return {status: r.status, body: j}; });
      })
      .then(function(res) {
        if (res.status === 200) {
          _closePromoteModal();
          if (typeof _showToast === 'function') {
            _showToast('Promoted ' + variant + ' as ' + (res.body && res.body.name || target));
          }
          _loadInvComposites(invName);
        } else {
          if (errEl) {
            errEl.textContent = (res.body && res.body.error) ||
              ('Promote failed (' + res.status + ')');
          }
        }
      })
      .catch(function(err) {
        if (errEl) errEl.textContent = 'Network error: ' + err;
      });
  }
  window._submitPromoteModal = _submitPromoteModal;

  // ── End Investigation Composites tab handlers ─────────────────────────────

  function _renderInvestigationRunsTable(runs, investigationName) {
    var rows = runs.map(function(r) {
      var pstr = Object.keys(r.params || {}).map(function(k) {
        return k + '=' + r.params[k];
      }).join(', ') || '—';
      var statusClass = ({completed: 'completed', failed: 'failed',
                          running: 'running'})[r.status] || 'planned';
      var rowId = _esc(r.run_id);
      var paramsJson = _esc(JSON.stringify(r.params || {}));
      return '<tr><td>' + _esc(r.sim_name) + '</td>' +
             '<td><code>' + _esc(pstr) + '</code></td>' +
             '<td>' + (r.n_steps || 0) + '</td>' +
             '<td><span class="ce-history-status ' + statusClass + '">' + _esc(r.status) + '</span></td>' +
             '<td><code style="font-size:0.78em">' + rowId.slice(-12) + '</code></td>' +
             '<td><button class="btn-mini" onclick=\'_dupRun("' + _esc(investigationName) + '","' + rowId + '","' + _esc(r.sim_name) + '",' + paramsJson + ',' + (r.n_steps || 10) + ')\'>Duplicate</button> ' +
                  '<button class="btn-mini" style="color:#c00" onclick="_deleteRun(\'' + _esc(investigationName) + '\',\'' + rowId + '\')">Delete</button></td>' +
           '</tr>';
    }).join('');
    var clearBtn = '<div style="margin-bottom:6px"><button class="btn-mini" style="color:#c00" ' +
                   'onclick="_clearRuns(\'' + _esc(investigationName) + '\')">Clear all runs</button></div>';
    return clearBtn + '<table style="width:100%"><thead><tr>' +
      '<th>Simulation</th><th>Params</th><th>Steps</th><th>Status</th><th>Run id</th><th>Actions</th>' +
      '</tr></thead><tbody>' + rows + '</tbody></table>';
  }

  function _runInvestigation(name) {
    var detail = document.getElementById('investigation-detail');
    var btn = detail.querySelector('button.action-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Running…'; }
    fetch('/api/investigation-run', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name}),
    }).then(function(r) { return r.json().then(function(j) { return [r.ok, j]; }); })
      .then(function(parts) {
        var ok = parts[0], j = parts[1];
        if (!ok) { alert('Run failed: ' + (j.error || 'unknown')); }
        // Refresh both the list (status update) and the detail panel
        window._investigationsLoaded = false;
        _loadInvestigations();
        _vivRefreshInvestigationsRail();
        _openInvestigation(name);
      })
      .catch(function(err) { alert('Network error: ' + err); });
  }
  window._runInvestigation = _runInvestigation;

  function _deleteInvestigation(name) {
    if (!confirm('Delete investigation "' + name + '"? This removes its runs.db, visualizations, and spec.yaml.')) return;
    fetch('/api/investigation-delete', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name}),
    }).then(function(r) { return r.json(); }).then(function(j) {
      if (!j.ok) { alert('Delete failed: ' + (j.error || 'unknown')); return; }
      var detail = document.getElementById('investigation-detail');
      if (detail) { detail.style.display = 'none'; detail.innerHTML = ''; }
      window._currentInvestigation = null;
      window._investigationsLoaded = false;
      _loadInvestigations();
      _vivRefreshInvestigationsRail();
    });
  }
  window._deleteInvestigation = _deleteInvestigation;

  function _deleteRun(investigationName, runId) {
    if (!confirm('Delete run ' + runId.slice(-12) + '?')) return;
    fetch('/api/investigation-run-delete', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({investigation: investigationName, run_id: runId}),
    }).then(function(r) { return r.json(); }).then(function(j) {
      if (!j.ok) { alert('Delete failed: ' + (j.error || 'unknown')); return; }
      _openInvestigation(investigationName);
    });
  }
  window._deleteRun = _deleteRun;

  function _clearRuns(investigationName) {
    if (!confirm('Clear ALL runs from ' + investigationName + '? (visualizations will be empty until you re-run)')) return;
    fetch('/api/investigation-runs-clear', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({investigation: investigationName}),
    }).then(function(r) { return r.json(); }).then(function(j) {
      if (!j.ok) { alert('Clear failed: ' + (j.error || 'unknown')); return; }
      _openInvestigation(investigationName);
    });
  }
  window._clearRuns = _clearRuns;

  function _dupRun(investigationName, runId, simName, params, steps) {
    // Prompt the user to edit params as JSON, then submit.
    var current = JSON.stringify(params, null, 2);
    var edited = prompt('Edit overrides for the duplicated run:\n(JSON; will append as a new ad-hoc run)', current);
    if (edited === null) return;
    var overrides;
    try { overrides = JSON.parse(edited); }
    catch (e) { alert('Invalid JSON: ' + e); return; }
    fetch('/api/investigation-run-one', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        investigation: investigationName,
        sim_name: simName + '-copy',
        overrides: overrides,
        steps: steps,
      }),
    }).then(function(r) { return r.json(); }).then(function(j) {
      if (!j.ok) { alert('Duplicate-run failed: ' + (j.error || 'unknown')); return; }
      // Re-render the investigation; the new run's viz HTML lives at
      // /investigations/<inv>/viz/<run_id>/<name>.html and is discoverable
      // via GET /api/investigation-viz-html?investigation=...&run_id=...
      _openInvestigation(investigationName);
      // Surface any inline viz from this run so the user sees confirmation
      // without hunting through the Visualizations tab.
      _renderRunViz(investigationName, j.run_id);
    });
  }
  window._dupRun = _dupRun;

  function _renderRunViz(investigationName, runId) {
    // Append a per-run viz panel beneath the runs table. Idempotent: each
    // call replaces the previous panel for the same run_id.
    if (!runId) return;
    var detail = document.getElementById('investigation-detail');
    if (!detail) return;
    var runsPanel = detail.querySelector('.investigation-detail-panel[data-tab="runs"]');
    if (!runsPanel) return;
    var existing = document.getElementById('run-viz-' + runId);
    if (existing) existing.remove();
    var url = '/api/investigation-viz-html?investigation=' +
              encodeURIComponent(investigationName) +
              '&run_id=' + encodeURIComponent(runId);
    fetch(url).then(function(r) { return r.json(); }).then(function(j) {
      var files = (j && j.viz_files) || [];
      var panel = document.createElement('div');
      panel.id = 'run-viz-' + runId;
      panel.style.marginTop = '14px';
      panel.style.padding = '10px';
      panel.style.border = '1px solid #ddd';
      panel.style.borderRadius = '4px';
      if (!files.length) {
        panel.innerHTML = '<p class="empty-state" style="margin:0">No visualizations for run <code>' +
                          _esc(runId.slice(-12)) + '</code>.</p>';
      } else {
        var iframes = files.map(function(f) {
          return '<figure style="margin:0 0 14px 0">' +
            '<figcaption style="font-size:0.85em;color:#555;margin-bottom:4px">' +
              _esc(f.name) +
              ' <small><a href="/' + _esc(f.html_path) + '" target="_blank">open ↗</a></small>' +
            '</figcaption>' +
            '<iframe src="/' + _esc(f.html_path) + '" sandbox="allow-scripts" ' +
              'style="width:100%;height:380px;border:1px solid #eee;background:#fff"></iframe>' +
          '</figure>';
        }).join('');
        panel.innerHTML = '<h4 style="margin:0 0 8px 0">Run ' + _esc(runId.slice(-12)) +
                          ' visualizations</h4>' + iframes;
      }
      runsPanel.appendChild(panel);
    });
  }
  window._renderRunViz = _renderRunViz;

  function _openWorkspaceVizModal() {
    var classSel = document.getElementById('viz-class-picker');
    var alreadyEl = document.getElementById('viz-already-registered');
    if (classSel) classSel.innerHTML = '<option value="">— none (description-only) —</option>';
    if (alreadyEl) alreadyEl.textContent = '';
    Promise.all([
      fetch('/api/visualization-classes').then(function(r) { return r.json(); }),
      fetch('/api/visualization-instances').then(function(r) { return r.json(); }),
      fetch('/workspace.yaml').then(function(r) { return r.ok ? r.text() : ''; }),
    ]).then(function(parts) {
      // Filter out Analysis classes — the workspace viz picker only shows Visualization classes.
      var classes = ((parts[0] && parts[0].classes) || []).filter(function(c) { return c.kind !== 'analysis'; });
      var instances = (parts[1] && parts[1].instances) || [];
      if (classSel) {
        classes.forEach(function(c) {
          var opt = document.createElement('option');
          opt.value = c.name;
          opt.textContent = c.name + (c.doc ? '  —  ' + c.doc : '');
          classSel.appendChild(opt);
        });
      }
      // Surface the existing workspace.yaml viz entries by name so the user
      // doesn't collide with one they already added.
      var ws = parts[2] || '';
      var existing = [];
      var inViz = false;
      ws.split(/\r?\n/).forEach(function(line) {
        if (/^visualizations:/.test(line)) { inViz = true; return; }
        if (inViz && /^[A-Za-z_]/.test(line)) { inViz = false; return; }
        if (inViz) {
          var m = line.match(/^\s*-\s*name:\s*(\S+)/);
          if (m) existing.push(m[1]);
        }
      });
      if (alreadyEl) {
        if (existing.length) {
          var instMap = {};
          instances.forEach(function(i) { instMap[i.name] = i['class']; });
          alreadyEl.innerHTML = 'Already registered: ' + existing.map(function(n) {
            return instMap[n]
              ? '<code>' + n + '</code> (' + instMap[n] + ')'
              : '<code>' + n + '</code>';
          }).join(', ');
        } else {
          alreadyEl.textContent = 'No visualizations registered yet.';
        }
      }
      openModal('modal-visualization');
    });
  }
  window._openWorkspaceVizModal = _openWorkspaceVizModal;

  function _openAddVizModal(investigationName) {
    document.getElementById('add-viz-investigation').value = investigationName;
    var sel = document.getElementById('add-viz-class');
    var cfgField = document.querySelector('#form-investigation-add-viz textarea[name="config"]');
    sel.innerHTML = '<option value="">— pick a registered instance or raw class —</option>';
    // Stash instance configs on the select so onchange can auto-fill.
    sel._vizInstanceConfigs = {};
    // ── B5: inject a Comparison dropdown at the top of the form so the user
    // can auto-fill sources/observable from a saved comparison. The dropdown
    // is created once and re-populated each open from the cached spec.
    _ensureAddVizComparisonDropdown();
    Promise.all([
      fetch('/api/visualization-instances').then(function(r) { return r.json(); }),
      fetch('/api/visualization-classes').then(function(r) { return r.json(); }),
    ]).then(function(parts) {
      var instances = (parts[0] && parts[0].instances) || [];
      // Filter out Analysis classes — the add-viz picker only offers Visualization classes.
      var classes = ((parts[1] && parts[1].classes) || []).filter(function(c) { return c.kind !== 'analysis'; });
      if (instances.length) {
        var gi = document.createElement('optgroup');
        gi.label = 'Registered instances (config pre-filled)';
        instances.forEach(function(inst) {
          var opt = document.createElement('option');
          opt.value = inst.address;
          opt.textContent = inst.name + '  —  ' + inst['class'] + (inst.description ? ' · ' + inst.description : '');
          opt.dataset.instanceName = inst.name;
          sel._vizInstanceConfigs[opt.value + '|' + inst.name] = inst.config || {};
          gi.appendChild(opt);
        });
        sel.appendChild(gi);
      }
      if (classes.length) {
        var gc = document.createElement('optgroup');
        gc.label = 'Raw classes (write config JSON)';
        classes.forEach(function(c) {
          var opt = document.createElement('option');
          opt.value = c.address;
          opt.textContent = c.name + (c.doc ? '  —  ' + c.doc : '');
          gc.appendChild(opt);
        });
        sel.appendChild(gc);
      }
      sel.onchange = function() {
        var picked = sel.options[sel.selectedIndex];
        if (!picked) return;
        var instName = picked.dataset && picked.dataset.instanceName;
        if (instName) {
          var key = sel.value + '|' + instName;
          var cfg = sel._vizInstanceConfigs[key] || {};
          if (cfgField) cfgField.value = JSON.stringify(cfg, null, 2);
          // Default the new investigation viz name to the instance name when empty.
          var nameField = document.querySelector('#form-investigation-add-viz input[name="name"]');
          if (nameField && !nameField.value) nameField.value = instName;
        }
      };
      openModal('modal-investigation-add-viz');
    });
  }
  window._openAddVizModal = _openAddVizModal;

  // ── B5: Comparison dropdown injected into the add-viz modal. Pulls
  // comparisons from window._invSpecCache (populated by _renderInvestigationDetail).
  function _ensureAddVizComparisonDropdown() {
    var form = document.getElementById('form-investigation-add-viz');
    if (!form) return;
    var sel = document.getElementById('add-viz-comparison');
    if (!sel) {
      var label = document.createElement('label');
      label.textContent = 'Comparison';
      sel = document.createElement('select');
      sel.id = 'add-viz-comparison';
      sel.name = 'comparison';
      label.appendChild(sel);
      // Insert right after the hidden investigation input (i.e. as the first
      // visible field of the form).
      var firstChild = form.firstChild;
      form.insertBefore(label, firstChild);
    }
    var spec = window._invSpecCache || {};
    var comparisons = Array.isArray(spec.comparisons) ? spec.comparisons : [];
    sel.innerHTML = '<option value="">— None (manual sources/observable) —</option>';
    comparisons.forEach(function(c) {
      var name = (c && c.name) ? String(c.name) : '';
      if (!name) return;
      var opt = document.createElement('option');
      opt.value = name;
      opt.textContent = name;
      sel.appendChild(opt);
    });
    // Reset selection each time the modal opens.
    sel.value = '';
    sel.onchange = function() {
      var picked = sel.value;
      if (!picked) return;
      var cmp = null;
      for (var i = 0; i < comparisons.length; i++) {
        if (comparisons[i] && comparisons[i].name === picked) {
          cmp = comparisons[i];
          break;
        }
      }
      if (!cmp) return;
      var cfgField = document.querySelector('#form-investigation-add-viz textarea[name="config"]');
      // Existing convention in the seed-fixture is `{"sources": [...], "observable": "..."}`
      // — we mirror that shape and merge into whatever JSON is already in the
      // textarea (so the user can pre-pick a class first, then a comparison).
      var existing = {};
      if (cfgField && cfgField.value.trim()) {
        try { existing = JSON.parse(cfgField.value) || {}; } catch (e) { existing = {}; }
        if (existing === null || typeof existing !== 'object' || Array.isArray(existing)) {
          existing = {};
        }
      }
      existing.sources = (cmp.variants || []).map(function(v) { return String(v); });
      var obs = (cmp.observables || []);
      existing.observable = obs.length ? _obsPath(obs[0]) : '';
      existing.comparison = cmp.name;
      if (cfgField) cfgField.value = JSON.stringify(existing, null, 2);
    };
  }
  window._ensureAddVizComparisonDropdown = _ensureAddVizComparisonDropdown;

  function _submitAddViz(form) {
    var data = new FormData(form);
    var errEl = form.querySelector('.form-error');
    if (errEl) errEl.textContent = '';
    var configRaw = (data.get('config') || '').trim();
    var config = {};
    if (configRaw) {
      try { config = JSON.parse(configRaw); }
      catch (e) {
        if (errEl) errEl.textContent = 'Invalid JSON in config: ' + String(e);
        return;
      }
    }
    var payload = {
      investigation: data.get('investigation'),
      name: data.get('name'),
      address: data.get('address'),
      config: config,
    };
    fetch('/api/investigation-add-viz', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    }).then(function(r) { return r.json().then(function(j) { return [r.ok, j]; }); })
      .then(function(parts) {
        var ok = parts[0], j = parts[1];
        if (!ok) {
          if (errEl) errEl.textContent = j.error || 'add failed';
          return;
        }
        closeModal('modal-investigation-add-viz');
        fetch('/api/investigation-render-viz', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({name: payload.investigation}),
        }).then(function() {
          _openInvestigation(payload.investigation);  // refresh detail panel
        });
      });
  }
  window._submitAddViz = _submitAddViz;

  // ---------------------------------------------------------------------------
  // Viz generate / accept / migration (Task 8)
  // ---------------------------------------------------------------------------

  function _submitVizGenerate(form) {
    var data = new FormData(form);
    var errEl = form.querySelector('.form-error');
    var statusEl = document.getElementById('viz-generate-status');
    if (errEl) errEl.textContent = '';
    var payload = {
      name: data.get('name'),
      description: data.get('description'),
    };
    fetch('/api/visualization-generate', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    }).then(function(r) { return r.json().then(function(j) { return [r.ok, j]; }); })
      .then(function(parts) {
        var ok = parts[0], j = parts[1];
        if (!ok) {
          if (errEl) errEl.textContent = j.error || 'generate failed';
          return;
        }
        if (statusEl) statusEl.innerHTML =
          'Request written to <code>' + j.request_path + '</code>.<br>' +
          'In your active Claude Code session, run <code>' + j.skill_command + '</code>.<br>' +
          'Target file: <code>' + j.target_file + '</code>.<br>' +
          'Polling for completion…';
        _pollForGeneratedClass(payload.name, j.target_file, 0);
      });
  }
  window._submitVizGenerate = _submitVizGenerate;

  function _pollForGeneratedClass(name, targetFile, attempt) {
    if (attempt > 600) {  // ~5 min
      var statusEl = document.getElementById('viz-generate-status');
      if (statusEl) statusEl.innerHTML += '<br><span style="color:#991b1b">Timed out waiting.</span>';
      return;
    }
    fetch('/' + targetFile + '?_=' + Date.now()).then(function(r) {
      if (r.ok) {
        var statusEl = document.getElementById('viz-generate-status');
        if (statusEl) statusEl.innerHTML +=
          '<br><span style="color:#1f7a3a">File detected.</span> ' +
          '<button class="btn-mini" onclick="_vizClassPreview(\'local:' + name + '\',\'' + name + '\')">' +
          'Preview</button> ' +
          '<button class="btn-mini" onclick="_acceptGeneratedClass(\'' + name + '\')">Accept &amp; commit</button>';
      } else {
        setTimeout(function() { _pollForGeneratedClass(name, targetFile, attempt + 1); }, 500);
      }
    }).catch(function() {
      setTimeout(function() { _pollForGeneratedClass(name, targetFile, attempt + 1); }, 500);
    });
  }

  function _acceptGeneratedClass(name) {
    fetch('/api/visualization-accept', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name}),
    }).then(function(r) { return r.json().then(function(j) { return [r.ok, j]; }); })
      .then(function(parts) {
        var ok = parts[0], j = parts[1];
        var statusEl = document.getElementById('viz-generate-status');
        if (!ok) {
          if (statusEl) statusEl.innerHTML +=
            '<br><span style="color:#991b1b">Accept failed: ' + (j.error || '') + '</span>';
          return;
        }
        if (statusEl) statusEl.innerHTML +=
          '<br><span style="color:#1f7a3a">Committed. Reloading catalog…</span>';
        setTimeout(function() { window.location.reload(); }, 600);
      });
  }
  window._acceptGeneratedClass = _acceptGeneratedClass;

  // ===========================================================================
  // Simulations tab — workspace-wide run listing + delete
  // ===========================================================================

  function _escSim(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function _simRelativeTime(epoch) {
    if (!epoch) return '—';
    var d = Math.floor(Date.now() / 1000 - epoch);
    if (d < 60)        return d + 's ago';
    if (d < 3600)      return Math.floor(d / 60) + 'm ago';
    if (d < 86400)     return Math.floor(d / 3600) + 'h ago';
    return Math.floor(d / 86400) + 'd ago';
  }

  function _simStatusChip(status) {
    var colors = {
      completed: ['#dcfce7', '#166534'],
      running:   ['#dbeafe', '#1e40af'],
      failed:    ['#fee2e2', '#991b1b'],
      orphaned:  ['#e5e7eb', '#374151'],
    };
    var c = colors[status] || ['#e5e7eb', '#374151'];
    return '<span style="background:' + c[0] + '; color:' + c[1] +
      '; padding:2px 8px; border-radius:10px; font-size:12px;">' +
      _escSim(status || '?') + '</span>';
  }

  // Emitter-type pill, keyed by the API's emitter_type ("SQLite"/"Parquet"/
  // "XArray"). Colors live in CSS classes emitter-sqlite/parquet/xarray.
  function _simEmitterPill(emitterType) {
    var t = (emitterType || 'SQLite');
    // "—" = genuinely emitter-less run (summary recorded in study.yaml, no
    // per-step trajectory persisted). Render an honest dash with a tooltip
    // rather than a fake emitter pill.
    if (t === '—' || t === 'none' || t === '') {
      return '<span class="emitter-pill emitter-none" ' +
        'title="no emitter (summary-only run)">—</span>';
    }
    var cls = 'emitter-' + t.toLowerCase();
    return '<span class="emitter-pill ' + cls + '" ' +
      'title="emitter / persistence format">' + _escSim(t) + '</span>';
  }

  // Format an epoch-seconds timestamp as a readable local time.
  function _simFmtTime(sec) {
    if (!sec) return '—';
    return new Date(sec * 1000).toLocaleString();
  }

  // Module-scope cache. _simRows = all runs from the API (the {simulations}
  // shape from simulations_index.list_simulations); _simCurrent = the current
  // investigation slug (default filter target, may be null).
  window._simRows = [];
  window._simCurrent = null;

  // Investigation/study come from the index's *_slug fields; the study slug
  // falls back to the first cross-referenced study name.
  function _simInvestigation(row) { return row.investigation_slug || ''; }
  function _simStudy(row) {
    return row.study_slug || (row.studies && row.studies.length ? row.studies[0] : '');
  }

  /** Open the Composite Explorer for a specific past simulation.
   *
   *  Mirrors _openCompositeExplorer but also seeds ?run_id=, so
   *  _initCompositeExplorer picks it up and renders the run's results +
   *  viz_html in the Run tab. Only meaningful for runs with a spec_id
   *  (Composite Explorer scratch runs / runs_meta rows).
   */
  function _openSimulationInExplorer(run_id, spec_id) {
    var url = new URL(window.location.href);
    url.searchParams.set('id', spec_id);
    url.searchParams.set('run_id', run_id);
    url.hash = '#composite-explore';
    window.history.pushState({}, '', url.toString());
    _switchPage('composite-explore');
  }
  window._openSimulationInExplorer = _openSimulationInExplorer;

  function _renderSimRow(row) {
    var inv = _simInvestigation(row);
    var invCell = inv
      ? '<code style="font-size:12px; color:#374151;">' + _escSim(inv) + '</code>'
      : '<span style="color:#9ca3af;">—</span>';
    var study = _simStudy(row);
    var studyCell = study
      ? '<code style="font-size:12px; color:#374151;">' + _escSim(study) + '</code>'
      : '<span style="color:#9ca3af;">—</span>';
    var runId = row.run_id || '';
    var runLabel = row.sim_name || row.label || runId;
    var runTitle = ' title="' + _escSim(runId + (row.db_path ? '\n' + row.db_path : '')) + '"';
    var timeSec = row.completed_at || row.started_at;
    // Actions: open-in-explorer (only when there's a spec_id to seed the
    // explorer with) + delete. The {simulations} shape carries spec_id +
    // db_path so both are reconstructable.
    var specId = row.spec_id || '';
    var explorerBtn = specId
      ? '<a href="?id=' + encodeURIComponent(specId) +
          '&run_id=' + encodeURIComponent(runId) + '#composite-explore" ' +
          'class="action-btn js-authoring" title="Open in Composite Explorer" ' +
          'style="text-decoration:none;" ' +
          'onclick="event.preventDefault(); _openSimulationInExplorer(\'' +
            _escSim(runId) + '\', \'' + _escSim(specId) + '\');">Open</a>'
      : '';
    var deleteBtn = '<button class="action-btn js-authoring" title="Delete simulation" ' +
      'onclick="_deleteSimulationRun(\'' + _escSim(runId) + '\')">🗑</button>';
    return (
      '<tr data-run-id="' + _escSim(runId) + '" style="border-bottom:1px solid #f3f4f6;">' +
      '<td style="padding:6px 8px;">' + invCell + '</td>' +
      '<td style="padding:6px 8px;">' + studyCell + '</td>' +
      '<td style="padding:6px 8px;"><code style="font-size:11px; color:#6b7280;"' +
        runTitle + '>' + _escSim(runLabel) + '</code></td>' +
      '<td style="padding:6px 8px;">' + _simEmitterPill(row.emitter_type) + '</td>' +
      '<td style="padding:6px 8px; color:#6b7280;">' + _escSim(_simFmtTime(timeSec)) + '</td>' +
      '<td style="padding:6px 8px;">' + _simStatusChip(row.status) + '</td>' +
      '<td style="padding:6px 8px; text-align:center; white-space:nowrap;">' +
        explorerBtn + (explorerBtn && deleteBtn ? ' ' : '') + deleteBtn + '</td>' +
      '</tr>'
    );
  }

  // Populate the Study + Emitter dropdowns from the data (preserving any
  // current selection), then render rows through the active filters.
  function _applySimFilter() {
    var rows = window._simRows || [];
    var allToggle = document.getElementById('sim-all-toggle');
    var studySel  = document.getElementById('sim-study-filter');
    var emitterSel = document.getElementById('sim-emitter-filter');

    var showAll = allToggle ? allToggle.checked : false;
    var studyVal = studySel ? studySel.value : '';
    var emitterVal = emitterSel ? emitterSel.value : '';

    var visible = rows.filter(function (r) {
      if (!showAll && window._simCurrent && _simInvestigation(r) !== window._simCurrent) return false;
      if (studyVal && _simStudy(r) !== studyVal) return false;
      if (emitterVal && (r.emitter_type || 'SQLite') !== emitterVal) return false;
      return true;
    });

    var tbody = document.getElementById('sim-tbody');
    var table = document.getElementById('sim-table');
    var empty = document.getElementById('sim-empty');
    if (tbody) tbody.innerHTML = visible.map(_renderSimRow).join('');
    if (table) table.style.display = visible.length ? '' : 'none';
    if (empty) empty.style.display = visible.length ? 'none' : '';
  }

  // Rebuild the Study + Emitter <select> option lists from the current data.
  function _populateSimFilters() {
    var rows = window._simRows || [];
    var studies = {}, emitters = {};
    rows.forEach(function (r) {
      var st = _simStudy(r);
      if (st) studies[st] = true;
      emitters[r.emitter_type || 'SQLite'] = true;
    });
    function fill(sel, values) {
      if (!sel) return;
      var prev = sel.value;
      var opts = ['<option value="">All</option>'];
      values.sort().forEach(function (v) {
        opts.push('<option value="' + _escSim(v) + '">' + _escSim(v) + '</option>');
      });
      sel.innerHTML = opts.join('');
      if (values.indexOf(prev) >= 0) sel.value = prev;
    }
    fill(document.getElementById('sim-study-filter'), Object.keys(studies));
    fill(document.getElementById('sim-emitter-filter'), Object.keys(emitters));
  }

  function _initSimulations() {
    var loading = document.getElementById('sim-loading');
    var empty   = document.getElementById('sim-empty');
    var table   = document.getElementById('sim-table');
    if (loading) loading.style.display = '';
    if (empty)   empty.style.display = 'none';
    if (table)   table.style.display = 'none';

    window.DataSource.loadSimulations()
      .then(function (data) {
        if (data.error) {
          if (loading) loading.innerHTML =
            '<span style="color:#c00;">Could not load simulations: ' +
            _escSim(data.error) + ' <button class="action-btn" ' +
            'onclick="_initSimulations()">Retry</button></span>';
          return;
        }
        window._simRows = data.simulations || [];
        window._simCurrent = data.current || null;
        if (loading) loading.style.display = 'none';
        _populateSimFilters();
        _applySimFilter();
      })
      .catch(function (err) {
        if (loading) loading.innerHTML =
          '<span style="color:#c00;">Network error: ' + _escSim(String(err)) +
          ' <button class="action-btn" onclick="_initSimulations()">Retry</button></span>';
      });
  }
  window._initSimulations = _initSimulations;

  // Wire the toggle + dropdown filters + refresh button (once, on first init).
  function _wireSimulationsUiOnce() {
    [['sim-all-toggle', 'change'],
     ['sim-study-filter', 'change'],
     ['sim-emitter-filter', 'change']].forEach(function (pair) {
      var el = document.getElementById(pair[0]);
      if (el && !el.dataset.wired) {
        el.addEventListener(pair[1], _applySimFilter);
        el.dataset.wired = '1';
      }
    });
    var r = document.getElementById('sim-refresh');
    if (r && !r.dataset.wired) {
      r.addEventListener('click', _initSimulations);
      r.dataset.wired = '1';
    }
    var cancel = document.getElementById('sim-delete-cancel');
    if (cancel && !cancel.dataset.wired) {
      cancel.addEventListener('click', function () {
        var dlg = document.getElementById('sim-delete-dialog');
        if (dlg) dlg.style.display = 'none';
      });
      cancel.dataset.wired = '1';
    }
  }
  window._wireSimulationsUiOnce = _wireSimulationsUiOnce;

  // Confirm + perform a full delete of one simulation (DB rows + history +
  // run dir + study.yaml refs) via DELETE /api/simulation-run. Reads the row
  // from the {simulations} cache for spec_id/db_path/studies to populate the
  // confirmation dialog.
  function _deleteSimulationRun(run_id) {
    _wireSimulationsUiOnce();
    var rows = window._simRows || [];
    var sim = null;
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].run_id === run_id) { sim = rows[i]; break; }
    }
    if (!sim) return;

    var studies = sim.studies || [];
    var studiesTxt = studies.length ? studies.map(_escSim).join(', ') : '<em>none</em>';
    var stillRunning = (sim.status === 'running')
      ? '<p style="color:#b45309; margin:8px 0 0;"><strong>⚠ This run is still running.</strong> ' +
        'Deleting now will orphan the detached process (it will fail-write later, harmlessly).</p>'
      : '';
    var composite = sim.spec_id
      ? '<p style="margin:0 0 8px;">Composite: <code>' + _escSim(sim.spec_id) + '</code></p>'
      : '';
    var body = document.getElementById('sim-delete-body');
    if (body) body.innerHTML =
      '<p style="margin:0 0 8px;"><code>' + _escSim(run_id) + '</code></p>' +
      composite +
      '<p style="margin:0 0 4px;">This will permanently remove:</p>' +
      '<ul style="margin:0 0 4px 24px;">' +
        '<li>1 row in <code>' + _escSim(sim.db_path || '?') + '</code></li>' +
        '<li>All history rows (trajectory data) for this run</li>' +
        '<li>The run directory <code>.pbg/runs/' + _escSim(run_id) + '/</code> (if any)</li>' +
        '<li>References from study.yaml(s): ' + studiesTxt + '</li>' +
      '</ul>' + stillRunning;
    var dlg = document.getElementById('sim-delete-dialog');
    if (dlg) dlg.style.display = 'flex';
    var confirm = document.getElementById('sim-delete-confirm');
    // Replace the confirm handler each time to bind the current run_id.
    confirm.onclick = function () {
      confirm.disabled = true;
      fetch('/api/simulation-run', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run_id: run_id }),
      }).then(function (r) { return r.json().then(function (d) {
        return { ok: r.ok, status: r.status, body: d };
      }); }).then(function (res) {
        confirm.disabled = false;
        if (dlg) dlg.style.display = 'none';
        if (!res.ok) {
          alert('Delete failed: ' + (res.body.error || 'HTTP ' + res.status));
          return;
        }
        if (res.body.errors && res.body.errors.length) {
          alert('Deleted, but with warnings:\n' + res.body.errors.join('\n'));
        }
        _initSimulations();
      }).catch(function (err) {
        confirm.disabled = false;
        if (dlg) dlg.style.display = 'none';
        alert('Network error: ' + err);
      });
    };
  }
  window._deleteSimulationRun = _deleteSimulationRun;

  // ===========================================================================
  // Composite Explorer — load a prior run into the Run tab
  // ===========================================================================

  // Module-scope interval id for the running-state poll. Owned by
  // _ceLoadRunFromId; cleared by _ceStopRunPoll (called from _switchPage on
  // navigation away, and on terminal status transitions).
  window._cePollIntervalId = null;

  function _ceStopRunPoll() {
    if (window._cePollIntervalId != null) {
      clearInterval(window._cePollIntervalId);
      window._cePollIntervalId = null;
    }
  }
  window._ceStopRunPoll = _ceStopRunPoll;

  /** Transform a per-step trajectory list into the observable-keyed shape the
   *  Run-tab table renderer wants. Skips rows without step or state. */
  function _trajectoryToObservables(trajectory) {
    var out = {};
    if (!trajectory || !trajectory.length) return out;
    for (var i = 0; i < trajectory.length; i++) {
      var row = trajectory[i];
      if (!row || row.step == null || !row.state) continue;
      var state = row.state;
      for (var k in state) {
        if (!Object.prototype.hasOwnProperty.call(state, k)) continue;
        if (!out[k]) out[k] = [];
        out[k].push(state[k]);
      }
    }
    return out;
  }
  window._trajectoryToObservables = _trajectoryToObservables;

  /** Render the Run-tab results panel from a canonical input.
   *
   *  Single writer of #ce-test-results. The same input shape is produced by
   *  both _ceLoadRunFromId (URL/prior-run flow) and the rewritten _ceTestRun
   *  (fresh in-Explorer Run flow), so the rendered DOM only depends on this
   *  data, not on which flow produced it.
   *
   *  Input fields:
   *    status        — 'running' | 'completed' | 'failed' | 'orphaned' | 'gone'
   *                    (the special value 'gone' is used when the run no
   *                    longer exists in the DB; renders the deleted banner)
   *    results       — {key: [entries, ...]}  (observable-keyed)
   *    viz_html      — {path: {html}}  (may be undefined / empty)
   *    n_steps       — int | null
   *    progress_step — int | null
   *    log_path      — workspace-relative string | undefined
   *    error         — string | undefined  (log excerpt for failed/orphaned)
   */
  function _ceRenderRunResults(input) {
    var el = document.getElementById('ce-test-results');
    if (!el) return;
    var status = (input && input.status) || 'unknown';
    var n = (input && input.n_steps != null) ? input.n_steps : '?';
    var prog = (input && input.progress_step != null) ? input.progress_step : 0;
    var results = (input && input.results) || {};
    var viz = (input && input.viz_html) || {};

    if (status === 'gone') {
      el.innerHTML =
        '<div style="background:#fef3c7; border:1px solid #fde68a; ' +
        'padding:10px 14px; border-radius:4px;">' +
        '<strong>This run no longer exists.</strong> It may have been deleted ' +
        'from the <a href="#simulations">Simulations tab</a>. Click <strong>' +
        'Run</strong> above to start a new one.</div>';
      return;
    }

    var bannerHtml = '';
    if (status === 'running') {
      var pct = (typeof n === 'number' && n > 0)
        ? Math.round((prog / n) * 100) : 0;
      bannerHtml =
        '<div style="margin:0 0 12px;">' +
        '<div style="background:#e5e7eb; border-radius:4px; height:10px; overflow:hidden;">' +
        '<div style="width:' + pct + '%; background:#3b82f6; height:100%;"></div>' +
        '</div>' +
        '<small style="color:#6b7280;">Running detached — step ' + _esc(String(prog)) +
        ' of ' + _esc(String(n)) + ' — safe to leave this tab.</small></div>';
    } else if (status === 'failed' || status === 'orphaned') {
      var logTxt = input && input.log_path
        ? ' See log: <code>' + _esc(input.log_path) + '</code>'
        : '';
      var errBlock = '';
      if (input && input.error) {
        errBlock =
          '<details style="margin-top:6px;"><summary style="cursor:pointer; color:#7f1d1d;">' +
          'Show log excerpt</summary><pre style="background:#fef2f2; border:1px solid #fecaca; ' +
          'padding:10px; font-size:11px; line-height:1.4; overflow:auto; max-height:320px; ' +
          'margin-top:6px; white-space:pre-wrap;">' + _esc(String(input.error).trim()) +
          '</pre></details>';
      }
      bannerHtml =
        '<div style="color:#c00; margin:0 0 12px;"><p style="margin:0;"><strong>Run ' +
        _esc(status) + '.</strong>' + logTxt + '</p>' + errBlock + '</div>';
    } else if (status === 'completed') {
      bannerHtml =
        '<p style="color:#6b7280; font-size:13px; margin:0 0 10px;">Run complete — ' +
        '<strong>' + _esc(String(n)) + '</strong> steps. ' +
        String(Object.keys(results).length) + ' observables.</p>';
    }

    var tableHtml = '';
    var keys = Object.keys(results).sort();
    if (!keys.length) {
      if (status === 'running') {
        tableHtml = '<p class="muted">No trajectory data yet.</p>';
      } else if (status === 'completed') {
        tableHtml = '<p class="muted">No observables in this run.</p>';
      }
    } else {
      tableHtml = '<table style="font-size:0.86em; width:100%;">' +
        '<thead><tr><th style="text-align:left;">Observable</th>' +
        '<th style="text-align:left; width:80px;">Steps</th>' +
        '<th style="text-align:left;">Final value</th></tr></thead><tbody>';
      keys.forEach(function(k) {
        var entries = results[k] || [];
        var last = entries[entries.length - 1];
        var preview;
        if (last == null || typeof last !== 'object') {
          preview = String(last);
        } else if (Array.isArray(last)) {
          preview = 'list[' + last.length + ']';
        } else {
          preview = '{' + Object.keys(last).length + ' keys}';
        }
        tableHtml += '<tr><td><code>' + _esc(k) + '</code></td>' +
          '<td>' + entries.length + '</td>' +
          '<td style="font-family:monospace; font-size:12px; color:#4b5563;">' +
          _esc(preview) + '</td></tr>';
      });
      tableHtml += '</tbody></table>';
    }

    var vizHtml = '';
    var vizKeys = Object.keys(viz);
    if (vizKeys.length) {
      vizHtml = '<div style="margin-top:20px;"><h4>Visualizations</h4>';
      vizKeys.forEach(function(path) {
        var payload = viz[path] || {};
        var html = payload.html || '<p>No HTML</p>';
        vizHtml +=
          '<div style="margin-bottom:12px; border:1px solid #e5e7eb; border-radius:4px;">' +
          '<div style="padding:6px 10px; background:#f3f4f6; font-family:monospace; ' +
          'font-size:12px;">' + _esc(path) + '</div>' +
          '<iframe srcdoc="' + _esc(html).replace(/&quot;/g, '&#34;') +
          '" style="width:100%; height:320px; border:0;" sandbox="allow-scripts"></iframe>' +
          '</div>';
      });
      vizHtml += '</div>';
    }

    el.innerHTML = bannerHtml + tableHtml + vizHtml;
  }
  window._ceRenderRunResults = _ceRenderRunResults;

  /** Load a prior run (or follow a live one) into the Run tab.
   *
   *  Fetches /api/composite-run/<id>/status and /api/composite-run/<id>,
   *  transforms the trajectory, renders. If status is 'running', starts a
   *  1.5s setInterval that re-fetches + re-renders until terminal.
   */
  // Monotonically-incrementing token. Every call to _ceLoadRunFromId bumps
  // this and captures its value in a closure; ticks check that they still
  // own the active token before writing to the DOM or stopping the poll.
  window._cePollToken = 0;

  function _ceLoadRunFromId(run_id) {
    if (!run_id) return;
    _ceStopRunPoll();  // clear any prior interval
    var myToken = ++window._cePollToken;
    var el = document.getElementById('ce-test-results');
    if (el) el.innerHTML = '<p class="empty-state">Loading run&hellip;</p>';

    function tick() {
      Promise.all([
        fetch('/api/composite-run/' + encodeURIComponent(run_id) + '/status')
          .then(function(r) {
            if (r.status === 404) return { _gone: true };
            return r.json();
          }),
        fetch('/api/composite-run/' + encodeURIComponent(run_id))
          .then(function(r) { return r.ok ? r.json() : { trajectory: [] }; })
          .catch(function() { return { trajectory: [] }; }),
      ]).then(function(parts) {
        // A newer _ceLoadRunFromId invocation has superseded this one —
        // drop the tick's writes on the floor to avoid stale-overwrite or
        // accidental stop of the newer poll.
        if (myToken !== window._cePollToken) return;
        var statusBody = parts[0] || {};
        var trajBody = parts[1] || {};
        if (statusBody._gone || statusBody.error === 'run not found') {
          _ceStopRunPoll();
          _ceRenderRunResults({ status: 'gone' });
          return;
        }
        var results = _trajectoryToObservables(trajBody.trajectory || []);
        _ceRenderRunResults({
          status: statusBody.status,
          results: results,
          viz_html: statusBody.viz_html,
          n_steps: statusBody.n_steps,
          progress_step: statusBody.progress_step,
          log_path: statusBody.log_path,
          error: statusBody.error,
        });
        var terminal = statusBody.status === 'completed'
                    || statusBody.status === 'failed'
                    || statusBody.status === 'orphaned';
        if (terminal) _ceStopRunPoll();
      }).catch(function(e) {
        // Transient — next tick retries. Surface to devtools for debugging.
        if (window.console && console.warn) console.warn('CE poll tick failed:', e);
      });
    }
    tick();
    window._cePollIntervalId = setInterval(tick, 1500);
  }
  window._ceLoadRunFromId = _ceLoadRunFromId;

  // -------------------------------------------------------------------------
  // Top-bar "Open PR" action
  // -------------------------------------------------------------------------

  function _openPRDialog() {
    fetch('/api/state').then(function (r) { return r.json(); }).then(function (state) {
      var branch = (state && state.active_branch) || '';
      var base = (state && state.base) || 'main';
      var titleField = document.querySelector('#form-open-pr input[name=title]');
      if (titleField && branch && !titleField.value) {
        // Strip the `investigation/` prefix in the suggested title since the
        // PR will already announce its head branch in the GitHub UI.
        var shortBranch = branch.replace(/^investigation\//, '');
        titleField.value = 'Investigation: ' + shortBranch;
      }
      var setText = function (id, txt) {
        var el = document.getElementById(id);
        if (el) el.textContent = txt;
      };
      setText('pr-head-display', branch || '<branch>');
      setText('pr-base-display', base);
      setText('pr-base-display-2', base);
      var ctx = document.getElementById('pr-suggest-context');
      if (ctx) {
        if (window._currentIsetData && window._currentIsetData.name) {
          var iset = window._currentIsetData;
          var nf = 0, nr = 0;
          (iset.studies || []).forEach(function (s) {
            nf += (s.findings || []).length;
            nr += (s.n_runs || 0);
          });
          ctx.innerHTML = '<em>Suggest</em> will draft from open investigation <code>' +
            _esc(iset.name) + '</code> (' + (iset.studies || []).length + ' studies · ' +
            nf + ' findings · ' + nr + ' runs).';
        } else {
          ctx.innerHTML = '<em>Suggest</em>: open an investigation first (Investigations tab) and re-open this dialog to draft from its findings.';
        }
      }
      openModal('modal-open-pr');
    });
  }
  window._openPRDialog = _openPRDialog;

  // ── Draft PR title / body from the active investigation ──────────────────
  // Pulls from window._currentIsetData (set by _openInvestigationDetail).
  // For title: a short kebab-style label derived from the dominant finding
  // kind or the highest-leverage follow-up. For body: a structured summary
  // (findings, runs, follow-ups) shaped as a GitHub PR description.
  function _draftPRFromInvestigation(field, form) {
    var iset = window._currentIsetData;
    if (!iset || !iset.name) {
      alert('No active investigation. Open an investigation in the Investigations tab first.');
      return;
    }
    var studies = iset.studies || [];
    var allFindings = [];
    var allFollowups = [];
    studies.forEach(function (s) {
      (s.findings || []).forEach(function (f) { allFindings.push({study: s.name, f: f}); });
      (s.follow_up_studies || []).forEach(function (f) { allFollowups.push({study: s.name, f: f}); });
    });
    var bioContradicts = allFindings.filter(function (e) { return e.f.kind === 'biological' && e.f.status === 'contradicts'; });
    var bioConfirms    = allFindings.filter(function (e) { return e.f.kind === 'biological' && e.f.status === 'confirms'; });
    var compNovel      = allFindings.filter(function (e) { return e.f.kind === 'computational' && e.f.status === 'novel'; });

    if (field === 'title') {
      var titleEl = form.querySelector('input[name=title]');
      if (!titleEl) return;
      // Heuristic: if any computational/novel findings, title leads with infra;
      // otherwise lead with the investigation question shortened.
      var label;
      if (compNovel.length && compNovel.length >= bioContradicts.length) {
        label = 'infra: ' + iset.name + ' — ' + compNovel.length + ' computational finding' + (compNovel.length === 1 ? '' : 's');
      } else if (bioContradicts.length || bioConfirms.length) {
        label = 'investigation: ' + iset.name + ' — ' +
                (bioConfirms.length ? bioConfirms.length + ' confirms' : '') +
                (bioConfirms.length && bioContradicts.length ? ' / ' : '') +
                (bioContradicts.length ? bioContradicts.length + ' contradicts vs literature' : '');
      } else {
        label = 'investigation: ' + iset.name + ' — ' + studies.length + ' studies (in-progress)';
      }
      if (label.length > 95) label = label.slice(0, 92) + '…';
      titleEl.value = label;
      titleEl.focus();
      return;
    }

    if (field === 'body') {
      var bodyEl = form.querySelector('textarea[name=body]');
      if (!bodyEl) return;
      var origBtn = (typeof event !== 'undefined') ? event.target : null;
      if (origBtn) { origBtn.disabled = true; origBtn.textContent = 'Drafting…'; }

      // Fetch composite diff in parallel so the "Model changes" section can
      // include actual file paths + line counts. Best-effort; renders without
      // the section if the fetch fails or returns no model-code changes.
      fetch('/api/work-composite-diff').then(function (r) { return r.ok ? r.json() : {changes: []}; })
        .catch(function () { return {changes: []}; })
        .then(function (diff) {
          var modelChanges = (diff && diff.changes) || [];
          bodyEl.value = _renderPRBody(iset, studies, allFindings, allFollowups, modelChanges);
          bodyEl.focus();
          if (origBtn) { origBtn.disabled = false; origBtn.textContent = 'Suggest from investigation'; }
        });
      return;
    }
  }
  window._draftPRFromInvestigation = _draftPRFromInvestigation;

  function _renderPRBody(iset, studies, allFindings, allFollowups, modelChanges) {
    var lines = [];
    // Header — investigation question.
    lines.push('## Investigation: `' + iset.name + '`');
    if (iset.question) lines.push('', '> ' + iset.question.replace(/\n+/g, ' ').trim());
    lines.push('');

    // ── Model changes (composite/process/step files) ─────────────────────
    if (modelChanges && modelChanges.length) {
      lines.push('## Model changes (' + modelChanges.length + ' file' + (modelChanges.length === 1 ? '' : 's') + ')');
      lines.push('');
      // Group by category for skimmability.
      var byCat = {};
      modelChanges.forEach(function (c) {
        (byCat[c.category] = byCat[c.category] || []).push(c);
      });
      Object.keys(byCat).sort().forEach(function (cat) {
        var rows = byCat[cat];
        var totalLines = rows.reduce(function (acc, c) { return acc + c.lines_added + c.lines_removed; }, 0);
        lines.push('**' + cat + '** (' + rows.length + ' file' + (rows.length === 1 ? '' : 's') + ', ±' + totalLines + ' lines)');
        rows.slice(0, 8).forEach(function (c) {
          lines.push('- `' + c.path + '` (+' + c.lines_added + '/−' + c.lines_removed + ')');
        });
        if (rows.length > 8) lines.push('- _…' + (rows.length - 8) + ' more_');
        lines.push('');
      });
    }

    // ── Findings (the biology/computational headline) ────────────────────
    if (allFindings.length) {
      lines.push('## Findings (' + allFindings.length + ')');
      lines.push('');
      ['biological', 'computational', 'methodological'].forEach(function (kind) {
        var kf = allFindings.filter(function (e) { return e.f.kind === kind; });
        if (!kf.length) return;
        lines.push('### ' + kind.charAt(0).toUpperCase() + kind.slice(1) + ' (' + kf.length + ')');
        kf.forEach(function (e) {
          var f = e.f;
          var glyph = ({confirms: '✓', contradicts: '✗', partial: '◐', novel: '◆'})[f.status || 'novel'];
          var stmt = (f.statement || '').split('\n')[0].slice(0, 220);
          var ref = '';
          if (f.expected && f.expected.cites && f.expected.cites.length) {
            ref = ' (cites: ' + f.expected.cites.slice(0, 3).map(function (c) { return '`' + c + '`'; }).join(', ') + ')';
          } else if (f.expert_reference && f.expert_reference.doc) {
            ref = ' (expert ref: `' + f.expert_reference.doc + '`)';
          }
          lines.push('- **' + glyph + ' ' + (f.id || '') + '** (' + e.study + '): ' + stmt + ref);
        });
        lines.push('');
      });
    }

    // ── Studies summary ──────────────────────────────────────────────────
    lines.push('## Studies (' + studies.length + ')');
    lines.push('');
    lines.push('| Study | Phase | Status | Findings | Follow-ups |');
    lines.push('|---|---|---|---|---|');
    studies.forEach(function (s) {
      lines.push('| `' + s.name + '` | ' + (s.phase || '—') + ' | ' + (s.status || '—') +
                 ' | ' + ((s.findings || []).length) + ' | ' + ((s.follow_up_studies || []).length) + ' |');
    });
    lines.push('');

    // ── Report ───────────────────────────────────────────────────────────
    // Committed by the Open-PR flow before the PR is created.
    lines.push('## Generated report');
    lines.push('');
    lines.push('Committed alongside this PR as `reports/investigation-' + iset.name + '.html`. ' +
               'Open it from the GitHub file browser to read the per-study findings inline.');
    lines.push('');

    // ── Test plan ────────────────────────────────────────────────────────
    var openF = allFollowups.filter(function (e) { return e.f.status !== 'done'; });
    if (openF.length) {
      lines.push('## Test plan');
      lines.push('');
      openF.slice(0, 10).forEach(function (e) {
        var t = (e.f.title || '').replace(/\n+/g, ' ').trim();
        lines.push('- [ ] ' + t + ' _(' + (e.f.kind || 'other') + ', from ' + e.study + ')_');
      });
      lines.push('');
    }

    lines.push('---');
    lines.push('_Drafted from the dashboard\'s Investigations view — `' + iset.name + '` (' +
               studies.length + ' studies). Edit freely before submitting._');
    return lines.join('\n');
  }

  function _submitOpenPR(form) {
    var fd = new FormData(form);
    var prBody = {
      title: (fd.get('title') || '').trim(),
      body: (fd.get('body') || '').trim(),
      draft: !!fd.get('draft'),
    };
    var submit = form.querySelector('button[type=submit]');
    var origLabel = submit ? submit.textContent : 'Create PR';
    var setStatus = function(label) {
      if (submit) { submit.disabled = true; submit.textContent = label; }
    };
    var resetStatus = function() {
      if (submit) { submit.disabled = false; submit.textContent = origLabel; }
    };

    // Step 1: when an investigation is open, generate + attach its HTML
    // report so the PR ships with the report under /reports/<name>.html.
    // The flow is best-effort — if report generation fails we still create
    // the PR (with a warning).
    var iset = window._currentIsetData;
    var attachPromise;
    if (iset && iset.name) {
      setStatus('Generating report…');
      attachPromise = _generateReportHtmlForCurrentIset()
        .then(function (html) {
          if (!html) return null;
          var filename = 'investigation-' + iset.name + '.html';
          setStatus('Committing report…');
          return fetch('/api/work-attach-report', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
              filename: filename,
              html: html,
              commit_message: 'docs(report): refresh investigation report for PR',
            }),
          }).then(function (r) { return r.json().then(function (j) { return [r.ok, j]; }); });
        });
    } else {
      attachPromise = Promise.resolve(null);
    }

    attachPromise.then(function (attachRes) {
      // Attachment is best-effort. Log + continue; don't block the PR.
      if (attachRes && Array.isArray(attachRes)) {
        var ok = attachRes[0], j = attachRes[1];
        if (!ok) {
          console.warn('Report attach failed (continuing without):', j);
        }
      }
      setStatus('Creating PR…');
      return fetch('/api/work-create-pr', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(prBody),
      }).then(function (r) { return r.json().then(function (j) { return [r.ok, j]; }); });
    })
    .then(function (pair) {
      var ok = pair[0], j = pair[1];
      if (!ok) {
        var msg = j.error || 'unknown error';
        if (j.manual_url) msg += '\n\nManual URL: ' + j.manual_url;
        alert('PR create failed: ' + msg);
        return;
      }
      closeModal('modal-open-pr');
      alert('PR created: ' + (j.pr_url || ''));
      window.open(j.pr_url, '_blank');
      _refreshGitStatus();
    })
    .catch(function (e) {
      console.error('Open-PR flow error:', e);
      alert('Open-PR flow error: ' + (e && e.message || e));
    })
    .finally(resetStatus);
  }
  window._submitOpenPR = _submitOpenPR;

  // Generate the investigation HTML report for the currently-open iset by
  // re-running the same client-side build path as the "Generate report"
  // button. Returns a Promise<string|null>.
  function _generateReportHtmlForCurrentIset() {
    var name = window._currentIset;
    if (!name) return Promise.resolve(null);
    // Route through DataSource so hosted/snapshot mode (sub-projects #2/#3)
    // honours the configured source here too.  Direct-fetch fallback keeps
    // behaviour unchanged when DataSource is not available.
    var _isetFetch = (window.DataSource && window.DataSource.loadInvestigation)
      ? window.DataSource.loadInvestigation(name)
      : fetch('/api/iset/' + encodeURIComponent(name)).then(function (r) { return r.json(); });
    return _isetFetch.then(function (iset) {
        var studyFetches = (iset.studies || []).map(function (s) {
          return ((window.DataSource && window.DataSource.loadStudy)
            ? window.DataSource.loadStudy(s.name).catch(function () { return {spec: {name: s.name}}; })
            : fetch('/api/study/' + encodeURIComponent(s.name))
                .then(function (r) { return r.ok ? r.json() : {spec: {name: s.name}}; }))
            .then(function (j) { return j.spec || j; });
        });
        var bibFetch = fetch('/api/references-bib')
          .then(function (r) { return r.ok ? r.json() : {entries: []}; })
          .then(function (j) { return j.entries || []; })
          .catch(function () { return []; });
        var chartFetches = (iset.studies || []).map(function (s) {
          return fetch('/api/study-charts/' + encodeURIComponent(s.name))
            .then(function (r) { return r.ok ? r.json() : {charts: []}; })
            .then(function (j) { return {name: s.name, charts: j.charts || []}; })
            .catch(function () { return {name: s.name, charts: []}; });
        });
        var ghRepoFetch = fetch('/api/github-repo')
          .then(function (r) { return r.ok ? r.json() : {repo: null}; })
          .then(function (j) { return (j && j.repo) || null; })
          .catch(function () { return null; });
        // Wave 3b #6/#16 — competing hypotheses + computed support_log.
        var hypFetch = fetch('/api/investigation-hypotheses?investigation=' + encodeURIComponent(iset.name))
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (j) { return (j && j.hypotheses) || null; })
          .catch(function () { return null; });
        return Promise.all([Promise.all(studyFetches), bibFetch, Promise.all(chartFetches), ghRepoFetch, hypFetch])
          .then(function (arr) {
            var chartsByStudy = {};
            arr[2].forEach(function (c) { chartsByStudy[c.name] = c.charts; });
            return _buildInvestigationReportHtml(iset, arr[0], arr[1], chartsByStudy, undefined, null, arr[3], undefined, undefined, arr[4]);
          });
      });
  }
  window._generateReportHtmlForCurrentIset = _generateReportHtmlForCurrentIset;


  // -------------------------------------------------------------------------
  // Top-bar live git-status strip
  // -------------------------------------------------------------------------

  // Populate the GitHub tab's "Workspace repository" rows. Hides each row
  // when its value is empty so the settings page stays tidy. Pass null to
  // reset all rows to a "no branch" state.
  function _setRow(id, html, hint) {
    var row = document.getElementById('viv-gh-row-' + id);
    var val = document.getElementById('viv-gh-' + id);
    if (!row || !val) return;
    if (html == null || html === '') { row.hidden = true; return; }
    row.hidden = false;
    val.innerHTML = html + (hint ? '<div class="gh-value-hint">' + hint + '</div>' : '');
  }

  function _renderGitStatusRows(s) {
    if (!document.getElementById('viv-gh-row-repo')) return;  // page not present
    if (s == null) {
      _setRow('repo', '<span class="muted">not a git workspace</span>');
      ['branch', 'push-state', 'ahead', 'dirty', 'pr'].forEach(function (id) { _setRow(id, ''); });
      return;
    }
    // Repository
    _setRow('repo', s.upstream_repo
      ? '<a href="' + s.repo_url + '" target="_blank" rel="noopener">' + _esc(s.upstream_repo) + '</a> ↗'
      : '<span class="muted">no upstream remote configured</span>');
    // Branch
    _setRow('branch', s.branch
      ? (s.branch_url
          ? '<a href="' + s.branch_url + '" target="_blank" rel="noopener"><code>' + _esc(s.branch) + '</code></a> ↗'
          : '<code>' + _esc(s.branch) + '</code>')
      : '<span class="muted">no branch</span>');
    // Push state
    var stateMap = {
      pushed:   '<span class="git-badge git-badge-ok">✓ pushed</span>',
      ahead:    '<span class="git-badge git-badge-ahead">↑ ' + s.ahead + ' ahead of remote</span>',
      behind:   '<span class="git-badge git-badge-behind">↓ ' + s.behind + ' behind remote</span>',
      diverged: '<span class="git-badge git-badge-warn">! diverged from remote</span>',
    };
    _setRow('push-state', stateMap[s.push_state] || '<span class="git-badge git-badge-warn">⊘ no origin</span>');
    // Ahead of base
    if (s.ahead_of_base > 0) {
      var aheadHtml = s.compare_url
        ? '<a href="' + s.compare_url + '" target="_blank" rel="noopener">' + s.ahead_of_base + ' commits ahead of <code>' + _esc(s.base) + '</code></a> ↗'
        : s.ahead_of_base + ' commits ahead of <code>' + _esc(s.base) + '</code>';
      _setRow('ahead', aheadHtml);
    } else {
      _setRow('ahead', s.base
        ? '<span class="muted">up to date with <code>' + _esc(s.base) + '</code></span>'
        : '');
    }
    // Working tree
    if (s.dirty_count > 0) {
      _setRow('dirty',
        '<a href="#" onclick="event.preventDefault();_toggleDirtyPanel();return false">'
        + s.dirty_count + ' uncommitted file' + (s.dirty_count === 1 ? '' : 's') + '</a>',
        'Click to view + stage');
    } else {
      _setRow('dirty', '<span class="muted">clean</span>');
    }
    // Pull request
    if (s.pr_url) {
      var prState = (s.pr_state || 'open').toLowerCase();
      _setRow('pr', '<a class="git-badge git-badge-pr pr-state-' + prState + '" href="'
        + s.pr_url + '" target="_blank" rel="noopener">PR #' + s.pr_number + ' ↗</a>'
        + ' <span class="muted small">(' + _esc(prState) + ')</span>');
    } else {
      _setRow('pr', '<span class="muted">no PR linked yet</span>');
    }
  }

  function _refreshGitStatus() {
    fetch('/api/git-status').then(function (r) { return r.json(); }).then(function (s) {
      // Legacy single-string box (still populated for any consumer that
      // reads it). The GitHub-tab settings page renders the same data into
      // individual rows via _renderGitStatusRows below.
      var box = document.getElementById('viv-git-status');
      if (box) {
        if (!s.branch) { box.hidden = true; }
        else { box.hidden = false; }
      }
      if (!s.branch) {
        _renderGitStatusRows(null);
        return;
      }

      // push-state badge
      var stateBadge;
      switch (s.push_state) {
        case 'pushed':    stateBadge = '<span class="git-badge git-badge-ok">✓ pushed</span>'; break;
        case 'ahead':     stateBadge = '<span class="git-badge git-badge-ahead">↑ ' + s.ahead + ' ahead</span>'; break;
        case 'behind':    stateBadge = '<span class="git-badge git-badge-behind">↓ ' + s.behind + ' behind</span>'; break;
        case 'diverged':  stateBadge = '<span class="git-badge git-badge-warn">! diverged</span>'; break;
        default:          stateBadge = '<span class="git-badge git-badge-warn">⊘ no origin</span>';
      }

      var repoPart = s.upstream_repo
        ? '<a href="' + s.repo_url + '" target="_blank" rel="noopener" class="git-repo">' + _esc(s.upstream_repo) + '</a>'
        : '<span class="muted">no upstream</span>';
      var branchPart = s.branch_url
        ? ' @ <a href="' + s.branch_url + '" target="_blank" rel="noopener" class="git-branch">' + _esc(s.branch) + '</a>'
        : ' @ <span class="git-branch">' + _esc(s.branch) + '</span>';

      // ahead-of-base badge
      var aheadOfBasePart = (s.ahead_of_base > 0 && s.compare_url)
        ? ' <a class="git-badge git-badge-info" href="' + s.compare_url + '" target="_blank" rel="noopener">↗ ' + s.ahead_of_base + ' ahead of ' + _esc(s.base) + '</a>'
        : (s.ahead_of_base > 0
          ? ' <span class="git-badge git-badge-info">↗ ' + s.ahead_of_base + ' ahead of ' + _esc(s.base) + '</span>'
          : '');

      // dirty-files pill
      var dirtyPart = (s.dirty_count > 0)
        ? ' <span class="git-badge git-badge-warn dirty-pill" onclick="event.stopPropagation();_toggleDirtyPanel()" title="' + s.dirty_count + ' uncommitted file' + (s.dirty_count === 1 ? '' : 's') + '">' + s.dirty_count + ' uncommitted</span>'
        : '';

      // PR badge
      var prState = (s.pr_state || 'open').toLowerCase();
      var prPart = s.pr_url
        ? ' <a class="git-badge git-badge-pr pr-state-' + prState + '" href="' + s.pr_url + '" target="_blank" rel="noopener">PR #' + s.pr_number + ' ↗</a>'
        : '';

      if (box) box.innerHTML = repoPart + branchPart + ' ' + stateBadge + aheadOfBasePart + dirtyPart + prPart;

      // Settings-style per-row population for the GitHub tab.
      _renderGitStatusRows(s);

      // Goal 5: hide "Open PR" button when a PR already exists
      var openPrBtn = document.getElementById('btn-open-pr');
      if (openPrBtn) openPrBtn.hidden = !!s.pr_url;

      // Action buttons (Link branch / Push / End workstream). When the
      // dedicated #viv-git-actions container exists (GitHub tab layout) we
      // render the action buttons there as a clear separate row alongside
      // the existing Open-PR button. Otherwise fall back to inline append
      // for layouts that still embed everything inside #viv-git-status.
      var actions = [];
      if (!s.upstream_repo) {
        actions.push(s.gh_available
          ? '<button class="ws-btn ws-primary" onclick="_linkBranch()">Link branch to upstream</button>'
          : '<span class="ws-warn" title="Install GitHub CLI">gh CLI missing</span>');
      } else if (s.push_state === 'ahead') {
        actions.push('<button class="ws-btn" onclick="_pushWork()">Push (' + s.ahead + ')</button>');
      }
      if (s.has_active_workstream) {
        actions.push('<button class="ws-btn ws-end" onclick="_endWork()" title="Switch back to ' + _esc(s.base) + ' (workstream branch is preserved)">End</button>');
      }
      var actionsHost = document.getElementById('viv-git-actions');
      if (actionsHost) {
        // Replace any previously-injected actions (preserve the static
        // Open-PR button that lives in the markup with id="btn-open-pr").
        actionsHost.querySelectorAll('[data-injected-action]').forEach(function (n) { n.remove(); });
        if (actions.length) {
          var tmp = document.createElement('span');
          tmp.dataset.injectedAction = '1';
          tmp.innerHTML = actions.join(' ');
          actionsHost.appendChild(tmp);
        }
      } else if (actions.length) {
        box.innerHTML += ' <span class="git-status-actions">' + actions.join(' ') + '</span>';
      }
    }).catch(function () { /* silent */ });
  }
  window._refreshGitStatus = _refreshGitStatus;

  // ------------------------------------------------------------------
  // GitHub tab — default-org picker. Populates #viv-gh-default-org from
  // /api/auth/github/orgs once the user is signed in. Persists the
  // selection to localStorage; new-workspace flows can read it. (Backend
  // workspace.yaml.github_org persistence is a follow-up; this UX gives
  // configurability now.)
  // ------------------------------------------------------------------
  var GH_DEFAULT_ORG_KEY = 'viv-dashboard-default-github-org';

  function _loadGithubOrgs() {
    var sel = document.getElementById('viv-gh-default-org');
    var hint = document.getElementById('viv-gh-default-org-hint');
    if (!sel) return;
    sel.disabled = true;
    fetch('/api/auth/github/orgs').then(function (r) {
      if (r.status === 401) {
        sel.innerHTML = '<option value="">Sign in to load orgs…</option>';
        if (hint) hint.textContent = '';
        return;
      }
      if (!r.ok) {
        sel.innerHTML = '<option value="">Could not load orgs</option>';
        if (hint) hint.textContent = 'GitHub returned HTTP ' + r.status + '.';
        return;
      }
      return r.json().then(function (data) {
        // API shape: {login, orgs: [{name, kind: "personal"|"org"}, ...]}
        var orgs = (data && data.orgs) || [];
        var saved = '';
        try { saved = localStorage.getItem(GH_DEFAULT_ORG_KEY) || ''; } catch (_e) {}
        sel.innerHTML = orgs.map(function (o) {
          var name = (o && o.name) ? o.name : String(o || '');
          var label = (o && o.kind === 'personal') ? (name + ' (personal)') : name;
          var selAttr = (name === saved) ? ' selected' : '';
          return '<option value="' + _esc(name) + '"' + selAttr + '>' + _esc(label) + '</option>';
        }).join('') || '<option value="">No orgs found</option>';
        if (hint) {
          hint.textContent = saved
            ? 'Default: ' + saved + ' (saved in this browser).'
            : 'Pick one to use as the default for new-repo flows.';
        }
      });
    }).catch(function () {
      sel.innerHTML = '<option value="">Network error</option>';
    }).then(function () {
      sel.disabled = false;
    });
  }
  window._loadGithubOrgs = _loadGithubOrgs;

  document.addEventListener('DOMContentLoaded', function () {
    var sel = document.getElementById('viv-gh-default-org');
    if (sel) {
      sel.addEventListener('change', function () {
        try { localStorage.setItem(GH_DEFAULT_ORG_KEY, sel.value || ''); } catch (_e) {}
        var hint = document.getElementById('viv-gh-default-org-hint');
        if (hint && sel.value) hint.textContent = 'Default: ' + sel.value + ' (saved in this browser).';
      });
    }
    _loadGithubOrgs();

    // Re-load orgs when the github-login chip flips to authenticated. Keeps
    // github-login.js untouched (no cross-file coupling) — we just observe
    // the data-state attribute the widget already maintains.
    var chip = document.getElementById('viv-gh-chip');
    if (chip && typeof MutationObserver !== 'undefined') {
      var lastState = chip.dataset.state;
      new MutationObserver(function () {
        var s = chip.dataset.state;
        if (s !== lastState && s === 'in') _loadGithubOrgs();
        lastState = s;
      }).observe(chip, { attributes: true, attributeFilter: ['data-state'] });
    }
  });

  document.addEventListener('DOMContentLoaded', _refreshGitStatus);

  // -------------------------------------------------------------------------
  // Spine A3: per-study readiness panel (lint findings)
  // -------------------------------------------------------------------------
  // Fetches the deterministic report linter ONCE (/api/report-lint), keys the
  // findings by study, and fills each `.study-readiness-panel` placeholder a
  // study section rendered. Mirrors the param-enforcement banner: surfaced per
  // study, connected to its source (the linter), and labeled code-computed
  // (vs human-authored). AI-free — pure deterministic linter output. Surfaces
  // the SP2b-ii readout-migration + SP2c band-citation-gap findings.
  function _readinessPanelHtml(findings) {
    var sev = { error: 0, warning: 0, info: 0 };
    var byCheck = {};
    findings.forEach(function (f) {
      var s = f.severity || 'info';
      if (sev[s] != null) sev[s]++; else sev.info++;
      var c = f.check || 'other';
      (byCheck[c] = byCheck[c] || []).push(f);
    });
    var gaps = sev.error + sev.warning;
    var head, bg, bd, col;
    if (!findings.length) { head = '✓ Ready'; bg = '#f0fdf4'; bd = '#16a34a'; col = '#166534'; }
    else if (gaps) { head = '⚠ ' + gaps + ' gap' + (gaps === 1 ? '' : 's'); bg = '#fffbeb'; bd = '#f59e0b'; col = '#92400e'; }
    else { head = 'ℹ ' + sev.info + ' note' + (sev.info === 1 ? '' : 's'); bg = '#eff6ff'; bd = '#3b82f6'; col = '#1e40af'; }
    var lbl = '<span class="small" style="color:#64748b">code-computed by the report linter (deterministic)</span>';
    // Ready → no dropdown needed.
    if (!findings.length) {
      return '<div class="readiness-banner" style="margin:12px 0;padding:12px 16px;background:' + bg
        + ';border:1px solid ' + bd + ';border-left-width:5px;border-radius:6px;color:' + col + '">'
        + '<strong>Readiness: ' + head + '</strong> ' + lbl + '</div>';
    }
    // Key info on top: per-check breakdown, most-frequent first (so noise like
    // viz_stale_vs_latest_run is summarised, not enumerated, until expanded).
    var checks = Object.keys(byCheck).sort(function (a, b) { return byCheck[b].length - byCheck[a].length; });
    var breakdown = checks.map(function (c) { return byCheck[c].length + '× ' + _h(c); }).join(' &nbsp;·&nbsp; ');
    var groups = checks.map(function (c) {
      var items = byCheck[c].map(function (f) {
        var s = f.severity || 'info';
        var dot = s === 'error' ? '#dc2626' : (s === 'warning' ? '#f59e0b' : '#3b82f6');
        return '<li style="margin-top:3px"><span style="color:' + dot + ';font-weight:700">●</span> ' + _h(f.message || '') + '</li>';
      }).join('');
      return '<div style="margin-top:9px"><code>' + _h(c) + '</code> '
        + '<span class="small" style="color:#94a3b8">(' + byCheck[c].length + ')</span>'
        + '<ul class="small" style="margin:3px 0 0 18px;padding:0">' + items + '</ul></div>';
    }).join('');
    return '<details class="readiness-banner" style="margin:12px 0;background:' + bg
      + ';border:1px solid ' + bd + ';border-left-width:5px;border-radius:6px;color:' + col + '">'
      + '<summary style="padding:12px 16px;cursor:pointer;list-style:none;outline:none">'
      + '<strong>Readiness: ' + head + '</strong> ' + lbl
      + '<div class="small" style="color:#64748b;margin-top:5px">' + breakdown
      + ' &nbsp;·&nbsp; <span style="opacity:.7;font-style:italic">click to expand</span></div>'
      + '</summary>'
      + '<div style="padding:2px 16px 12px 16px">' + groups + '</div>'
      + '</details>';
  }
  // Idempotent + safe to call repeatedly. The linter findings are fetched ONCE
  // and cached on the function; every call (re-)keys whatever
  // `.study-readiness-panel` placeholders are currently in the DOM by
  // overwriting their innerHTML — so a second call after more panels render
  // fills the new ones without issuing a duplicate fetch or double-rendering.
  // No outer closure state is used (cache lives on the function object) so the
  // investigation report can bake an exact copy via `.toString()` — see
  // _buildInvestigationReportHtml; the served/downloaded report has no
  // walkthrough.js, so it carries its own copy and invokes it after its study
  // sections render (the placeholders are emitted async, after DOMContentLoaded).
  function _populateReadinessPanels() {
    var panels = document.querySelectorAll('.study-readiness-panel');
    if (!panels.length) return;
    function _apply(byStudy) {
      document.querySelectorAll('.study-readiness-panel').forEach(function (el) {
        var slug = el.getAttribute('data-study') || '';
        el.innerHTML = _readinessPanelHtml(byStudy[slug] || []);
      });
    }
    if (_populateReadinessPanels._cache) { _apply(_populateReadinessPanels._cache); return; }
    if (_populateReadinessPanels._pending) return;
    _populateReadinessPanels._pending = true;
    fetch('/api/report-lint')
      .then(function (r) { return r.ok ? r.json() : { findings: [] }; })
      .then(function (j) {
        var byStudy = {};
        (j.findings || []).forEach(function (f) {
          var k = f.study || '<workspace>';
          (byStudy[k] = byStudy[k] || []).push(f);
        });
        _populateReadinessPanels._pending = false;
        _populateReadinessPanels._cache = byStudy;
        _apply(byStudy);
      })
      .catch(function () { _populateReadinessPanels._pending = false; });
  }
  window._populateReadinessPanels = _populateReadinessPanels;
  // study-detail / live-DOM contexts: panels that exist at parse time get
  // populated on load. The investigation report renders its panels async, so it
  // additionally bakes + invokes this from its own render-completion (below).
  document.addEventListener('DOMContentLoaded', _populateReadinessPanels);

})();
