// walkthrough.js — v0.8.0: Registry Full view is now directly runnable — the config bar IS the editable config and the left ports ARE the editable input fields (no "Run this process" dropdown); Run lives in the body, outputs on the right. Middle (grid) zoom shows ports+types inline (no dropdown); double-click a card → runnable Full. Modules table split into Installed-here vs Marketplace sections with a Repos (imported-into) column, GitHub link on the name, and Install/Uninstall in one Action column (n_repos from module_stats federation scan). bigraph-loom: Explore (graph) is the default left tab; the right dock defaults to Processes with Nodes/Inspector collapsed. v0.7.0: Composites semantic zoom (Table/Cards full-row compact/Loom on-demand embed) + double-click to zoom in; /api/composites now runs in the WARM pooled worker (flake fix). v0.6.9: registry filter now data-driven (works in Table/Cards/Full); middle Cards zoom is full-row with composite/study usage split + details; double-click zooms in centered; select persists across zoom; run panel input ports as per-field form (type + resolved default, auto-grow) + Copy outputs; loom config bar lightened to match workbench palette. v0.6.8: run panel lazy-loads RESOLVED defaults (core.fill via /api/registry/process-template) into a per-field config form + inputs JSON (no more null-heavy templates); loom card restyled as a crisp rectangle. v0.6.7: Registry Full-view interactive runner — editable config + input-port JSON, Run → outputs (POST /api/registry/run-process; env_worker._run_process instantiates + Step.update / Process.update(interval)); loom inputs left / outputs right. v0.6.6: Registry semantic zoom (compact/detailed/full loom-rectangle: inputs left, outputs right, config top) + Cards⇄Table sortable view (_setRegistryZoom/_setRegistryView/_renderRegistryTable); rail pins hover-only + ungrouped back to a collapsible folder. v0.6.5: Registry processes sorted by USE (most-referenced across composites/runners first) with a use-count badge (build_registry._annotate_use_counts source-scan). v0.6.4: Registry page — "Discovered registry"→"Registry" (main tab), "Modules"→"Marketplace"; rich registry entries (description + inputs/outputs ports/contract + full config schema, loom-like) and a new Report Cards tab (_renderRegistryEntry/_regPortColumn). v0.6.3: STUDIES rail — per-study pin toggle (localStorage) with a "Pinned" strip at the top for quick access, and ungrouped studies rendered as a flat list at the bottom instead of a collapsible dropdown (_toggleStudyPin/_loadPinnedStudies; _railStudyItem + _renderRailInvestigationGroups). v0.6.2: Marketplace merged into the Modules tab — Modules grid loads the FULL ecosystem via /api/marketplace (available modules under the "Available to install" divider), installed cards gain an Uninstall action gated by an impact-confirmation modal (_showUninstallImpactModal via /api/catalog-uninstall-impact), viva-* display names + stat chips. v0.6.1: Marketplace sub-tab — browse the FULL viva ecosystem (unfiltered by registry.include) + install (_loadMarketplace/_renderMarketplace via /api/marketplace; shared _renderModuleGrid/_moduleActionFor with the Modules tab). v0.6.0: system-deps awareness — pre-install check + consent modal (_installFromCatalog → _showSystemDepsModal; new _checkSystemDepsForInstalled on Registry rows); v0.5.3: investigation detail panel — Spec/Runs/Visualizations tabs + Run button + Delete; v0.5.2: composite explorer UX fixes (no focus-mode hijack, one-row-per-param layout, lazy-load composite cache); v0.5.1: composite explorer page (bigraph-viz + test run + promote to simulation); v0.4.14: Available Composites picker + Emitter Use feedback + drop process multi-select; v0.4.5: _renderInstallError structured diagnosis; v0.4.1: _loadCatalog + _installFromCatalog; v0.4.0b: active-branch workstream strip; v0.3.7-A: _installImport; v0.3.6: Registry tab; v0.1.9: drag-drop uploads; v0.1.7: interactive forms.
(function () {
  "use strict";

  // Prefix a root-absolute /api path with the dashboard base path (e.g. /workbench)
  // so composite-explore run/resolve/status calls reach the workbench under the
  // co-tenant ALB instead of misrouting to sms-api → 404. No-op at root; composes
  // safely with the global _base_path_shim (which skips already-prefixed URLs).
  function _api(p) {
    return (window.DataSource && window.DataSource.apiUrl) ? window.DataSource.apiUrl(p) : p;
  }

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
  // Investigation DAG band state
  // -------------------------------------------------------------------------
  var aigBand = 1;                 // 0=far, 1=mid, 2=near (default = current detail)
  var _lastDagArgs = null;         // [studies, chainsBySlug] for re-render on band change

  // Layer-4 pull-or-compute: per-study cached/compute status for the current
  // investigation DAG, keyed by study slug, plus the investigation slug the
  // "Run this study" / "Continue from here" buttons trigger against. Populated
  // from GET /api/investigation-trigger-status alongside the graph fetch.
  var _dagTriggerBySlug = {};
  var _dagInvSlug = '';

  // -------------------------------------------------------------------------
  // Investigation DAG orientation (LR = left-to-right, TB = top-to-bottom).
  // Auto-picked per investigation shape (see chooseGraphOrientation in
  // aig-graph.js) unless the user has manually toggled it, in which case the
  // choice is persisted per-investigation in localStorage and wins over auto.
  // -------------------------------------------------------------------------
  function _graphOrientationKey(name) {
    return 'aig-orientation:' + (name || 'default');
  }
  function _getStoredGraphOrientation(name) {
    try {
      var v = window.localStorage.getItem(_graphOrientationKey(name));
      if (v === 'LR' || v === 'TB') return v;
    } catch (e) { /* private mode / no localStorage */ }
    return null;
  }
  function _setGraphOrientation(o) {
    if (o !== 'LR' && o !== 'TB') return;
    try { window.localStorage.setItem(_graphOrientationKey(window._currentIset), o); } catch (e) { /* ignore */ }
    if (_lastDagArgs) _renderInvestigationDag(_lastDagArgs[0], _lastDagArgs[1], _lastDagArgs[2]);
  }
  window._setGraphOrientation = _setGraphOrientation;
  function _resetGraphOrientation() {
    try { window.localStorage.removeItem(_graphOrientationKey(window._currentIset)); } catch (e) { /* ignore */ }
    if (_lastDagArgs) _renderInvestigationDag(_lastDagArgs[0], _lastDagArgs[1], _lastDagArgs[2]);
  }
  window._resetGraphOrientation = _resetGraphOrientation;
  // Reflect the active/override state on the toggle control, if present.
  function _syncGraphOrientToggleUI(orient, isOverride) {
    var lrBtn = document.getElementById('aig-orient-lr');
    var tbBtn = document.getElementById('aig-orient-tb');
    var autoBtn = document.getElementById('aig-orient-auto');
    function _mark(btn, active) {
      if (!btn) return;
      btn.style.background = active ? '#e0e7ff' : 'transparent';
      btn.style.color = active ? '#3730a3' : '#64748b';
      btn.style.fontWeight = active ? '700' : '400';
      btn.setAttribute('aria-pressed', active ? 'true' : 'false');
    }
    _mark(lrBtn, orient === 'LR');
    _mark(tbBtn, orient === 'TB');
    if (autoBtn) autoBtn.style.visibility = isOverride ? 'visible' : 'hidden';
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
    // Snapshot (read-only) mode: there is no in-memory state to re-post and no
    // live API. Open the popup straight at the static loom URL (same ?static=1
    // &stateUrl= the iframe uses) so it fetches and renders the cached state.
    if (snapshot.snapshot && snapshot.loomUrl) {
      var sw = window.open(snapshot.loomUrl, '_blank',
        'width=1200,height=800,menubar=no,toolbar=no,location=no,resizable=yes,scrollbars=yes');
      if (!sw) {
        alert('Popup blocked. Allow popups from this site to pop out the wiring view.');
        return;
      }
      _showPopoutPlaceholder(iframeId, sw);
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
    var href = base + '/studies/' + encodeURIComponent(name);
    // Static snapshot bundles are served by object storage (e.g. Cloudflare R2)
    // that does NOT auto-serve index.html for a directory path — so a bare
    // '/studies/<name>' 404s there. Address the shell file explicitly in snapshot
    // mode. (The live server's /studies/<name> route is unaffected: mode is not
    // 'snapshot' there.)
    if ((window.__DASH_CONFIG__ || {}).mode === 'snapshot') href += '/index.html';
    return href;
  }
  window._studyHref = _studyHref;

  // Size an embedded study iframe to the space actually left below it, instead
  // of the hardcoded calc(100vh - 220px) guess at how tall the chrome above is.
  // That guess was ~150px short on a 1000px viewport: an 850px porthole onto a
  // 2100px report, so the report's scrollbar sat inside the page's scrollbar
  // with a strip of dead space under it. These embeds keep their OWN scroll
  // context on purpose (the study's .study-tabs are position:sticky and stick to
  // the iframe's top), so the fix is to make the porthole reach the bottom of
  // the window — not to auto-grow the iframe, which would strand the tabs.
  // `panel` is the embed's wrapper (header + iframe). Measuring the header
  // WITHIN the panel keeps this independent of scroll position — reading the
  // frame's viewport top instead would race the smooth scrollIntoView that
  // opens the embed and, mid-flight, compute a negative height that clamps to
  // the minimum (the bug this replaced).
  function _fitEmbedToViewport(frame, panel, minH) {
    if (!frame) return;
    var fit = function () {
      if (!frame.isConnected) return;
      var chrome = 0;
      if (panel) {
        var pr = panel.getBoundingClientRect(), fr = frame.getBoundingClientRect();
        chrome = Math.max(0, Math.round(fr.top - pr.top));   // the embed's own header
      }
      var h = Math.max(minH || 480, Math.round(window.innerHeight - chrome - 24));
      frame.style.height = h + 'px';
    };
    fit();
    if (!frame._fitBound) {
      window.addEventListener('resize', fit);
      frame._fitBound = true;
    }
  }
  window._fitEmbedToViewport = _fitEmbedToViewport;

  // Auto-grow a SAME-ORIGIN embed iframe to its CONTENT height so the OUTER page
  // scrolls as one continuous surface (contrast _fitEmbedToViewport, which pins
  // the frame to one screen with an internal scrollbar). Used by the workspace
  // study porthole: with the investigation context kept expanded above it, you
  // can scroll straight up out of the study into the knowledge graph and past it
  // to the overview. Measures at height:0 first so a study page whose body
  // stretches to the frame (min-height:100%) can't inflate the reading into a
  // feedback loop — at 0 height, body.scrollHeight is the true content height.
  // Snapshot the scrollTop of every scrollable ancestor of `el` (plus the
  // document scroller). The workbench scrolls inside a container, not the
  // window, so a fix that only touches window scroll is a no-op — capture all
  // of them and restore whichever the browser clamped.
  function _captureScrollTops(el) {
    var out = [];
    var n = el && el.parentElement;
    while (n) {
      if (n.scrollHeight - n.clientHeight > 1 && n.scrollTop > 0) out.push([n, n.scrollTop]);
      n = n.parentElement;
    }
    var se = document.scrollingElement || document.documentElement;
    if (se && se.scrollTop > 0) out.push([se, se.scrollTop]);
    return out;
  }
  function _restoreScrollTops(savers) {
    for (var i = 0; i < savers.length; i++) {
      if (savers[i][0].scrollTop !== savers[i][1]) savers[i][0].scrollTop = savers[i][1];
    }
  }

  function _fitEmbedToContent(frame, minH) {
    if (!frame) return;
    var fit = function (fromObserver) {
      if (!frame.isConnected) return;
      // During the initial landing scroll (set by _wsOpenStudyTab), skip
      // observer-driven refits. Their synchronous height:0 measure clamps
      // window.scrollY and the restore below cancels the in-flight smooth
      // scroll-to-study — the "starts down, then snaps back up to the graph"
      // glitch. _wsOpenStudyTab runs one final _refit once the window closes.
      if (fromObserver && window._embedLandingUntil && Date.now() < window._embedLandingUntil) return;
      var doc;
      try { doc = frame.contentDocument; } catch (_) { return; }   // cross-origin -> bail
      if (!doc || !doc.body) return;
      // Preserve the scroll position across the height:0 measurement. Reading
      // scrollHeight at height:0 forces a reflow with the porthole collapsed;
      // when the frame sits below the fold, that momentary shrink drops the
      // document/container scrollHeight below its current scrollTop, so the
      // browser CLAMPS scrollTop toward 0 — and once we restore the height the
      // view is left yanked up to the investigation graph ("jumps back up").
      // CRUCIAL: the workbench content scrolls INSIDE a container
      // (.viv-content, overflow-y:auto), NOT the window — window.scrollY stays
      // 0, so restoring window did nothing (the residual bug). Snapshot every
      // scrollable ancestor of the frame (plus the document scroller) and put
      // each back. The measure + restore is synchronous, so 0px never paints.
      var savers = _captureScrollTops(frame);
      frame.style.height = '0px';
      var h = Math.max(
        doc.body.scrollHeight || 0,
        doc.documentElement ? doc.documentElement.scrollHeight : 0);
      frame.style.height = Math.max(minH || 0, h) + 'px';
      _restoreScrollTops(savers);
    };
    // Expose a direct (non-observer) refit so _wsOpenStudyTab can run a final
    // fit after the landing window closes.
    frame._refit = function () { fit(false); };
    var onload = function () {
      fit(false);
      try {
        var doc = frame.contentDocument;
        if (doc && doc.body && window.ResizeObserver && !frame._roFit) {
          frame._roFit = new ResizeObserver(function () { fit(true); });
          frame._roFit.observe(doc.body);
        }
      } catch (_) { /* cross-origin */ }
    };
    frame.addEventListener('load', onload);
    try {
      if (frame.contentDocument && frame.contentDocument.readyState === 'complete') onload();
    } catch (_) {}
    if (!frame._fitContentBound) {
      window.addEventListener('resize', function () { fit(false); });
      frame._fitContentBound = true;
    }
  }
  window._fitEmbedToContent = _fitEmbedToContent;

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
    _fitEmbedToViewport(frame, panel, 560);
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
    // Read-only / remote-only mode: hide authoring controls (.js-authoring) via
    // CSS; the Source panel reads this flag at render time to go remote-only.
    if (window._uiConfig.readonly) document.body.classList.add('readonly');
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
      // 'github' (Source page) IS available in snapshot now — it's the published
      // workspace switcher (repo navigator + Sync-to-local). Only 'studies'
      // (the legacy flat list) redirects to the investigations view.
      if (pageId === 'studies') {
        pageId = 'investigations';
      }
    }
    document.querySelectorAll('.page').forEach(function(s) { s.classList.remove('active'); });
    document.querySelectorAll('.menu-link').forEach(function(a) { a.classList.remove('active'); });
    var page = document.getElementById('page-' + pageId);
    var link = document.querySelector('.menu-link[data-page="' + pageId + '"]');
    if (page) page.classList.add('active');
    if (link) link.classList.add('active');
    // Load the faceted Market on switch, and the build_core() registry on
    // Modules / Simulation Setup / Visualizations.
    if (pageId === 'market') {
      _loadMarket();
    }
    if (pageId === 'modules' || pageId === 'simulation-setup' || pageId === 'visualizations') {
      if (!window._registryLoaded) {
        window._registryLoaded = true;
        _loadRegistry(false);
      }
    }
    // Analyses page: always refresh from /api/visualization-classes on navigate.
    if (pageId === 'visualizations') {
      _loadAnalysesPage();
    }
    // Branch page: render the Source (Scope Local/Remote, repo, branch) section on
    // every navigation, so it's populated from the first visit instead of relying
    // on the one-time DOMContentLoaded render (which may not have finished — or ran
    // before the page existed — the first time the user opens Branch).
    if (pageId === 'github' && typeof window._renderBranchSource === 'function') {
      window._renderBranchSource();
    }
    if (pageId === 'simulation-setup') {
      _loadComposites();
    }
    // Stop any running poll-loop started by the Composite Explorer's Run tab
    // before activating a new page. _ceLoadRunFromId will restart polling if
    // the next page is the explorer with a still-running run.
    if (typeof _ceStopRunPoll === 'function') _ceStopRunPoll();
    if (typeof _stopSimAutoRefresh === 'function') _stopSimAutoRefresh();

    // Initialize composite explorer when switching to that page.
    if (pageId === 'composite-explore') {
      window._initCompositeExplorer();
    }
    if (pageId === 'simulations') {
      _wireSimulationsUiOnce();
      _initSimulations();
      _startSimAutoRefresh();
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
      // Clicking the Investigations tab always returns to the top-level list,
      // even when an investigation detail is currently open.
      if (typeof _closeInvestigationDetail === 'function') _closeInvestigationDetail();
      _loadInvestigationSets();
    }
    if (pageId === 'workspace-inputs') {
      _loadInputs();
    }
    if (pageId === 'audit' && typeof window._loadAudit === 'function') {
      _loadAudit();
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
        ? ['workspace-inputs', 'simulation-setup', 'modules', 'market', 'investigations', 'simulations', 'visualizations', 'audit', 'composite-explore', 'github', 'about']
        : ['workspace-inputs', 'simulation-setup', 'visualizations', 'modules', 'market', 'investigations', 'studies', 'simulations', 'audit', 'composite-explore', 'github', 'about'];
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

    // ?popcard=<address>&kind=<kind> → a focused single-card pop-out window.
    var _qsPop = new URLSearchParams(window.location.search).get('popcard');
    if (_qsPop) {
      _enterPopcardMode(_qsPop, new URLSearchParams(window.location.search).get('kind') || 'process');
      return;   // skip normal hash routing in a pop-out window
    }
    // ?maxcard=<address>&kind=<kind> → the FULL workbench (with the side rail)
    // showing this composite maximized + Explore open. The "pop back in" target.
    var _qsMax = new URLSearchParams(window.location.search).get('maxcard');
    if (_qsMax) {
      _enterMaxcardMode(_qsMax, new URLSearchParams(window.location.search).get('kind') || 'composite');
      return;
    }

    if (!focusedPage) {
      function fromHash() {
        var h = (window.location.hash || '').replace(/^#/, '');
        var _snap = document.body.classList.contains('snapshot');
        var validPages = _snap
          ? ['workspace-inputs', 'modules', 'market', 'simulation-setup', 'investigations', 'simulations', 'visualizations', 'audit', 'composite-explore', 'github', 'about']
          : ['workspace-inputs', 'modules', 'market', 'simulation-setup', 'visualizations', 'investigations', 'studies', 'simulations', 'audit', 'composite-explore', 'github', 'about'];
        _switchPage(validPages.indexOf(h) >= 0 ? h : 'workspace-inputs');
      }
      window.addEventListener('hashchange', fromHash);
      fromHash();
    }

    // The Investigations menu-link must return to the top-level card list even
    // when an investigation detail is already open. That detail is a sub-state
    // of the #investigations hash, so re-clicking the link sets an UNCHANGED
    // hash → no hashchange fires → _switchPage never runs. Force the reset on
    // click when we're already on #investigations.
    document.querySelectorAll('.menu-link[data-page="investigations"]').forEach(function (link) {
      link.addEventListener('click', function () {
        if ((window.location.hash || '').replace(/^#/, '') === 'investigations') {
          _switchPage('investigations');
        }
        if (typeof _showExplore === 'function') _showExplore();
      });
    });

    // ?investigation=<name> → auto-open that investigation's detail view.
    // The setTimeout retries to handle the race where the iframe / API
    // load races with the page swap.
    var qInv = new URLSearchParams(window.location.search).get('investigation');
    if (qInv) {
      if (!focusedPage) _switchPage('investigations');
      var tries = 0;
      var attemptOpen = function() {
        var detailEl = document.getElementById('investigation-detail-view');
        if (detailEl && typeof _showInvestigationWorkspace === 'function') {
          _showInvestigationWorkspace(qInv);
        } else if (detailEl && typeof _openInvestigationDetail === 'function') {
          _openInvestigationDetail(qInv);
        } else if (tries++ < 20) {
          setTimeout(attemptOpen, 100);
        }
      };
      setTimeout(attemptOpen, 50);
    }
  }

  window._switchPage = _switchPage;

  // Composites are now a tab on the Processes page. The rail "Composites" item
  // opens that page and activates the Composites tab (and owns the rail
  // highlight, since two rail items share the modules page).
  function _openCompositesTab() {
    _switchPage('modules');
    if (typeof _setRegistryTab === 'function') _setRegistryTab('composite');
    document.querySelectorAll('.menu-link').forEach(function (a) { a.classList.remove('active'); });
    var link = document.querySelector('.menu-link[data-rail="composites"]');
    if (link) link.classList.add('active');
  }
  window._openCompositesTab = _openCompositesTab;
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
    var _pList = fetch(_api('/api/investigation-summaries'))
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

  // A download link to a workspace-relative path.
  //  - Live server: GET-serves any file under the workspace by its
  //    workspace-relative path (do_GET -> WORKSPACE / rel), so href = '/' + path.
  //  - Published snapshot: input binaries (expert docs / datasets) are NOT
  //    staged in the bundle, so a '/' + path href 404s on GitHub Pages. Instead
  //    link to the committed file in the GitHub source repo via the raw base
  //    injected as __DASH_CONFIG__.inputsDownloadBase (see publish.py). Falls
  //    back to '/' + path when no base is configured.
  function _inputsDownloadLink(path, label) {
    if (!path) return '';
    var cfg = window.__DASH_CONFIG__ || {};
    var rel = String(path).replace(/^\/+/, '');
    var href = (cfg.mode === 'snapshot' && cfg.inputsDownloadBase)
      ? String(cfg.inputsDownloadBase).replace(/\/+$/, '') + '/' + rel
      : '/' + rel;
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
        // Wrap instead of scroll: a BibTeX entry's `title = {…}` line runs
        // 200-400px past the panel, and overflow:auto turned every reference
        // into its own horizontal scrollbar. Wrapping costs a line and removes
        // the scrollbar entirely.
        '<pre id="' + _esc(bibId) + '" style="background:#f8fafc;border:1px solid #e2e8f0;' +
        'border-radius:4px;padding:8px;font-size:0.78em;margin:6px 0;' +
        'white-space:pre-wrap;overflow-wrap:anywhere">' +
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

  // ── Drag-and-drop source upload ──────────────────────────────────────────
  function _inputsDzHi(z, on) {
    if (!z) return;
    z.style.background = on ? '#eef2ff' : '#f8fafc';
    z.style.borderColor = on ? '#818cf8' : '#cbd5e1';
  }
  function _inputsDragOver(e) {
    e.preventDefault(); e.stopPropagation();
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
    _inputsDzHi(e.currentTarget, true);
  }
  function _inputsDragLeave(e) {
    e.preventDefault(); _inputsDzHi(e.currentTarget, false);
  }
  function _inputsDrop(e) {
    e.preventDefault(); e.stopPropagation();
    _inputsDzHi(e.currentTarget, false);
    var slug = window._inputsSelectedSlug || window._currentIsetSlug || '';
    if (!slug) { alert('Select an investigation first (Load sources into: …).'); return; }
    var files = (e.dataTransfer && e.dataTransfer.files)
      ? Array.prototype.slice.call(e.dataTransfer.files) : [];
    files.forEach(_inputsUploadDropped);
  }
  // Infer the source category from a dropped file's extension and upload it,
  // deriving the display name from the filename (no prompt).
  function _inputsUploadDropped(f) {
    var name = f.name || 'file';
    var dot = name.lastIndexOf('.');
    var ext = (dot >= 0 ? name.slice(dot + 1) : '').toLowerCase();
    var stem = (dot > 0 ? name.slice(0, dot) : name);
    var DOCLIKE = ['md', 'markdown', 'txt', 'rst', 'org', 'doc', 'docx', 'odt', 'tex'];
    _inputsReadFileB64(f, function (b64) {
      if (ext === 'pdf') {
        _inputsPost('/api/reference-pdf', { pdf_b64: b64 });
      } else if (DOCLIKE.indexOf(ext) >= 0) {
        _inputsPost('/api/expert-doc', { name: stem, filename: name, file_b64: b64 });
      } else {
        _inputsPost('/api/dataset', { name: stem, filename: name, file_b64: b64 });
      }
    });
  }
  window._inputsDragOver = _inputsDragOver;
  window._inputsDragLeave = _inputsDragLeave;
  window._inputsDrop = _inputsDrop;
  window._inputsUploadDropped = _inputsUploadDropped;

  function _renderInputs(el, data) {
    var inv = data.investigation || {};
    var glob = data.global || {};
    var current = data.current || null;
    // Keep the selected-slug in sync with the investigation actually shown, so
    // the drop zone + "+ Add" buttons resolve the right target even before the
    // user touches the "Load sources into" dropdown (data.current reflects the
    // git branch / last selection). Without this, dropping a file falsely
    // reported "Select an investigation first".
    if (current) window._inputsSelectedSlug = current;

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
      // Drag-and-drop upload zone: drop files straight in — no name prompt,
      // no file picker. Category is inferred from the extension and the name
      // from the filename (the "+ Add" buttons below remain for manual naming).
      html += '<div id="inputs-dropzone" ' +
        'ondragover="_inputsDragOver(event)" ondragleave="_inputsDragLeave(event)" ondrop="_inputsDrop(event)" ' +
        'style="border:2px dashed #cbd5e1;border-radius:8px;padding:16px 14px;text-align:center;' +
        'color:#64748b;font-size:0.9em;margin:10px 0 6px;background:#f8fafc;transition:background .12s,border-color .12s">' +
        '<div style="font-weight:600;color:#475569">⬆ Drag datasets or expert docs here to upload</div>' +
        '<div style="font-size:0.78em;color:#94a3b8;margin-top:3px">' +
          'PDFs → references · .md / .txt / .docx → expert docs · everything else → datasets' +
        '</div></div>';
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

  // Origin-repo provenance badge. Empty string for own (null) content.
  function _originBadge(repo) {
    if (!repo) return '';
    return '<span class="origin-badge" title="From installed module ' +
      _esc(repo) + '">📦 ' + _esc(repo) + '</span>';
  }
  window._originBadge = _originBadge;

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
      html += '<p class="empty-state muted" style="margin:0">No Visualization classes found. Install a pbg-* package that provides one (Catalog tab &rarr; Available modules).</p>';
    } else {
      html += vizzes.map(_renderClassCard).join('');
    }
    html += '</div>';

    container.innerHTML = html;
  }

  // -------------------------------------------------------------------------
  // Analyses page: repo-contributed analysis viewers (launcher/embed cards).
  // Backed by GET /api/analysis-viewers (a package's workbench_viewers module).
  // -------------------------------------------------------------------------

  function _render3dVizCard(v) {
    // Snapshot base-path: in a hosted read-only bundle (e.g. /v2ecoli/dashboard)
    // both the parsimony viewer assets and the saved pack live under the base
    // path, so prefix both. basePath is "" in local mode, leaving URLs unchanged.
    var base = (window.DataSource && window.DataSource.basePath)
      ? window.DataSource.basePath()
      : ((window.__DASH_CONFIG__ && window.__DASH_CONFIG__.basePath) || "");
    var packUrl = base + v.pack_url;
    // An external viewer_url (e.g. assets hosted on Cloudflare R2) overrides the
    // bundled gh-pages viewer + pack — used to dodge GitHub Pages rate-limiting
    // for heavy packs. Configured via ui.viz_viewer_urls in workspace.yaml.
    var src = v.viewer_url
      ? v.viewer_url
      : (base + '/parsimony-viewer/index.html?file=' + encodeURIComponent(packUrl));
    var meta = [];
    if (v.study) meta.push('study: ' + _esc(v.study));
    if (v.n_placed) meta.push(Number(v.n_placed).toLocaleString() + ' instances');
    // How the model was built. Server may supply a per-pack `description`;
    // otherwise fall back to the default E. coli structural-model blurb.
    var desc = v.description ||
      'Generated from <strong>v2ecoli</strong>\'s whole-cell molecular state: the ' +
      'simulated copy number of each protein and complex sets how many copies are ' +
      'placed in the cell. Each species is mapped to a real 3D structure &mdash; ' +
      'AlphaFold-predicted monomers plus curated experimental assemblies (e.g. the ' +
      '70S ribosome and RNA polymerase) &mdash; and packed into a capsule-shaped ' +
      'cell volume by the <strong>parsimony</strong> engine, a Rust cellPACK-style ' +
      'packer (via pbg-parsimony). Colors group molecules by functional category.';
    return '<div class="analyses-card">' +
      '<div class="analyses-card-head">' +
        '<strong>' + _esc(v.name || '3D model') + '</strong>' +
        '<a class="btn-mini" href="' + _esc(src) + '" target="_blank" rel="noopener" title="Open full-window in a new tab">Open &#8599;</a>' +
      '</div>' +
      (meta.length ? '<div class="muted" style="font-size:0.82em;margin:2px 0 6px">' + meta.join(' &middot; ') + '</div>' : '') +
      '<p class="muted" style="font-size:0.85em;line-height:1.45;margin:2px 0 8px">' + desc + '</p>' +
      '<iframe class="viz-embed" src="' + _esc(src) + '" loading="lazy" ' +
        'style="width:100%;height:460px;border:1px solid #2a313c;border-radius:6px;background:#0e1116"></iframe>' +
    '</div>';
  }

  function _renderReportCardCard(rc) {
    // Embed a saved vEcoli<->v2ecoli comparison report (statistical-equivalence
    // report cards). Self-contained HTML served from the workspace tree, same
    // base-path handling as the 3D cards for the hosted snapshot.
    var base = (window.DataSource && window.DataSource.basePath)
      ? window.DataSource.basePath()
      : ((window.__DASH_CONFIG__ && window.__DASH_CONFIG__.basePath) || "");
    var src = base + rc.url;
    var meta = [];
    if (rc.study) meta.push('study: ' + _esc(rc.study));
    if (rc.verdict) meta.push('overall: ' + _esc(rc.verdict));
    var desc =
      'Statistical-equivalence <strong>report cards</strong> from the ' +
      '<strong>vEcoli &#8596; v2ecoli comparison harness</strong>: the same config ' +
      'loaded into both engines, their converted processes and ParCa/sim_data ' +
      'diffed, and cell mass / growth rate compared per condition (Welch t &middot; ' +
      'Cohen\'s d &middot; relative-mean &Delta;) against a within-tolerance band.';
    return '<div class="analyses-card">' +
      '<div class="analyses-card-head">' +
        '<strong>' + _esc(rc.name || 'comparison report') + '</strong>' +
        '<a class="btn-mini" href="' + _esc(src) + '" target="_blank" rel="noopener" title="Open full-window in a new tab">Open &#8599;</a>' +
      '</div>' +
      (meta.length ? '<div class="muted" style="font-size:0.82em;margin:2px 0 6px">' + meta.join(' &middot; ') + '</div>' : '') +
      '<p class="muted" style="font-size:0.85em;line-height:1.45;margin:2px 0 8px">' + desc + '</p>' +
      '<iframe class="viz-embed" src="' + _esc(src) + '" loading="lazy" ' +
        'style="width:100%;height:520px;border:1px solid #2a313c;border-radius:6px;background:#fff"></iframe>' +
    '</div>';
  }

  // Generic repo-contributed analysis viewer card (launcher kind). The workbench
  // knows nothing repo-specific: a viewer is discovered via /api/analysis-viewers
  // (contributed by a package's workbench_viewers module) and launched via
  // /api/analysis-viewer/{uid}/launch. `targets` are the launchable rows the
  // contributor computed (e.g. studies with exported data).
  function _renderViewerCard(v) {
    v = v || {};
    var targets = v.targets || [];
    var html = '<div class="analyses-card">' +
      '<div class="analyses-card-head"><strong>' + _esc(v.title || v.id || 'Viewer') + '</strong></div>';
    if (v.description) {
      html += '<p class="muted" style="font-size:0.85em;margin:4px 0 8px">' + _esc(v.description) + '</p>';
    }
    if (!targets.length) {
      html += '<p class="empty-state muted" style="margin:0">No launchable data found yet.</p>';
    } else {
      // A contributed launcher opens against a workspace-local service; the
      // hosted read-only snapshot has neither the launch backend nor that
      // service, so surface an honest note instead of a button that would 404.
      var _isSnapshot = (window.__DASH_CONFIG__ || {}).mode === 'snapshot';
      html += '<div class="viewer-target-list">' + targets.map(function(t) {
        // A target may carry a self-contained external URL (e.g. a publicly
        // hosted 3D viewer) — it opens directly in BOTH live and read-only,
        // since it needs no local launch backend. Otherwise fall back to the
        // live Launch button / the read-only "local workbench" note.
        // A workspace-root-absolute href (/studies/…) must carry the hosting
        // base path in the snapshot, or it 404s to the domain root; an external
        // (http/protocol-relative) href opens as-is. Mirrors sim-table.toolsCell.
        var _bp = window.__BASE_PATH__ || '';
        var _openHref = (t.href && /^https?:|^\/\//.test(t.href)) ? t.href : (_bp + (t.href || ''));
        var action = t.href
          ? '<a class="btn-mini" href="' + _esc(_openHref) + '" target="_blank" rel="noopener">Open ↗</a>'
          : (_isSnapshot
          ? '<span class="muted" style="font-size:0.8em">Launch from the local workbench</span>'
          : '<button class="btn-mini" onclick="_launchViewer(\'' + _esc(v.uid) + '\',\'' + _esc(t.study) + '\')">Launch</button>');
        return '<div class="picker-row">' +
          '<div class="picker-row-main"><strong>' + _esc(t.label || t.study) + '</strong>' +
            (t.detail ? ' <span class="muted" style="font-size:0.82em">' + _esc(t.detail) + '</span>' : '') + '</div>' +
          '<div class="picker-row-actions">' + action + '</div>' +
        '</div>';
      }).join('') + '</div>';
      var _needsLaunch = targets.some(function(t) { return !t.href; });
      if (_isSnapshot && _needsLaunch) {
        html += '<p class="muted" style="font-size:0.8em;margin:8px 0 0">' +
          'Available in the live workbench: this viewer launches against a local ' +
          'service, so this read-only view lists which studies have exports rather ' +
          'than opening it.</p>';
      }
    }
    html += '</div>';
    return html;
  }

  function _launchViewer(uid, study) {
    // The read-only snapshot has no launch backend to call. Bail with a clear
    // message rather than fetch a 404 HTML page and throw a JSON-parse error.
    if ((window.__DASH_CONFIG__ || {}).mode === 'snapshot') {
      alert('This viewer launches against a local service and is only available ' +
            'when running the workbench locally.');
      return;
    }
    var url = '/api/analysis-viewer/' + encodeURIComponent(uid) + '/launch' +
      (study ? '?study=' + encodeURIComponent(study) : '');
    fetch(url).then(function(r) {
      return r.text().then(function(t) {
        var d = {};
        try { d = t ? JSON.parse(t) : {}; }
        catch (e) { d = { error: 'server returned ' + r.status }; }
        return { status: r.status, body: d };
      });
    }).then(function(res) {
      var b = res.body || {};
      if (res.status === 200 && b.url) {
        window.open(b.url, '_blank');
      } else {
        alert('Launch failed: ' + (b.error || res.status));
      }
    }).catch(function(err) { alert('Launch failed: ' + err); });
  }
  window._launchViewer = _launchViewer;

  // Snapshot-safe base-path resolution shared by the tool cards (mirrors
  // _render3dVizCard): "" in local mode; the hosted bundle's base path in a
  // read-only snapshot so both viewer assets and study JSON resolve.
  function _analysesBase() {
    return (window.DataSource && window.DataSource.basePath)
      ? window.DataSource.basePath()
      : ((window.__DASH_CONFIG__ && window.__DASH_CONFIG__.basePath) || "");
  }

  function _analysesSnapshot() {
    return (window.__DASH_CONFIG__ || {}).mode === 'snapshot';
  }

  // Build the parsimony-viewer src for a matched 3D study. Prefer a hosted
  // viewer_url (assets on R2, dodging Pages rate-limits); else the bundled
  // viewer pointed at the study's 3D models manifest.
  function _build3dSrc(m) {
    var base = _analysesBase();
    var ref = m.ref || m.study || '';
    return m.viewer_url
      ? m.viewer_url
      : base + '/parsimony-viewer/index.html?models=' +
          encodeURIComponent(base + '/api/study/' + encodeURIComponent(ref) + '/3d/models.json');
  }

  function _buildSimulariumSrc(m) {
    var base = _analysesBase();
    var trajs = (m && m.trajectories) || [];
    var url = trajs.length ? trajs[0].url : '';
    return base + '/simularium-viewer.html?traj=' + encodeURIComponent(url);
  }

  // Human-readable label for a matched run/study in a card's result dropdown.
  function _toolItemLabel(m) {
    m = m || {};
    var label = m.label || m.study || m.ref || m.run_id || '(result)';
    return m.detail ? label + ' — ' + m.detail : label;
  }

  function _toolItems(t) {
    return (t && t.matched && t.matched.length)
      ? t.matched
      : ((t && t.targets && t.targets.length) ? t.targets : []);
  }

  // id -> tool descriptor, populated by _loadAnalysesPage; read by _openTool to
  // build the right full-window URL for the card's currently-selected result.
  var _TOOLS_BY_ID = {};

  // One compact tool card: title, a small description, a result selector
  // (dropdown when several results match, a static line for one), and an Open
  // button that launches the viewer FULL-WINDOW in a new tab for the selected
  // result. No inline embeds — the tab stays a lightweight launcher that scales
  // as viewers are added.
  function _renderToolCard(t) {
    t = t || {};
    var items = _toolItems(t);
    var head = '<div class="tool-head"><strong>' + _esc(t.title || t.id || 'Tool') + '</strong>' +
      ((t.requires && t.requires.length)
        ? '<span class="tool-need muted">needs ' + _esc(t.requires.join(', ')) + '</span>' : '') +
      '</div>';
    var desc = t.description
      ? '<p class="tool-desc muted">' + _esc(t.description) + '</p>' : '';
    var id = _esc(String(t.id || ''));
    var body;
    if (!items.length) {
      body = '<div class="tool-foot"><span class="muted tool-empty">' +
        _esc(t.unmatched_reason || 'No compatible results.') + '</span></div>';
    } else {
      var control;
      if (items.length > 1) {
        control = '<select class="tool-select" id="tool-sel-' + id + '">' +
          items.map(function(m, i) {
            return '<option value="' + i + '">' + _esc(_toolItemLabel(m)) + '</option>';
          }).join('') + '</select>';
      } else {
        control = '<span class="tool-one muted">' + _esc(_toolItemLabel(items[0])) + '</span>';
      }
      body = '<div class="tool-foot">' + control +
        '<button class="btn-mini tool-open" onclick="_openTool(\'' + id + '\', this)">' +
        'Open &#8599;</button></div>';
    }
    return '<div class="analyses-card tool-card" data-tool="' + id + '">' +
      head + desc + body + '</div>';
  }

  // Open a tool's selected result full-window in a new tab. Per kind:
  //   embed-3d       -> the (hosted or bundled) parsimony viewer for the study
  //   launcher       -> the target's external href, else the live launch endpoint
  function _openTool(toolId, btn) {
    var t = _TOOLS_BY_ID[toolId]; if (!t) return;
    var items = _toolItems(t);
    var card = (btn && btn.closest) ? btn.closest('.tool-card') : null;
    var sel = card ? card.querySelector('.tool-select') : null;
    var idx = sel ? (parseInt(sel.value, 10) || 0) : 0;
    var m = items[idx] || items[0]; if (!m) return;
    if (t.kind === 'embed-3d') {
      window.open(_build3dSrc(m), '_blank', 'noopener');
    } else if (t.kind === 'embed-simularium') {
      window.open(_buildSimulariumSrc(m), '_blank', 'noopener');
    } else if (m.href) {
      window.open(m.href, '_blank', 'noopener');
    } else {
      // Contributed launcher viewers are keyed by full uid (package::id) at the
      // launch endpoint; t.id is the bare id, so prefer t.uid.
      _launchViewer(t.uid || t.id, m.study || m.ref || '');  // resolves via endpoint / snapshot note
    }
  }
  window._openTool = _openTool;

  function _loadAnalysesPage() {
    var container = document.getElementById('analyses-gallery');
    var countEl   = document.getElementById('viz-count');
    if (!container) return;
    // Tools-first Analysis Tools tab, backed by GET /api/analysis-tools: built-in
    // tools (Parsimony Viewer) + external contributed viewers, each
    // capability-matched to the runs/studies that satisfy its `requires`. Snapshot
    // mode reads the static api/analysis-tools.json bundle file; live mode hits the
    // endpoint. Parse defensively via text() so a missing/HTML response degrades to
    // an empty tools list instead of throwing a JSON SyntaxError into the page.
    var _base = _analysesBase();
    var _toolsUrl = _analysesSnapshot()
      ? _base + '/api/analysis-tools.json'
      : '/api/analysis-tools';
    fetch(_toolsUrl)
      .then(function(r) { return r.text(); })
      .then(function(t) {
        var data = {};
        try { data = t ? JSON.parse(t) : {}; } catch (e) { data = {}; }
        return data;
      })
      .then(function(data) {
        data = data || {};
        var tools = data.tools || [];
        if (!tools.length) {
          container.innerHTML = '<p class="empty-state">No analysis tools for this workspace. Tools are built-in (Parsimony Viewer) or contributed by the repo (a package\'s <code>workbench_viewers</code> module).</p>';
          if (countEl) countEl.textContent = '';
          return;
        }
        _TOOLS_BY_ID = {};
        tools.forEach(function(t) { if (t && t.id != null) _TOOLS_BY_ID[t.id] = t; });
        container.innerHTML = tools.map(_renderToolCard).join('');
        if (countEl) countEl.textContent = '(' + tools.length + ')';
      })
      .catch(function(err) {
        container.innerHTML = '<p class="empty-state" style="color:#991b1b">Error loading analysis tools: ' + _esc(String(err)) + '</p>';
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
      container.innerHTML = '<p class="empty-state">No ' + kind + 's registered. Install a pbg-* package that provides one (Catalog tab &rarr; Available modules).</p>';
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
      _switchPage('modules');
      // Legacy: the inline simulation form (once on Simulation Setup) is gone —
      // this early-returns, leaving the Modules page shown.
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

  // Compact, readable label for a bigraph type schema (a port's value).
  function _regTypeLabel(v) {
    if (v === null || v === undefined) return '';
    if (typeof v === 'string') return v;
    if (typeof v === 'object') {
      if (v._type) return String(v._type);
      var keys = Object.keys(v).filter(function (k) { return k.charAt(0) !== '_'; });
      if (keys.length) return '{' + keys.slice(0, 4).join(', ') + (keys.length > 4 ? ', …' : '') + '}';
      return 'store';
    }
    return String(v);
  }
  // Exported: static/composite-card.js's _regPortColumn (moved out in the
  // study-spine-reorg Task 6 extraction) calls this as a global.
  window._regTypeLabel = _regTypeLabel;

  // _regPortColumn (one port column: Inputs or Outputs) moved to
  // static/composite-card.js (study-spine-reorg Task 6).

  // ── Registry semantic zoom ──────────────────────────────────────────────
  // 'table' (dense sortable table) | 'grid' (card grid; config/ports on expand)
  // | 'full' (loom-style: inputs left margin, outputs right margin, rich middle).
  // Persists in localStorage.
  window._registryZoom = (function () {
    var z; try { z = localStorage.getItem('viv.registryZoom'); } catch (e) { z = null; }
    return (z === 'table' || z === 'grid' || z === 'full') ? z : 'grid';
  })();

  // ── Cards-grid column control (shared: registry / composites / modules) ──
  // The middle "Cards" view is a multi-column grid. Default is 'auto' (fit
  // columns to width, auto-fill); a slider overrides with a fixed count.
  window._cardCols = (function () {
    var d = {};
    ['registry', 'composites', 'modules', 'market', 'isets'].forEach(function (s) {
      var v; try { v = localStorage.getItem('viv.cols.' + s); } catch (e) { v = null; }
      d[s] = (v && v !== 'auto' && !isNaN(+v)) ? Math.max(1, Math.min(8, +v)) : 'auto';
    });
    return d;
  })();
  function _applyCardCols(container, surface) {
    if (!container) return;
    container.classList.add('cards-grid-cols');
    var v = window._cardCols[surface];
    container.style.gridTemplateColumns = (v === 'auto')
      ? 'repeat(auto-fill, minmax(300px, 1fr))'
      : 'repeat(' + v + ', minmax(0, 1fr))';
  }
  function _cardContainersFor(surface) {
    var sel = surface === 'registry' ? '.reg-cards-grid'
      : (surface === 'composites' ? '.ccard-rows'
      : (surface === 'market' ? '.market-grid-cards'
      : (surface === 'isets' ? '.investigations-grid' : '.mrows')));
    return Array.prototype.slice.call(document.querySelectorAll(sel));
  }
  function _colsControl(surface) {
    var v = window._cardCols[surface], isAuto = (v === 'auto');
    return '<button class="cols-auto-btn' + (isAuto ? ' active' : '') +
        '" title="Fit columns to width" onclick="_setCardCols(\'' + surface + '\',\'auto\')">Auto</button>' +
      '<input type="range" min="1" max="6" value="' + (isAuto ? 3 : v) +
        '" class="cols-slider" title="Number of columns" oninput="_setCardCols(\'' + surface + '\', this.value)">' +
      '<span class="cols-count">' + (isAuto ? 'auto' : v) + '</span>';
  }
  // The column control only makes sense in the multi-column Cards zoom — hide
  // it in Table / Full so it doesn't read as a stray slider elsewhere.
  function _updateColsSlotVisibility() {
    var show = {
      registry: (window._registryZoom === 'grid'),
      composites: ((window._compositesZoom || 'cards') === 'cards'),
      modules: ((window._catalogZoom || 'cards') === 'cards'),
      market: ((window._marketZoom || 'cards') === 'cards'),
      isets: ((window._isetZoom || 'cards') === 'cards'),
    };
    Object.keys(show).forEach(function (s) {
      var slot = document.querySelector('.cols-ctl-slot[data-cols-surface="' + s + '"]');
      if (slot) slot.style.display = show[s] ? '' : 'none';
    });
  }
  function _syncColsControls() {
    document.querySelectorAll('.cols-ctl-slot').forEach(function (slot) {
      var s = slot.getAttribute('data-cols-surface');
      if (s && !slot.innerHTML.trim()) slot.innerHTML = _colsControl(s);
    });
    _updateColsSlotVisibility();
  }
  window._syncColsControls = _syncColsControls;
  function _setCardCols(surface, value) {
    var v = (value === 'auto') ? 'auto' : Math.max(1, Math.min(8, parseInt(value, 10) || 3));
    window._cardCols[surface] = v;
    try { localStorage.setItem('viv.cols.' + surface, String(v)); } catch (e) { /* private mode */ }
    // Update the control label + Auto state in place (do NOT rebuild the slider
    // mid-drag), then re-apply the grid to the visible cards containers.
    document.querySelectorAll('.cols-ctl-slot[data-cols-surface="' + surface + '"]').forEach(function (slot) {
      var cnt = slot.querySelector('.cols-count'); if (cnt) cnt.textContent = (v === 'auto') ? 'auto' : v;
      var ab = slot.querySelector('.cols-auto-btn'); if (ab) ab.classList.toggle('active', v === 'auto');
    });
    _cardContainersFor(surface).forEach(function (c) { _applyCardCols(c, surface); });
  }
  window._setCardCols = _setCardCols;

  // ── Light / dark theme toggle ──────────────────────────────────────────
  // The theme is applied to <html data-theme> before first paint by a small
  // inline script in <head> (no flash); this drives the toggle + persistence.
  function _syncThemeLogo() {
    var img = document.querySelector('.viv-rail-logo');
    if (!img || !img.dataset) return;
    var dark = document.documentElement.getAttribute('data-theme') === 'dark';
    var next = dark ? img.dataset.darkSrc : img.dataset.lightSrc;
    if (next && img.getAttribute('src') !== next) img.src = next;
  }
  function _setTheme(t) {
    document.documentElement.setAttribute('data-theme', t);
    try { localStorage.setItem('viv.theme', t); } catch (e) { /* private mode */ }
    var b = document.getElementById('viv-theme-toggle');
    if (b) b.setAttribute('aria-checked', t === 'dark' ? 'true' : 'false');
    _syncThemeLogo();
  }
  function _toggleTheme() {
    var cur = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
    _setTheme(cur === 'dark' ? 'light' : 'dark');
  }
  window._toggleTheme = _toggleTheme;
  window._setTheme = _setTheme;
  (function () {
    var sync = function () {
      var b = document.getElementById('viv-theme-toggle');
      if (b) b.setAttribute('aria-checked', document.documentElement.getAttribute('data-theme') === 'dark' ? 'true' : 'false');
      _syncThemeLogo();
    };
    if (document.readyState !== 'loading') sync();
    else document.addEventListener('DOMContentLoaded', sync);
  })();

  function _syncRegistryToolbar() {
    // Scoped to [data-zoom] — .reg-zoom-btn is shared with the Investigations/
    // Studies zoom toolbar (data-izoom), which must not be touched here.
    document.querySelectorAll('.reg-zoom-btn[data-zoom]').forEach(function (b) {
      b.classList.toggle('active', b.getAttribute('data-zoom') === window._registryZoom);
    });
  }
  function _rerenderRegistryKinds() {
    var m = window._registryByKind || {};
    Object.keys(m).forEach(function (cid) { _renderRegistryGrid(cid, m[cid]); });
    if (typeof _renderRegistryComposites === 'function') _renderRegistryComposites();
  }
  function _setRegistryZoom(z) {
    window._registryZoom = z;
    try { localStorage.setItem('viv.registryZoom', z); } catch (e) { /* private mode */ }
    _syncRegistryToolbar(); _rerenderRegistryKinds();
    _updateColsSlotVisibility();   // slider only in Cards zoom (table returns early)
    _refocusRegistrySelection();   // keep the selected process in focus on zoom
  }
  window._setRegistryZoom = _setRegistryZoom;

  function _nPorts(schema) {
    return (schema && typeof schema === 'object') ? Object.keys(schema).length : 0;
  }

  // Zoom dispatcher: the grid calls this per entry (table is rendered in bulk).
  function _renderRegistryEntry(p) {
    return (window._registryZoom === 'full') ? _renderRegistryEntryFull(p) : _renderRegistryEntryGrid(p);
  }

  function _regUseBadge(p) {
    return p.use_count
      ? '<span class="registry-use-badge" title="Referenced by ' + p.use_count +
        ' composite(s) / runner script(s) in this workspace">' + p.use_count +
        ' use' + (p.use_count === 1 ? '' : 's') + '</span>'
      : '';
  }

  // Process vs Step. In process-bigraph a Process advances state over a
  // timestep ("Temporal"); a Step is a dataflow node that runs to fixed point
  // (no timestep). Both are Processes (edges) — these helpers say which kind.
  function _procKindLabel(kind) {
    if (kind === 'step') return 'Step';
    if (kind === 'process') return 'Temporal';
    return kind ? kind.charAt(0).toUpperCase() + kind.slice(1) : '';
  }
  // Small pill for the two runnable kinds; '' for anything else (emitter/type/…).
  function _procKindBadge(kind) {
    if (kind !== 'process' && kind !== 'step') return '';
    var isStep = kind === 'step';
    return '<span class="proc-kind-badge proc-kind-' + (isStep ? 'step' : 'temporal') + '" title="' +
      (isStep ? 'Step — dataflow node, runs to fixed point (no timestep)'
              : 'Temporal — a Process that advances state over a timestep') +
      '">' + (isStep ? 'Step' : 'Temporal') + '</span>';
  }

  // Config-schema + ports body, revealed by a per-card "config & ports" dropdown
  // in the grid view — keeps the grid dense but the contract one click away.
  function _regDetailsBody(p) {
    var out = '';
    var hasPorts = (p.inputs && typeof p.inputs === 'object') || (p.outputs && typeof p.outputs === 'object');
    if (hasPorts) {
      out += '<div class="reg-ports reg-ports-compact">' +
        _regPortColumn('Inputs', p.inputs === undefined ? null : p.inputs) +
        _regPortColumn('Outputs', p.outputs === undefined ? null : p.outputs) +
      '</div>';
    }
    var cfgBody = '';
    if (p.config_schema && typeof p.config_schema === 'object' && Object.keys(p.config_schema).length) {
      try { cfgBody = JSON.stringify(p.config_schema, null, 2); } catch (_) { cfgBody = p.schema_preview || ''; }
    } else if (p.schema_preview) { cfgBody = p.schema_preview; }
    if (cfgBody) out += '<pre class="json-tree reg-card-cfg">' + _esc(cfgBody) + '</pre>';
    return out;
  }

  // Inline ports + types for the middle (grid) zoom — read-only detail, always
  // visible (no dropdown). Double-click a card to reach the runnable Full view.
  function _regInlinePorts(p) {
    // Compact bigraph-loom-style layout: config strip on top, input ports down
    // the left, output ports down the right — small chips with a port dot.
    function ports(schema, side) {
      var keys = (schema && typeof schema === 'object') ? Object.keys(schema) : null;
      if (keys === null) return '<span class="reg-ip-na" title="Ports depend on a configured instance.">—</span>';
      if (!keys.length) return '<span class="reg-ip-na">(none)</span>';
      return keys.map(function (k) {
        var t = _regTypeLabel(schema[k]);
        return '<span class="reg-ip reg-ip-' + side + '"><span class="reg-ip-dot"></span>' +
          '<code>' + _esc(k) + '</code>' +
          (t ? '<span class="reg-ip-type">' + _esc(t) + '</span>' : '') + '</span>';
      }).join('');
    }
    var hasPorts = !(p.inputs === undefined && p.outputs === undefined);
    var cfgKeys = (p.config_schema && typeof p.config_schema === 'object') ? Object.keys(p.config_schema) : [];
    if (!hasPorts && !cfgKeys.length) return '';
    var cfg = cfgKeys.length
      ? '<div class="reg-mid-config"><span class="reg-mid-label">config</span>' +
        cfgKeys.slice(0, 16).map(function (k) { return '<code>' + _esc(k) + '</code>'; }).join('') +
        (cfgKeys.length > 16 ? ' <span class="reg-ip-na">+' + (cfgKeys.length - 16) + '</span>' : '') + '</div>'
      : '';
    var portsRow = hasPorts
      ? '<div class="reg-mid-ports">' +
          '<div class="reg-mid-col reg-mid-in"><span class="reg-ip-title">inputs</span>' +
            ports(p.inputs === undefined ? null : p.inputs, 'in') + '</div>' +
          '<div class="reg-mid-col reg-mid-out"><span class="reg-ip-title">outputs</span>' +
            ports(p.outputs === undefined ? null : p.outputs, 'out') + '</div>' +
        '</div>'
      : '';
    return '<div class="reg-mid">' + cfg + portsRow + '</div>';
  }

  // Grid card (middle zoom): name, use, one-line description, and the ports+types
  // shown inline. Double-click zooms to the runnable Full view.
  // _regStatsHtml (usage stats chips, shared by grid + full cards) moved to
  // static/composite-card.js (study-spine-reorg Task 6).

  // Small info-box popup anchored under the clicked element; closes on outside
  // click / scroll. Shared by the clickable card stats.
  function _regInfoPop(e, html) {
    if (e) { e.stopPropagation(); if (e.preventDefault) e.preventDefault(); }
    var old = document.querySelector('.reg-infopop'); if (old) old.remove();
    var pop = document.createElement('div');
    pop.className = 'reg-infopop';
    pop.innerHTML = html;
    document.body.appendChild(pop);
    var target = (e && (e.currentTarget || e.target)) || document.body;
    var r = target.getBoundingClientRect();
    var w = pop.getBoundingClientRect().width || 300;
    pop.style.left = Math.round(Math.max(8, Math.min(r.left, window.innerWidth - w - 12)) + window.scrollX) + 'px';
    pop.style.top = Math.round(r.bottom + 6 + window.scrollY) + 'px';
    var close = function (ev) { if (pop.contains(ev.target)) return; pop.remove(); document.removeEventListener('mousedown', close); window.removeEventListener('scroll', close, true); };
    setTimeout(function () { document.addEventListener('mousedown', close); window.addEventListener('scroll', close, true); }, 0);
  }
  window._regInfoPop = _regInfoPop;

  // Stacked pass / inconclusive / fail bar over a process's report-card outcomes.
  function _successBar(sp) {
    if (!sp || !sp.total) return '';
    var pass = sp.pass || 0, incon = sp.inconclusive || 0, fail = sp.fail || 0, total = sp.total;
    var seg = function (cls, n) { return n ? '<span class="reg-succbar-seg ' + cls + '" style="width:' + (n / total * 100) + '%"></span>' : ''; };
    return '<div class="reg-succbar" title="' + pass + ' passed · ' + incon + ' inconclusive · ' + fail + ' failed of ' + total + ' report-card outcomes">' +
        '<div class="reg-succbar-track">' + seg('reg-succbar-pass', pass) + seg('reg-succbar-incon', incon) + seg('reg-succbar-fail', fail) + '</div>' +
        '<div class="reg-succbar-legend">' +
          '<span class="reg-succbar-t-pass">' + pass + ' pass</span>' +
          (incon ? ' · <span class="reg-succbar-t-incon">' + incon + ' incon</span>' : '') +
          (fail ? ' · <span class="reg-succbar-t-fail">' + fail + ' fail</span>' : '') +
          ' · ' + total + ' total' +
        '</div>' +
      '</div>';
  }
  window._successBar = _successBar;

  // Clickable card stats: composites-using (opens a list), requires-N, studies
  // (opens a breakdown). Studies/success hidden for non-runnable kinds.
  function _regClickStats(p) {
    var addr = _esc(p.address || '');
    var out = [];
    if (p.composite_uses) out.push('<button type="button" class="reg-stat reg-stat-btn" onclick="_showProcessComposites(event,\'' + addr + '\')" title="See which composites use this"><span class="reg-stat-glyph">▦</span><strong>' + p.composite_uses + '</strong> ' + (p.composite_uses === 1 ? 'composite' : 'composites') + '</button>');
    if (p.requires && p.requires.processes && p.requires.processes.length) out.push('<span class="reg-stat"><span class="reg-stat-glyph">⚙</span><strong>' + p.requires.processes.length + '</strong> ' + (p.requires.processes.length === 1 ? 'process' : 'processes') + '</span>');
    var noStudies = /^(emitter|visualization|analysis|type|report_card)$/.test(p.kind || '');
    var sp = noStudies ? null : (p.study_participation || p.studies);
    if (sp && sp.studies) out.push('<button type="button" class="reg-stat reg-stat-btn" onclick="_showProcessStudies(event,\'' + addr + '\')" title="Study participation breakdown"><span class="reg-stat-glyph">◆</span><strong>' + sp.studies + '</strong> ' + (sp.studies === 1 ? 'study' : 'studies') + '</button>');
    return out.join('');
  }

  // Popup: composites that use this process (derived from loaded composite specs).
  function _showProcessComposites(e, address) {
    var p = _registryEntryByAddress(address) || {};
    var name = p.name;
    var comps = (window._composites || []).filter(function (c) { return c.requires && c.requires.processes && c.requires.processes.indexOf(name) >= 0; });
    var html = '<div class="reg-infopop-title">Composites using <code>' + _esc(name || '') + '</code></div>';
    html += comps.length
      ? '<ul class="reg-infopop-list">' + comps.map(function (c) {
          return '<li><a href="#" onclick="_openCompositeExplorer(\'' + _esc(c.id) + '\');return false;" title="Open in the Composite Explorer">' + _esc(c.name) + '</a> <span class="muted">' + _esc(c.module || '') + '</span></li>';
        }).join('') + '</ul>'
      : '<p class="muted">Not required by any loaded composite spec' + (p.composite_uses ? ' (used via generators — open the Composites tab).' : '.') + '</p>';
    _regInfoPop(e, html);
  }
  window._showProcessComposites = _showProcessComposites;

  // Popup: study participation breakdown (names need a backend annotation).
  function _showProcessStudies(e, address) {
    var p = _registryEntryByAddress(address) || {};
    var sp = p.study_participation || p.studies || {};
    var list = (sp && Array.isArray(sp.study_list)) ? sp.study_list : [];
    var n = sp.studies || 0;
    var html = '<div class="reg-infopop-title">Study participation</div>' +
      '<div class="reg-infopop-stats">' +
        '<div><strong>' + n + '</strong> stud' + (n === 1 ? 'y' : 'ies') + ' participated</div>' +
      '</div>' + _successBar(sp);
    if (list.length) {
      html += '<ul class="reg-infopop-list">' + list.map(function (slug) {
        return '<li><a href="#" onclick="_openStudyEmbeddedNewTab(\'' + _esc(slug) + '\');return false;" title="Open this study">' + _esc(slug) + '</a></li>';
      }).join('') + '</ul>';
    } else {
      html += '<p class="muted reg-infopop-note">Individual study names aren\'t indexed for this process yet — browse them under ' +
        '<a href="#investigations" onclick="_switchPage(\'investigations\');return false;">Studies</a>.</p>';
    }
    _regInfoPop(e, html);
  }
  window._showProcessStudies = _showProcessStudies;

  // Popup: full config keys + input/output ports (replaces the inline expander).
  function _showConfigPorts(e, address) {
    var p = _registryEntryByAddress(address) || {};
    var cfgKeys = (p.config_schema && typeof p.config_schema === 'object') ? Object.keys(p.config_schema) : [];
    function portRows(schema) {
      var keys = (schema && typeof schema === 'object') ? Object.keys(schema) : [];
      if (!keys.length) return '<span class="muted">none</span>';
      return keys.map(function (k) { var t = _regTypeLabel(schema[k]); return '<div class="reg-infopop-port"><code>' + _esc(k) + '</code>' + (t ? '<span class="reg-infopop-type">' + _esc(t) + '</span>' : '') + '</div>'; }).join('');
    }
    var html = '<div class="reg-infopop-title">Config &amp; ports</div>' +
      '<div class="reg-infopop-sec"><span class="reg-infopop-label">config · ' + cfgKeys.length + '</span>' +
        (cfgKeys.length ? cfgKeys.map(function (k) { return '<code>' + _esc(k) + '</code>'; }).join(' ') : '<span class="muted">none</span>') + '</div>' +
      '<div class="reg-infopop-sec"><span class="reg-infopop-label">inputs · ' + _nPorts(p.inputs) + '</span>' + portRows(p.inputs) + '</div>' +
      '<div class="reg-infopop-sec"><span class="reg-infopop-label">outputs · ' + _nPorts(p.outputs) + '</span>' + portRows(p.outputs) + '</div>';
    _regInfoPop(e, html);
  }
  window._showConfigPorts = _showConfigPorts;

  function _renderRegistryEntryGrid(p) {
    var sourceAttr = p.source ? ' data-source="' + _esc(p.source) + '"' : '';
    var esc = _esc, addr = _esc(p.address || '');
    var defaultBadge = p.is_workspace_default
      ? ' <span class="count-badge" style="background:#1f7a36;color:#fff;font-size:0.66em;padding:1px 5px;border-radius:3px;vertical-align:middle">DEFAULT</span>'
      : '';
    var desc = (p.description || '').trim();
    var short = desc ? desc.split('\n')[0] : '';
    var noStudies = /^(emitter|visualization|analysis|type|report_card)$/.test(p.kind || '');
    var sp = noStudies ? null : (p.study_participation || p.studies);
    var nCfg = (p.config_schema && typeof p.config_schema === 'object') ? Object.keys(p.config_schema).length : 0;
    var nIn = _nPorts(p.inputs), nOut = _nPorts(p.outputs);
    // Config & ports → a click-popup info button (like the other stats).
    var cfgPortsBtn = (nCfg || nIn || nOut)
      ? '<button type="button" class="reg-cfgports-btn" onclick="event.stopPropagation();_showConfigPorts(event,\'' + addr + '\')" title="See config &amp; ports">' +
        'config &amp; ports <span class="reg-mid-sum">' + nCfg + ' config · ' + nIn + ' in · ' + nOut + ' out</span></button>'
      : '';
    var selCls = (window._registrySelected && window._registrySelected === p.address) ? ' reg-selected' : '';
    return '<div class="registry-card' + selCls + '"' + sourceAttr + ' data-address="' + addr + '"' +
        ' onclick="_selectRegistryEntry(\'' + addr + '\')" ondblclick="_zoomInOn(\'' + addr + '\')"' +
        ' title="Double-click to zoom in on this ' + (p.kind || 'process') + '">' +
      '<div class="reg-card-row">' +
        '<div class="reg-card-main">' +
          '<div class="reg-card-head"><strong class="reg-card-name">' + esc(p.name) + '</strong>' + _procKindBadge(p.kind) + defaultBadge + _regUseBadge(p) + '</div>' +
          '<code class="reg-card-addr">' + addr + '</code>' +
          (short ? '<p class="reg-card-desc">' + esc(short) + '</p>' : '') +
        '</div>' +
        '<div class="reg-card-stats">' + _regClickStats(p) + '</div>' +
      '</div>' +
      _successBar(sp) +
      cfgPortsBtn +
      _runCmdChip(p.run_command) +
    '</div>';
  }

  // Full: the loom-style process rectangle — inputs down the left edge, outputs
  // down the right edge, name/type centered, config across the top. Mirrors
  // bigraph-loom's ProcessNode, as a static, accessible card.
  function _renderRegistryEntryFull(p) {
    var sourceAttr = p.source ? ' data-source="' + _esc(p.source) + '"' : '';
    var kind = p.kind || 'process';
    var runnable = (kind === 'process' || kind === 'step');
    var desc = (p.description || '').trim();
    var selClsFull = (window._registrySelected && window._registrySelected === p.address) ? ' reg-selected' : '';
    var addrAttr = ' data-address="' + _esc(p.address || '') + '" data-kind="' + kind + '"';
    // Static port column (used for outputs always, and for both sides on
    // non-runnable kinds). Runnable inputs are rendered as editable fields.
    function ports(schema, side) {
      var keys = (schema && typeof schema === 'object') ? Object.keys(schema) : [];
      if (!keys.length) return '<div class="loom-port loom-port-empty">—</div>';
      return keys.map(function (k) {
        var t = _regTypeLabel(schema[k]);
        return '<div class="loom-port loom-port-' + side + '" title="' + _esc(t || '') + '">' +
          '<span class="loom-port-dot"></span>' +
          '<span class="loom-port-name">' + _esc(k) + '</span>' +
          (t ? '<span class="loom-port-type">' + _esc(t) + '</span>' : '') +
          '</div>';
      }).join('');
    }
    var bodyHead =
      '<div class="loom-body-head"><span class="loom-name">' + _esc(p.name) + '</span>' + _procKindBadge(kind) + _regUseBadge(p) + '</div>' +
      '<code class="loom-addr">' + _esc(p.address || kind) + '</code>' +
      (desc ? '<p class="loom-desc">' + _esc(desc) + '</p>' : '');

    if (!runnable) {
      // Non-runnable kinds (emitter/visualization/analysis/type/report_card):
      // the SAME accordion ProcessCard, minus the Run bar — config/inputs/
      // outputs shown as static name·type contracts.
      return _renderProcessCard(p, kind, { selCls: selClsFull, sourceAttr: sourceAttr, addrAttr: addrAttr, nonRunnable: true });
    }

    // Runnable (process/step): the unified ProcessCard — a config top-bar
    // (expandable + settable, with Apply that re-derives ports), an
    // inputs-left / contract-middle / outputs-right body row (each region
    // collapsible), and a Run bar pinned at the bottom. This is the loom node's
    // grammar, restacked as an accessible static card. Config + input fields
    // load lazily (resolved defaults) when the card scrolls into view.
    return _renderProcessCard(p, kind, { selCls: selClsFull, sourceAttr: sourceAttr, addrAttr: addrAttr, bodyHead: bodyHead });
  }

  // ── Shared ProcessCard building blocks (used by process + composite cards) ──
  // _pcardInfoRow / _pcardSection / _pcardRunBar / _compositeBadge /
  // _cardPopoutBtn / _cardMaximizeBtn / _positionMaximizedCard /
  // _toggleCardMaximize / _maximizeCardFromHeader / _compositeJsonBtn /
  // _shareCompositeBtn moved to static/composite-card.js (study-spine-reorg
  // Task 6 — shared with the Study Detail Model tab). composite-card.js loads
  // before this file, so the bare references below still resolve globally.

  // A card header "pop out" control — opens the whole card (Explore/loom and
  // all) in its own focused window. Stays here: its ?popcard= handshake is
  // this page's own bootstrap (_enterPopcardMode below), not shared.
  function _popoutCard(address, kind) {
    var url = location.origin + location.pathname +
      '?popcard=' + encodeURIComponent(address) + '&kind=' + encodeURIComponent(kind || 'process');
    window.open(url, '_blank', 'width=1180,height=940,menubar=no,toolbar=no,location=no,resizable=yes,scrollbars=yes');
  }
  window._popoutCard = _popoutCard;

  function _shareCompositeFromHeader(btn) {
    var card = btn.closest('.registry-entry-full');
    var id = card ? card.getAttribute('data-address') : null;
    if (!id) return;
    var apiUrl = (window.DataSource && window.DataSource.apiUrl)
      ? window.DataSource.apiUrl.bind(window.DataSource) : function (p) { return p; };
    // chrome=off → a view-only share: just the bigraph graph + toolbar, no tab
    // strip / left Config panel / bottom run bar (matches the loom Share button).
    var rel = document.body.classList.contains('snapshot')
      ? apiUrl('/bigraph-loom/index.html') + '?static=1&chrome=off&stateUrl=' + encodeURIComponent(_compositeStateUrl(id))
      : apiUrl('/bigraph-loom/index.html') + '?id=' + encodeURIComponent(id) + '&chrome=off';
    var url;
    try { url = new URL(rel, window.location.href).href; } catch (e) { url = rel; }
    var flash = function () {
      var old = btn.textContent; btn.textContent = '✓ Link copied'; btn.classList.add('active');
      setTimeout(function () { btn.textContent = old; btn.classList.remove('active'); }, 1600);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(flash).catch(function () { window.prompt('Copy this link:', url); });
    } else { window.prompt('Copy this link:', url); }
  }
  window._shareCompositeFromHeader = _shareCompositeFromHeader;
  function _toggleCompositeJson(btn) {
    var card = btn.closest('.registry-entry-full'); if (!card) return;
    var panel = card.querySelector('[data-role="composite-json"]'); if (!panel) return;
    var show = panel.hidden;
    panel.hidden = !show;
    btn.classList.toggle('active', show);
    if (!show || panel._loaded) return;
    panel._loaded = true;
    var id = card.getAttribute('data-address');
    var body = panel.querySelector('.pcard-json-body');
    if (body) body.innerHTML = '<span class="muted" style="font-size:0.85em">Resolving composite JSON…</span>';
    var url = (typeof _compositeStateUrl === 'function')
      ? _compositeStateUrl(id)
      : '/api/composite-resolve?id=' + encodeURIComponent(id);
    fetch(url).then(function (r) { return r.json(); }).then(function (j) {
      var doc = (j && j.state) ? j.state : j;
      if (body) {
        body.innerHTML =
          '<div class="pcard-json-toolbar">' +
            '<button class="btn-mini" type="button" onclick="_copyCompositeJson(this)">⧉ Copy</button>' +
            '<a class="btn-mini" href="' + _esc(url) + '" target="_blank" rel="noopener">Raw ↗</a>' +
          '</div>' +
          (typeof _jsonViewer === 'function' ? _jsonViewer(doc) : '<pre>' + _esc(JSON.stringify(doc, null, 2)) + '</pre>') +
          '<pre class="pcard-json-raw" hidden>' + _esc(JSON.stringify(doc, null, 2)) + '</pre>';
      }
      panel._json = doc;
    }).catch(function (e) {
      if (body) body.innerHTML = '<span class="loom-run-err">Failed to load JSON: ' + _esc(String(e)) + '</span>';
      panel._loaded = false;
    });
  }
  window._toggleCompositeJson = _toggleCompositeJson;
  function _copyCompositeJson(btn) {
    var panel = btn.closest('[data-role="composite-json"]');
    var pre = panel && panel.querySelector('.pcard-json-raw');
    if (!pre) return;
    var txt = pre.textContent || '';
    var done = function () { var o = btn.textContent; btn.textContent = '✓ Copied'; setTimeout(function () { btn.textContent = o; }, 1200); };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(txt).then(done).catch(done);
    else done();
  }
  window._copyCompositeJson = _copyCompositeJson;

  // In a ?popcard= window: strip the shell to just the single requested card.
  function _enterPopcardMode(address, kind) {
    // focus-mode strips the rail/topbar (content-only window); popcard-mode
    // additionally hides the registry tabs + toolbar to leave just the card.
    document.body.classList.add('focus-mode', 'popcard-mode');
    // "Pop back in" is injected into the card HEADER's action row (top-right,
    // where the pop-out button was) after the card renders — see below.
    var isComposite = (kind === 'composite');
    if (typeof _switchPage === 'function') _switchPage('modules');
    window._registryZoom = 'full';
    try { localStorage.setItem('viv.registryZoom', 'full'); } catch (e) { /* private mode */ }
    if (typeof _setRegistryTab === 'function') _setRegistryTab(isComposite ? 'composite' : 'process');
    document.title = address.split('.').pop() + ' — Vivarium card';
    var tries = 0;
    (function attempt() {
      var host = null, html = null;
      if (isComposite) {
        var c = (window._compositesById || {})[address];
        if (c) { host = document.getElementById('registry-composites-container'); html = _renderCompositeCardFull(c); }
      } else {
        var e = _registryEntryByAddress(address);
        if (e) { host = document.getElementById('registry-processes-container'); html = _renderRegistryEntryFull(e); }
      }
      if (host && html) {
        host.innerHTML = '<div class="reg-cards reg-cards-full popcard-single">' + html + '</div>';
        if (typeof _observeRunnableCards === 'function') _observeRunnableCards(host);
        // "Pop back in" in the header action row (top-right, replacing the now-
        // redundant ↗ pop-out button — hidden via .popcard-mode CSS).
        var _hdr = host.querySelector('.pcard-header');
        if (_hdr && !_hdr.querySelector('.popcard-backin-hdr')) {
          var _bi = document.createElement('button');
          _bi.className = 'popcard-backin-hdr'; _bi.type = 'button';
          _bi.textContent = '◀ Pop back in';
          _bi.title = 'Return to the workbench (with the side menu) and show this composite here';
          _bi.onclick = function (ev) { ev.stopPropagation(); _popCardBackIn(address, kind); };
          _hdr.appendChild(_bi);
        }
        // For composites, auto-open Explore so the loom is visible immediately
        // (via the header Explore button so its label stays in sync).
        if (isComposite) {
          var expBtn = host.querySelector('.pcard-explore-btn');
          if (expBtn && typeof _toggleLoomCard === 'function') _toggleLoomCard(expBtn);
        }
        return;
      }
      // Not ready yet — (re)trigger the load. The registry/composites endpoints
      // can transiently 500 under concurrency, which would otherwise leave the
      // one-shot load empty; re-trigger every ~1.2s until the data arrives.
      if (tries % 6 === 0) {
        if (isComposite) { if (typeof _loadComposites === 'function') _loadComposites(); }
        else { window._registryLoaded = false; if (typeof _loadRegistry === 'function') _loadRegistry(false); }
      }
      if (tries++ < 120) setTimeout(attempt, 200);
    })();
  }
  window._enterPopcardMode = _enterPopcardMode;

  // Open a composite in the FULL workbench (rail visible), maximized with Explore
  // open — shared by the card-grid "Explore" button and the "pop back in" target.
  function _enterMaxcardMode(address, kind) {
    var isComposite = (kind !== 'process');
    if (typeof _switchPage === 'function') _switchPage('modules');
    window._registryZoom = 'full';
    try { localStorage.setItem('viv.registryZoom', 'full'); } catch (e) { /* private mode */ }
    if (typeof _setRegistryTab === 'function') _setRegistryTab(isComposite ? 'composite' : 'process');
    var tries = 0;
    (function attempt() {
      var host = null, html = null;
      if (isComposite) {
        var c = (window._compositesById || {})[address];
        if (c) { host = document.getElementById('registry-composites-container'); html = _renderCompositeCardFull(c); }
      } else {
        var e = _registryEntryByAddress(address);
        if (e) { host = document.getElementById('registry-processes-container'); html = _renderRegistryEntryFull(e); }
      }
      if (host && html) {
        host.innerHTML = '<div class="reg-cards reg-cards-full popcard-single">' + html + '</div>';
        if (typeof _observeRunnableCards === 'function') _observeRunnableCards(host);
        // Maximize (fills the pane, pins to top, and auto-opens Explore/loom).
        var card = host.querySelector('.registry-entry-full');
        var maxBtn = card && card.querySelector('.pcard-maximize');
        if (maxBtn) setTimeout(function () { _toggleCardMaximize(maxBtn); }, 60);
        return;
      }
      if (tries % 6 === 0) {
        if (isComposite) { if (typeof _loadComposites === 'function') _loadComposites(); }
        else { window._registryLoaded = false; if (typeof _loadRegistry === 'function') _loadRegistry(false); }
      }
      if (tries++ < 120) setTimeout(attempt, 200);
    })();
  }
  window._enterMaxcardMode = _enterMaxcardMode;

  // "Pop back in" from a pop-out window: return to the full workbench (rail
  // visible) with this composite maximized. Prefer navigating the opener so the
  // pop-out closes; fall back to navigating this window.
  function _popCardBackIn(address, kind) {
    var url = location.origin + location.pathname +
      '?maxcard=' + encodeURIComponent(address) + '&kind=' + encodeURIComponent(kind || 'composite');
    if (window.opener && !window.opener.closed) {
      try { window.opener.location.href = url; window.opener.focus(); window.close(); return; } catch (e) { /* fall through */ }
    }
    window.location.href = url;
  }
  window._popCardBackIn = _popCardBackIn;

  // The unified ProcessCard renderer (§ unified-process-card design):
  //   header: name + kind badge + address
  //   summary: a little INFO PANEL (Config / Inputs / Outputs counts — click to
  //     jump) beside the contract line + description.
  //   accordion: four collapsible sections, in order —
  //     Configure ▸ Inputs ▸ Run ▸ Outputs. Each expands in place; several can be
  //     open at once. Config + input fields load lazily (resolved defaults).
  //   Click the header name to pin the card to the top of the scroll region.
  // Static name·type rows (no editable value) — used for non-runnable kinds'
  // config/inputs/outputs, which are a contract to read, not a form to fill.
  function _schemaRowsHtml(schema) {
    var keys = (schema && typeof schema === 'object') ? Object.keys(schema) : [];
    if (!keys.length) return '<p class="muted" style="font-size:0.82em;padding:2px 0">none</p>';
    return '<div class="cfg-list cfg-list-static">' + keys.map(function (k) {
      var t = _regTypeLabel(schema[k]);
      return '<div class="cfg-row"><div class="cfg-row-name"><span class="cfg-key">' + _esc(k) + '</span>' +
        (t ? '<span class="cfg-type">' + _esc(t) + '</span>' : '') + '</div></div>';
    }).join('') + '</div>';
  }

  function _renderProcessCard(p, kind, o) {
    o = o || {};
    var nonRun = !!o.nonRunnable;   // emitter/visualization/analysis/type/report_card
    var cfgKeys = (p.config_schema && typeof p.config_schema === 'object') ? Object.keys(p.config_schema) : [];
    var nCfg = cfgKeys.length, nIn = _nPorts(p.inputs), nOut = _nPorts(p.outputs);
    var desc = (p.description || '').trim();
    var isProc = (kind === 'process');
    var timestep = isProc
      ? '<label class="loom-run-field loom-run-interval-field">Timestep <input type="number" step="any" class="loom-run-interval" value="1"></label>'
      : '<span class="muted pcard-run-note">Step — runs to fixed point (no timestep)</span>';
    var section = _pcardSection;
    var kindBadge = _procKindBadge(kind) || (nonRun ? '<span class="proc-kind-badge proc-kind-other">' + _esc(_procKindLabel(kind)) + '</span>' : '');
    var contractMeta = nonRun
      ? _procKindLabel(kind).toLowerCase() + ' · <strong>' + nIn + '</strong> in / <strong>' + nOut + '</strong> out'
      : _procKindLabel(kind).toLowerCase() + ' process · <strong>' + nIn + '</strong> in / <strong>' + nOut + '</strong> out';

    var configBody = nonRun
      ? _schemaRowsHtml(p.config_schema)
      : '<div class="cfg-list" data-role="cfg"><span class="muted loom-load-hint">resolving defaults…</span></div>' +
        _cfgJsonTools() +
        '<div class="pcard-config-actions">' +
          '<button class="btn-mini pcard-apply" type="button" onclick="_applyProcessConfig(this)" title="Apply config &amp; re-derive ports">✓ Apply</button>' +
          '<button class="btn-mini" type="button" onclick="_resetRunPanel(this)" title="Reset to resolved defaults">↺ Reset</button>' +
          _cfgJsonToggle() +
          '<span class="pcard-apply-status muted" data-role="apply-status"></span>' +
        '</div>';

    var inputsBody = nonRun
      ? _schemaRowsHtml(p.inputs)
      : '<div class="cfg-list" data-role="inputs"><span class="muted loom-load-hint">resolving defaults…</span></div>';

    // Run is a persistent bar (not an accordion) — only for runnable kinds.
    // Same treatment as the composite card: the ▶ RUN label IS the button, with
    // the Timestep/step-note beside it (no separate white Run button).
    var runBar = nonRun ? '' : _pcardRunBar(
      '<button class="pcard-run-go" type="button" onclick="_runRegistryProcess(this)">▶ Run</button>' +
      timestep +
      '<span class="pcard-run-out" data-role="run-inline-status"></span>');

    var outputsBody = nonRun
      ? _schemaRowsHtml(p.outputs)
      : '<div class="pcard-outputs" data-pane="outputs">' + _pcardRail('outputs', p.outputs, 'out') + '</div>' +
        '<div class="loom-run-output" data-role="run-output"><span class="muted pcard-run-hint">Run to see outputs.</span></div>';
    var dlBtn = nonRun ? '' : '<button class="btn-mini pcard-dl" type="button" title="Download outputs" disabled onclick="event.stopPropagation();_downloadProcessOutputs(this)">⬇</button>';

    return '<div class="registry-entry registry-entry-full' + (nonRun ? '' : ' loom-runnable') + ' pcard pcard-accordion' + (o.selCls || '') + '"' + (o.sourceAttr || '') + (o.addrAttr || '') + '>' +
      '<div class="loom-card loom-card-stack loom-card-' + kind + '">' +
        '<div class="pcard-top">' +
          '<div class="pcard-header pcard-title" onclick="_pinCardTop(this)" title="Click to pin to top">' +
            '<span class="loom-name">' + _esc(p.name) + '</span>' + kindBadge + _regUseBadge(p) +
            '<code class="loom-addr">' + _esc(p.address || kind) + '</code>' +
            _cardPopoutBtn(p.address || kind, kind) +
          '</div>' +
          '<div class="pcard-summary">' +
            '<div class="pcard-desc-col">' +
              '<div class="pcard-contract-meta" data-role="contract-meta">' + contractMeta + '</div>' +
              (function () { var s = _regStatsHtml(p); return s ? '<div class="reg-card-stats pcard-usage">' + s + '</div>' : ''; })() +
              (desc ? '<p class="loom-desc pcard-desc-clamp" onclick="_pcardToggleDesc(this)" title="Click to expand / collapse">' + _esc(desc) + '</p>' : '') +
            '</div>' +
          '</div>' +
        '</div>' +
        '<div class="pcard-acc">' +
          section('configure', 'Configure', '<span class="pcard-sec-count">' + nCfg + '</span><span class="pcard-config-chips" data-role="config-chips" hidden></span>', configBody, { resizable: true }) +
          section('inputs', 'Inputs', '<span class="pcard-sec-count">' + nIn + '</span>', inputsBody, { resizable: true }) +
          runBar +
          section('outputs', 'Outputs', '<span class="pcard-sec-count">' + nOut + '</span>', outputsBody, dlBtn ? { headExtra: dlBtn } : {}) +
        '</div>' +
      '</div>' +
    '</div>';
  }

  // _pcardToggleDesc / _pcardSecGripDown / _pcardSecGripFull / _pcardToggleSec /
  // _pcardJumpSec / _compositeLoomExplore / _runCmdChip / _copyRunCmd /
  // _renderCompositeCardGrid moved to static/composite-card.js
  // (study-spine-reorg Task 6). composite-card.js's _pcardToggleSec still
  // calls _loadFullRunFields (below, process-only) and
  // _openCompositeLoomInline (this page's live-loom glue) as globals.

  // Composite table (Table / dense zoom).
  function _renderCompositeTableHtml(list) {
    var mod = function (c) { return (c.module || ''); };
    var rows = list.map(function (c) {
      var sp = c.studies || {};
      var np = (c.parameters && typeof c.parameters === 'object') ? Object.keys(c.parameters).length : 0;
      var nr = (c.requires && c.requires.processes) ? c.requires.processes.length : 0;
      var sel = (window._registrySelected === c.id) ? ' reg-selected' : '';
      return '<tr class="reg-tr' + sel + '" data-address="' + _esc(c.id) + '" onclick="_selectRegistryEntry(\'' + _esc(c.id) + '\')" ondblclick="_setRegistryZoom(\'full\')" title="Double-click to open the full card">' +
        '<td class="reg-td-name"><strong>' + _esc(c.name) + '</strong> <code>' + _esc(c.id) + '</code></td>' +
        '<td>' + _esc(mod(c)) + '</td>' +
        '<td class="num">' + np + '</td>' +
        '<td class="num">' + nr + '</td>' +
        '<td class="num">' + (sp.studies || 0) + '</td>' +
        '<td class="num">' + _successCell(sp) + '</td>' +
      '</tr>';
    }).join('');
    return '<div class="registry-table-wrap"><table class="registry-table"><thead><tr>' +
      '<th>Name</th><th>Module</th><th class="num">Params</th><th class="num">Needs</th><th class="num">Studies</th><th class="num">Success</th>' +
      '</tr></thead><tbody>' + rows + '</tbody></table></div>';
  }

  // _compositeOutIdle / _compositeOutControls / _syncOutEmitter /
  // _loadCompositeObservables moved to static/composite-card.js
  // (study-spine-reorg Task 6).

  // Poll a launched composite run and render its progress → visualizations into
  // the card's Outputs panel. /api/composite-run/<id>/status returns
  // {status, progress_step, n_steps, ...} and, on completion, viz_html.
  // When a composite run finishes: reset the ▶ RUN button from its "Running…"
  // indicator and drop the Outputs section open so the results are visible.
  function _endRunIndicator(card, statusText) {
    var b = card && card._runBtn;
    if (b) { b.disabled = false; b.textContent = card._runBtnOrig || '▶ Run'; card._runBtn = null; }
    var st = card && card.querySelector('.pcard-run-status');
    if (st) { st.classList.remove('pcard-apply-err'); if (statusText != null) st.innerHTML = statusText; }
  }
  function _openOutputsSection(card) {
    var sec = card && card.querySelector('.pcard-sec-outputs');
    if (sec && !sec.classList.contains('pcard-sec-open')) {
      var h = sec.querySelector('.pcard-sec-head'); if (h) _pcardToggleSec(h);
    }
  }
  function _pollCompositeRun(card, runId) {
    var panel = card.querySelector('[data-role="out-panel"]'); if (!panel) return;
    var dl = _api('/api/composite-run/' + encodeURIComponent(runId) + '/download');
    var runsLink = '<a href="#simulations" onclick="_switchPage(\'simulations\');return false;">Runs</a>';
    card._pollRun = runId;   // guard: a newer run supersedes this poll
    var tick = function () {
      if (card._pollRun !== runId) return;   // superseded
      fetch(_api('/api/composite-run/' + encodeURIComponent(runId) + '/status'))
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
        .then(function (res) {
          if (card._pollRun !== runId) return;
          var j = res.j || {};
          var st = j.status || (res.ok ? 'running' : 'unknown');
          var prog = (j.progress_step != null && j.n_steps)
            ? ' <span class="muted">step ' + j.progress_step + ' / ' + j.n_steps + '</span>' : '';
          if (st === 'running' || st === 'queued' || st === 'starting') {
            panel.innerHTML = '<div class="pcard-out-run"><p class="pcard-out-empty-title">Running…' + prog + '</p>' +
              '<div class="pcard-out-progress"><span style="width:' + (j.n_steps ? Math.round((j.progress_step || 0) / j.n_steps * 100) : 0) + '%"></span></div>' +
              '<p class="muted"><code>' + _esc(runId) + '</code></p></div>';
            setTimeout(tick, 1500);
          } else if (st === 'completed' || st === 'done' || st === 'success') {
            // viz_html is a { name: htmlString } map (parsed from the run's viz
            // JSON) — collect the HTML fragments; may be empty for runs with no
            // declared visualizations.
            var viz = j.viz_html;
            var htmls = [];
            if (viz && typeof viz === 'object' && !Array.isArray(viz)) {
              Object.keys(viz).forEach(function (k) { if (typeof viz[k] === 'string' && viz[k].trim()) htmls.push(viz[k]); });
            } else if (typeof viz === 'string' && viz.trim()) { htmls.push(viz); }
            var art = function (name, label) { return '<a href="' + _esc(_api('/api/composite-run/' + encodeURIComponent(runId) + '/artifact/' + name)) + '" target="_blank" rel="noopener">' + label + '</a>'; };
            var links = ['<a href="' + _esc(dl) + '" target="_blank" rel="noopener">Download ZIP</a>'];
            if (j.has_report) links.push(art('report', 'Report'));
            if (j.has_analyses) links.push(art('analyses', 'Analyses'));
            links.push('open in ' + runsLink);
            panel.innerHTML = '<div class="pcard-out-runhead">✓ completed · ' + links.join(' · ') + '</div>' +
              (htmls.length
                ? '<iframe class="pcard-out-viz" sandbox="allow-scripts allow-same-origin"></iframe>'
                : '<p class="muted">This run produced no inline visualization. Use Download ZIP' + (j.has_report || j.has_analyses ? ' / the report/analyses above' : '') + ', or open it in ' + runsLink + '.</p>');
            if (htmls.length) { var f = panel.querySelector('.pcard-out-viz'); if (f) f.srcdoc = htmls.join('\n<hr>\n'); }
            _endRunIndicator(card, '✓ done — see Outputs');
            _openOutputsSection(card);   // drop Outputs open now that results are ready
          } else {   // failed / orphaned / error
            panel.innerHTML = '<div class="pcard-out-run"><p class="pcard-out-empty-title loom-run-err">✗ ' + _esc(st) + '</p>' +
              (j.error ? '<pre class="loom-run-pre">' + _esc(String(j.error)) + '</pre>' : '') +
              '<p class="muted">Details under ' + runsLink + '.</p></div>';
            _endRunIndicator(card, '✗ ' + _esc(st));
            _openOutputsSection(card);
          }
        })
        .catch(function () { if (card._pollRun === runId) setTimeout(tick, 3000); });
    };
    panel.innerHTML = '<div class="pcard-out-run"><p class="pcard-out-empty-title">Launching…</p><p class="muted"><code>' + _esc(runId) + '</code></p></div>';
    tick();
  }
  window._pollCompositeRun = _pollCompositeRun;

  // _renderCompositeCardFull moved to static/composite-card.js
  // (study-spine-reorg Task 6) — shared verbatim with the Study Detail Model
  // tab. composite-card.js loads before this file and exports it on
  // `window`, so callers below (_enterPopcardMode/_enterMaxcardMode/etc.)
  // keep working unchanged.

  // Apply edited parameters → re-resolve the Explore bigraph with the overrides.
  function _applyCompositeConfig(btn) {
    var card = btn.closest('.registry-entry-full'); if (!card) return;
    var status = card.querySelector('[data-role="apply-status"]');
    var cfg = _collectCardConfig(card);
    if (cfg.__error) { if (status) { status.textContent = cfg.__error; status.classList.add('pcard-apply-err'); } return; }
    card._appliedConfig = cfg;
    _updateConfigChips(card, cfg);
    var id = card.getAttribute('data-address');
    var embed = card.querySelector('.ccard-loom-embed');
    var host = embed && embed.querySelector('.ccard-loom-frame');
    if (host) host.innerHTML = '<p class="muted" style="padding:10px;font-size:0.85em">Re-resolving with new parameters…</p>';
    if (status) { status.textContent = 'Re-resolving…'; status.classList.remove('pcard-apply-err'); }
    // Validate the overrides by resolving FIRST. If the build rejects them
    // (e.g. a single-value param handed a comma-list), surface the exception
    // instead of silently rendering the default (unoverridden) wiring.
    var url = _api('/api/composite-resolve?id=' + encodeURIComponent(id) +
      '&overrides=' + encodeURIComponent(JSON.stringify(cfg)));
    fetch(url).then(function (r) { return r.json(); }).then(function (j) {
      if (j && (j.wiring_status === 'error' || (j.error && !j.state))) {
        var msg = j.notice || j.error || 'configuration rejected';
        if (status) { status.textContent = '✗ ' + msg; status.classList.add('pcard-apply-err'); }
        if (host) host.innerHTML = '<div class="loom-run-err" style="padding:10px">✗ ' + _esc(String(msg)) + '</div>';
        return;   // do NOT reload the loom — it would show a stale/default graph
      }
      if (embed) {
        embed._overrides = JSON.stringify(cfg);
        embed._loomLoaded = false;
        var sec = embed.closest('.pcard-sec');
        if (sec && !sec.classList.contains('pcard-sec-open')) { var h = sec.querySelector('.pcard-sec-head'); if (h) _pcardToggleSec(h); }
        else _openCompositeLoomInline(embed);
      }
      if (status) { status.textContent = '✓ applied — Explore re-resolved'; status.classList.remove('pcard-apply-err'); }
    }).catch(function (e) {
      if (status) { status.textContent = '✗ ' + ((e && e.message) || 'resolve failed'); status.classList.add('pcard-apply-err'); }
      if (host) host.innerHTML = '<div class="loom-run-err" style="padding:10px">✗ resolve failed</div>';
    });
  }
  window._applyCompositeConfig = _applyCompositeConfig;

  function _resetCompositeConfig(btn) {
    var card = btn.closest('.registry-entry-full'); if (!card) return;
    var c = (window._compositesById || {})[card.getAttribute('data-address')];
    if (!c) return;
    var params = (c.parameters && typeof c.parameters === 'object') ? c.parameters : {};
    var cfgBox = card.querySelector('[data-role="cfg"]');
    if (cfgBox) {
      var ks = Object.keys(params);
      cfgBox.innerHTML = ks.length
        ? ks.map(function (k) { var pv = params[k] || {}; return _runField(k, ('default' in pv) ? pv.default : null, { type: pv.type, description: pv.description }); }).join('')
        : '<p class="muted" style="font-size:0.82em">No parameters.</p>';
    }
    card._appliedConfig = null;
    var embed = card.querySelector('.ccard-loom-embed'); if (embed) embed._overrides = null;
    var status = card.querySelector('[data-role="apply-status"]'); if (status) { status.textContent = ''; status.classList.remove('pcard-apply-err'); }
  }
  window._resetCompositeConfig = _resetCompositeConfig;

  // item 20a: shared pre-dispatch gate for every live /api/composite-test-run
  // launcher below (_runComposite's inline pcard Run bar, _ceTestRun's
  // Composite Explorer Test Run panel) -- before a remote-pinned deployment
  // dispatches to AWS Batch, fetch the server-resolved
  // repo/branch/commit/simulator_id and require explicit confirmation
  // (mirrors study-detail.js's _dispatchRemotePinned), so a workspace-
  // identity mismatch is caught here, before money gets spent, not
  // discovered afterward via aws batch describe-jobs. A plain local-engine
  // run (unchanged, pre-existing behavior) fires with no confirm.
  function _confirmRemoteDispatchThen(fireFn, cancelFn) {
    fetch(_api('/api/remote-run-config')).then(function (r) { return r.json(); }).catch(function () { return {}; }).then(function (cfg) {
      cfg = cfg || {};
      if (cfg.pinned) {
        var msg = 'Dispatch to AWS Batch:\n\n' +
          '  repo:    ' + (cfg.repo_url || '(unknown)') + '\n' +
          '  branch:  ' + (cfg.branch || '(unknown)') + '\n' +
          '  commit:  ' + ((cfg.commit || '(unknown)').slice(0, 12)) + '\n' +
          '  simulator id: ' + (cfg.simulator_id != null ? cfg.simulator_id : '(unknown)') + '\n\n' +
          'Proceed?';
        if (!confirm(msg)) { if (cancelFn) cancelFn(); return; }
      }
      fireFn();
    });
  }
  window._confirmRemoteDispatchThen = _confirmRemoteDispatchThen;

  // Launch a composite run directly (no modal): POST /api/simulation with the
  // inline Time (t_end), the Configure params as overrides, and an auto name.
  // Detached run — feedback + a link to Runs; the "Configure & Run" modal is
  // still available via _useComposite for advanced setup.
  function _runComposite(btn) {
    var card = btn.closest('.registry-entry-full'); if (!card) return;
    var id = card.getAttribute('data-address');
    var c = (window._compositesById || {})[id] || {};
    var bar = btn.closest('.pcard-runbar');
    var status = bar && bar.querySelector('.pcard-run-status');
    if (!status && bar) { status = document.createElement('span'); status.className = 'pcard-run-status muted'; bar.appendChild(status); }
    var setErr = function (m) { if (status) { status.textContent = m; status.classList.add('pcard-apply-err'); } };
    var tEl = card.querySelector('.pcard-run-time');
    var steps = (tEl && tEl.value !== '') ? parseInt(tEl.value, 10) : NaN;
    if (isNaN(steps) || steps <= 0) { setErr('Enter the number of steps first'); if (tEl) tEl.focus(); return; }
    var overrides = _collectCardConfig(card);
    if (overrides.__error) { setErr(overrides.__error); return; }
    // Detached composite run launcher: POST /api/composite-test-run → 202 {run_id}.
    var payload = { id: id, steps: steps, overrides: overrides, label: (c.name || 'composite') + '-run' };
    // Observables selection (Outputs tab). Server semantics: empty/omitted →
    // emit all stores (the default). So all-checked → omit (unchanged default);
    // a subset → emit exactly those; none → block (empty would mean "all").
    if (card._obsLoaded) {
      var obsCbs = card.querySelectorAll('.pcard-obs-cb');
      if (obsCbs.length) {
        var checked = [];
        obsCbs.forEach(function (cb) { if (cb.checked) checked.push(cb.value); });
        if (checked.length === 0) { setErr('Select at least one observable to emit (in Outputs)'); return; }
        if (checked.length < obsCbs.length) payload.emit_paths = checked;
      }
    }
    var orig = btn.textContent;
    _confirmRemoteDispatchThen(function () {
      btn.disabled = true; btn.textContent = 'Launching…';
      if (status) { status.classList.remove('pcard-apply-err'); status.textContent = 'launching run…'; }
      fetch(_api('/api/composite-test-run'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, status: r.status, j: j }; }); })
        .then(function (res) {
          var rid = res.j && res.j.run_id;
          if ((res.status === 202 || rid) && rid) {
            // Keep the ▶ RUN button as a live "Running…" indicator until the poll
            // resolves; the Outputs section auto-drops-down when results are READY
            // (see _pollCompositeRun's completed / failed branches).
            btn.disabled = true; btn.textContent = '⏳ Running…';
            card._runBtn = btn; card._runBtnOrig = orig;
            if (status) { status.classList.remove('pcard-apply-err'); status.innerHTML = '<span class="pcard-run-live">● running…</span>'; }
            _pollCompositeRun(card, rid);
          } else if (res.status === 202 || rid) {
            btn.disabled = false; btn.textContent = orig;
            if (status) { status.classList.remove('pcard-apply-err'); status.innerHTML = '✓ launched — tracking in Outputs'; }
          } else if (res.status === 429) {
            btn.disabled = false; btn.textContent = orig;
            setErr('too many runs in progress — try again shortly');
          } else {
            btn.disabled = false; btn.textContent = orig;
            setErr('✗ ' + ((res.j && res.j.error) || ('HTTP ' + res.status)));
          }
        })
        .catch(function (e) { btn.disabled = false; btn.textContent = orig; setErr('network error: ' + String(e)); });
    }, function () { setErr('Cancelled.'); });
  }
  window._runComposite = _runComposite;

  // Populate a runnable Full card's editable config (top) + input fields (left)
  // from resolved defaults. Shared by lazy-load and Reset.
  function _fillFullFields(card, config, inputs, inSchema) {
    var cfgBox = card.querySelector('[data-role="cfg"]');
    var inBox = card.querySelector('[data-role="inputs"]');
    if (cfgBox) {
      var entry = _registryEntryByAddress(card.getAttribute('data-address')) || {};
      var cfgSchema = (entry.config_schema && typeof entry.config_schema === 'object') ? entry.config_schema : {};
      var ck = Object.keys(config || {});
      cfgBox.innerHTML = ck.length
        ? ck.map(function (k) { return _runField(k, config[k], { type: _regTypeLabel(cfgSchema[k]) }); }).join('')
        : '<span class="muted" style="font-size:0.82em">no config parameters</span>';
    }
    if (inBox) {
      var ik = Object.keys(inputs || {});
      inBox.innerHTML = ik.length
        ? ik.map(function (k) { return _runInputField(k, inputs[k], (inSchema || {})[k]); }).join('')
        : '<div class="loom-port loom-port-empty muted">(no input ports)</div>';
      card.querySelectorAll('textarea.loom-in-field').forEach(_autoGrow);
    }
  }

  // The registry entry for an address (config_schema/inputs live here — the
  // authoritative contract, independent of whether the class can be
  // instantiated with an empty config).
  function _registryEntryByAddress(address) {
    var m = window._registryByKind || {};
    var kinds = Object.keys(m);
    for (var i = 0; i < kinds.length; i++) {
      var arr = m[kinds[i]] || [];
      for (var j = 0; j < arr.length; j++) {
        if (arr[j] && arr[j].address === address) return arr[j];
      }
    }
    return null;
  }

  // A sensible editable default for a port/config type schema (used when the
  // instantiation-based template can't resolve a value).
  function _defaultForSchema(s) {
    if (s && typeof s === 'object' && !Array.isArray(s) && ('_default' in s)) return s._default;
    var t = String(_regTypeLabel(s) || '').toLowerCase();
    if (/(^|\b)(map|tree|dict|node|inplace)/.test(t)) return {};
    if (/(^|\b)(list|array|tuple|set)/.test(t)) return [];
    if (t.indexOf('int') >= 0 || t.indexOf('float') >= 0 || t.indexOf('number') >= 0) return 0;
    if (t.indexOf('bool') >= 0) return false;
    if (t.indexOf('string') >= 0) return '';
    return null;
  }

  // Field set = the STATIC schema's keys (the real contract), each valued from
  // the template when it resolved one, else a type-based default.
  function _mergeSchemaDefaults(schema, template) {
    schema = schema || {}; template = template || {};
    var keys = Object.keys(schema);
    if (!keys.length) keys = Object.keys(template);
    var out = {};
    keys.forEach(function (k) {
      out[k] = (k in template) ? template[k] : _defaultForSchema(schema[k]);
    });
    return out;
  }

  // Populate a runnable Full card's config + input fields. The field SET comes
  // from the entry's static config_schema/inputs (always present); the template
  // (core.fill via instantiation) only refines default VALUES — heavy Steps
  // like Metabolism can't be instantiated with an empty config, so the template
  // returns empty and we must not blank the contract.
  function _loadFullRunFields(card) {
    if (!card || card._loaded) return;
    card._loaded = true;
    var address = card.getAttribute('data-address');
    var entry = _registryEntryByAddress(address) || {};
    var cfgSchema = (entry.config_schema && typeof entry.config_schema === 'object') ? entry.config_schema : {};
    var inSchemaStatic = (entry.inputs && typeof entry.inputs === 'object') ? entry.inputs : {};
    var url = (window.DataSource && window.DataSource.apiUrl ? window.DataSource.apiUrl('/api/registry/process-template') : '/api/registry/process-template') +
      '?address=' + encodeURIComponent(address);
    var apply = function (tCfg, tIn, tInSchema) {
      var config = _mergeSchemaDefaults(cfgSchema, tCfg);
      var inputs = _mergeSchemaDefaults(inSchemaStatic, tIn);
      var inputsSchema = (tInSchema && Object.keys(tInSchema).length) ? tInSchema : inSchemaStatic;
      card._defaults = { config: config, inputs: inputs, inputsSchema: inputsSchema };
      _fillFullFields(card, config, inputs, inputsSchema);
    };
    fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (j) {
        apply(
          (j && j.ok && j.config && typeof j.config === 'object') ? j.config : {},
          (j && j.ok && j.inputs && typeof j.inputs === 'object') ? j.inputs : {},
          (j && j.inputs_schema && typeof j.inputs_schema === 'object') ? j.inputs_schema : {}
        );
      })
      .catch(function () { apply({}, {}, {}); });
  }
  // Exported: static/composite-card.js's _pcardToggleSec (moved out in the
  // study-spine-reorg Task 6 extraction) calls this as a global for
  // non-composite (process) cards.
  window._loadFullRunFields = _loadFullRunFields;

  // Load resolved defaults for runnable Full cards as they scroll into view, so
  // the Full zoom doesn't fire N template fetches for every process at once.
  function _observeRunnableCards(root) {
    if (typeof _syncPcardSplit === 'function') _syncPcardSplit(root);
    var cards = (root || document).querySelectorAll('.loom-runnable');
    if (!cards.length) return;
    if (!('IntersectionObserver' in window)) { cards.forEach(_loadFullRunFields); return; }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { if (e.isIntersecting) { _loadFullRunFields(e.target); io.unobserve(e.target); } });
    }, { rootMargin: '250px' });
    cards.forEach(function (c) { io.observe(c); });
  }

  // _runField / _cfgJsonTools / _cfgJsonToggle moved to
  // static/composite-card.js (study-spine-reorg Task 6).

  // Reveal/hide the JSON panel; on reveal, prefill it with the current values.
  function _toggleConfigJson(btn) {
    var body = btn.closest('.pcard-sec-body') || btn.closest('.pcard-region-body'); if (!body) return;
    var panel = body.querySelector('[data-role="cfg-json"]'); if (!panel) return;
    var show = panel.hidden;
    panel.hidden = !show;
    btn.classList.toggle('active', show);
    if (show) {
      var card = btn.closest('.registry-entry-full');
      var cfg = card ? _collectCardConfig(card) : {};
      var box = panel.querySelector('.cfg-json-box');
      if (box && !cfg.__error) { box.value = JSON.stringify(cfg, null, 2); _autoGrow(box); }
      if (box) box.focus();
    }
  }
  window._toggleConfigJson = _toggleConfigJson;
  // Set a config field's value from a JS value, respecting its editor vtype.
  function _setCfgFieldValue(el, v) {
    var vt = el.getAttribute('data-vtype');
    if (vt === 'boolean') { el.checked = !!v; return; }
    if (vt === 'number') { el.value = (v == null ? '' : v); return; }
    if (vt === 'json') { el.value = (typeof v === 'string') ? v : (function () { try { return JSON.stringify(v); } catch (e) { return ''; } })(); }
    else { el.value = (v == null ? '' : String(v)); }
    if (el.tagName === 'TEXTAREA') _autoGrow(el);
  }
  // Parse the JSON box and push each key onto its matching field.
  function _applyConfigJson(btn) {
    var panel = btn.closest('[data-role="cfg-json"]'); if (!panel) return;
    var box = panel.querySelector('.cfg-json-box');
    var status = panel.querySelector('.cfg-json-status');
    var setErr = function (m) { if (status) { status.textContent = '✗ ' + m; status.classList.add('pcard-apply-err'); } };
    var obj;
    try { obj = JSON.parse(box.value); } catch (e) { return setErr(e.message); }
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return setErr('expected a JSON object');
    var card = btn.closest('.registry-entry-full'); if (!card) return;
    var esc = (window.CSS && CSS.escape) ? function (s) { return CSS.escape(s); } : function (s) { return s; };
    var n = 0, unknown = [];
    Object.keys(obj).forEach(function (k) {
      var el = card.querySelector('.loom-cfg-field[data-key="' + esc(k) + '"]');
      if (!el) { unknown.push(k); return; }
      _setCfgFieldValue(el, obj[k]); n++;
    });
    if (status) {
      status.classList.remove('pcard-apply-err');
      status.textContent = '✓ set ' + n + ' field' + (n === 1 ? '' : 's') +
        (unknown.length ? ' · ignored ' + unknown.length + ' unknown (' + unknown.slice(0, 3).join(', ') + (unknown.length > 3 ? '…' : '') + ')' : '');
    }
  }
  window._applyConfigJson = _applyConfigJson;

  // One input-port field: name + expected TYPE + editable default. Scalars get a
  // typed input; nested/complex values get an auto-growing JSON box (no scroll).
  function _runInputField(key, value, typeSchema) {
    var typeLabel = _regTypeLabel(typeSchema);
    var t = (value === null) ? 'null' : (Array.isArray(value) ? 'json' : typeof value);
    var attr = 'class="loom-in-field" data-key="' + _esc(key) + '" data-vtype="';
    var field;
    if (t === 'boolean') {
      field = '<input type="checkbox" ' + attr + 'boolean"' + (value ? ' checked' : '') + '>';
    } else if (t === 'number') {
      field = '<input type="number" step="any" ' + attr + 'number" value="' + _esc(String(value)) + '">';
    } else if (t === 'string') {
      field = '<input type="text" ' + attr + 'string" value="' + _esc(value) + '">';
    } else {
      var jv = ''; try { jv = JSON.stringify(value); } catch (e) { jv = ''; }
      field = '<textarea ' + attr + 'json" rows="1" spellcheck="false" oninput="_autoGrow(this)">' + _esc(jv) + '</textarea>';
    }
    // Same organized row layout as config (name · type · value field).
    return '<div class="cfg-row">' +
        '<div class="cfg-row-name"><span class="cfg-key">' + _esc(key) + '</span>' +
          (typeLabel ? '<span class="cfg-type" title="expected type">' + _esc(typeLabel) + '</span>' : '') + '</div>' +
        '<div class="cfg-row-input">' + field + '</div>' +
      '</div>';
  }

  function _autoGrow(el) {
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = (el.scrollHeight + 2) + 'px';
  }
  window._autoGrow = _autoGrow;
  function _autoGrowRunFields(root) {
    (root || document).querySelectorAll('textarea.loom-in-field').forEach(_autoGrow);
  }

  function _renderRunForm(kind, config, inputs, inputsSchema) {
    var isProc = (kind === 'process');
    var cfgKeys = Object.keys(config || {});
    var cfgFields = cfgKeys.length
      ? cfgKeys.map(function (k) { return _runField(k, config[k]); }).join('')
      : '<p class="muted" style="font-size:0.82em">No config parameters.</p>';
    inputsSchema = inputsSchema || {};
    var inKeys = Object.keys(inputs || {});
    var inFields = inKeys.length
      ? inKeys.map(function (k) { return _runInputField(k, inputs[k], inputsSchema[k]); }).join('')
      : '<p class="muted" style="font-size:0.82em">No input ports.</p>';
    return '<div class="loom-run-form">' +
        '<div class="loom-run-section"><div class="loom-run-legend">Config</div>' +
          '<div class="loom-cfg-fields">' + cfgFields + '</div></div>' +
        '<div class="loom-run-section"><div class="loom-run-legend">Input ports</div>' +
          '<div class="loom-in-fields">' + inFields + '</div></div>' +
        (isProc ? '<label class="loom-run-field loom-run-interval-field">Timestep <input type="number" step="any" class="loom-run-interval" value="1"></label>' : '') +
        '<div class="loom-run-actions">' +
          '<button class="action-btn" onclick="_runRegistryProcess(this)">▶ Run</button>' +
          '<button class="btn-mini" onclick="_resetRunPanel(this)" title="Reset to resolved defaults">↺ Reset</button>' +
        '</div>' +
        '<div class="loom-run-output"></div>' +
      '</div>';
  }

  function _resetRunPanel(btn) {
    var card = btn.closest('.registry-entry-full');
    if (!card) return;
    var d = card._defaults || { config: {}, inputs: {}, inputsSchema: {} };
    _fillFullFields(card, d.config, d.inputs, d.inputsSchema);
    card._appliedConfig = null;
    // Restore the config chips + a cleared run state (ports revert to the card's
    // static contract on the next Apply / render).
    if (typeof _updateConfigChips === 'function') _updateConfigChips(card, d.config);
    var out = card.querySelector('.loom-run-output'); if (out) out.innerHTML = '';
    var dl = card.querySelector('.pcard-dl'); if (dl) dl.disabled = true;
    card._lastOutputs = null;
    var status = card.querySelector('[data-role="apply-status"]'); if (status) { status.textContent = ''; status.classList.remove('pcard-apply-err'); }
  }
  window._resetRunPanel = _resetRunPanel;

  // ---- ProcessCard interactions: config expand / Apply / regions / pin / download ----

  // Toggle the config top-bar panel (chips ⇄ editable fields). Lazily loads the
  // resolved defaults the first time it opens.
  // Collapse / expand one card region (config · inputs · contract · outputs) on
  // double-click of its header. Expanding config ensures its fields are loaded.
  function _toggleRegion(head) {
    var region = head.closest('.pcard-region'); if (!region) return;
    var collapsed = region.classList.toggle('pcard-collapsed');
    var caret = head.querySelector('.pcard-caret');
    if (caret) caret.textContent = collapsed ? '▸' : '▾';
    if (!collapsed && region.querySelector('[data-role="cfg"]')) {
      var card = head.closest('.registry-entry-full'); if (card) _loadFullRunFields(card);
    }
  }
  window._toggleRegion = _toggleRegion;

  // Composite Run bar: double-click pulls the embedded loom in/out (the internal
  // bigraph graph). Wired for composite cards; slice 2 mounts the loom here.
  function _toggleLoomView(bar) {
    var card = bar.closest('.pcard'); if (!card) return;
    var view = card.querySelector('[data-role="loom-view"]'); if (!view) return;
    var open = view.hidden;
    view.hidden = !open;
    card.classList.toggle('pcard-loom-open', open);
    // slice 2: lazily mount the composite's loom iframe into `view` on first open.
  }
  window._toggleLoomView = _toggleLoomView;

  // ── ProcessCard inputs|outputs splitter ──────────────────────────────────────
  // One draggable gutter sets the boundary `b` (inputs width %); outputs takes
  // the rest. Each pane degrades by its pixel width through three levels:
  //   full (>248px, editable) → info (name+type) → dots (≤64px; drag to an edge).
  // The split is one shared layout (drag once, all cards reflow) and persists.
  window._pcardSplit = (function () {
    try { var s = parseFloat(localStorage.getItem('viv.pcardSplit')); if (isFinite(s) && s >= 0 && s <= 100) return s; } catch (e) { /* private mode */ }
    return 45;
  })();
  function _paneLevel(px) { return px <= 64 ? 'dots' : (px <= 248 ? 'info' : 'full'); }
  function _applyPcardSplitRow(row, b) {
    row._b = b;
    row.style.setProperty('--pb', b);
    var rw = row.getBoundingClientRect().width || 900;
    var inPx = b / 100 * rw, outPx = rw - inPx - 10;
    var ip = row.querySelector('.pcard-inputs'); if (ip) ip.setAttribute('data-level', _paneLevel(inPx));
    var op = row.querySelector('.pcard-outputs'); if (op) op.setAttribute('data-level', _paneLevel(outPx));
  }
  function _applyPcardSplitAll(b) {
    b = Math.max(0, Math.min(100, b));
    window._pcardSplit = b;
    try { localStorage.setItem('viv.pcardSplit', String(b)); } catch (e) { /* private mode */ }
    document.querySelectorAll('.pcard-ports-row').forEach(function (row) { _applyPcardSplitRow(row, b); });
  }
  window._applyPcardSplitAll = _applyPcardSplitAll;
  function _syncPcardSplit(root) {
    var b = (typeof window._pcardSplit === 'number') ? window._pcardSplit : 45;
    (root || document).querySelectorAll('.pcard-ports-row').forEach(function (row) { _applyPcardSplitRow(row, b); });
  }
  window._syncPcardSplit = _syncPcardSplit;
  // Expand a card's Outputs accordion section so run results are visible.
  function _ensureOutputsOpen(card) {
    var sec = (card || document).querySelector('.pcard-sec[data-sec="outputs"]');
    if (sec && !sec.classList.contains('pcard-sec-open')) {
      var head = sec.querySelector('.pcard-sec-head'); if (head) _pcardToggleSec(head);
    }
  }
  window._ensureOutputsOpen = _ensureOutputsOpen;
  function _pcardGutterDown(e, gutter) {
    if (e.cancelable) e.preventDefault();
    var row = gutter.closest('.pcard-ports-row'); if (!row) return;
    var rect = row.getBoundingClientRect();
    var b = (typeof window._pcardSplit === 'number') ? window._pcardSplit : 45;
    document.body.classList.add('pcard-splitting');
    function clientX(ev) { return (ev.touches && ev.touches[0]) ? ev.touches[0].clientX : ev.clientX; }
    function pct(x) { return Math.max(0, Math.min(100, ((x - rect.left) / rect.width) * 100)); }
    function onMove(ev) { b = pct(clientX(ev)); _applyPcardSplitAll(b); if (ev.cancelable) ev.preventDefault(); }
    function onUp() {
      document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp);
      document.removeEventListener('touchmove', onMove); document.removeEventListener('touchend', onUp);
      document.body.classList.remove('pcard-splitting');
      // Snap to a thin dots rail at either edge.
      var rw = rect.width || 900, edge = 46 / rw * 100;
      if (b / 100 * rw <= 70) b = edge;
      else if ((100 - b) / 100 * rw <= 70) b = 100 - edge;
      _applyPcardSplitAll(b);
    }
    document.addEventListener('mousemove', onMove); document.addEventListener('mouseup', onUp);
    document.addEventListener('touchmove', onMove, { passive: false }); document.addEventListener('touchend', onUp);
  }
  window._pcardGutterDown = _pcardGutterDown;

  // A snapped-closed inputs/outputs pane collapses to this compact RAIL: each
  // port as dot + name + type (drag the divider back open for the full API).
  function _pcardRail(which, schema, side) {
    var keys = (schema && typeof schema === 'object') ? Object.keys(schema) : [];
    if (!keys.length) return '<div class="pcard-rail"><span class="pcard-rail-empty">—</span></div>';
    var rows = keys.map(function (k) {
      var t = _regTypeLabel(schema[k]);
      return '<div class="pcard-rail-port loom-port-' + side + '" title="' + _esc(k + (t ? ' : ' + t : '')) +
        '" data-port="' + _esc(k) + '" data-type="' + _esc(t || '') + '" onclick="_portDotInfo(event,this)">' +
        '<span class="pcard-rail-dot"></span>' +
        '<span class="pcard-rail-name">' + _esc(k) + '</span>' +
        (t ? '<span class="pcard-rail-type">' + _esc(t) + '</span>' : '') +
        '</div>';
    }).join('');
    return '<div class="pcard-rail' + (side === 'out' ? ' pcard-rail-out' : '') + '">' + rows + '</div>';
  }
  window._pcardRail = _pcardRail;

  // Click a rail dot / port row → a small popover with the port name + type
  // (the primary "more info" affordance when a pane is collapsed to dots).
  function _portDotInfo(e, el) {
    if (e) { e.stopPropagation(); if (e.preventDefault) e.preventDefault(); }
    var old = document.querySelector('.pcard-portpop'); if (old) old.remove();
    var name = el.getAttribute('data-port'), type = el.getAttribute('data-type');
    var pop = document.createElement('div');
    pop.className = 'pcard-portpop';
    pop.innerHTML = '<code>' + _esc(name) + '</code>' + (type ? '<span class="pcard-portpop-type">' + _esc(type) + '</span>' : '');
    document.body.appendChild(pop);
    var r = el.getBoundingClientRect();
    pop.style.left = Math.round(r.right + 8 + window.scrollX) + 'px';
    pop.style.top = Math.round(r.top + window.scrollY - 3) + 'px';
    var close = function () { pop.remove(); document.removeEventListener('mousedown', close); window.removeEventListener('scroll', close, true); };
    setTimeout(function () { document.addEventListener('mousedown', close); window.addEventListener('scroll', close, true); }, 0);
  }
  window._portDotInfo = _portDotInfo;

  // Config top-bar as a PULL-DOWN: drag the header down to reveal the full config
  // API (editable fields), up to collapse back to the informative chips panel. A
  // plain click (no drag) toggles. Lazily loads resolved fields on first expand.
  function _configPull(e, head) {
    if (e && e.preventDefault) e.preventDefault();
    var bar = head.closest('.pcard-config-region'); if (!bar) return;
    function y(ev) { return (ev.touches && ev.touches[0]) ? ev.touches[0].clientY : ev.clientY; }
    var startY = y(e), moved = false;
    function setExpanded(exp) {
      bar.classList.toggle('pcard-collapsed', !exp);
      var caret = bar.querySelector('.pcard-caret'); if (caret) caret.textContent = exp ? '▾' : '▸';
      var grip = bar.querySelector('.pcard-config-grip'); if (grip) grip.textContent = exp ? '⌃' : '⌄';
      if (exp) { var card = bar.closest('.registry-entry-full'); if (card && typeof _loadFullRunFields === 'function') _loadFullRunFields(card); }
    }
    function onMove(ev) {
      var dy = y(ev) - startY;
      if (Math.abs(dy) > 16) { moved = true; setExpanded(dy > 0); }
      if (ev.cancelable) ev.preventDefault();
    }
    function onUp() {
      document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp);
      document.removeEventListener('touchmove', onMove); document.removeEventListener('touchend', onUp);
      if (!moved) setExpanded(bar.classList.contains('pcard-collapsed'));  // click = toggle
    }
    document.addEventListener('mousemove', onMove); document.addEventListener('mouseup', onUp);
    document.addEventListener('touchmove', onMove, { passive: false }); document.addEventListener('touchend', onUp);
  }
  window._configPull = _configPull;

  // Clicking a card header scrolls it up to just below the sticky toolbar, so
  // it lines up at the top without manual scrolling. (No sticky pin — a plain
  // one-time scroll.)
  function _pinCardTop(el) {
    var card = el.closest('.pcard'); if (!card) return;
    var sticky = document.querySelector('.registry-sticky');
    var offset = (sticky ? Math.round(sticky.getBoundingClientRect().height) : 0) + 8;
    card.style.scrollMarginTop = offset + 'px';
    try { card.scrollIntoView({ block: 'start', behavior: 'smooth' }); } catch (e) { card.scrollIntoView(); }
  }
  window._pinCardTop = _pinCardTop;

  // Collect the config fields into an object (or {__error} on bad JSON).
  function _collectCardConfig(card) {
    var config = {}, bad = null;
    card.querySelectorAll('.loom-cfg-field').forEach(function (el) {
      if (bad) return;
      var key = el.getAttribute('data-key'), vt = el.getAttribute('data-vtype'), v;
      if (vt === 'boolean') v = el.checked;
      else if (vt === 'number') v = (el.value === '' ? null : parseFloat(el.value));
      else if (vt === 'json') { try { v = (el.value === '' ? null : JSON.parse(el.value)); } catch (e) { bad = 'Config "' + key + '": ' + e.message; return; } }
      else v = el.value;
      config[key] = v;
    });
    return bad ? { __error: bad } : config;
  }

  // Rebuild a pane's rail (dot · name · type list) from a re-derived schema.
  function _rebuildRail(card, side, schema) {
    var pane = card.querySelector(side === 'in' ? '.pcard-inputs' : '.pcard-outputs'); if (!pane) return;
    var rail = pane.querySelector('.pcard-rail');
    var html = _pcardRail(side === 'in' ? 'inputs' : 'outputs', schema || {}, side);
    if (rail) rail.outerHTML = html; else pane.insertAdjacentHTML('beforeend', html);
  }

  function _updateContractMeta(card, kind, nIn, nOut) {
    var meta = card.querySelector('[data-role="contract-meta"]'); if (!meta) return;
    var mOut = meta.textContent.match(/(\d+)\s*out/);
    var outN = (nOut != null) ? nOut : (mOut ? mOut[1] : 0);
    meta.innerHTML = _esc(kind || 'process') + ' · <strong>' + nIn + '</strong> in / <strong>' + outN + '</strong> out';
  }

  function _updateConfigChips(card, config) {
    var box = card.querySelector('[data-role="config-chips"]'); if (!box) return;
    var keys = Object.keys(config || {});
    box.innerHTML = keys.length
      ? keys.slice(0, 6).map(function (k) { return '<code class="pcard-chip">' + _esc(k) + '</code>'; }).join('') +
        (keys.length > 6 ? ' <span class="muted pcard-chip-more">+' + (keys.length - 6) + '</span>' : '')
      : '<span class="muted">none</span>';
  }

  // Apply the edited config: re-derive the ports (re-instantiate with the config
  // override) and refill input fields + output chips + contract counts.
  function _applyProcessConfig(btn) {
    var card = btn.closest('.registry-entry-full'); if (!card) return;
    var address = card.getAttribute('data-address');
    var status = card.querySelector('[data-role="apply-status"]');
    var config = _collectCardConfig(card);
    if (config.__error) { if (status) { status.textContent = config.__error; status.classList.add('pcard-apply-err'); } return; }
    if (status) { status.textContent = 'applying…'; status.classList.remove('pcard-apply-err'); }
    var base = (window.DataSource && window.DataSource.apiUrl) ? window.DataSource.apiUrl('/api/registry/process-template') : '/api/registry/process-template';
    var url = base + '?address=' + encodeURIComponent(address) + '&config=' + encodeURIComponent(JSON.stringify(config));
    fetch(url).then(function (r) { return r.json(); }).then(function (j) {
      if (!j || !j.ok) { if (status) { status.textContent = (j && j.error) ? j.error : 'apply failed'; status.classList.add('pcard-apply-err'); } return; }
      var inputs = (j.inputs && typeof j.inputs === 'object') ? j.inputs : {};
      var inSchema = (j.inputs_schema && typeof j.inputs_schema === 'object') ? j.inputs_schema : {};
      var inBox = card.querySelector('[data-role="inputs"]');
      if (inBox) {
        var ik = Object.keys(inputs);
        inBox.innerHTML = ik.length ? ik.map(function (k) { return _runInputField(k, inputs[k], inSchema[k]); }).join('')
          : '<div class="loom-port loom-port-empty muted">(no input ports)</div>';
        inBox.querySelectorAll('textarea.loom-in-field').forEach(_autoGrow);
      }
      _rebuildRail(card, 'in', inSchema);   // update the inputs rail (info/dots levels)
      var outSchema = (j.outputs_schema && typeof j.outputs_schema === 'object' && Object.keys(j.outputs_schema).length) ? j.outputs_schema : null;
      if (outSchema) _rebuildRail(card, 'out', outSchema);
      _updateContractMeta(card, j.kind || card.getAttribute('data-kind'), Object.keys(inputs).length, outSchema ? Object.keys(outSchema).length : null);
      _updateConfigChips(card, config);
      card._appliedConfig = config;
      if (status) { status.textContent = '✓ applied'; status.classList.remove('pcard-apply-err'); }
    }).catch(function () { if (status) { status.textContent = 'network error'; status.classList.add('pcard-apply-err'); } });
  }
  window._applyProcessConfig = _applyProcessConfig;

  // Download the last run's outputs. A single process update() has no run dir to
  // zip, so we serialize the returned outputs to a JSON file client-side.
  function _downloadProcessOutputs(btn) {
    var card = btn.closest('.registry-entry-full'); if (!card || !card._lastOutputs) return;
    var name = (card.getAttribute('data-address') || 'process').split('.').pop();
    var blob;
    try { blob = new Blob([JSON.stringify(card._lastOutputs, null, 2)], { type: 'application/json' }); } catch (e) { return; }
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name + '-outputs.json';
    document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 0);
  }
  window._downloadProcessOutputs = _downloadProcessOutputs;

  // Collect the Full card's config (per-field) + inputs (JSON), run, show outputs.
  function _runRegistryProcess(btn) {
    var card = btn.closest('.registry-entry-full');
    if (!card) return;
    var out = card.querySelector('.loom-run-output');
    var address = card.getAttribute('data-address');

    var config = {}, bad = null;
    card.querySelectorAll('.loom-cfg-field').forEach(function (el) {
      if (bad) return;
      var key = el.getAttribute('data-key'), vt = el.getAttribute('data-vtype'), v;
      if (vt === 'boolean') v = el.checked;
      else if (vt === 'number') v = (el.value === '' ? null : parseFloat(el.value));
      else if (vt === 'json') {
        try { v = (el.value === '' ? null : JSON.parse(el.value)); }
        catch (e) { bad = 'Config "' + key + '": ' + e.message; return; }
      } else v = el.value;
      config[key] = v;
    });
    if (bad) { out.innerHTML = '<div class="loom-run-err">' + _esc(bad) + '</div>'; return; }

    var inputs = {};
    card.querySelectorAll('.loom-in-field').forEach(function (el) {
      if (bad) return;
      var key = el.getAttribute('data-key'), vt = el.getAttribute('data-vtype'), v;
      if (vt === 'boolean') v = el.checked;
      else if (vt === 'number') v = (el.value === '' ? null : parseFloat(el.value));
      else if (vt === 'json') {
        try { v = (el.value === '' ? null : JSON.parse(el.value)); }
        catch (e) { bad = 'Input "' + key + '": ' + e.message; return; }
      } else v = el.value;
      inputs[key] = v;
    });
    if (bad) { out.innerHTML = '<div class="loom-run-err">' + _esc(bad) + '</div>'; return; }

    var ivEl = card.querySelector('.loom-run-interval');
    var interval = ivEl ? parseFloat(ivEl.value) : undefined;
    var orig = btn.textContent;
    btn.disabled = true; btn.textContent = 'Running…';
    out.innerHTML = '<div class="muted" style="font-size:0.85em">Running…</div>';
    fetch('/api/registry/run-process', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ address: address, config: config, inputs: inputs, interval: interval }),
    })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        btn.disabled = false; btn.textContent = orig;
        if (j && j.ok) {
          card._lastOutputs = j.outputs;
          if (typeof _ensureOutputsOpen === 'function') _ensureOutputsOpen(card);
          var dl = card.querySelector('.pcard-dl'); if (dl) { dl.disabled = false; dl.title = 'Download outputs (JSON)'; }
          out.innerHTML = '<div class="loom-run-ok">✓ ran — outputs' +
            '<button class="btn-mini loom-copy-btn" onclick="_copyRunOutput(this)" title="Copy outputs JSON">⧉ Copy</button></div>' +
            _jsonViewer(j.outputs) +
            '<pre class="loom-run-raw" hidden>' + _esc(JSON.stringify(j.outputs, null, 2)) + '</pre>';
        } else {
          var stage = (j && j.stage) ? '[' + j.stage + '] ' : '';
          out.innerHTML = '<div class="loom-run-err">✗ ' + _esc(stage) + _esc((j && j.error) || 'run failed') + '</div>' +
            (j && j.trace ? '<pre class="json-tree loom-run-pre loom-run-trace">' + _esc(j.trace) + '</pre>' : '');
        }
      })
      .catch(function (e) {
        btn.disabled = false; btn.textContent = orig;
        out.innerHTML = '<div class="loom-run-err">Network error: ' + _esc(String(e)) + '</div>';
      });
  }
  window._runRegistryProcess = _runRegistryProcess;

  // Copy the run output's JSON to the clipboard.
  // Collapsible JSON viewer — objects/arrays are <details> nodes (top two
  // levels open); scalars are typed leaves. Big/nested run outputs stay
  // navigable instead of a wall of pretty-printed text.
  function _jsonNode(key, val, depth) {
    var keyHtml = (key !== null) ? '<span class="jt-key">' + _esc(key) + '</span><span class="jt-colon">:</span> ' : '';
    if (val === null) return '<div class="jt-row">' + keyHtml + '<span class="jt-null">null</span></div>';
    if (Array.isArray(val)) {
      if (!val.length) return '<div class="jt-row">' + keyHtml + '<span class="jt-punct">[ ]</span></div>';
      var ob = depth < 2 ? ' open' : '';
      var ab = val.map(function (v, i) { return _jsonNode(String(i), v, depth + 1); }).join('');
      return '<details class="jt-branch"' + ob + '><summary>' + keyHtml +
        '<span class="jt-punct">[</span><span class="jt-count">' + val.length + '</span><span class="jt-punct">]</span></summary>' +
        '<div class="jt-children">' + ab + '</div></details>';
    }
    if (typeof val === 'object') {
      var keys = Object.keys(val);
      if (!keys.length) return '<div class="jt-row">' + keyHtml + '<span class="jt-punct">{ }</span></div>';
      var oo = depth < 2 ? ' open' : '';
      var ob2 = keys.map(function (k) { return _jsonNode(k, val[k], depth + 1); }).join('');
      return '<details class="jt-branch"' + oo + '><summary>' + keyHtml +
        '<span class="jt-punct">{</span><span class="jt-count">' + keys.length + '</span><span class="jt-punct">}</span></summary>' +
        '<div class="jt-children">' + ob2 + '</div></details>';
    }
    var t = typeof val;
    var cls = t === 'number' ? 'jt-num' : (t === 'boolean' ? 'jt-bool' : 'jt-str');
    var disp = t === 'string' ? '"' + _esc(val) + '"' : _esc(String(val));
    return '<div class="jt-row">' + keyHtml + '<span class="' + cls + '">' + disp + '</span></div>';
  }
  function _jsonViewer(value) {
    return '<div class="json-viewer">' + _jsonNode(null, value, 0) + '</div>';
  }

  function _copyRunOutput(btn) {
    var out = btn.closest('.loom-run-output');
    var pre = out ? out.querySelector('.loom-run-raw') : null;
    if (!pre) return;
    var text = pre.textContent || '';
    var done = function () { var o = btn.textContent; btn.textContent = '✓ Copied'; setTimeout(function () { btn.textContent = o; }, 1200); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(function () { done(); });
    } else {
      try {
        var ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta);
        ta.select(); document.execCommand('copy'); document.body.removeChild(ta); done();
      } catch (e) { /* ignore */ }
    }
  }
  window._copyRunOutput = _copyRunOutput;

  // Table view: all entries in one sortable table (Name/Module/Uses/In/Out/Source).
  function _renderRegistryTable(el, entries) {
    var sortKey = window._registryTableSort || 'use';
    var sortDir = window._registryTableDir || 'desc';
    var mod = function (p) { return (p.address || '').split('.')[0]; };
    var rows = entries.slice().sort(function (a, b) {
      var av, bv;
      if (sortKey === 'name') { av = (a.name || '').toLowerCase(); bv = (b.name || '').toLowerCase(); }
      else if (sortKey === 'kind') { av = _procKindLabel(a.kind).toLowerCase(); bv = _procKindLabel(b.kind).toLowerCase(); }
      else if (sortKey === 'module') { av = mod(a).toLowerCase(); bv = mod(b).toLowerCase(); }
      else if (sortKey === 'in') { av = _nPorts(a.inputs); bv = _nPorts(b.inputs); }
      else if (sortKey === 'out') { av = _nPorts(a.outputs); bv = _nPorts(b.outputs); }
      else if (sortKey === 'studies') { av = (a.study_participation || {}).studies || 0; bv = (b.study_participation || {}).studies || 0; }
      else if (sortKey === 'success') {
        av = (a.study_participation || {}).success_pct; av = (av == null ? -1 : av);
        bv = (b.study_participation || {}).success_pct; bv = (bv == null ? -1 : bv);
      }
      else if (sortKey === 'source') { av = a.source || ''; bv = b.source || ''; }
      else { av = a.use_count || 0; bv = b.use_count || 0; }
      var c = av < bv ? -1 : (av > bv ? 1 : (a.name || '').localeCompare(b.name || ''));
      return sortDir === 'desc' ? -c : c;
    });
    // Per-column widths (%), persisted across re-renders so a resize sticks.
    var COLS = ['name', 'kind', 'module', 'use', 'studies', 'success', 'in', 'out', 'source'];
    var W = (window._registryColWidths && window._registryColWidths.length === COLS.length)
      ? window._registryColWidths
      : [30, 9, 13, 8, 9, 10, 6, 6, 9];
    window._registryColWidths = W;
    var colgroup = '<colgroup>' + W.map(function (w) { return '<col style="width:' + w + '%">'; }).join('') + '</colgroup>';
    function th(key, label, cls, idx) {
      var on = sortKey === key;
      // Resize grip on every column but the last; stops the sort click.
      var grip = (idx < COLS.length - 1)
        ? '<span class="col-resize" onmousedown="_startColResize(event,' + idx + ')" onclick="event.stopPropagation()" title="Drag to resize"></span>'
        : '';
      return '<th class="reg-th' + (cls ? ' ' + cls : '') + (on ? ' active' : '') +
        '" onclick="_setRegistryTableSort(\'' + key + '\')"><span class="reg-th-label">' + label +
        (on ? (sortDir === 'desc' ? ' ▾' : ' ▴') : '') + '</span>' + grip + '</th>';
    }
    var body = rows.map(function (p) {
      var sel = (window._registrySelected && window._registrySelected === p.address) ? ' reg-selected' : '';
      return '<tr class="reg-tr' + sel + '" data-source="' + _esc(p.source || '') + '" data-address="' + _esc(p.address || '') +
          '" onclick="_selectRegistryEntry(\'' + _esc(p.address || '') + '\')" ondblclick="_zoomInOn(\'' + _esc(p.address || '') + '\')"' +
          ' title="Click to select · double-click to zoom in on this process">' +
        '<td class="reg-td-name" title="' + _esc(p.address || p.name || '') + '"><strong>' + _esc(p.name) + '</strong> <code>' + _esc(p.address || '') + '</code></td>' +
        '<td class="reg-td-kind">' + (_procKindBadge(p.kind) || _esc(_procKindLabel(p.kind))) + '</td>' +
        '<td title="' + _esc(mod(p)) + '">' + _esc(mod(p)) + '</td>' +
        '<td class="num">' + (p.use_count || 0) + '</td>' +
        '<td class="num">' + ((p.study_participation || {}).studies || 0) + '</td>' +
        '<td class="num">' + _successCell(p.study_participation) + '</td>' +
        '<td class="num">' + _nPorts(p.inputs) + '</td>' +
        '<td class="num">' + _nPorts(p.outputs) + '</td>' +
        '<td class="reg-td-src">' + _esc(p.source || '') + '</td>' +
      '</tr>';
    }).join('');
    el.innerHTML = '<div class="registry-table-wrap"><table class="registry-table reg-table-fill">' + colgroup + '<thead><tr>' +
      th('name', 'Name', '', 0) + th('kind', 'Type', '', 1) + th('module', 'Module', '', 2) + th('use', 'Uses', 'num', 3) +
      th('studies', 'Studies', 'num', 4) + th('success', 'Success', 'num', 5) +
      th('in', 'In', 'num', 6) + th('out', 'Out', 'num', 7) + th('source', 'Source', '', 8) +
      '</tr></thead><tbody>' + body + '</tbody></table></div>';
  }

  // Drag a column's right-edge grip to resize it, stealing width from the next
  // column so the table stays exactly as wide as the window (no hidden columns).
  function _startColResize(e, i) {
    e.preventDefault(); e.stopPropagation();
    var table = e.target.closest('table');
    var cols = table.querySelectorAll('colgroup col');
    if (!cols[i] || !cols[i + 1]) return;
    var startX = e.clientX, tableW = table.getBoundingClientRect().width || 1;
    var wA = parseFloat(cols[i].style.width), wB = parseFloat(cols[i + 1].style.width);
    function move(ev) {
      var d = (ev.clientX - startX) / tableW * 100;
      // Clamp so neither column collapses below a usable minimum.
      d = Math.max(-(wA - 4), Math.min(wB - 4, d));
      cols[i].style.width = (wA + d) + '%';
      cols[i + 1].style.width = (wB - d) + '%';
    }
    function up() {
      document.removeEventListener('mousemove', move);
      document.removeEventListener('mouseup', up);
      document.body.classList.remove('col-resizing');
      var out = []; cols.forEach(function (c) { out.push(parseFloat(c.style.width)); });
      window._registryColWidths = out;   // persist across re-renders
    }
    document.body.classList.add('col-resizing');
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
  }
  window._startColResize = _startColResize;

  // _successCell moved to static/composite-card.js (study-spine-reorg Task 6).
  function _setRegistryTableSort(key) {
    if (window._registryTableSort === key) {
      window._registryTableDir = (window._registryTableDir === 'desc') ? 'asc' : 'desc';
    } else {
      window._registryTableSort = key;
      window._registryTableDir = (key === 'name' || key === 'module' || key === 'source') ? 'asc' : 'desc';
    }
    // Keep the Sort dropdown + grid ordering in sync with header clicks.
    window._registrySort = key;
    var sel = document.getElementById('registry-sort'); if (sel) sel.value = key;
    _rerenderRegistryKinds();
  }
  window._setRegistryTableSort = _setRegistryTableSort;

  // Imported repositories panel: one card per workspace.yaml::imports entry,
  // showing the repo (linked to its source) + the registry classes it
  // contributes (grouped by kind). Lets a user see "the actual repositories
  // that are imported, as well as their processes/steps" without hunting
  // through the flat per-kind tabs.
  function _renderImportedRepos(imports, processes, types) {
    var section = document.getElementById('registry-imports-section');
    var el = document.getElementById('registry-imports-container');
    var countEl = document.getElementById('registry-imports-count');
    if (!section || !el) return;
    if (!imports || !imports.length) { section.hidden = true; return; }
    section.hidden = false;
    if (countEl) countEl.textContent = imports.length;

    // Index classes by top-level package.
    var byPkg = {};
    (processes || []).forEach(function(p) {
      var pkg = (p.address || '').split('.')[0];
      (byPkg[pkg] = byPkg[pkg] || []).push(p);
    });
    (types || []).forEach(function(t) {
      var pkg = (t.address || t.name || '').split('.')[0];
      (byPkg[pkg] = byPkg[pkg] || []).push({name: t.name, kind: 'type'});
    });

    var KIND_LABEL = {process: 'Processes', step: 'Steps', emitter: 'Emitters',
                      visualization: 'Visualizations', type: 'Types', other: 'Other'};
    var KIND_ORDER = ['process', 'step', 'emitter', 'visualization', 'type', 'other'];

    el.innerHTML = imports.map(function(imp) {
      var classes = byPkg[imp.package] || [];
      var groups = {};
      classes.forEach(function(c) {
        var k = c.kind || 'other';
        (groups[k] = groups[k] || []).push(c.name);
      });
      var body = KIND_ORDER.filter(function(k) { return groups[k] && groups[k].length; })
        .map(function(k) {
          var names = groups[k].slice().sort();
          return '<div class="imported-repo-kind">' +
            '<span class="imported-repo-kind-label">' + _esc(KIND_LABEL[k] || k) +
            ' (' + names.length + ')</span> ' +
            names.map(function(n) {
              return '<span class="tag-pill" style="background:#eef2ff;color:#3730a3">' + _esc(n) + '</span>';
            }).join(' ') +
            '</div>';
        }).join('');
      if (!classes.length) {
        body = '<p class="muted" style="font-size:0.85em;margin:6px 0 0">No registered classes discovered (package may be install-gated).</p>';
      }
      var refBadge = imp.ref
        ? ' <span class="tag-pill" style="background:#f1f5f9;color:#475569">@' + _esc(imp.ref) + '</span>'
        : '';
      var title = imp.source
        ? '<a href="' + _esc(imp.source) + '" target="_blank" rel="noopener">' + _esc(imp.name) + '</a>'
        : _esc(imp.name);
      return '<div class="module-card module-card-workspace">' +
        '<div class="module-card-header"><strong>' + title + '</strong>' + refBadge +
        ' <span class="tag-pill" style="background:#dcfce7;color:#166534">' + classes.length + ' classes</span></div>' +
        (imp.description ? '<p class="module-desc">' + _esc(imp.description) + '</p>' : '') +
        body +
        '</div>';
    }).join('');
  }

  function _renderRegistryGrid(containerId, entries) {
    // In a pop-out window the single card owns its container — don't clobber it.
    if (document.body.classList.contains('popcard-mode')) return;
    var el = document.getElementById(containerId);
    if (!el) return;
    if (!entries || !entries.length) {
      el.innerHTML = '<p class="empty-state">None registered.</p>';
      return;
    }
    // Apply the (data-driven) text filter uniformly across every layout.
    if (window._registryFilter) {
      entries = entries.filter(_registryEntryMatches);
      if (!entries.length) {
        el.innerHTML = '<p class="empty-state muted" style="font-size:0.9em">No entries match “' + _esc(window._registryFilter) + '”.</p>';
        return;
      }
    }

    var zoom = window._registryZoom || 'grid';

    // Most-compact zoom IS the table: one sortable table over all entries.
    if (zoom === 'table') { _renderRegistryTable(el, entries); return; }

    // Partition by source: in_workspace first, then framework, then environment_only.
    var inWs = entries.filter(function(p) { return p.source === 'in_workspace'; });
    var framework = entries.filter(function(p) { return p.source === 'framework'; });
    var envOnly = entries.filter(function(p) { return p.source === 'environment_only' || !p.source; });

    // Grid zoom packs cards into a multi-column grid; Full is one wide card/row.
    var cardsCls = 'reg-cards reg-cards-' + (zoom === 'full' ? 'full' : 'grid');
    var html = '';

    // In-workspace and framework entries, ordered by the current sort control.
    var _sortKey = window._registrySort || 'use';
    var primary = inWs.concat(framework).sort(function(a, b) { return _registryGridCmp(a, b, _sortKey); });
    envOnly.sort(function(a, b) { return _registryGridCmp(a, b, _sortKey); });
    if (primary.length) {
      html += '<div class="' + cardsCls + '">' + primary.map(_renderRegistryEntry).join('') + '</div>';
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
        '<div class="' + cardsCls + '" style="opacity:0.6;margin-top:6px">' +
        envOnly.map(_renderRegistryEntry).join('') +
        '</div>' +
        '<p style="font-size:0.8em;color:#9ca3af;margin:4px 0 0">Run <code>/pbg-install &lt;pkg&gt;</code> to add a package to this workspace\'s imports.</p>' +
        '</details>';
    }

    el.innerHTML = html;
    // Full zoom only: lazily resolve+inject each runnable card's config/inputs.
    if (zoom === 'full') _observeRunnableCards(el);
    // Sync the column control (visible only in Cards zoom); apply the
    // multi-column layout when in Cards.
    _syncColsControls();
    if (zoom === 'grid') el.querySelectorAll('.reg-cards-grid').forEach(function (c) { _applyCardCols(c, 'registry'); });
  }

  // Render Analysis classes (v2ecoli ANALYSIS_REGISTRY entries) in the Registry
  // Discovered → Analyses tab. These have {name, address, doc} shape (from
  // /api/visualization-classes, kind === 'analysis') — no source/schema info.
  function _renderAnalysisRegistryGrid(containerId, entries) {
    var el = document.getElementById(containerId);
    if (!el) return;
    if (!entries || !entries.length) {
      el.innerHTML = '<p class="empty-state">No Analysis classes registered. Install a workspace that provides them (e.g. v2ecoli\'s <code>ANALYSIS_REGISTRY</code>).</p>';
      return;
    }
    el.innerHTML = entries.map(function(c) {
      return '<div class="registry-entry">' +
        '<strong>' + _esc(c.name) + '</strong><br>' +
        '<small><code>' + _esc(c.address) + '</code></small>' +
        (c.doc ? '<br><small style="color:#666">' + _esc(c.doc) + '</small>' : '') +
      '</div>';
    }).join('');
  }

  // Enrich the Registry Discovered tabs with the class catalog that the Analyses
  // page used to own: the v2ecoli Analysis classes (new Analyses tab) and any
  // Visualization classes from _list_visualization_classes() not already present
  // via build_core() introspection (so nothing is lost when moving the catalog
  // here). Best-effort — a failure leaves the build_core-derived tabs intact.
  function _enrichRegistryWithVizClasses(vizEntries) {
    var entries = vizEntries || [];
    var analyses = entries.filter(function(c) { return c.kind === 'analysis'; })
      .map(function (c) { return { name: c.name, address: c.address, description: c.doc || '', kind: 'analysis', source: 'framework' }; });
    var vizzes   = entries.filter(function(c) { return c.kind !== 'analysis'; });

    // Analyses tab — same card renderers as everything else (grid/full/table),
    // and registered so semantic-zoom re-renders pick them up.
    (window._registryByKind = window._registryByKind || {})['registry-analyses-container'] = analyses;
    _renderRegistryGrid('registry-analyses-container', analyses);
    var aCount = document.getElementById('registry-analysis-count');
    if (aCount) aCount.textContent = analyses.length;

    // Merge viz classes into the Visualizations tab. build_core entries already
    // rendered there carry source info; append catalog-only ones (e.g.
    // pbg_superpowers base classes) as framework so they show, deduped by name.
    var existing = {};
    document.querySelectorAll('#registry-visualizations-container .registry-entry strong')
      .forEach(function(s) { existing[(s.textContent || '').trim()] = true; });
    var extra = vizzes.filter(function(c) { return !existing[(c.name || '').trim()]; })
      .map(function(c) {
        return { name: c.name, address: c.address, source: 'framework', aliases: [] };
      });
    if (extra.length) {
      var container = document.getElementById('registry-visualizations-container');
      if (container) {
        // Re-render with the union so source grouping stays correct.
        var current = (window._registryVizEntries || []);
        _renderRegistryGrid('registry-visualizations-container', current.concat(extra));
      }
    }
  }
  window._enrichRegistryWithVizClasses = _enrichRegistryWithVizClasses;

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

  // Data-driven filter: store the query and re-render so it works uniformly
  // across the Table / Cards / Full layouts (the old per-.registry-entry DOM
  // hide didn't match the new table rows or grid cards).
  // Sort state (default: most-used first). Ordering — including grouping by
  // Type or Source — is handled by the Sort control; there is no facet filter.
  window._registrySort = window._registrySort || 'use';

  function _filterRegistry(query) {
    // Pull an optional `sort:` token out; the remainder is a free-text match.
    var text = (query || '').replace(/\bsort:([a-z_%]+)/gi, function (_m, val) {
      window._registrySort = val.toLowerCase(); return '';
    });
    window._registryFilter = text.toLowerCase().trim();
    var sel = document.getElementById('registry-sort'); if (sel) sel.value = window._registrySort || 'use';
    _rerenderRegistryKinds();
  }
  window._filterRegistry = _filterRegistry;

  function _setRegistrySort(val) {
    window._registrySort = val || 'use';
    // Keep the table's header-sort in sync so the order is stable across zooms.
    window._registryTableSort = window._registrySort;
    window._registryTableDir = (val === 'name' || val === 'kind' || val === 'source' || val === 'module') ? 'asc' : 'desc';
    _rerenderRegistryKinds();
  }
  window._setRegistrySort = _setRegistrySort;

  // Shared ordering comparator (grid + full). Table has its own dir-aware sort
  // via clickable headers, kept in sync through _setRegistrySort.
  function _registryGridCmp(a, b, key) {
    function studies(p) { return (p.study_participation || {}).studies || (typeof p.studies === 'number' ? p.studies : 0) || 0; }
    function succ(p) { var s = (p.study_participation || {}).success_pct; return s == null ? -1 : s; }
    var byName = String(a.name || '').localeCompare(String(b.name || ''));
    if (key === 'name') return byName;
    if (key === 'kind') return _procKindLabel(a.kind).localeCompare(_procKindLabel(b.kind)) || byName;
    if (key === 'source') return String(a.source || '').localeCompare(String(b.source || '')) || byName;
    if (key === 'studies') return (studies(b) - studies(a)) || byName;
    if (key === 'success') return (succ(b) - succ(a)) || byName;
    return ((b.use_count || 0) - (a.use_count || 0)) || byName;   // 'use' (default)
  }

  // Composite ordering for the Sort control. Composites carry study info under
  // `studies` (an object with .studies/.success_pct) and `workspace_local`
  // instead of a process's `study_participation`/`source`, and have no
  // Temporal/Step kind or use-count — so they get their own comparator.
  function _compositeSortCmp(a, b, key) {
    function studies(c) { return ((c.study_participation || c.studies || {}).studies) || 0; }
    function succ(c) { var s = (c.study_participation || c.studies || {}).success_pct; return s == null ? -1 : s; }
    var byName = String(a.name || '').localeCompare(String(b.name || ''));
    var wsFirst = (a.workspace_local ? 0 : 1) - (b.workspace_local ? 0 : 1);
    if (key === 'name') return byName;
    if (key === 'studies') return (studies(b) - studies(a)) || byName;
    if (key === 'success') return (succ(b) - succ(a)) || byName;
    if (key === 'source') return wsFirst || byName;
    return wsFirst || byName;   // 'use' (default) / 'kind' — keep workspace-first, then name
  }

  function _registryEntryMatches(p) {
    var q = window._registryFilter;
    if (!q) return true;
    var hay = ((p.name || '') + ' ' + (p.address || '') + ' ' + (p.description || '') + ' ' +
      ((p.aliases || []).join(' '))).toLowerCase();
    return hay.indexOf(q) !== -1;
  }

  // Select a process (from any zoom): remembered so that when you switch zoom
  // the view re-focuses on it. Highlights the row/card and scrolls it in.
  function _selectRegistryEntry(address) {
    window._registrySelected = address;
    var panel = document.querySelector('.registry-tab-panel.active');
    if (!panel) return;
    panel.querySelectorAll('[data-address].reg-selected').forEach(function (el) { el.classList.remove('reg-selected'); });
    var sel = panel.querySelector('[data-address="' + (window.CSS && CSS.escape ? CSS.escape(address) : address) + '"]');
    if (sel) {
      sel.classList.add('reg-selected');
      try { sel.scrollIntoView({ behavior: 'smooth', block: 'center' }); } catch (e) { /* ignore */ }
    }
  }
  window._selectRegistryEntry = _selectRegistryEntry;

  // Double-click a process → zoom in one level (table → cards → full), centered
  // on it.
  function _zoomInOn(address) {
    var order = ['table', 'grid', 'full'];
    var i = order.indexOf(window._registryZoom || 'grid');
    window._registrySelected = address;
    _setRegistryZoom(order[Math.min(order.length - 1, i + 1)]);
  }
  window._zoomInOn = _zoomInOn;

  // Re-apply the selection highlight + scroll after a re-render (zoom change).
  function _refocusRegistrySelection() {
    var addr = window._registrySelected;
    if (!addr) return;
    var panel = document.querySelector('.registry-tab-panel.active');
    if (!panel) return;
    var sel = panel.querySelector('[data-address="' + (window.CSS && CSS.escape ? CSS.escape(addr) : addr) + '"]');
    if (!sel) return;
    sel.classList.add('reg-selected');
    // A Full/Grid card that carries a run panel: nudge it into view.
    setTimeout(function () { try { sel.scrollIntoView({ behavior: 'smooth', block: 'center' }); } catch (e) { /* ignore */ } }, 30);
  }

  function _loadRegistry(refresh) {
    // The tab panels show their own "Loading…" placeholder, so leave the status
    // line blank during load (it's still used for error messages below).
    var status = document.getElementById('registry-status');
    if (status) status.textContent = '';
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
        var byKind = {process: [], step: [], emitter: [], visualization: [], report_card: [], other: []};
        processes.forEach(function(p) {
          var k = p.kind || 'other';
          if (!byKind[k]) byKind[k] = [];
          byKind[k].push(p);
        });

        // ("Imported repositories" panel removed — those repos live in the
        // Modules tab; see _renderImportedRepos (now unused) for the old render.)

        // Cache per-kind entries so the semantic-zoom / view-mode toggles can
        // re-render without re-fetching. Keyed container-id -> entries.
        // Processes and Steps share one "Processes" tab — both are Processes
        // (edges); each card/row is badged Temporal vs Step (_procKindBadge).
        var procsAndSteps = byKind.process.concat(byKind.step);
        window._registryByKind = {
          'registry-processes-container': procsAndSteps,
          'registry-emitters-container': byKind.emitter,
          'registry-visualizations-container': byKind.visualization,
          'registry-report_cards-container': byKind.report_card,
        };
        // Render tabbed Registry browser (Registry page).
        _renderRegistryGrid('registry-processes-container', procsAndSteps);
        _renderRegistryGrid('registry-emitters-container', byKind.emitter);
        window._registryVizEntries = byKind.visualization;
        _renderRegistryGrid('registry-visualizations-container', byKind.visualization);
        _renderRegistryGrid('registry-report_cards-container', byKind.report_card);
        _renderRegistryTypesGrid('registry-types-container', types);
        _syncRegistryToolbar();   // reflect saved zoom / view-mode on the toolbar

        // Enrich Visualizations + populate the new Analyses tab from the class
        // catalog (/api/visualization-classes) — the catalog the Analyses page
        // used to own now lives in the Registry. Best-effort.
        window.DataSource.loadVisualizationClasses()
          .then(function(vc) { _enrichRegistryWithVizClasses((vc && vc.classes) || []); })
          .catch(function() { _enrichRegistryWithVizClasses([]); });

        // Per-tab count badges: show workspace-declared count + total in parens.
        // "in_workspace" entries are the actionable ones; environment_only are dimmed.
        var setCount = function(id, entries) {
          var el = document.getElementById(id);
          if (!el) return;
          var wsCount = entries.filter(function(e) { return e.source === 'in_workspace'; }).length;
          var total = entries.length;
          // Always show the plain total (like every other tab); the workspace vs
          // environment split lives in the tooltip rather than a cryptic "0 / 6".
          el.textContent = total;
          el.title = (wsCount === total)
            ? total + ' total'
            : wsCount + ' from this workspace, ' + (total - wsCount) + ' from environment';
        };
        setCount('registry-process-count', procsAndSteps);
        setCount('registry-emitter-count', byKind.emitter);
        setCount('registry-visualization-count', byKind.visualization);
        setCount('registry-report_card-count', byKind.report_card);
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

        // Composites live as a peer tab on this page — load + render them too.
        if (typeof _loadComposites === 'function') _loadComposites();
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
  // Retained: read by the registry toolbar sync to default the composites view.
  window._compositesZoom = (function () {
    var z; try { z = localStorage.getItem('viv.compositesZoom'); } catch (e) { z = null; }
    return (z === 'table' || z === 'cards' || z === 'loom') ? z : 'cards';
  })();



  // Flip an inline loom embed from read-only preview to LIVE mode (editable
  // config + Run), reloading the iframe at the same URL the pop-out uses
  // (?id=<ref>, no ?static=1). Works whether or not the loom has loaded yet.
  function _enableInlineLoomRun(btn) {
    var det = btn && btn.closest('.ccard-loom-embed');
    if (!det) return;
    det._loomLive = true;
    var id = det.getAttribute('data-id');
    var apiUrl = (window.DataSource && window.DataSource.apiUrl) ? window.DataSource.apiUrl.bind(window.DataSource) : function (p) { return p; };
    var liveUrl = apiUrl('/bigraph-loom/index.html') + '?id=' + encodeURIComponent(id) + '&chrome=off';
    var iframe = det.querySelector('.ccard-loom-iframe');
    if (iframe) iframe.src = liveUrl;         // already open → swap in place
    else { det._loomLoaded = false; _openCompositeLoomInline(det); }  // not open yet → load live
    var bar = det.querySelector('.ccard-loom-bar');
    if (bar) bar.innerHTML = '<span class="ccard-loom-mode live">&#9679; Live — running enabled</span>';
  }
  window._enableInlineLoomRun = _enableInlineLoomRun;


  // The static composite-state URL the loom fetches. In a PUBLISHED snapshot the
  // live /api/composite-resolve endpoint doesn't exist — the pre-resolved state
  // is a static file at /api/composite-state/<id>.json — so point there; in live
  // mode use the resolve endpoint. (Without this, "View" 404'd in the snapshot.)
  function _compositeStateUrl(id, overrides) {
    var apiUrl = (window.DataSource && window.DataSource.apiUrl)
      ? window.DataSource.apiUrl.bind(window.DataSource) : function (p) { return p; };
    if (document.body.classList.contains('snapshot')) {
      return apiUrl('/api/composite-state/' + encodeURIComponent(id) + '.json');
    }
    return apiUrl('/api/composite-resolve?id=' + encodeURIComponent(id)) +
      (overrides ? '&overrides=' + encodeURIComponent(overrides) : '');
  }

  // "Pop out" — open this composite's loom in a separate window directly (live,
  // full config + run), bypassing the standalone explorer page. In a published
  // snapshot there's no live API, so open the static (?static=1&stateUrl=) URL.
  function _popoutCompositeLoom(id) {
    var apiUrl = (window.DataSource && window.DataSource.apiUrl)
      ? window.DataSource.apiUrl.bind(window.DataSource) : function (p) { return p; };
    var url;
    if (document.body.classList.contains('snapshot')) {
      url = apiUrl('/bigraph-loom/index.html') + '?static=1&stateUrl=' + encodeURIComponent(_compositeStateUrl(id));
    } else {
      url = apiUrl('/bigraph-loom/index.html') + '?id=' + encodeURIComponent(id);
    }
    var w = window.open(url, '_blank',
      'width=1280,height=860,menubar=no,toolbar=no,location=no,resizable=yes,scrollbars=yes');
    if (!w) alert('Popup blocked. Allow popups from this site to pop out the composite.');
  }
  window._popoutCompositeLoom = _popoutCompositeLoom;



  function _openCompositeLoomInline(det) {
    if (!det || det._loomLoaded) return;
    // <details> embeds only mount when open; a plain container (the ProcessCard
    // Explore section) has no `.open` and mounts as soon as it's asked to.
    if (det.tagName === 'DETAILS' && !det.open) return;
    det._loomLoaded = true;
    var id = det.getAttribute('data-id');
    var host = det.querySelector('.ccard-loom-frame');
    if (!host) return;
    host.innerHTML = '<p class="muted" style="padding:10px;font-size:0.85em">Resolving composite (this can take a moment)…</p>';
    var apiUrl = (window.DataSource && window.DataSource.apiUrl) ? window.DataSource.apiUrl.bind(window.DataSource) : function (p) { return p; };
    // Live mode (user hit "Enable running") loads the same URL as the pop-out
    // (?id=<ref>) so config is editable and Run works; otherwise a read-only
    // static render pointed at live composite-resolve.
    // chrome=off → embedded (no breadcrumb/tab strip). An optional data-view
    // (e.g. "visualizations"/"results"/"document") selects which loom tab the
    // embed shows — used by the card's Outputs section.
    var tabParam = det.getAttribute('data-view') ? '&tab=' + encodeURIComponent(det.getAttribute('data-view')) : '';
    // On a live dashboard the view-only loom still carries the composite id +
    // live=1 so drilling into an inner Composite (a Composite Process like
    // EcoliWCM) resolves via the live /api/composite-inner-state endpoint —
    // static=1 alone (a published snapshot) would look for a pre-built file that
    // only a snapshot ships. Omit both under body.snapshot (truly no server).
    var liveInner = document.body.classList.contains('snapshot')
      ? '' : '&id=' + encodeURIComponent(id) + '&live=1';
    // `data-surface="full"` → the WHOLE stacked loom surface (Configure/Inputs +
    // bigraph + Run/Step + Outputs), header hidden (the card names the composite).
    // It runs LIVE (id-based) so Run + Apply work; the card no longer wraps its
    // own Configure/Run/Outputs. Everything else keeps the chrome=off bigraph-only
    // preview.
    var fullSurface = det.getAttribute('data-surface') === 'full';
    var isSnapshot = document.body.classList.contains('snapshot');
    var chromeParam = fullSurface ? '&header=off' : '&chrome=off';
    var loomUrl = (det._loomLive || (fullSurface && !isSnapshot))
      ? apiUrl('/bigraph-loom/index.html') + '?id=' + encodeURIComponent(id) +
          (det._overrides ? '&overrides=' + encodeURIComponent(det._overrides) : '') + chromeParam + tabParam
      : apiUrl('/bigraph-loom/index.html') + '?static=1&stateUrl=' +
          encodeURIComponent(_compositeStateUrl(id, det._overrides)) + liveInner + chromeParam + tabParam;
    var f = document.createElement('iframe');
    f.className = 'ccard-loom-iframe' + (fullSurface ? ' ccard-loom-iframe-full' : '');
    f.setAttribute('title', 'Loom — ' + id);
    f.src = loomUrl;
    host.innerHTML = '';
    // Restore a previously dragged height (shared across all loom embeds); the
    // full surface needs more room by default (four stacked zones).
    var savedH = 0;
    try { savedH = parseInt(localStorage.getItem('viv.loomFrameH') || '', 10) || 0; } catch (e) { /* private mode */ }
    if (!savedH && fullSurface) savedH = Math.round(window.innerHeight * 0.72);
    if (savedH) host.style.height = Math.max(fullSurface ? 480 : 220, Math.min(Math.round(window.innerHeight * 0.92), savedH)) + 'px';
    host.appendChild(f);
    _wireLoomResize(host, f);
  }
  window._openCompositeLoomInline = _openCompositeLoomInline;

  // Drag-to-resize the embedded loom panel. A full-width grip below the iframe
  // grows/shrinks the frame; the card grows with it. Height persists across
  // embeds via localStorage. Pointer events are disabled on the iframe mid-drag
  // so the gesture keeps tracking when the cursor moves over the loom.
  function _wireLoomResize(frame, iframe) {
    var grip = document.createElement('div');
    grip.className = 'ccard-loom-resize';
    grip.title = 'Drag to resize';
    frame.appendChild(grip);
    var startY = 0, startH = 0;
    function pointY(e) { return e.touches && e.touches[0] ? e.touches[0].clientY : e.clientY; }
    function onMove(e) {
      var maxH = Math.round(window.innerHeight * 0.92);
      var h = Math.max(220, Math.min(maxH, startH + (pointY(e) - startY)));
      frame.style.height = h + 'px';
      if (e.cancelable) e.preventDefault();
      try { localStorage.setItem('viv.loomFrameH', String(Math.round(h))); } catch (err) { /* private mode */ }
    }
    function onUp() {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      document.removeEventListener('touchmove', onMove);
      document.removeEventListener('touchend', onUp);
      if (iframe) iframe.style.pointerEvents = '';
      frame.classList.remove('is-resizing');
    }
    function onDown(e) {
      startY = pointY(e);
      startH = frame.getBoundingClientRect().height;
      if (iframe) iframe.style.pointerEvents = 'none';
      frame.classList.add('is-resizing');
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
      document.addEventListener('touchmove', onMove, { passive: false });
      document.addEventListener('touchend', onUp);
      if (e.cancelable) e.preventDefault();
    }
    grip.addEventListener('mousedown', onDown);
    grip.addEventListener('touchstart', onDown, { passive: false });
  }
  window._wireLoomResize = _wireLoomResize;


  // Lazily fetch a composite's process/store counts when its "structure"
  // <details> is first opened (building a composite can be ParCa-heavy, so this
  // is never done on the list load — only on demand, once, per card).
  window._loadCompositeStructure = function(det, id) {
    if (!det || !det.open) return;
    var body = det.querySelector('.ccard-struct-body');
    if (!body || body.getAttribute('data-loaded') === '1') return;
    body.setAttribute('data-loaded', '1');
    body.textContent = 'building…';
    fetch('/api/composite-state?ref=' + encodeURIComponent(id))
      .then(function(r) { return r.json(); })
      .then(function(d) {
        var root = (d && d.state) ? (d.state.state || d.state) : null;
        if (!root) { body.textContent = 'unavailable'; return; }
        var np = 0, ns = 0;
        (function walk(n) {
          if (!n || typeof n !== 'object') return;
          if (Array.isArray(n)) { n.forEach(walk); return; }
          var t = n._type;
          if (t === 'process' || t === 'step') { np++; return; }
          if (t) { ns++; return; }
          Object.keys(n).forEach(function(k) {
            if (k !== '_declared_emit_paths') walk(n[k]);
          });
        })(root);
        body.innerHTML = '<strong>' + np + '</strong> processes · <strong>' + ns + '</strong> stores';
      })
      .catch(function() { body.textContent = 'unavailable'; });
  };

  function _loadComposites() {
    var _p = window.DataSource
      ? window.DataSource.loadComposites()
      : fetch('/api/composites').then(function(r) { return r.json(); });
    _p
      .then(function(data) {
        var composites = data.composites || [];
        // Cache by id so onclick handlers pass just the id; _useComposite
        // looks the full object up. Inline JSON.stringify in onclick attrs
        // breaks when descriptions contain apostrophes / quotes.
        window._compositesById = {};
        composites.forEach(function(c) { window._compositesById[c.id] = c; });
        window._composites = composites;

        // (a) Registry/Processes-page "Composites" tab — accordion cards.
        _renderRegistryComposites(composites);

      });
  }
  window._loadComposites = _loadComposites;

  // Render composites as unified accordion ProcessCards into the Processes-page
  // "Composites" tab (respecting the shared registry filter). One wide card/row.
  function _renderRegistryComposites(composites) {
    if (document.body.classList.contains('popcard-mode')) return;
    var el = document.getElementById('registry-composites-container');
    if (!el) return;
    composites = composites || window._composites || [];
    var count = document.getElementById('registry-composite-count');
    if (count) count.textContent = composites.length;
    var q = window._registryFilter;
    var list = composites;
    if (q) {
      list = composites.filter(function (c) {
        return ((c.name || '') + ' ' + (c.description || '') + ' ' + (c.module || '') + ' ' + (c.tags || []).join(' ')).toLowerCase().indexOf(q) !== -1;
      });
    }
    if (!list.length) {
      el.innerHTML = q
        ? '<p class="empty-state muted" style="font-size:0.9em">No composites match “' + _esc(q) + '”.</p>'
        : '<p class="empty-state">No composites registered.</p>';
      return;
    }
    // Honour the Sort control (default keeps workspace-local first, then name).
    var _sortKey = window._registrySort || 'use';
    list = list.slice().sort(function (a, b) { return _compositeSortCmp(a, b, _sortKey); });
    // Group by figure (stable within each figure) so a figure's draft /
    // executable / live-topology cards cluster together; non-figure composites
    // keep to the end.
    var _figNum = function (c) { var m = ((c && c.id) || '').match(/\.fig0*(\d+)/i); return m ? parseInt(m[1], 10) : 9999; };
    list = list
      .map(function (c, i) { return { c: c, i: i, n: _figNum(c) }; })
      .sort(function (a, b) { return (a.n - b.n) || (a.i - b.i); })
      .map(function (x) { return x.c; });
    // Semantic zoom: Table (dense) → Cards (grid + usage) → Full (accordion).
    var zoom = window._registryZoom || 'grid';
    if (zoom === 'table') { el.innerHTML = _renderCompositeTableHtml(list); return; }
    var cardsCls = 'reg-cards reg-cards-' + (zoom === 'full' ? 'full' : 'grid');
    var render = (zoom === 'full') ? _renderCompositeCardFull : _renderCompositeCardGrid;
    var _prevF = null;
    var _cardsHtml = list.map(function (c) {
      var n = _figNum(c), head = '';
      if (n !== 9999 && (!_prevF || _figNum(_prevF) !== n)) {
        head = '<div class="composite-figure-group"><span>Fig ' + n + '</span></div>';
      }
      _prevF = c;
      return head + render(c);
    }).join('');
    el.innerHTML = '<div class="' + cardsCls + '">' + _cardsHtml + '</div>';
    if (zoom === 'grid') el.querySelectorAll('.reg-cards-grid').forEach(function (cc) { _applyCardCols(cc, 'registry'); });
  }
  window._renderRegistryComposites = _renderRegistryComposites;

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
  window._catalogSort = 'default';

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

  // Modules has TWO zoom levels: Table (dense sortable) → Cards (multi-column
  // grid). Persists; double-click a module zooms in one level. A stored 'full'
  // (the removed high-detail level) resolves to Cards.
  window._catalogZoom = (function () {
    var z; try { z = localStorage.getItem('viv.catalogZoom'); } catch (e) { z = null; }
    return (z === 'table' || z === 'cards') ? z : 'cards';
  })();
  function _setCatalogZoom(z) {
    window._catalogZoom = z;
    try { localStorage.setItem('viv.catalogZoom', z); } catch (e) { /* private mode */ }
    document.querySelectorAll('#catalog-toolbar .reg-zoom-btn').forEach(function (b) {
      b.classList.toggle('active', b.getAttribute('data-mzoom') === z);
    });
    _renderCatalog();
    _updateColsSlotVisibility();   // slider only in Cards zoom (table hidden)
  }
  window._setCatalogZoom = _setCatalogZoom;
  function _zoomInModule(name) {
    var order = ['table', 'cards'];
    var i = order.indexOf(window._catalogZoom || 'cards');
    window._catalogSelected = name;
    _setCatalogZoom(order[Math.min(order.length - 1, i + 1)]);
    setTimeout(function () {
      var sel = document.querySelector('#catalog-modules-grid [data-mid="' + (window.CSS && CSS.escape ? CSS.escape(name) : name) + '"]');
      if (sel) { sel.classList.add('reg-selected'); try { sel.scrollIntoView({ behavior: 'smooth', block: 'center' }); } catch (e) { /* ignore */ } }
    }, 60);
  }
  window._zoomInModule = _zoomInModule;

  // -------------------------------------------------------------------------
  // Shared module-card rendering (Modules tab + Marketplace tab)
  // -------------------------------------------------------------------------

  // Search / installed / tag filter. `f` is a {search, installed, tags} state
  // object (window._catalogFilter or window._marketplaceFilter).
  function _filterModules(list, f) {
    var search = (f.search || '').toLowerCase();
    var activeTags = f.tags || new Set();
    return (list || []).filter(function(m) {
      // Workspace package is exempt from text-search / tag hiding so it always
      // stays pinned as the anchor card at the top of the grid.
      if (search && m.kind !== 'workspace') {
        var haystack = (m.name + ' ' + (m.display_name || '') + ' ' + (m.description || '') + ' ' + (m.tags || []).join(' ')).toLowerCase();
        if (haystack.indexOf(search) === -1) return false;
      }
      if (f.installed === 'installed' && !m.installed && m.kind !== 'workspace') return false;
      if (f.installed === 'uninstalled' && (m.installed || m.kind === 'workspace')) return false;
      if (activeTags.size > 0 && m.kind !== 'workspace') {
        var mTags = m.tags || [];
        var match = false;
        activeTags.forEach(function(t) { if (mTags.indexOf(t) !== -1) match = true; });
        if (!match) return false;
      }
      return true;
    });
  }

  function _moduleInstalledMeta(m) {
    // Source / ref / path / last-updated rows surfaced inline on installed cards.
    if (!m.installed && m.kind !== 'workspace') return '';
    var bits = [];
    if (m.source) bits.push('<small class="muted">Source: <code>' + _esc(m.source) + '</code>' +
      (m.ref ? ' @ <code>' + _esc(m.ref) + '</code>' : '') + '</small>');
    var path = m.install_path || m.path;
    if (path) bits.push('<small class="muted">Path: <code>' + _esc(path) + '</code></small>');
    if (m.last_updated) bits.push('<small class="muted">Updated: ' + _esc(String(m.last_updated).slice(0, 10)) + '</small>');
    return bits.length ? '<div class="module-installed-meta">' + bits.join('<br>') + '</div>' : '';
  }

  // Compact content-count + workspace-usage row (module cards): "N composites
  // · N studies · N investigations · ★N used". A zero-count metric is omitted
  // except "used", which stays visible whenever it's >0 (the whole point of
  // the metric is to draw the eye). Renders '' when the module carries no
  // stats at all (wheel-only / available-to-install modules).
  function _moduleStatsRow(m) {
    // "Used here" — how many of THIS workspace's studies use the module. The
    // headline signal, so it leads: a filled green bar when used, nothing when
    // not (rather than a noisy "0"). Replaces the old ★ chip.
    var usageHtml = '';
    if (m.n_used) {
      usageHtml = '<div class="module-usage" title="Used by ' + m.n_used +
        ' of this workspace’s studies">' +
        '<span class="module-usage-dot"></span>Used by <strong>' + m.n_used +
        '</strong> stud' + (m.n_used === 1 ? 'y' : 'ies') + '</div>';
    }
    // What the module PROVIDES — a quiet, comma-free count strip.
    function count(n, singular, plural) {
      return '<span class="module-count"><strong>' + n + '</strong> ' +
        (n === 1 ? singular : plural) + '</span>';
    }
    var counts = [];
    if (m.n_composites) counts.push(count(m.n_composites, 'composite', 'composites'));
    if (m.n_studies) counts.push(count(m.n_studies, 'study', 'studies'));
    if (m.n_investigations) counts.push(count(m.n_investigations, 'investigation', 'investigations'));
    var countsHtml = counts.length ? '<div class="module-counts">' + counts.join('') + '</div>' : '';
    if (!usageHtml && !countsHtml) return '';
    return '<div class="module-stats-row">' + usageHtml + countsHtml + '</div>';
  }

  function _moduleActionFor(m, marketplace) {
    // Workspace's own first-party package is not uninstallable — show a
    // "first-party" pill. Installed modules show an install-source badge.
    // Available modules show an Install button (authoring-only).
    if (m.kind === 'workspace') {
      return '<span class="status-pill installed" title="The workspace\'s own first-party package. Always present; cannot be uninstalled.">first-party</span>';
    }
    if (m.installed) {
      var src = m.install_source || 'imports';
      var srcBadge = '';
      if (src === 'venv') {
        var via = (m.installed_via || []);
        if (via.length === 0) {
          srcBadge = '<span class="install-src-pill install-src-unmanaged" title="Installed in the venv but not declared in workspace.yaml.imports and not required by any installed package.">📦 unmanaged</span>';
        } else {
          var viaText = 'via ' + via.slice(0, 3).map(_esc).join(', ') + (via.length > 3 ? ' +' + (via.length - 3) : '');
          srcBadge = '<span class="install-src-pill install-src-venv" title="Brought in by another installed package.">📦 ' + viaText + '</span>';
        }
      } else if (src === 'pyproject') {
        srcBadge = '<span class="install-src-pill install-src-pyproject" title="Declared in pyproject.toml [project.dependencies].">📋 via pyproject</span>';
      } else {
        srcBadge = '<span class="status-pill installed">installed</span>';
      }
      // Transitive venv deps (brought in by another installed package) are NOT
      // directly uninstallable — removing one would break its parent. Anything
      // the user explicitly added (imports / pyproject / unmanaged venv) gets
      // an Uninstall action, gated behind the impact-confirmation modal.
      var via = (m.installed_via || []);
      var uninstallBtn = '';
      if (!(src === 'venv' && via.length > 0)) {
        uninstallBtn = '<button class="btn-mini module-uninstall-btn js-authoring" ' +
          'onclick="_uninstallFromCatalog(\'' + _esc(m.name) + '\')" ' +
          'title="Uninstall this module from the workspace">Uninstall</button>';
      }
      return '<span class="module-action-installed">' + srcBadge + uninstallBtn + '</span>';
    }
    // Merged Modules tab: installing an available module uses the full-repo
    // (git submodule) path so its composites/studies/investigations land on
    // disk and federate — same behaviour the Marketplace tab used to force.
    return '<button class="action-btn js-authoring" onclick="_installFromMarketplace(\'' + _esc(m.name) + '\')">Install</button>';
  }

  // Section divider injected at boundaries: workspace → installed → available.
  function _moduleSectionDivider(prev, cur) {
    if (!prev || !cur) return '';
    var prevSection = prev.kind === 'workspace' ? 0 : (prev.installed ? 1 : 2);
    var curSection  = cur.kind  === 'workspace' ? 0 : (cur.installed  ? 1 : 2);
    if (prevSection === curSection) return '';
    var label = (curSection === 1) ? 'Installed in this workspace' : 'Available to install';
    return '<div class="module-section-divider"><span>' + label + '</span></div>';
  }

  // Render an already-filtered module list into `grid` (grid or list view).
  // Sort: a chosen primary key (window._catalogSort / window._marketplaceSort
  // — 'default' means "no primary key", i.e. skip straight to the tiebreak)
  // then the original tiebreak: workspace package first (anchor), then
  // installed (alpha), then available (alpha) — "what's in your workspace
  // surfaces first".
  function _renderModuleGrid(grid, modules, view, marketplace) {
    var sortKey = (marketplace ? window._marketplaceSort : window._catalogSort) || 'default';
    modules = modules.slice().sort(function(a, b) {
      var primary = 0;
      if (sortKey === 'used') primary = (b.n_used || 0) - (a.n_used || 0);
      else if (sortKey === 'composites') primary = (b.n_composites || 0) - (a.n_composites || 0);
      else if (sortKey === 'studies') primary = (b.n_studies || 0) - (a.n_studies || 0);
      else if (sortKey === 'investigations') primary = (b.n_investigations || 0) - (a.n_investigations || 0);
      else if (sortKey === 'updated') {
        var at = a.last_updated ? new Date(a.last_updated).getTime() : -Infinity;
        var bt = b.last_updated ? new Date(b.last_updated).getTime() : -Infinity;
        primary = bt - at;
      }
      if (primary !== 0) return primary;
      var aw = a.kind === 'workspace' ? 0 : 1;
      var bw = b.kind === 'workspace' ? 0 : 1;
      if (aw !== bw) return aw - bw;
      var ai = a.installed ? 0 : 1;
      var bi = b.installed ? 0 : 1;
      if (ai !== bi) return ai - bi;
      return (a.display_name || a.name || '').localeCompare(b.display_name || b.name || '');
    });

    if (!modules.length) {
      grid.innerHTML = '<p class="empty-state">No modules match the current filter.</p>';
      grid.className = '';
      return;
    }

    // Semantic zoom: Table (dense sortable) → Cards (full-row) → Full (high
    // detail). Keep the workspace→installed→available section dividers.
    var zoom = window._catalogZoom || 'cards';
    document.querySelectorAll('#catalog-toolbar .reg-zoom-btn').forEach(function (b) {
      b.classList.toggle('active', b.getAttribute('data-mzoom') === zoom);
    });
    if (zoom === 'table') { _renderModulesTable(grid, modules, marketplace); return; }
    grid.className = 'mrows' + (zoom === 'full' ? ' mrows-full' : '');
    var prevG = null;
    var cards = modules.map(function (m) {
      var divider = _moduleSectionDivider(prevG, m);
      prevG = m;
      return divider + _moduleFullRowCard(m, marketplace, zoom);
    });
    grid.innerHTML = cards.join('');
    // Cards zoom: multi-column grid + column control (Full stays single-column).
    _syncColsControls();
    if (zoom === 'cards') _applyCardCols(grid, 'modules');
    else { grid.classList.remove('cards-grid-cols'); grid.style.gridTemplateColumns = ''; }
  }

  // Full-row module card — identity | description | stats | install-action, with
  // source/ref/path detail shown at the Full zoom. Double-click zooms in.
  function _moduleFullRowCard(m, marketplace, zoom) {
    var name = _esc(m.display_name || m.name);
    var _hp = m.homepage || (/^https?:\/\//.test(m.source || '') ? m.source : '');
    var homepage = _hp ? ' <a href="' + _esc(_hp) + '" target="_blank" class="module-link">GitHub &#8599;</a>' : '';
    var wsCls = (m.kind === 'workspace') ? ' module-card-workspace' : (m.installed ? ' module-card-installed' : '');
    var sel = (window._catalogSelected && window._catalogSelected === m.name) ? ' reg-selected' : '';
    var meta = _moduleInstalledMeta(m);
    return '<div class="module-card mrow' + sel + wsCls + '" data-mid="' + _esc(m.name) + '"' +
        ' ondblclick="_zoomInModule(\'' + _esc(m.name) + '\')" title="Double-click to zoom in on this module">' +
      '<div class="mrow-grid">' +
        '<div class="mrow-identity"><div class="mrow-name"><strong class="mrow-name-text" title="' + _esc(m.display_name || m.name) + '">' + name + '</strong>' + homepage + '</div></div>' +
        '<div class="mrow-desc">' + _esc(m.description || '') + '</div>' +
        '<div class="mrow-stats">' + _moduleStatsRow(m) + '</div>' +
        '<div class="mrow-action">' + _moduleActionFor(m, marketplace) + '</div>' +
      '</div>' +
      (zoom === 'full' && meta ? '<div class="mrow-meta">' + meta + '</div>' : '') +
    '</div>';
  }

  // Modules Table — installed-here and marketplace grouped into separate
  // sections; sortable (Name / Source / Composites / Repos / Studies / Used);
  // GitHub link on the name; Install/Uninstall in a single Action column.
  function _renderModulesTable(grid, modules, marketplace) {
    var sk = window._catalogTableSort || 'used';
    var sd = window._catalogTableDir || ((sk === 'name' || sk === 'source') ? 'asc' : 'desc');
    var pkg = function (m) { return (m.package || m.name || '').split('.')[0].replace(/_/g, '-'); };
    function cmp(a, b) {
      var av, bv;
      if (sk === 'source') { av = pkg(a).toLowerCase(); bv = pkg(b).toLowerCase(); }
      else if (sk === 'composites') { av = a.n_composites || 0; bv = b.n_composites || 0; }
      else if (sk === 'repos') { av = a.n_repos || 0; bv = b.n_repos || 0; }
      else if (sk === 'studies') { av = a.n_studies || 0; bv = b.n_studies || 0; }
      else if (sk === 'used') { av = a.n_used || 0; bv = b.n_used || 0; }
      else { av = (a.display_name || a.name || '').toLowerCase(); bv = (b.display_name || b.name || '').toLowerCase(); }
      var c = av < bv ? -1 : (av > bv ? 1 : (a.display_name || a.name || '').localeCompare(b.display_name || b.name || ''));
      return sd === 'desc' ? -c : c;
    }
    var isInstalled = function (m) { return m.kind === 'workspace' || m.installed; };
    var installed = modules.filter(isInstalled).sort(cmp);
    var available = modules.filter(function (m) { return !isInstalled(m); }).sort(cmp);
    function gh(m) {
      var hp = m.homepage || (/^https?:\/\//.test(m.source || '') ? m.source : '');
      return hp ? ' <a href="' + _esc(hp) + '" target="_blank" class="module-link" onclick="event.stopPropagation()">GitHub &#8599;</a>' : '';
    }
    function th(key, label, cls) {
      var on = sk === key;
      return '<th class="reg-th' + (cls ? ' ' + cls : '') + (on ? ' active' : '') +
        '" onclick="_setCatalogTableSort(\'' + key + '\')">' + label + (on ? (sd === 'desc' ? ' ▾' : ' ▴') : '') + '</th>';
    }
    function row(m) {
      return '<tr class="reg-tr" data-mid="' + _esc(m.name) + '" ondblclick="_zoomInModule(\'' + _esc(m.name) + '\')" title="Double-click to zoom in">' +
        '<td class="reg-td-name"><strong>' + _esc(m.display_name || m.name) + '</strong>' + gh(m) + '</td>' +
        '<td>' + _esc(m.kind === 'workspace' ? 'workspace' : pkg(m)) + '</td>' +
        '<td class="num">' + (m.n_composites || 0) + '</td>' +
        '<td class="num" title="Repos in the federated ecosystem that import this module">' + (m.n_repos || 0) + '</td>' +
        '<td class="num">' + (m.n_studies || 0) + '</td>' +
        '<td class="num">' + (m.n_used || 0) + '</td>' +
        '<td class="reg-td-action">' + _moduleActionFor(m, marketplace) + '</td>' +
      '</tr>';
    }
    var NCOLS = 7;
    function section(label, list) {
      if (!list.length) return '';
      return '<tr class="module-table-section"><td colspan="' + NCOLS + '"><span>' + label +
        ' <span class="muted">(' + list.length + ')</span></span></td></tr>' + list.map(row).join('');
    }
    grid.className = '';
    grid.innerHTML = '<div class="registry-table-wrap"><table class="registry-table modules-table"><thead><tr>' +
      th('name', 'Name') + th('source', 'Source') + th('composites', 'Composites', 'num') +
      th('repos', 'Repos', 'num') + th('studies', 'Studies', 'num') + th('used', 'Used', 'num') +
      '<th class="reg-th">Action</th>' +
      '</tr></thead><tbody>' +
      section('Installed in this workspace', installed) +
      section('Available in marketplace', available) +
      '</tbody></table></div>';
  }
  function _setCatalogTableSort(key) {
    if (window._catalogTableSort === key) window._catalogTableDir = (window._catalogTableDir === 'desc') ? 'asc' : 'desc';
    else { window._catalogTableSort = key; window._catalogTableDir = (key === 'name' || key === 'source') ? 'asc' : 'desc'; }
    _renderCatalog();
  }
  window._setCatalogTableSort = _setCatalogTableSort;

  function _renderCatalog() {
    var grid = document.getElementById('catalog-modules-grid');
    if (!grid) return;
    // Full ecosystem: workspace package + installed modules first, then
    // available-to-install modules under the "Available to install" divider.
    // The install-state radio (All / Installed / Available) narrows this.
    var modules = _filterModules(window._catalogModules, window._catalogFilter);
    _renderModuleGrid(grid, modules, window._catalogView, false);
  }
  window._renderCatalog = _renderCatalog;

  // (The standalone Marketplace sub-tab was merged into the Modules grid above,
  // which now loads the full ecosystem via /api/marketplace in _loadCatalog and
  // renders available-to-install modules under the "Available to install"
  // divider. _setMarketplaceView / _renderMarketplace / _loadMarketplace and
  // their window._marketplace* state were removed with it.)

  // Registry page sub-tab toggle. Two sub-tabs: "modules" (the catalog
  // grid above, where the workspace package + installed modules now
  // pin at top) and "discovered" (the live build_core() introspection
  // — Processes / Steps / Emitters / Visualizations / Types). The old
  // layout stacked these as three scrolling panels; sub-tabs let users
  // flip without scrolling.
  function _setRegistrySubtab(name) {
    name = name || 'modules';   // Modules is the main tab; Registry is secondary
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
        '<td>' + (sysDepsBtn.trim() || '<span style="color:#9ca3af;font-size:0.85em">—</span>') + '</td>' +
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
        if (typeof _loadMarket === 'function') _loadMarket(true);
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
    // The Modules tab now shows the FULL viva ecosystem (workspace package +
    // installed modules pinned at top, available-to-install modules below) —
    // the former standalone Marketplace tab is merged in here. Live mode loads
    // /api/marketplace (build_catalog full=True); snapshot bundles only publish
    // the workspace-scoped /api/catalog.json, so fall back to that there (the
    // Install/Uninstall actions are hidden in read-only mode anyway).
    var _snapshot = window.DataSource && window.DataSource.config
      && window.DataSource.config().mode === 'snapshot';
    var _marketUrl = window.DataSource && window.DataSource.apiUrl
      ? window.DataSource.apiUrl('/api/marketplace') : '/api/marketplace';
    var _p = _snapshot
      ? window.DataSource.loadCatalog()
      : fetch(_marketUrl).then(function(r) { return r.json(); });
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
        var sortEl = document.getElementById('catalog-sort');
        if (sortEl && !sortEl._pbgWired) {
          sortEl._pbgWired = true;
          sortEl.value = window._catalogSort || 'default';
          sortEl.onchange = function() {
            window._catalogSort = this.value;
            _renderCatalog();
          };
        }
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

  // ── Market: faceted browser over the ecosystem's artifacts ────────────────
  // Search processes, composites, studies and investigations from the
  // workspace + every installed module (which span the public repos), with
  // three zoom levels (List → Cards → Detail) surfacing how much each is used
  // and what for. Uninstalled repos' individual artifacts are out of scope
  // (only counts are available without an ecosystem index).
  window._marketItems = null;
  window._marketFacet = 'repo';   // land on the Repositories overview
  window._marketZoom = 'cards';
  // The Registry defaults to THIS repository's own artifacts ('workspace',
  // shown as "Repository"); 'external' ("Other repos") is opt-in. The legacy
  // 'all' scope was retired, so only the two valid values are honored from
  // storage (anything else — incl. a stored 'all' — falls back to the default).
  window._marketOrigin = 'all';   // All | workspace | imported | available
  try { var _mz = localStorage.getItem('viv.market-zoom'); if (_mz) window._marketZoom = _mz; } catch (e) {}
  try { var _mo = localStorage.getItem('viv.market-origin'); if (['all', 'workspace', 'imported', 'available'].indexOf(_mo) !== -1) window._marketOrigin = _mo; } catch (e) {}

  var _MARKET_TYPES = [
    { key: 'process',       label: 'Processes & Steps', ico: '⚙' },
    { key: 'composite',     label: 'Composites',     ico: '▩' },
    { key: 'study',         label: 'Studies',        ico: '▤' },
    { key: 'investigation', label: 'Investigations', ico: '❖' }
  ];
  function _marketIco(t) {
    for (var i = 0; i < _MARKET_TYPES.length; i++) if (_MARKET_TYPES[i].key === t) return _MARKET_TYPES[i].ico;
    return '•';
  }
  // Three-way provenance category, uniform across facets: an artifact is
  // "workspace" (the workspace's own package), "imported" (an installed
  // dependency repo), or "available" (an ecosystem repo not installed here).
  var _MARKET_CAT_ORDER = { workspace: 0, imported: 1, available: 2 };
  function _marketCatOf(it) {
    return it.category || (it.origin === 'workspace' ? 'workspace' : 'imported');
  }
  function _marketCatChip(it) {
    var c = _marketCatOf(it);
    return '<span class="market-cat market-cat-' + c + '" title="' + c + '">' + c + '</span>';
  }
  // 'viva_munk.processes.x.Y' / 'pbg_copasi.composites' → display repo 'viva-munk'
  function _marketRepoOf(s) {
    if (!s) return '';
    return String(s).split('.')[0].replace(/_/g, '-');
  }

  // Load incrementally: the composites/studies/investigations endpoints answer
  // in 1-4s, but the build_core() registry (processes) can take ~45s cold, so
  // each source renders as it arrives instead of blocking on the slowest.
  function _loadMarket(force) {
    // Load once: re-navigations just re-render the cache; install/uninstall
    // pass force=true. A guard stops redundant concurrent loads (which reset the
    // accumulator mid-flight and made the first paint flaky).
    if (!force && window._marketItems && window._marketItems.length) { _renderMarket(); return; }
    if (window._marketLoading) return;
    window._marketLoading = true;
    var host = document.getElementById('market-results');
    if (host) host.innerHTML = '<p class="empty-state">Loading&hellip;</p>';
    window._marketByType = { composite: [], study: [], investigation: [], process: [] };
    window._marketItems = [];
    var rebuild = function () {
      var t = window._marketByType;
      window._marketItems = [].concat(t.process, t.composite, t.study, t.investigation);
      _renderMarket();
    };
    // Route through DataSource so the SNAPSHOT (read-only) bundle reads the
    // static api/*.json files (with the bundle's base path) instead of hitting
    // live /api/* endpoints that don't exist offline — that mismatch left the
    // published Registry stuck on "Loading…". Each call degrades to null on
    // error so one missing payload can't blank the whole page. Live mode is
    // unchanged: the same endpoints, just via the shared loader.
    var DS = window.DataSource;
    var J = function (which) {
      try {
        if (DS) {
          if (which === 'isets')   return DS.loadIsetList().catch(function () { return null; });
          if (which === 'studies') return DS.loadInvestigationsFlat().catch(function () { return null; });
          if (which === 'comps')   return DS.loadComposites().catch(function () { return null; });
          if (which === 'reg')     return DS.loadRegistry().catch(function () { return null; });
          if (which === 'catalog') return DS.loadCatalog().catch(function () { return null; });
        }
      } catch (e) { /* fall through to raw fetch */ }
      // The marketplace payload (full ecosystem incl. available-to-install) has
      // no DataSource loader; resolve it snapshot-aware here (static .json in a
      // published bundle, live endpoint otherwise).
      if (which === 'marketplace' || which === 'ecoindex') {
        var snap = DS && DS.config && DS.config().mode === 'snapshot';
        var leaf = which === 'ecoindex' ? 'ecosystem-index' : 'marketplace';
        var mu = snap ? (DS.basePath() + '/api/' + leaf + '.json')
                      : ((DS && DS.apiUrl) ? DS.apiUrl('/api/' + leaf) : '/api/' + leaf);
        return fetch(mu).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
      }
      var path = { isets: '/api/investigation-summaries', studies: '/api/investigations',
                   comps: '/api/composites', reg: '/api/registry', catalog: '/api/catalog' }[which] || which;
      var u = (DS && DS.apiUrl) ? DS.apiUrl(path) : path;
      return fetch(u).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
    };

    // Provenance is per REPO: an artifact is "workspace" only if it belongs to
    // the workspace's OWN package; everything else (imported dependency repos
    // like viva-munk / pbg-*) is "external" so it surfaces under "Other repos".
    var wsNorm = _marketRepoNorm(window._workspaceName || '');
    var wsLabel = window._workspaceName || 'workspace';
    // Three-way provenance. Local artifacts (from the installed registry) are
    // "workspace" (the workspace's own package) or "imported" (an installed
    // dependency repo); "available" is added later from the ecosystem index for
    // repos NOT installed here.
    var _categoryOf = function (repo, workspaceLocal) {
      if (workspaceLocal || (_marketRepoNorm(repo) === wsNorm && wsNorm)) return 'workspace';
      return 'imported';
    };
    var _originOf = function (repo, workspaceLocal) {
      return _categoryOf(repo, workspaceLocal) === 'workspace' ? 'workspace' : 'external';
    };

    // The single-worker server hangs on CONCURRENT env_worker-backed requests
    // (composites + registry), so fetch strictly SEQUENTIALLY — light/file
    // endpoints first for a fast first paint, the heavy build_core() registry
    // (~45s cold) last. Each step renders as it lands.
    J('isets').then(function (isets) {
      window._marketByType.investigation = ((isets || {}).investigations || []).map(function (iv) {
        var repo = iv.origin_repo ? _marketRepoOf(iv.origin_repo) : wsLabel;
        return { type: 'investigation', name: iv.name, title: iv.title || iv.name,
          repo: repo, origin: iv.origin_repo ? 'external' : 'workspace',
          category: _categoryOf(repo, !iv.origin_repo),
          desc: iv.description || '', status: iv.effective_status || iv.status || '',
          question: iv.question || '', hypothesis: iv.hypothesis || '',
          studies: iv.studies || [], nStudies: iv.n_studies || (iv.studies || []).length || 0 };
      });
      rebuild();
      return J('studies');
    }).then(function (studies) {
      window._marketByType.study = ((studies || {}).investigations || []).map(function (s) {
        var repo = s.origin_repo ? _marketRepoOf(s.origin_repo) : wsLabel;
        return { type: 'study', name: s.name, title: s.title || s.name, repo: repo,
          origin: s.origin_repo ? 'external' : 'workspace',
          category: _categoryOf(repo, !s.origin_repo), desc: s.description || '',
          status: s.effective_status || s.status || '', composite: s.composite || '',
          question: s.question || s.objective || '', invs: s.investigations || [],
          nBeh: s.n_behaviors || 0, nRuns: s.n_runs || 0, nSims: s.n_simulations || 0, use: s.n_runs || 0 };
      });
      rebuild();
      return J('comps');
    }).then(function (comps) {
      var carr = Array.isArray(comps) ? comps : (comps && comps.composites) || [];
      window._marketByType.composite = carr.map(function (c) {
        var wl = c.workspace_local || c.source === 'workspace';
        var repo = wl ? wsLabel : _marketRepoOf(c.origin_repo || c.module);
        var params = c.parameters;
        var nParams = Array.isArray(params) ? params.length : (params ? Object.keys(params).length : 0);
        return { type: 'composite', name: c.name, repo: repo,
          origin: _originOf(repo, wl), category: _categoryOf(repo, wl),
          desc: c.description || '', requires: ((c.requires || {}).processes) || [],
          affectedStudies: (c.studies && c.studies.studies) || 0,
          nParams: nParams, nSteps: c.default_n_steps || 0, use: 0 };
      });
      rebuild();
      return J('reg');
    }).then(function (reg) {
      var wpkgs = (reg && reg.workspace_pkgs) || [];
      window._marketByType.process = ((reg || {}).processes || []).filter(function (p) {
        return p.kind === 'process' || p.kind === 'step';   // emitters live on the Modules page
      }).map(function (p) {
        var wl = p.source === 'in_workspace';
        var repo = wl ? wsLabel : _marketRepoOf(p.address || p.source);
        return { type: 'process', kind: p.kind || 'process', name: p.name, repo: repo,
          origin: _originOf(repo, wl), category: _categoryOf(repo, wl),
          desc: p.description || '', address: p.address || '',
          usage: { comp: p.composite_uses || 0, study: p.study_uses || 0, total: p.use_count || 0 },
          use: p.use_count || 0,
          ports: { in: (p.inputs || []).length, out: (p.outputs || []).length,
                   config: Object.keys(p.config_schema || {}).length } };
      });
      rebuild();
      // The Repositories facet browses the whole ecosystem, so also pull the
      // module catalog — the authoritative repo list (real GitHub URLs,
      // descriptions, install status) including repos with no loaded artifacts.
      return J('catalog');
    }).then(function (cat) {
      window._marketCatalog = (cat && cat.modules) || (Array.isArray(cat) ? cat : []);
      _renderMarket();
      // Upgrade to the FULL ecosystem ledger (installed + available-to-install).
      // Heavier (federation scan live), so it lands after the fast installed-only
      // catalog; on success it supersedes it and the repo list re-renders with
      // the "Available" repos + Install actions.
      return J('marketplace');
    }).then(function (mkt) {
      var mods = (mkt && mkt.modules) || (Array.isArray(mkt) ? mkt : []);
      if (mods.length) { window._marketCatalog = mods; if (window._marketFacet === 'repo') _renderMarket(); }
      // Finally, fold in the viva-marketplace ecosystem index so artifacts from
      // OTHER repos (incl. ones not installed here) show in every facet.
      return J('ecoindex');
    }).then(function (eco) {
      _mergeEcosystemIndex(eco);
      _renderMarket();
      window._marketLoading = false;
    }).catch(function () { window._marketLoading = false; });
  }
  window._loadMarket = _loadMarket;

  // Fold the viva-marketplace ecosystem index (per-repo artifact lists) into the
  // facet buckets — but ONLY for repos NOT installed here. Installed repos (the
  // workspace itself + imported deps) are already loaded from the local registry,
  // so re-adding them from the index would duplicate them (and put the
  // workspace's own studies/investigations under "Available"). Added items are
  // category "available": browse now, install the repo to use.
  function _mergeEcosystemIndex(eco) {
    var repos = (eco && eco.repos) || [];
    if (!repos.length) return;
    var wsN = _marketRepoNorm(window._workspaceName || '');
    var installed = {};
    (window._marketCatalog || []).forEach(function (m) {
      if (m && m.installed !== false) installed[_marketRepoNorm(m.name || m.package)] = true;
    });
    var seen = {};
    var TYPES = ['process', 'composite', 'study', 'investigation'];
    TYPES.forEach(function (t) {
      (window._marketByType[t] || []).forEach(function (it) {
        seen[t + '|' + _marketRepoNorm(it.repo) + '|' + it.name] = true;
      });
    });
    var add = { process: [], composite: [], study: [], investigation: [] };
    repos.forEach(function (r) {
      var repo = _marketRepoNorm(r.repo || r.name);
      if (!repo || repo === wsN || installed[repo]) return;   // only uninstalled repos
      var push = function (arr, type, kind) {
        (arr || []).forEach(function (a) {
          var nm = a && a.name; if (!nm) return;
          var key = type + '|' + repo + '|' + nm;
          if (seen[key]) return; seen[key] = true;
          var it = { type: type, name: nm, repo: repo, origin: 'external',
            category: 'available', desc: (a.description || ''),
            available: true, ecosystem: true, use: 0 };
          if (type === 'process') {
            it.kind = kind; it.address = '';
            it.usage = { comp: 0, study: 0, total: 0 };
            it.ports = { in: 0, out: 0, config: 0 };
          } else if (type === 'composite') {
            it.requires = []; it.nParams = 0; it.nSteps = 0;
          } else if (type === 'study') {
            it.status = ''; it.composite = ''; it.nBeh = 0; it.nRuns = 0; it.nSims = 0;
          } else if (type === 'investigation') {
            it.title = a.title || nm; it.status = ''; it.nStudies = 0;
          }
          add[type].push(it);
        });
      };
      push(r.processes, 'process', 'process');
      push(r.steps, 'process', 'step');
      push(r.composites, 'composite');
      push(r.studies, 'study');
      push(r.investigations, 'investigation');
    });
    TYPES.forEach(function (t) {
      if (add[t].length) window._marketByType[t] = (window._marketByType[t] || []).concat(add[t]);
    });
    // Rebuild the flat list from the (now-augmented) per-type buckets.
    var t = window._marketByType;
    window._marketItems = [].concat(t.process, t.composite, t.study, t.investigation);
  }

  function _setMarketFacet(f) {
    window._marketFacet = f || 'all';
    document.querySelectorAll('.market-facet').forEach(function (b) {
      b.classList.toggle('active', b.dataset.facet === window._marketFacet);
    });
    _renderMarket();
  }
  window._setMarketFacet = _setMarketFacet;

  function _setMarketZoom(z) {
    window._marketZoom = z || 'cards';
    try { localStorage.setItem('viv.market-zoom', window._marketZoom); } catch (e) {}
    document.querySelectorAll('[data-mkzoom]').forEach(function (b) {
      b.classList.toggle('active', b.dataset.mkzoom === window._marketZoom);
    });
    _renderMarket();
  }
  window._setMarketZoom = _setMarketZoom;

  function _setMarketOrigin(o) {
    window._marketOrigin = o || 'all';
    try { localStorage.setItem('viv.market-origin', window._marketOrigin); } catch (e) {}
    document.querySelectorAll('.market-origin-chip').forEach(function (b) {
      b.classList.toggle('active', b.dataset.origin === window._marketOrigin);
    });
    _renderMarket();
  }
  window._setMarketOrigin = _setMarketOrigin;

  // Open an artifact where it lives: navigate to the owning page and filter to it.
  function _marketOpen(type, name) {
    if (type === 'investigation') {
      _switchPage('investigations');
      if (typeof _showInvestigationWorkspace === 'function') _showInvestigationWorkspace(name);
      return;
    }
    if (type === 'study') {
      _switchPage('studies');
      var si = document.getElementById('investigations-search');
      if (si) { si.value = name; si.dispatchEvent(new Event('input', { bubbles: true })); }
      return;
    }
    if (type === 'composite') {
      _switchPage('modules');
      var ci = document.getElementById('registry-search');
      if (ci) { ci.value = name; if (typeof _filterRegistry === 'function') _filterRegistry(name); }
      return;
    }
    if (type === 'process') {
      _switchPage('modules');
      var ri = document.getElementById('registry-search');
      if (ri) { ri.value = name; if (typeof _filterRegistry === 'function') _filterRegistry(name); }
      return;
    }
  }
  window._marketOpen = _marketOpen;

  // Usage summary — how much an artifact is used ("used for" surfaced in detail).
  function _marketUsage(it) {
    var p = [];
    if (it.type === 'process') {
      if (it.usage.comp) p.push(it.usage.comp + ' composite' + (it.usage.comp > 1 ? 's' : ''));
      if (it.usage.study) p.push(it.usage.study + ' stud' + (it.usage.study > 1 ? 'ies' : 'y'));
      return p.length ? 'used by ' + p.join(' · ') : 'unused';
    }
    if (it.type === 'composite') {
      if (it.requires.length) p.push('needs ' + it.requires.length + ' process' + (it.requires.length > 1 ? 'es' : ''));
      if (it.nSteps) p.push(it.nSteps + ' steps');
      if (it.nParams) p.push(it.nParams + ' param' + (it.nParams > 1 ? 's' : ''));
      return p.join(' · ');
    }
    if (it.type === 'study') {
      if (it.status) p.push(it.status);
      if (it.nBeh) p.push(it.nBeh + ' behavior' + (it.nBeh > 1 ? 's' : ''));
      if (it.nRuns) p.push(it.nRuns + ' run' + (it.nRuns > 1 ? 's' : ''));
      return p.join(' · ');
    }
    if (it.type === 'investigation') {
      if (it.status) p.push(it.status);
      if (it.nStudies) p.push(it.nStudies + ' stud' + (it.nStudies > 1 ? 'ies' : 'y'));
      return p.join(' · ');
    }
    return '';
  }

  function _marketHead(it) {
    return '<span class="market-type-ico" title="' + (it.kind || it.type) + '">' + _marketIco(it.type) + '</span>'
      + '<span class="market-name">' + _esc(it.title || it.name) + '</span>'
      + (it.type === 'process' ? _procKindBadge(it.kind) : '')
      + _marketCatChip(it)
      + (it.repo ? '<span class="market-repo">' + _esc(it.repo) + '</span>' : '');
  }
  function _marketOpenBtn(it) {
    return '<button class="btn-mini market-open" data-open-type="' + it.type +
      '" data-open-name="' + _esc(it.name) + '">Open ↗</button>';
  }

  // List (dense): one row per artifact.
  function _marketRow(it) {
    var u = _marketUsage(it);
    return '<div class="market-row market-type-' + it.type + '">'
      + '<div class="market-row-main">' + _marketHead(it)
      +   (it.desc ? '<span class="market-row-desc">' + _esc(it.desc) + '</span>' : '') + '</div>'
      + '<span class="market-usage">' + _esc(u) + '</span>'
      + _marketOpenBtn(it) + '</div>';
  }

  // Per-render registry so a card can be expanded in place: each card gets a
  // small id that maps back to its item (reset every _renderMarket).
  function _mkRegister(it) {
    if (!window._mkCardReg) window._mkCardReg = {};
    var id = 'mk' + (window._mkSeq = (window._mkSeq || 0) + 1);
    window._mkCardReg[id] = it;
    return id;
  }

  // Stable identity for an item across zoom re-renders (type · repo · name).
  function _mkKey(it) { return it.type + '|' + (it.repo || '') + '|' + it.name; }

  // Expand (or force-open) a card's in-place detail region.
  function _mkToggleCard(card, forceOpen) {
    if (!card) return;
    var it = (window._mkCardReg || {})[card.getAttribute('data-mk-id')];
    var det = card.querySelector('.market-card-detail');
    if (!it || !det) return;
    var open = forceOpen ? true : card.classList.toggle('mk-expanded');
    if (forceOpen) card.classList.add('mk-expanded');
    if (open && det.getAttribute('data-filled') !== '1') {
      det.innerHTML = _marketDetailBody(it); det.setAttribute('data-filled', '1');
    }
    det.hidden = !open;
  }

  // Double-click an item → advance one semantic-zoom level (list → cards →
  // detail), then center + highlight that same item. This stays WITHIN the
  // registry — it never navigates to another tab (an available/external item
  // has no row in the Processes tab, so opening it there just 404s the search).
  // Use the explicit "Open ↗" button to jump to where an installed item lives.
  function _marketZoomTo(key) {
    var order = ['list', 'cards', 'detail'];
    var i = order.indexOf(window._marketZoom || 'cards');
    if (i < 0) i = 1;
    var next = order[Math.min(order.length - 1, i + 1)];   // clamp at detail
    if (next !== window._marketZoom) _setMarketZoom(next);   // re-renders the facet
    setTimeout(function () {
      var host = document.getElementById('market-results'); if (!host) return;
      var els = host.querySelectorAll('[data-mk-key]');
      for (var k = 0; k < els.length; k++) {
        if (els[k].getAttribute('data-mk-key') === key) {
          els[k].classList.add('mk-focused');
          try { els[k].scrollIntoView({ behavior: 'smooth', block: 'center' }); } catch (e) { /* ignore */ }
          if (els[k].classList.contains('market-card-x')) _mkToggleCard(els[k], true);
          break;
        }
      }
    }, 90);
  }

  // A status → color for the little study dots in an investigation's detail.
  function _mkStatusColor(st) {
    st = String(st || '').toLowerCase();
    if (/complete|ran|pass|done/.test(st)) return '#22c55e';
    if (/run|progress/.test(st)) return '#3b82f6';
    if (/fail|error/.test(st)) return '#ef4444';
    if (/inconclusive|partial/.test(st)) return '#f59e0b';
    return '#cbd5e1';   // planned / unknown
  }

  // An investigation's member studies, joined against the loaded study bucket so
  // each row shows the study's real title + status, with a completion summary.
  function _marketInvStudies(it) {
    var slugs = it.studies || [];
    if (!slugs.length) {
      return it.nStudies
        ? '<div class="market-dl"><div class="market-dl-k">Studies</div><div class="market-dl-v">' + it.nStudies + '</div></div>'
        : '';
    }
    var byName = {};
    ((window._marketByType && window._marketByType.study) || []).forEach(function (s) { byName[s.name] = s; });
    var done = 0, counted = 0;
    var rowsHtml = slugs.map(function (slug) {
      var s = byName[slug];
      var st = s ? (s.status || 'planned') : '';   // '' → status not loaded yet (honest)
      if (s) { counted++; if (/complete|ran|pass|done|evaluated/i.test(st)) done++; }
      return '<div class="mk-study-row" data-open-type="study" data-open-name="' + _esc(slug) + '">' +
        '<span class="mk-study-dot" style="background:' + _mkStatusColor(st) + '"></span>' +
        '<span class="mk-study-name">' + _esc((s && (s.title || s.name)) || slug) + '</span>' +
        '<span class="mk-study-status">' + _esc(st || '—') + '</span></div>';
    }).join('');
    var pct = counted ? Math.round(100 * done / counted) : 0;
    var summary = counted
      ? '<div class="mk-study-summary"><span class="mk-study-bar"><span style="width:' + pct + '%"></span></span>' +
        '<span class="mk-study-summary-txt">' + done + ' / ' + counted + ' complete · ' + pct + '%</span></div>'
      : '';
    return '<div class="mk-studies"><div class="mk-studies-head">Member studies ' +
      '<span class="market-count">' + slugs.length + '</span></div>' + summary + rowsHtml + '</div>';
  }

  // Shared detail body (description + attribute rows + type-specific extras).
  // Used both by the Detail zoom and by a Card's click-to-expand region.
  function _marketDetailBody(it) {
    var rows = [];
    var R = function (k, v) { if (v) rows.push('<div class="market-dl-k">' + k + '</div><div class="market-dl-v">' + v + '</div>'); };
    var extra = '';
    if (it.type === 'process') {
      R('Address', '<code>' + _esc(it.address || it.name) + '</code>');
      R('Used by', it.usage.total
        ? _esc((it.usage.comp || 0) + ' composite' + (it.usage.comp === 1 ? '' : 's') + ' · ' + (it.usage.study || 0) + ' stud' + (it.usage.study === 1 ? 'y' : 'ies'))
        : 'not yet used');
      R('Ports', _esc(it.ports.in + ' in · ' + it.ports.out + ' out · ' + it.ports.config + ' config'));
    } else if (it.type === 'composite') {
      if (it.requires.length) R('Requires', it.requires.slice(0, 12).map(function (p) { return '<code>' + _esc(p) + '</code>'; }).join(' '));
      R('Structure', _esc(it.nSteps + ' steps · ' + it.nParams + ' parameters'));
      if (it.affectedStudies) R('Affected studies', '<span title="studies in your investigations that use this composite">' + it.affectedStudies + '</span>');
    } else if (it.type === 'study') {
      R('Status', _esc(it.status || '—'));
      if (it.composite) R('Composite', '<code>' + _esc(it.composite) + '</code>');
      if (it.question) R('Question', _esc(String(it.question).split('\n')[0].slice(0, 220)));
      if (it.invs && it.invs.length) R('Part of', it.invs.map(function (n) { return _esc(n); }).join(', '));
      R('Activity', _esc((it.nBeh || 0) + ' behavior' + (it.nBeh === 1 ? '' : 's') + ' · ' + (it.nRuns || 0) + ' run' + (it.nRuns === 1 ? '' : 's') + ' · ' + (it.nSims || 0) + ' sim' + (it.nSims === 1 ? '' : 's')));
    } else if (it.type === 'investigation') {
      R('Status', _esc(it.status || '—'));
      if (it.question) R('Question', _esc(String(it.question).split('\n')[0].slice(0, 220)));
      if (it.hypothesis) R('Hypothesis', _esc(String(it.hypothesis).split('\n')[0].slice(0, 220)));
      extra = _marketInvStudies(it);
    }
    // Attributes + the type-specific extra (e.g. an investigation's member
    // studies with success) lead; the long-form description follows.
    return (rows.length ? '<div class="market-dl">' + rows.join('') + '</div>' : '')
      + extra
      + (it.desc ? '<div class="market-desc market-desc-full mk-detail-desc">' + _esc(it.desc) + '</div>' : '');
  }

  // Cards (middle zoom): a legible full name on its own line, provenance chips
  // below, a clamped description, and a foot with usage + Open. Single-click the
  // card body to expand the detail (description + attributes) in place.
  function _marketCard(it) {
    var u = _marketUsage(it);
    var id = _mkRegister(it);
    var tags = _marketCatChip(it)
      + (it.type === 'process' ? _procKindBadge(it.kind) : '')
      + (it.repo ? '<span class="market-repo">' + _esc(it.repo) + '</span>' : '');
    return '<div class="market-card market-card-x market-type-' + it.type + '" data-mk-id="' + id + '"'
      + ' data-mk-key="' + _esc(_mkKey(it)) + '"'
      + ' role="button" tabindex="0" title="Click for details · double-click to zoom in">'
      + '<div class="market-card-title">'
      +   '<span class="market-type-ico" title="' + _esc(it.kind || it.type) + '">' + _marketIco(it.type) + '</span>'
      +   '<span class="market-name-full">' + _esc(it.title || it.name) + '</span>'
      +   '<span class="market-card-caret" aria-hidden="true">▸</span>'
      + '</div>'
      + '<div class="market-card-tags">' + tags + '</div>'
      + (it.desc ? '<div class="market-desc">' + _esc(it.desc) + '</div>' : '')
      + '<div class="market-card-foot">'
      +   '<span class="market-usage">' + _esc(u) + '</span>' + _marketOpenBtn(it) + '</div>'
      + '<div class="market-card-detail" hidden></div>'
      + '</div>';
  }

  // Detail (max zoom): a full-row card with the full body always expanded.
  function _marketDetail(it) {
    return '<div class="market-card market-detail market-type-' + it.type + '"'
      + ' data-mk-key="' + _esc(_mkKey(it)) + '" title="Double-click to open">'
      + '<div class="market-card-head">' + _marketHead(it) + _marketOpenBtn(it) + '</div>'
      + _marketDetailBody(it)
      + '</div>';
  }

  // List (minimized) zoom = a sortable table: info rolls up into columns
  // instead of overflowing to the right. Sort by name, repo, location, or use.
  window._marketSort = { key: 'name', dir: 1 };
  function _marketUseNum(it) {
    if (it.type === 'process') return (it.usage || {}).total || 0;
    if (it.type === 'study') return it.nRuns || 0;
    if (it.type === 'investigation') return it.nStudies || 0;
    return 0;   // composites carry no usage count
  }
  var _MARKET_COLS = [
    { key: 'type',   label: 'Type',     get: function (it) { return it.type; }, w: '92px' },
    { key: 'name',   label: 'Name',     get: function (it) { return it.title || it.name; } },
    { key: 'repo',   label: 'Repo',     get: function (it) { return it.repo || ''; }, w: '150px' },
    { key: 'origin', label: 'Location', get: function (it) { return it.origin === 'workspace' ? 'Repository' : 'External'; }, w: '110px' },
    { key: 'use',    label: 'Use',      get: _marketUseNum, num: true, w: '70px' }
  ];
  function _setMarketSort(key) {
    var s = window._marketSort;
    if (s.key === key) s.dir = -s.dir; else { s.key = key; s.dir = 1; }
    _renderMarket();
  }
  window._setMarketSort = _setMarketSort;
  function _marketTable(items, showType) {
    var s = window._marketSort;
    var cols = _MARKET_COLS.filter(function (c) { return c.key !== 'type' || showType; });
    var col = null; _MARKET_COLS.forEach(function (c) { if (c.key === s.key) col = c; });
    if (!col) col = _MARKET_COLS[1];
    var sorted = items.slice().sort(function (a, b) {
      var av = col.get(a), bv = col.get(b);
      if (col.num) return ((+av || 0) - (+bv || 0)) * s.dir;
      return String(av).toLowerCase().localeCompare(String(bv).toLowerCase()) * s.dir;
    });
    var head = cols.map(function (c) {
      var arrow = s.key === c.key ? (s.dir > 0 ? ' ▲' : ' ▼') : '';
      return '<th class="market-th' + (s.key === c.key ? ' sorted' : '') + '" data-sort="' + c.key + '"'
        + (c.w ? ' style="width:' + c.w + '"' : '') + '>' + c.label + arrow + '</th>';
    }).join('');
    var body = sorted.map(function (it) {
      var tds = cols.map(function (c) {
        if (c.key === 'type') return '<td class="market-td-type"><span class="market-type-ico" title="' + it.type + '">' + _marketIco(it.type) + '</span></td>';
        if (c.key === 'name') return '<td class="market-td-name">' + _esc(it.title || it.name)
          + (it.desc ? '<span class="market-td-desc">' + _esc(it.desc) + '</span>' : '') + '</td>';
        if (c.key === 'origin') return '<td><span class="market-origin market-origin-' + it.origin + '">'
          + (it.origin === 'workspace' ? 'Repository' : 'External') + '</span></td>';
        if (c.key === 'use') { var u = _marketUseNum(it); return '<td class="market-td-use">' + (u || '—') + '</td>'; }
        return '<td>' + _esc(String(c.get(it))) + '</td>';
      }).join('');
      return '<tr class="market-tr" data-open-type="' + it.type + '" data-open-name="' + _esc(it.name) + '"'
        + ' data-mk-key="' + _esc(_mkKey(it)) + '" title="Double-click to zoom in">' + tds + '</tr>';
    }).join('');
    return '<table class="market-table"><thead><tr>' + head + '</tr></thead><tbody>' + body + '</tbody></table>';
  }

  // ── Repositories facet ────────────────────────────────────────────────────
  // A whole-ecosystem view: one entry per repo the workspace imports (from the
  // module catalog — authoritative list, real GitHub URLs, descriptions, install
  // status) MERGED with per-type counts from the loaded artifacts. Honors the
  // three semantic-zoom levels (List / Cards / Detail) like every other facet.
  function _marketRepoNorm(name) {
    return String(name || '').toLowerCase().replace(/\.git$/, '').replace(/_/g, '-').trim();
  }
  function _marketRepoCanon(r) {
    var ws = window._workspaceName || '';
    if (!r || r === 'workspace') return _marketRepoNorm(ws || 'workspace');
    return _marketRepoNorm(r);
  }
  function _marketRepoUrl(repo) {
    if (window._workspaceName && repo === _marketRepoNorm(window._workspaceName) && window._workspaceRepoUrl) {
      return window._workspaceRepoUrl;
    }
    return 'https://github.com/vivarium-collective/' + repo;
  }
  // Merge the catalog (repo universe) with loaded artifacts (fine per-type counts).
  // viva-* UI label for a module/repo name — mirrors catalog._viva_display_name
  // so artifact-derived repos (no curated display_name) still read viva-*.
  // pbg-torch / pbg_torch -> viva-torch; already-viva / non-pbg pass through.
  function _vivaLabel(name) {
    if (!name) return name;
    var s = String(name), low = s.toLowerCase();
    if (low.indexOf('pbg-') === 0 || low.indexOf('pbg_') === 0) return 'viva-' + s.slice(4).replace(/_/g, '-');
    return s;
  }
  function _marketRepoList() {
    var wsName = _marketRepoNorm(window._workspaceName || '');
    var byRepo = {};
    var get = function (raw) {
      var r = _marketRepoNorm(raw);
      if (!r) return null;
      return byRepo[r] || (byRepo[r] = {
        repo: r, isWorkspace: (r === wsName), installed: true, url: '', desc: '',
        installName: '', process: 0, composite: 0, study: 0, investigation: 0, total: 0, use: 0,
        composites: [], _fromArtifacts: false, _cat: null
      });
    };
    // 1) Every ecosystem repo from the catalog/marketplace ledger (incl. repos
    // with no artifacts AND available-to-install repos not present locally).
    (window._marketCatalog || []).forEach(function (m) {
      var b = get(m.name || m.package); if (!b) return;
      if (m.source && !b.url) b.url = String(m.source).replace(/\.git$/, '');
      if (m.description && !b.desc) b.desc = m.description;
      if (m.installed === false) b.installed = false;
      if (!b.installName) b.installName = m.name || m.package || '';
      // viva-* UI label (catalog carries it; name/installName stay pbg-* so
      // install/uninstall resolution is unchanged). Workspace pkg has none.
      if (m.display_name && !b.display_name) b.display_name = m.display_name;
      b._cat = m;
    });
    // 2) Per-type counts from the loaded registry/composites/studies/investigations.
    (window._marketItems || []).forEach(function (it) {
      var b = get(it.repo); if (!b) return;
      b._fromArtifacts = true;
      if (typeof b[it.type] === 'number') b[it.type]++;
      b.total++; b.use += _marketUseNum(it);
      if (it.type === 'composite' && b.composites.length < 6) b.composites.push(it.title || it.name);
    });
    // 3) Backfill counts from the catalog for repos with no loaded artifacts.
    Object.keys(byRepo).forEach(function (k) {
      var b = byRepo[k], c = b._cat;
      if (!b._fromArtifacts && c) {
        b.composite = c.n_composites || 0;
        b.study = c.n_studies || 0;
        b.investigation = c.n_investigations || 0;
        b.total = b.process + b.composite + b.study + b.investigation;
        b.use = c.n_used || 0;
      }
      // Affected studies = this workspace's OWN studies that depend on the repo
      // (module_stats.n_used — deep, via composite→process usage). The real
      // "what breaks if I uninstall" signal, distinct from total artifact uses.
      b.affected = (c && typeof c.n_used === 'number') ? c.n_used : 0;
      if (!b.url) b.url = _marketRepoUrl(b.repo);
    });
    return Object.keys(byRepo).map(function (k) { return byRepo[k]; }).sort(function (a, b) {
      if (a.isWorkspace !== b.isWorkspace) return a.isWorkspace ? -1 : 1;   // workspace first
      if ((a.installed === false) !== (b.installed === false)) return a.installed === false ? 1 : -1;
      if (b.total !== a.total) return b.total - a.total;
      return a.repo.localeCompare(b.repo);
    });
  }
  function _repoBadge(b) {
    if (b.isWorkspace) return '<span class="repo-badge repo-badge-ws">This workspace</span>';
    if (b.installed === false) return '<span class="repo-badge repo-badge-avail">Available</span>';
    return '<span class="repo-badge repo-badge-imported">Imported</span>';
  }
  var _REPO_STATS = [
    ['composite', 'composite', 'composites'],
    ['process', 'process', 'processes'],
    ['study', 'study', 'studies'],
    ['investigation', 'investigation', 'investigations']
  ];
  function _repoStatLine(b, showZero) {
    var parts = _REPO_STATS.map(function (s) {
      var n = b[s[0]] || 0;
      if (!n && !showZero) return null;
      return '<span class="repo-stat' + (n ? '' : ' zero') + '"><b>' + n + '</b> ' + (n === 1 ? s[1] : s[2]) + '</span>';
    }).filter(Boolean);
    return parts.length ? parts.join('') : '<span class="repo-stat zero">no artifacts yet</span>';
  }
  // Install action for an available (not-installed) repo. Class js-authoring so
  // the read-only snapshot auto-suppresses it (browse-only there); live workbench
  // shows it and _installFromMarketplace runs the submodule + pip install flow.
  function _repoInstallBtn(b) {
    if (b.installed !== false || b.isWorkspace) return '';   // only not-installed repos
    var target = b.installName || b.repo;
    return '<button class="btn-mini js-authoring repo-install" '
      + 'onclick="event.preventDefault();event.stopPropagation();_installFromMarketplace(\'' + _esc(target) + '\');return false;">'
      + '+ Install</button>';
  }
  // Uninstall an imported (installed, non-workspace) repo — routes through the
  // existing impact-confirmation modal (composites/studies lost + workspace
  // studies that would be left with a dangling reference).
  function _repoUninstallBtn(b) {
    if (b.installed === false || b.isWorkspace) return '';
    var target = b.installName || b.repo;
    return '<button class="btn-mini js-authoring repo-uninstall" title="Uninstall — shows what it affects first" '
      + 'onclick="event.preventDefault();event.stopPropagation();_uninstallFromCatalog(\'' + _esc(target) + '\');return false;">'
      + 'Uninstall</button>';
  }
  // GitHub link + Install/Uninstall, shared by card + detail.
  function _repoActions(b) {
    return '<span class="repo-actions">'
      + '<a class="btn-mini repo-gh" href="' + _esc(b.url) + '" target="_blank" rel="noopener" onclick="event.stopPropagation()">GitHub ↗</a>'
      + _repoInstallBtn(b) + _repoUninstallBtn(b) + '</span>';
  }
  function _repoFoot(b) {
    var meta = [];
    if (b.total) meta.push(b.total + ' artifact' + (b.total === 1 ? '' : 's'));
    if (b.affected) meta.push('<span title="studies in your investigations that depend on this repo"><b>' + b.affected + '</b> affected stud' + (b.affected === 1 ? 'y' : 'ies') + '</span>');
    return '<div class="repo-card-foot"><span class="repo-meta">' + (meta.join(' · ') || '&nbsp;') + '</span>'
      + _repoActions(b) + '</div>';
  }
  function _marketRepoCard(b) {
    return '<div class="market-card repo-card' + (b.isWorkspace ? ' repo-card-ws' : '') + '">'
      + '<div class="repo-card-head">'
      +   '<span class="repo-ico">📦</span>'
      +   '<a class="repo-name" href="' + _esc(b.url) + '" target="_blank" rel="noopener">' + _esc(_vivaLabel(b.display_name || b.repo)) + '</a>'
      +   _repoBadge(b)
      + '</div>'
      + (b.desc ? '<div class="repo-desc">' + _esc(b.desc) + '</div>' : '')
      + '<div class="repo-stats">' + _repoStatLine(b, false) + '</div>'
      + _repoFoot(b)
      + '</div>';
  }
  function _marketRepoDetail(b) {
    return '<div class="market-card repo-card repo-card-detail' + (b.isWorkspace ? ' repo-card-ws' : '') + '">'
      + '<div class="repo-card-head">'
      +   '<span class="repo-ico">📦</span>'
      +   '<a class="repo-name" href="' + _esc(b.url) + '" target="_blank" rel="noopener">' + _esc(_vivaLabel(b.display_name || b.repo)) + '</a>'
      +   _repoBadge(b)
      + '</div>'
      + (b.desc ? '<div class="repo-desc repo-desc-full">' + _esc(b.desc) + '</div>' : '')
      + '<div class="repo-stats repo-stats-detail">' + _repoStatLine(b, true) + '</div>'
      + (b.composites.length ? '<div class="repo-sample"><span class="repo-sample-lbl">Composites:</span> '
          + b.composites.slice(0, 5).map(function (c) { return '<code>' + _esc(c) + '</code>'; }).join(' ')
          + (b.composite > 5 ? ' <span class="repo-stat zero">+' + (b.composite - 5) + ' more</span>' : '') + '</div>' : '')
      + _repoFoot(b)
      + '</div>';
  }
  function _marketRepoTable(repos) {
    var head = '<tr>'
      + '<th class="repo-th">Repository</th>'
      + '<th class="repo-th" style="width:120px">Status</th>'
      + '<th class="repo-th" style="width:100px">Processes</th>'
      + '<th class="repo-th" style="width:100px">Composites</th>'
      + '<th class="repo-th" style="width:90px">Studies</th>'
      + '<th class="repo-th" style="width:130px" title="Studies in your investigations that depend on this repo">Affected studies</th>'
      + '<th class="repo-th" style="width:190px"></th></tr>';
    var body = repos.map(function (b) {
      var aff = b.affected
        ? '<span class="repo-affected" title="studies in your investigations that depend on this repo">' + b.affected + '</span>'
        : '<span class="repo-td-zero">—</span>';
      return '<tr class="repo-tr">'
        + '<td class="market-td-name">📦 ' + _esc(_vivaLabel(b.display_name || b.repo))
        +   (b.desc ? '<span class="market-td-desc">' + _esc(b.desc) + '</span>' : '') + '</td>'
        + '<td>' + _repoBadge(b) + '</td>'
        + '<td class="repo-td-num">' + (b.process || '—') + '</td>'
        + '<td class="repo-td-num">' + (b.composite || '—') + '</td>'
        + '<td class="repo-td-num">' + (b.study || '—') + '</td>'
        + '<td class="repo-td-num">' + aff + '</td>'
        + '<td class="repo-td-actions">' + _repoActions(b) + '</td>'
        + '</tr>';
    }).join('');
    return '<table class="market-table repo-table"><thead>' + head + '</thead><tbody>' + body + '</tbody></table>';
  }
  function _repoCategory(b) {
    if (b.isWorkspace) return 'workspace';
    if (b.installed === false) return 'available';
    return 'imported';
  }
  function _renderMarketRepos(zoom, q, cat) {
    var repos = _marketRepoList();
    // Same category toggle as the artifact facets: Workspace / Imported /
    // Available (All = everything, ordered by category).
    if (cat && cat !== 'all') repos = repos.filter(function (b) { return _repoCategory(b) === cat; });
    else repos.sort(function (a, b) { return _MARKET_CAT_ORDER[_repoCategory(a)] - _MARKET_CAT_ORDER[_repoCategory(b)] || 0; });
    if (q) repos = repos.filter(function (b) {
      return (b.repo + ' ' + b.desc + ' ' + b.composites.join(' ')).toLowerCase().indexOf(q) !== -1;
    });
    if (!repos.length) {
      var loaded = (window._marketItems && window._marketItems.length) || (window._marketCatalog && window._marketCatalog.length);
      return loaded ? '<p class="empty-state">No repositories match.</p>' : '<p class="empty-state">Loading&hellip;</p>';
    }
    if (zoom === 'list') return _marketRepoTable(repos);
    var render = zoom === 'detail' ? _marketRepoDetail : _marketRepoCard;
    return '<div class="market-grid market-grid-' + (zoom === 'detail' ? 'detail' : 'cards') + '">'
      + repos.map(render).join('') + '</div>';
  }

  // Draggable column widths for the registry tables (Repositories list + the
  // artifact list). A thin grip on each header's right edge resizes that column;
  // widths persist per (facet · column) in localStorage.
  function _enableColResize(root) {
    (root || document).querySelectorAll('table.market-table').forEach(function (tbl) {
      var facet = window._marketFacet || 'all';
      var ths = tbl.querySelectorAll('thead th');
      ths.forEach(function (th, i) {
        if (i === ths.length - 1) return;   // last column absorbs the slack
        // Restore a saved width.
        try {
          var saved = localStorage.getItem('viv.colw.' + facet + '.' + i);
          if (saved) th.style.width = saved + 'px';
        } catch (e) { /* private mode */ }
        var grip = document.createElement('span');
        grip.className = 'col-resizer';
        th.style.position = 'relative';
        th.appendChild(grip);
        grip.addEventListener('click', function (e) { e.stopPropagation(); });   // don't sort
        grip.addEventListener('mousedown', function (e) {
          e.preventDefault(); e.stopPropagation();
          var startX = e.pageX, startW = th.offsetWidth;
          document.body.classList.add('col-resizing');
          function mv(ev) { th.style.width = Math.max(48, startW + (ev.pageX - startX)) + 'px'; }
          function up() {
            document.removeEventListener('mousemove', mv);
            document.removeEventListener('mouseup', up);
            document.body.classList.remove('col-resizing');
            try { localStorage.setItem('viv.colw.' + facet + '.' + i, String(parseInt(th.style.width, 10) || th.offsetWidth)); } catch (e) { /* ignore */ }
          }
          document.addEventListener('mousemove', mv);
          document.addEventListener('mouseup', up);
        });
      });
    });
  }

  function _renderMarket() {
    var host = document.getElementById('market-results');
    if (!host) return;
    if (!host._marketWired) {   // delegate clicks once (survives re-renders)
      host._marketWired = true;
      var _clearTimer = function () { if (host._mkTimer) { clearTimeout(host._mkTimer); host._mkTimer = null; } };
      host.addEventListener('click', function (e) {
        var th = e.target.closest('.market-th'); if (th) { _setMarketSort(th.dataset.sort); return; }
        var b = e.target.closest('.market-open'); if (b) { _clearTimer(); _marketOpen(b.dataset.openType, b.dataset.openName); return; }
        // A member-study row inside an investigation's detail → open that study.
        var sr = e.target.closest('.mk-study-row'); if (sr && sr.dataset.openName) { _clearTimer(); _marketOpen('study', sr.dataset.openName); return; }
        // Single-click, DEBOUNCED so a double-click can preempt it and zoom
        // instead: a card expands its detail in place; a table row opens.
        var card = e.target.closest('.market-card-x');
        var tr = e.target.closest('.market-tr');
        if (card) { _clearTimer(); host._mkTimer = setTimeout(function () { host._mkTimer = null; _mkToggleCard(card); }, 220); return; }
        // A list row drills into Cards (centered) — it stays in the registry
        // rather than navigating to the owning tab (which 404s for external items).
        if (tr && tr.getAttribute('data-mk-key')) { _clearTimer(); var k = tr.getAttribute('data-mk-key'); host._mkTimer = setTimeout(function () { host._mkTimer = null; _marketZoomTo(k); }, 220); return; }
      });
      // Double-click an item → advance one zoom level, centered on it.
      host.addEventListener('dblclick', function (e) {
        var el = e.target.closest('[data-mk-key]');
        if (el) { _clearTimer(); _marketZoomTo(el.getAttribute('data-mk-key')); }
      });
      host.addEventListener('keydown', function (e) {
        if ((e.key === 'Enter' || e.key === ' ') && e.target.closest('.market-card-x')) {
          e.preventDefault(); _mkToggleCard(e.target.closest('.market-card-x'));
        }
      });
    }
    window._mkCardReg = {}; window._mkSeq = 0;   // reset the per-render card registry
    var items = window._marketItems || [];
    var q = ((document.getElementById('market-search') || {}).value || '').trim().toLowerCase();
    var facet = window._marketFacet || 'all';
    var zoom = window._marketZoom || 'cards';
    var cat = window._marketOrigin || 'all';   // All | workspace | imported | available
    var pageMarket = document.getElementById('page-market');
    if (pageMarket) pageMarket.classList.toggle('market-facet-repo', facet === 'repo');
    document.querySelectorAll('[data-mkzoom]').forEach(function (b) { b.classList.toggle('active', b.dataset.mkzoom === zoom); });
    document.querySelectorAll('.market-origin-chip').forEach(function (b) { b.classList.toggle('active', b.dataset.origin === cat); });
    // Repositories facet: whole-ecosystem repo browse with its own List/Cards/
    // Detail zoom, filtered by the same category toggle. Rendered before the
    // per-artifact filtering below.
    if (facet === 'repo') {
      host.innerHTML = _renderMarketRepos(zoom, q, cat);
      if (zoom === 'cards') {
        _syncColsControls();
        _cardContainersFor('market').forEach(function (c) { _applyCardCols(c, 'market'); });
      } else { _updateColsSlotVisibility(); }
      if (zoom === 'list') _enableColResize(host);
      return;
    }
    var match = function (it) {
      if (facet !== 'all' && it.type !== facet) return false;
      if (cat !== 'all' && _marketCatOf(it) !== cat) return false;
      if (!q) return true;
      return (it.name + ' ' + (it.title || '') + ' ' + it.desc + ' ' + it.repo).toLowerCase().indexOf(q) !== -1;
    };
    var filtered = items.filter(match);
    // Order by category (workspace → imported → available); stable sort keeps the
    // within-category order (e.g. processes by use). So "All" reads as grouped.
    filtered.sort(function (a, b) { return _MARKET_CAT_ORDER[_marketCatOf(a)] - _MARKET_CAT_ORDER[_marketCatOf(b)]; });
    if (!filtered.length) {
      host.innerHTML = items.length ? '<p class="empty-state">No matches.</p>' : '<p class="empty-state">Loading&hellip;</p>';
      return;
    }
    var html = '';
    if (zoom === 'list') {
      // One sortable table; a Type column appears only in the All facet.
      html = _marketTable(filtered, facet === 'all');
    } else {
      var render = zoom === 'detail' ? _marketDetail : _marketCard;
      var wrap = function (arr) { return '<div class="market-grid market-grid-' + zoom + '">' + arr.map(render).join('') + '</div>'; };
      if (facet === 'all') {
        _MARKET_TYPES.forEach(function (t) {
          var group = filtered.filter(function (it) { return it.type === t.key; });
          if (!group.length) return;
          html += '<div class="market-group"><div class="market-group-head">' + t.label
            + ' <span class="market-count">' + group.length + '</span></div>' + wrap(group) + '</div>';
        });
      } else {
        html = wrap(filtered);
      }
    }
    host.innerHTML = html;
    // Cards zoom: honor the column dial (shared with composites/processes).
    if (facet !== 'repo') {
      if (zoom === 'cards') {
        _syncColsControls();
        _cardContainersFor('market').forEach(function (c) { _applyCardCols(c, 'market'); });
      } else {
        _updateColsSlotVisibility();
      }
      if (zoom === 'list') _enableColResize(host);
    }
  }
  window._renderMarket = _renderMarket;

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

  function _installFromCatalog(name, opts) {
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
          return _proceedWithCatalogInstall(name, opts);
        }
        _showSystemDepsModal(name, j, opts);
      })
      .catch(function() {
        // Network/parse error: don't block the user — let the install try.
        _proceedWithCatalogInstall(name, opts);
      });
  }
  window._installFromCatalog = _installFromCatalog;

  // Marketplace-tab installs must force the full-repo git-submodule path
  // (not the lightweight PyPI wheel) so the module's top-level studies/ and
  // investigations/ land on disk under external/<name>/ and can federate.
  function _installFromMarketplace(name) {
    _installFromCatalog(name, {full_repo: true});
  }
  window._installFromMarketplace = _installFromMarketplace;

  function _proceedWithCatalogInstall(name, opts) {
    if (!confirm("Install '" + name + "' on the active investigation branch?\n\nThis adds a submodule, pip installs the package, and appends it to pyproject.toml. Requires an active investigation branch.")) return;
    var body = {name: name};
    if (opts && opts.skip_system_deps_check) body.skip_system_deps_check = true;
    if (opts && opts.full_repo) body.full_repo = true;
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
            }, opts);
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

  function _showSystemDepsModal(name, depsResult, opts) {
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
        _installSystemDeps(name, installableNames, opts);
      });
    }
    var skipBtnEl = document.getElementById('sysdeps-skip-btn');
    if (skipBtnEl) {
      skipBtnEl.addEventListener('click', function() {
        if (!confirm("Skip system-deps check and install '" + name + "' anyway?\n\nThis is unsafe — the install will likely succeed at the pip step but fail with a native-library error at first Run.")) return;
        _closeSystemDepsModal();
        var skipOpts = {skip_system_deps_check: true};
        if (opts && opts.full_repo) skipOpts.full_repo = true;
        _proceedWithCatalogInstall(name, skipOpts);
      });
    }
  }
  window._showSystemDepsModal = _showSystemDepsModal;

  function _installSystemDeps(name, checkNames, opts) {
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
          _proceedWithCatalogInstall(name, opts);
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

  // Uninstall flow: fetch the impact report first (the module's own content
  // that will disappear + this workspace's OWN studies/investigations that
  // reference it and would be left dangling), show it in a confirmation modal,
  // and only POST /api/catalog-uninstall on explicit confirm.
  function _uninstallFromCatalog(name) {
    var base = (window.DataSource && window.DataSource.apiUrl)
      ? window.DataSource.apiUrl('/api/catalog-uninstall-impact')
      : '/api/catalog-uninstall-impact';
    fetch(base + '?name=' + encodeURIComponent(name))
      .then(function(r) { return r.json().then(function(j) { return [r.ok, j]; }); })
      .then(function(parts) { _showUninstallImpactModal(name, parts[0] ? parts[1] : null); })
      .catch(function() { _showUninstallImpactModal(name, null); });
  }
  window._uninstallFromCatalog = _uninstallFromCatalog;

  function _closeUninstallModal() {
    var el = document.getElementById('modal-uninstall-impact');
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }
  window._closeUninstallModal = _closeUninstallModal;

  function _showUninstallImpactModal(name, impact) {
    _closeUninstallModal();

    function _group(title, items, render) {
      if (!items || !items.length) return '';
      return '<div class="uninstall-impact-group"><div class="uninstall-impact-group-title">' +
        _esc(title) + ' <span class="muted">(' + items.length + ')</span></div>' +
        '<ul class="uninstall-impact-list">' +
        items.map(function(it) { return '<li>' + render(it) + '</li>'; }).join('') +
        '</ul></div>';
    }

    var mc = (impact && impact.module_content) || {};
    var wr = (impact && impact.workspace_refs) || {};
    var refStudies = wr.studies || [];
    var refInvs = wr.investigations || [];
    var nModule = (mc.composites || []).length + (mc.studies || []).length + (mc.investigations || []).length;
    var nRefs = refStudies.length + refInvs.length;

    // Section 1: module content that will stop showing up.
    var removedBody =
      _group('Composites', mc.composites, function(c) { return '<code>' + _esc(c) + '</code>'; }) +
      _group('Studies', mc.studies, function(s) { return '<code>' + _esc(s) + '</code>'; }) +
      _group('Investigations', mc.investigations, function(i) { return '<code>' + _esc(i) + '</code>'; });
    var removedSection = nModule
      ? '<h4 class="uninstall-impact-h">Content that will be removed</h4>' + removedBody
      : (impact
          ? '<p class="muted">This module contributes no composites, studies, or investigations to this workspace.</p>'
          : '<p class="muted">Could not compute what this module contributes (impact probe unavailable) — proceed with care.</p>');

    // Section 2: this workspace's own content that references it (the warning).
    var refSection = '';
    if (nRefs) {
      refSection =
        '<div class="uninstall-impact-warn">' +
          '<strong>⚠ ' + nRefs + ' of your workspace’s own item' + (nRefs === 1 ? '' : 's') +
          ' reference this module</strong> and will be left with a dangling reference if you uninstall:' +
        '</div>' +
        _group('Your studies → composite', refStudies, function(r) {
          return '<code>' + _esc(r.study) + '</code> <span class="muted">→</span> <code>' + _esc(r.composite) + '</code>';
        }) +
        _group('Your investigations → study', refInvs, function(r) {
          return '<code>' + _esc(r.investigation) + '</code> <span class="muted">→</span> <code>' + _esc(r.study) + '</code>';
        });
    } else if (impact) {
      refSection = '<p class="muted" style="margin-top:10px;">Nothing in your workspace references this module.</p>';
    }

    var modal = document.createElement('div');
    modal.id = 'modal-uninstall-impact';
    modal.className = 'modal-overlay';
    modal.style.display = 'flex';
    modal.innerHTML =
      '<div class="modal-box" style="max-width:640px;">' +
        '<button class="modal-close" onclick="_closeUninstallModal()">&times;</button>' +
        '<h3>Uninstall <code>' + _esc(name) + '</code>?</h3>' +
        '<p class="muted" style="margin:4px 0 10px;">This removes the package from the workspace venv, ' +
          'pyproject.toml, and workspace.yaml imports, and commits the change on the active branch.</p>' +
        '<div class="uninstall-impact-body">' + removedSection + refSection + '</div>' +
        '<div id="uninstall-error" class="form-error" style="color:#c00;min-height:1em;margin-top:8px;"></div>' +
        '<div style="margin-top:14px;display:flex;gap:8px;flex-wrap:wrap;">' +
          '<button type="button" class="action-btn danger" id="uninstall-confirm-btn">Uninstall ' + _esc(name) + '</button>' +
          '<button type="button" class="btn-mini" onclick="_closeUninstallModal()">Cancel</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(modal);

    var btn = document.getElementById('uninstall-confirm-btn');
    if (btn) btn.addEventListener('click', function() { _proceedWithUninstall(name); });
  }
  window._showUninstallImpactModal = _showUninstallImpactModal;

  function _proceedWithUninstall(name) {
    var btn = document.getElementById('uninstall-confirm-btn');
    var errEl = document.getElementById('uninstall-error');
    if (errEl) errEl.textContent = '';
    if (btn) { btn.disabled = true; btn.textContent = 'Uninstalling…'; }
    fetch('/api/catalog-uninstall', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name}),
    })
      .then(function(r) { return r.json().then(function(j) { return {ok: r.ok, json: j}; }); })
      .then(function(p) {
        if (!p.ok) {
          if (btn) { btn.disabled = false; btn.textContent = 'Uninstall ' + name; }
          if (errEl) errEl.textContent = 'Uninstall failed: ' + ((p.json && p.json.error) || 'unknown');
          return;
        }
        _closeUninstallModal();
        var msg = p.json.already_uninstalled ? 'Already uninstalled.' : 'Uninstalled ' + name + '.';
        if (p.json.branch) msg += '\n\nBranch: ' + p.json.branch + (p.json.commit ? ' (' + p.json.commit + ')' : '');
        alert(msg);
        if (typeof _loadCatalog === 'function') _loadCatalog();
        if (typeof _loadMarket === 'function') _loadMarket(true);
        if (typeof _loadRegistry === 'function') _loadRegistry(true);
      })
      .catch(function(e) {
        if (btn) { btn.disabled = false; btn.textContent = 'Uninstall ' + name; }
        if (errEl) errEl.textContent = 'Network error: ' + e;
      });
  }
  window._proceedWithUninstall = _proceedWithUninstall;

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
          location.hash = '#modules';
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

  function _vivSyncRailToggleLabel(collapsed) {
    var btn = document.getElementById('viv-rail-toggle');
    if (!btn) return;
    var label = collapsed ? 'Open sidebar' : 'Collapse sidebar';
    btn.setAttribute('title', label);
    btn.setAttribute('aria-label', label);
  }

  function _vivToggleRail() {
    var rail = document.getElementById('viv-rail');
    if (!rail) return;
    var collapsed = rail.classList.toggle('viv-rail-collapsed');
    _vivSyncRailToggleLabel(collapsed);
    try { localStorage.setItem('vivarium.rail-collapsed', collapsed ? '1' : '0'); } catch (e) {}
  }
  window._vivToggleRail = _vivToggleRail;

  function _vivRestoreRailState() {
    var stored = null;
    try { stored = localStorage.getItem('vivarium.rail-collapsed'); } catch (e) {}
    var collapsed = stored === '1';
    var rail = document.getElementById('viv-rail');
    if (rail) {
      // Apply the saved expanded width (the collapsed rule overrides it via
      // higher CSS specificity, so this is safe even when collapsed).
      _vivRailApplyWidth(rail, _vivRailSavedWidth());
      if (collapsed) rail.classList.add('viv-rail-collapsed');
    }
    _vivSyncRailToggleLabel(collapsed);
  }
  window._vivRestoreRailState = _vivRestoreRailState;

  // ---- Rail resize (drag the right edge to widen; snap to normal; drag
  //      narrower to snap into the collapsed bar) ----------------------------
  var _RAIL_NORMAL = 240;      // the "normal" snap width (matches CSS default)
  var _RAIL_MIN = 160;         // expanded floor (~2/3 of normal) — narrower than this collapses
  var _RAIL_MAX = 560;         // don't let the rail eat the whole viewport
  var _RAIL_COLLAPSE_AT = 140; // drag below this (px from left edge) → collapse
  var _RAIL_SNAP = 28;         // within this of normal → snap to exactly normal

  function _vivRailSavedWidth() {
    var raw = null;
    try { raw = localStorage.getItem('vivarium.rail-width'); } catch (e) {}
    var w = parseInt(raw, 10);
    if (!w || isNaN(w)) return _RAIL_NORMAL;
    return Math.min(_RAIL_MAX, Math.max(_RAIL_MIN, w));
  }
  function _vivRailApplyWidth(rail, w) {
    rail.style.setProperty('--rail-w', w + 'px');
  }
  function _vivRailResizeStart(ev) {
    ev.preventDefault();
    var rail = document.getElementById('viv-rail');
    if (!rail) return;
    rail.classList.add('viv-rail-resizing');
    document.body.classList.add('viv-rail-resizing-active');
    var railLeft = rail.getBoundingClientRect().left;
    var lastW = _vivRailSavedWidth();

    function _move(e) {
      var raw = e.clientX - railLeft;         // desired width: left edge → pointer
      if (raw < _RAIL_COLLAPSE_AT) {          // snap into the collapsed bar look
        rail.classList.add('viv-rail-collapsed');
        return;
      }
      rail.classList.remove('viv-rail-collapsed');
      var w = Math.min(_RAIL_MAX, Math.max(_RAIL_MIN, raw));
      if (Math.abs(w - _RAIL_NORMAL) <= _RAIL_SNAP) w = _RAIL_NORMAL;  // snap to normal
      lastW = w;
      _vivRailApplyWidth(rail, w);
    }
    function _up() {
      document.removeEventListener('mousemove', _move);
      document.removeEventListener('mouseup', _up);
      rail.classList.remove('viv-rail-resizing');
      document.body.classList.remove('viv-rail-resizing-active');
      var collapsed = rail.classList.contains('viv-rail-collapsed');
      try {
        localStorage.setItem('vivarium.rail-collapsed', collapsed ? '1' : '0');
        if (!collapsed) localStorage.setItem('vivarium.rail-width', String(lastW));
      } catch (e) { /* private mode */ }
      if (typeof _vivSyncRailToggleLabel === 'function') _vivSyncRailToggleLabel(collapsed);
    }
    document.addEventListener('mousemove', _move);
    document.addEventListener('mouseup', _up);
  }
  window._vivRailResizeStart = _vivRailResizeStart;

  function _vivRailResizeReset() {
    var rail = document.getElementById('viv-rail');
    if (!rail) return;
    rail.classList.remove('viv-rail-collapsed');
    _vivRailApplyWidth(rail, _RAIL_NORMAL);
    try {
      localStorage.setItem('vivarium.rail-collapsed', '0');
      localStorage.setItem('vivarium.rail-width', String(_RAIL_NORMAL));
    } catch (e) { /* private mode */ }
    if (typeof _vivSyncRailToggleLabel === 'function') _vivSyncRailToggleLabel(false);
  }
  window._vivRailResizeReset = _vivRailResizeReset;

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
      ? (window.DataSource && window.DataSource.loadIsetList
          ? window.DataSource.loadIsetList()
          : fetch('/api/investigation-summaries').then(function(r) { return r.json(); })
        ).catch(function() { return {investigations: []}; })
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
    // Open the detail panel and refresh the rail so the active-state moves
    // with the selection. Page activation is handled by _showWorkspace()
    // (via _showInvestigationWorkspace), which self-activates
    // #page-investigations — no separate _switchPage call needed here.
    if (typeof _showInvestigationWorkspace === 'function') _showInvestigationWorkspace(name);
    else if (typeof _openInvestigation === 'function') _openInvestigation(name);
    _vivRefreshInvestigationsRail();
  }
  window._vivOpenInvestigationFromRail = _vivOpenInvestigationFromRail;

  // Open an investigation's DETAIL view (summary + DAG) from the rail, from any
  // page. Activates the Investigations page directly rather than via
  // _switchPage('investigations') — that path calls _loadInvestigationSets(),
  // which async-re-renders the LIST over the detail we just opened.
  // ── Investigations rail: most-recently-opened ordering + pinning ──────────
  // Per-user convenience kept in localStorage (no workspace write). MRU keeps the
  // rail reliable — the investigation you just opened floats to the top; pins
  // override MRU and sit above everything.
  function _loadInvMru() {
    if (!window._invMru || typeof window._invMru !== 'object') {
      try { window._invMru = JSON.parse(window.localStorage.getItem('viv.invMru') || '{}') || {}; }
      catch (e) { window._invMru = {}; }
      if (!window._invMru || typeof window._invMru !== 'object') window._invMru = {};
    }
    return window._invMru;
  }
  function _recordInvOpen(name) {
    if (!name) return;
    var mru = _loadInvMru();
    mru[name] = new Date().getTime();
    try { window.localStorage.setItem('viv.invMru', JSON.stringify(mru)); } catch (e) { /* private mode */ }
  }
  function _loadPinnedInvestigations() {
    if (!Array.isArray(window._pinnedInvestigations)) {
      try { window._pinnedInvestigations = JSON.parse(window.localStorage.getItem('viv.pinnedInvestigations') || '[]'); }
      catch (e) { window._pinnedInvestigations = []; }
      if (!Array.isArray(window._pinnedInvestigations)) window._pinnedInvestigations = [];
    }
    return window._pinnedInvestigations;
  }
  function _isInvestigationPinned(name) { return _loadPinnedInvestigations().indexOf(name) !== -1; }
  function _toggleInvestigationPin(name) {
    var pins = _loadPinnedInvestigations();
    var i = pins.indexOf(name);
    if (i === -1) pins.push(name); else pins.splice(i, 1);
    try { window.localStorage.setItem('viv.pinnedInvestigations', JSON.stringify(pins)); } catch (e) { /* private mode */ }
    if (typeof _renderRailInvestigationGroups === 'function') _renderRailInvestigationGroups();
  }
  window._toggleInvestigationPin = _toggleInvestigationPin;

  window._railOpenInvestigationDetail = function (name) {
    _recordInvOpen(name);
    document.querySelectorAll('.page').forEach(function (s) { s.classList.remove('active'); });
    document.querySelectorAll('.menu-link').forEach(function (a) { a.classList.remove('active'); });
    var page = document.getElementById('page-investigations');
    var link = document.querySelector('.menu-link[data-page="investigations"]');
    if (page) page.classList.add('active');
    if (link) link.classList.add('active');
    // Route through the workspace so the graph + objective render inside
    // #ws-context (where _renderInvestigationDetailInto relocates the shared
    // detail-view node), not the retired standalone page-level detail view.
    if (typeof _showInvestigationWorkspace === 'function') _showInvestigationWorkspace(name);
    else if (typeof _openInvestigationDetail === 'function') _openInvestigationDetail(name);
  };

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
    window._ceCurrent = {id: id, overrides: (window._ceIncomingOverrides || {}), run_id: run_id || null};
    window._ceIncomingOverrides = null;  // consumed once (Sim-DB "open this run's config")
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
    fetch('/api/study-create-from-composite', {
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
    fetch(_api('/api/composite-runs?spec_id=' + encodeURIComponent(id)))
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
      return fetch(_api('/api/composite-run/' + encodeURIComponent(id)))
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
    fetch(_api('/api/composite-run/' + encodeURIComponent(run_id)))
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
      var url = _api('/api/composite-resolve?id=' + encodeURIComponent(id) +
        '&overrides=' + encodeURIComponent(JSON.stringify(window._ceCurrent.overrides)));
      // Parse defensively: an unguarded r.json() on a non-2xx / non-JSON
      // response throws "SyntaxError: The string did not match the expected
      // pattern" (Safari) → a useless "Network error". Unregistered refs 404;
      // other errors carry a server {error}/{detail}/{notice} — surface those
      // rather than a hardcoded local-build message.
      p = fetch(url).then(function(r) {
        return r.text().then(function(t) {
          var d = null;
          try { d = t ? JSON.parse(t) : null; } catch (e) { d = null; }
          if (r.ok && d) return d;
          if (r.status === 404) return { unresolved: true, ref: id };
          var msg = (d && (d.error || d.detail || d.notice)) ? (d.error || d.detail || d.notice)
            : ('HTTP ' + r.status + ' — could not resolve this composite.');
          return { error: msg };
        });
      });
    }
    p.then(function(data) {
        // Guard: a null/empty response (e.g. an unexpected miss) is treated as
        // unresolved instead of crashing on ``data.unresolved``.
        data = data || { unresolved: true, ref: id };
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
        if (data.wiring_status === 'unavailable' || data.state == null) {
          // Wiring state is not available (e.g. a generator composite on a local
          // workspace whose build artifact hasn't been produced yet).  Show the
          // server notice as an amber info banner instead of crashing on null
          // state.  The Configure & Run panel is handled separately so the user
          // can still trigger a build run.
          document.getElementById('ce-loading').innerHTML =
            '<div style="color:#92400e;background:#fffbeb;border:1px solid #f59e0b;' +
            'border-radius:6px;padding:10px 14px">ℹ️ ' +
            _esc(data.notice || 'Wiring diagram unavailable for this composite.') +
            '</div>';
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
    fetch(_api('/api/composite-resolve?id=' + encodeURIComponent(ref)))
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
      // apiBase lets the loom locate pre-built inner-composite states
      // (api/composite-inner-state/<key>.json) for the drill-in mini-map in
      // static mode, where the live /api/composite-inner-state has no backend.
      var loomUrl = _snapshotBase + '/bigraph-loom/index.html?static=1&apiBase=' +
        encodeURIComponent(_snapshotBase) + '&stateUrl=' + encodeURIComponent(stateUrl);
      iframe.src = loomUrl;
      iframe.style.display = '';
      // Record the loaded composite so "Pop out" works in snapshot mode. There
      // is no in-memory state to re-post (the loom iframe fetches stateUrl
      // itself) and no live API, so we stash the static loom URL for the popup
      // to open directly. Without this, _popoutLoom finds no _loomLastState
      // entry and falsely reports "No composite loaded in this view yet."
      window._loomLastState = window._loomLastState || {};
      window._loomLastState[iframe.id] = {
        snapshot: true,
        loomUrl: loomUrl,
        metadata: { name: nameHint || ref, library: libraryHint || '', id: ref },
      };
      window._explorerEmitPaths = [];
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
    _confirmRemoteDispatchThen(function () {
      resultsEl.innerHTML = '<p class="empty-state">Starting run&hellip;</p>';
      fetch(_api('/api/composite-test-run'), {
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
    }, function () { resultsEl.innerHTML = '<p class="empty-state">Cancelled.</p>'; });
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
          _openStudyEmbeddedNewTab(name);
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
      : fetch('/api/investigation-summaries', {headers: {Accept: 'application/json'}})
          .then(function(r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); });
    _p
      .then(function(j) {
        window._isetIndex = j.investigations || [];
        _renderInvestigationSets();
        // Resolve the current investigation once, if nothing set it yet:
        // remembered selection (validated against known isets) → the server's
        // `current` flag (branch/running/first) → none (rail shows the chooser).
        if (!window._currentIsetSlug) {
          var _isets = window._isetIndex || [];
          var _persisted = '';
          try { _persisted = window.localStorage.getItem(_railIsetKey()) || ''; } catch (_) { /* ignore */ }
          var _valid = _persisted && _isets.some(function(i) { return i.name === _persisted; });
          var _cur = (_isets.filter(function(i) { return i.current; })[0] || {}).name || '';
          window._currentIsetSlug = _valid ? _persisted : _cur;
        }
        _renderRailInvestigationGroups();
        if (!window._isetIndex.length) return;
        // LIST-FIRST UX: show the cards and let the user pick. Auto-open only
        // when (a) a detail is already open (refresh/deep-link), or (b) there
        // is exactly one investigation (a one-item list is pointless).
        var switchBtn = document.getElementById('investigation-switch-btn');
        if (switchBtn) switchBtn.style.display = window._isetIndex.length > 1 ? '' : 'none';
        // List-first: clicking the Investigations menu always returns to the
        // card list. Auto-open ONLY when there is exactly one investigation
        // (a one-item list is pointless); cards and deep-links open a detail
        // explicitly via _openInvestigationDetail. (Previously this auto-opened
        // the "current" investigation, so the menu never returned to the list.)
        if (window._isetIndex.length === 1) {
          if (typeof _showInvestigationWorkspace === 'function') _showInvestigationWorkspace(window._isetIndex[0].name);
          else _openInvestigationDetail(window._isetIndex[0].name);
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

  // ── Investigations/Studies semantic zoom ────────────────────────────────
  // Same 3-level framework as the Registry: 'table' (dense, sortable-looking)
  // | 'cards' (grid — the historical default) | 'full' (cards with detail
  // expanded). Drives BOTH the Investigations and Studies browse tabs.
  // Persists in localStorage, mirroring window._registryZoom.
  window._isetZoom = (function () {
    var z; try { z = localStorage.getItem('viv.isetZoom'); } catch (e) { z = null; }
    return (z === 'table' || z === 'cards' || z === 'full') ? z : 'cards';
  })();
  function _syncIsetToolbar() {
    // Scoped to [data-izoom] — .reg-zoom-btn is shared with the Registry zoom
    // toolbar (data-zoom), which must not be touched here.
    document.querySelectorAll('.reg-zoom-btn[data-izoom]').forEach(function (b) {
      b.classList.toggle('active', b.getAttribute('data-izoom') === window._isetZoom);
    });
    // Populate + show/hide the column-count control (Cards zoom only).
    if (typeof _syncColsControls === 'function') _syncColsControls();
  }

  // Apply the chosen column count to the visible investigation/study card grids.
  // Only in the Cards zoom — Table has no grid, and Full is a fixed wide layout.
  function _applyIsetCols() {
    if ((window._isetZoom || 'cards') !== 'cards') return;
    _cardContainersFor('isets').forEach(function (c) { _applyCardCols(c, 'isets'); });
  }
  window._syncIsetToolbar = _syncIsetToolbar;
  function _setIsetZoom(z) {
    window._isetZoom = z;
    try { localStorage.setItem('viv.isetZoom', z); } catch (e) { /* private mode */ }
    _syncIsetToolbar();
    _renderInvestigationSets();
  }
  window._setIsetZoom = _setIsetZoom;
  // Double-click a card → zoom in one level (table → cards → full), mirroring
  // the Registry's _zoomInOn.
  function _isetZoomIn() {
    var order = ['table', 'cards', 'full'];
    var i = order.indexOf(window._isetZoom || 'cards');
    _setIsetZoom(order[Math.min(order.length - 1, i + 1)]);
  }
  window._isetZoomIn = _isetZoomIn;

  function _renderInvestigationSets() {
    var list = document.getElementById('investigations-list');
    if (!list) return;
    _syncIsetToolbar();
    if (window._isetBrowseTab === 'studies') {
      if (window._isetZoom === 'table') _renderStudyBrowseTable(list);
      else _renderStudyBrowseCards(list, window._isetZoom === 'full');
      return;
    }
    if (!window._isetIndex.length) {
      list.innerHTML = '<p class="empty-state">No investigations declared. Author one at <code>investigations/&lt;name&gt;/investigation.yaml</code>.</p>';
      return;
    }
    if (window._isetZoom === 'table') {
      _renderInvestigationTable(window._isetIndex, list);
      _filterInvestigations();
      var _icT = document.getElementById('iset-tab-inv-count');
      if (_icT) _icT.textContent = (window._isetIndex || []).length || '';
      var _scT = document.getElementById('iset-tab-study-count');
      if (_scT) _scT.textContent = (window._investigations || []).length || '';
      return;
    }
    // Closed/archived sink to the bottom; baseline floats to the top; else
    // declaration order. The Active/Closed grouping below makes the split visual.
    var ordered = (window._isetIndex || []).map(function(it, idx) { return [it, idx]; });
    ordered.sort(function(a, b) {
      var ac = (a[0].status === 'archived' || a[0].status === 'closed') ? 1 : 0;
      var bc = (b[0].status === 'archived' || b[0].status === 'closed') ? 1 : 0;
      if (ac !== bc) return ac - bc;
      var ab = /baseline/i.test(a[0].name || '') ? 0 : 1;
      var bb = /baseline/i.test(b[0].name || '') ? 0 : 1;
      if (ab !== bb) return ab - bb;
      return a[1] - b[1];
    });

    function _isetCardHtml(iset, full) {
      var closed = (iset.status === 'archived' || iset.status === 'closed');
      var descFull = (iset.description || '').split('\n')[0];
      var desc = full ? descFull : descFull.slice(0, 240);
      // Prefer server effective_status; fall back to author status. Intent
      // divergence goes into the status-pill tooltip (not a separate line).
      var effStatus  = iset.effective_status || iset.status || 'planning';
      var authStatus = iset.status || 'planning';
      // Effective status is derived from the investigation's member studies
      // (server: compute_investigation_status). Human label + color + a tooltip
      // that says what each state actually MEANS — "running" (a study is
      // executing right now) vs "in_progress" (partly done, nothing running)
      // were the confusing pair.
      var STATUS_META = {
        planning:    {label:'Planned',     bg:'#f1f5f9', fg:'#475569', bd:'#cbd5e1', tip:'Not started — every study is still planned.'},
        in_progress: {label:'In progress', bg:'#fef9c3', fg:'#854d0e', bd:'#fde047', tip:'Partly done — some studies have results, but none are running right now.'},
        running:     {label:'Running now', bg:'#dbeafe', fg:'#1e40af', bd:'#93c5fd', tip:'A study is executing right now.'},
        complete:    {label:'Complete',    bg:'#dcfce7', fg:'#166534', bd:'#86efac', tip:'All studies are done.'},
        failed:      {label:'Failed',      bg:'#fee2e2', fg:'#991b1b', bd:'#fca5a5', tip:'A study failed or is invalid — needs attention.'}
      };
      var meta = STATUS_META[effStatus] || {label: effStatus, bg:'#f1f5f9', fg:'#475569', bd:'#cbd5e1', tip:'status: ' + effStatus};
      var statusTip = meta.tip + (authStatus && authStatus !== effStatus ? '  ·  author intent: ' + authStatus : '');
      // "Current branch" is NOT a status — it means this investigation is your
      // current git checkout (what you're working on). Render it as a distinct
      // context chip (indigo outline + branch glyph) so it doesn't read as the
      // green "Complete" status it used to mimic.
      var pillBase = 'font-size:0.72em;border-radius:9999px;padding:1px 9px;display:inline-flex;align-items:center;gap:4px;white-space:nowrap;';
      var currentPill = iset.current
        ? '<span class="iset-here-chip" title="You are working on this investigation — it is the current git branch." style="' + pillBase + 'background:#eef2ff;color:#4338ca;border:1px solid #c7d2fe;font-weight:600">⎇ current branch</span>'
        : '';
      var statusPill = closed
        ? '<span class="status-pill" style="' + pillBase + 'background:#e5e7eb;color:#4b5563;border:1px solid #d1d5db">Closed</span>'
        : '<span class="status-pill" style="' + pillBase + 'background:' + meta.bg + ';color:' + meta.fg + ';border:1px solid ' + meta.bd + '" title="' + _esc(statusTip) + '">' + _esc(meta.label) + '</span>';
      var cardStyle = 'background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:14px 16px;cursor:pointer;transition:box-shadow 0.1s,border-color 0.1s;' +
        (closed ? 'opacity:0.6;' : '');
      var filterStatus = (closed ? 'closed' : effStatus);

      // Per-study status → a compact breakdown line + an expandable study list.
      // Keep the "done" vocabulary in sync with the backend roll-up
      // (_STUDY_STATUS_DONE_ROLLUP): complete/ran/passed/evaluated/decided are all
      // green "done" states, so a passed study never mislabels as "planned".
      var _SD = { complete:['#16a34a','done'], ran:['#16a34a','done'], passed:['#16a34a','passed'],
                  evaluated:['#16a34a','evaluated'], decided:['#16a34a','decided'],
                  running:['#2563eb','running'], analyzing:['#2563eb','running'],
                  in_progress:['#d97706','in progress'], failed:['#dc2626','failed'], invalid:['#dc2626','invalid'],
                  planning:['#94a3b8','planned'] };
      function _sMeta(st) { return _SD[st] || _SD[st === 'ran' ? 'complete' : 'planning'] || ['#94a3b8','planned']; }
      var studyObjs = _isetStudyObjs(iset);
      var byStatus = {};
      studyObjs.forEach(function(s) {
        var st = (s && (s.effective_status || s.status)) || 'planning';
        byStatus[st] = (byStatus[st] || 0) + 1;
      });
      var breakdown = Object.keys(byStatus).sort(function(a, b) {
        return (_statusRank[a] ?? 9) - (_statusRank[b] ?? 9);
      }).map(function(st) {
        var m = _sMeta(st);
        return '<span style="display:inline-flex;align-items:center;gap:4px;white-space:nowrap">' +
          '<span style="width:8px;height:8px;border-radius:50%;background:' + m[0] + '"></span>' +
          byStatus[st] + ' ' + _esc(m[1]) + '</span>';
      }).join('<span style="color:#cbd5e1">·</span>');

      // Expandable study list (revealed by clicking the studies count): each row
      // pulls the study's objective text + the consistent action set — downloads
      // (↓ figures / ↓ notebook, all modes) and, live only, ▶ run / ↻ reproduce.
      var _isSnap = (window.__DASH_CONFIG__ || {}).mode === 'snapshot';
      var studyRows = studyObjs.map(function(s) {
        var m = _sMeta((s && (s.effective_status || s.status)) || 'planning');
        var slug = (s && s.name) || '';
        var title = (s && s.title) ? String(s.title) : '';
        var obj = (s && (s.objective || s.description)) ? String(s.objective || s.description) : '';
        var objShort = obj ? (obj.length > 150 ? obj.slice(0, 150).replace(/\s+\S*$/, '') + '…' : obj) : '';
        var lnk = 'font-size:0.82em;color:#3b82f6;text-decoration:none;white-space:nowrap;cursor:pointer';
        // Card rows carry the DOWNLOADS only (↓ figures / ↓ notebook). The
        // run/reproduce launch actions live on the study tab's header
        // (▶ Run current spec / ↻ Reproduce), not here — so a card stays a
        // browse+download surface.
        var acts =
          '<a href="#" style="' + lnk + '" title="Download this study\'s figures (and embedded HTML reports) as a zip" ' +
            'onclick="window._vivStudyFiguresFromCard(event,\'' + _esc(slug) + '\');return false;">↓ figures</a>' +
          '<a href="#" style="' + lnk + '" title="Download this study\'s own runnable notebook (composite + parameters + figures)" ' +
            'onclick="window._vivStudyNotebookFromCard(event,\'' + _esc(slug) + '\',\'' + _esc(iset.name) + '\');return false;">↓ notebook</a>';
        return '<div class="iset-study-row" style="padding:6px;border-radius:5px" ' +
          'onmouseover="this.style.background=\'#f8fafc\'" onmouseout="this.style.background=\'\'">' +
          '<div style="display:flex;align-items:center;gap:8px">' +
            '<span style="width:7px;height:7px;border-radius:50%;background:' + m[0] + '"></span>' +
            '<a href="/studies/' + encodeURIComponent(slug) + '" onclick="event.stopPropagation()" style="text-decoration:none">' +
              '<code style="font-size:0.92em;color:#475569">' + _esc(slug) + '</code></a>' +
            (title ? '<span style="font-size:0.86em;color:#334155">' + _esc(title) + '</span>' : '') +
            '<span style="margin-left:auto;color:#94a3b8;font-size:0.82em">' + _esc(m[1]) + '</span>' +
          '</div>' +
          (objShort ? '<div style="font-size:0.8em;color:#64748b;margin:2px 0 0 15px;line-height:1.35">' + _esc(objShort) + '</div>' : '') +
          '<div style="display:flex;gap:14px;margin:4px 0 0 15px">' + acts + '</div>' +
        '</div>';
      }).join('');

      var qFull = iset.question ? String(iset.question).split('\n')[0] : '';
      var qLine = iset.question
        ? '<p style="margin:0 0 6px 0;font-size:0.9em;color:#334155"><span style="color:#94a3b8;font-weight:600">Q</span> ' + _esc(full ? qFull : qFull.slice(0, 200)) + '</p>'
        : (desc ? '<p style="margin:0 0 6px 0;font-size:0.9em;color:#475569">' + _esc(desc) + (!full && iset.description.length > 240 ? '…' : '') + '</p>' : '');
      var lifeChip = iset.lifecycle && iset.lifecycle !== 'active'
        ? '<span style="font-size:0.72em;color:#64748b;background:#f1f5f9;border-radius:9999px;padding:1px 8px">' + _esc(iset.lifecycle) + '</span>' : '';

      return '<div class="investigation-set-card' + (full ? ' iset-card-full' : '') + (iset.read_only ? ' federated-readonly' : '') + '" onclick="_showInvestigationWorkspace(\'' + _esc(iset.name) + '\')" ondblclick="_isetZoomIn()" ' +
             'title="' + _esc(iset.name) + '" ' +
             'data-iset-title="' + _esc(String(iset.title || iset.name).toLowerCase()) + '" ' +
             'data-iset-slug="' + _esc(String(iset.name).toLowerCase()) + '" ' +
             'data-iset-status="' + _esc(String(filterStatus).toLowerCase()) + '" ' +
             'style="' + cardStyle + '">' +
        '<div style="display:flex;align-items:baseline;gap:6px 10px;flex-wrap:wrap;margin-bottom:6px;">' +
          '<strong style="font-size:1.05em;flex:1 1 100%">' + _esc(iset.title || iset.name) + '</strong>' +
          currentPill +
          statusPill +
          _originBadge(iset.origin_repo) +
        '</div>' +
        qLine +
        (breakdown ? '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:0.82em;color:#64748b;margin:0 0 8px">' + breakdown + (lifeChip ? '<span style="margin-left:auto">' + lifeChip + '</span>' : '') + '</div>' : '') +
        '<div style="display:flex;align-items:center;gap:12px;font-size:0.85em;color:#64748b">' +
          '<span class="iset-studies-toggle" role="button" tabindex="0" ' +
            'onclick="event.stopPropagation();var d=this.closest(\'.investigation-set-card\').querySelector(\'.iset-studies-detail\');var open=d.style.display===\'none\';d.style.display=open?\'block\':\'none\';this.querySelector(\'.iset-chev\').textContent=open?\'▾\':\'▸\'" ' +
            'style="flex:1;cursor:pointer;user-select:none"><strong>' + iset.n_studies + '</strong> stud' + (iset.n_studies === 1 ? 'y' : 'ies') + ' <span class="iset-chev" style="color:#94a3b8">' + (full ? '▾' : '▸') + '</span></span>' +
          '<a href="#" title="Download the rendered HTML report for this investigation" ' +
            'onclick="window._vivReportFromCard(event,\'' + _esc(iset.name) + '\');return false;" ' +
            'style="color:#3b82f6;text-decoration:none;white-space:nowrap">↓ report</a>' +
          '<a href="#" title="Download the runnable notebook for this investigation" ' +
            'onclick="window._vivNotebookFromCard(event,\'' + _esc(iset.name) + '\');return false;" ' +
            'style="color:#3b82f6;text-decoration:none;white-space:nowrap">↓ notebook</a>' +
          (iset.n_figures ? '<a href="#" title="Download all figures for this investigation (studies figures + post-study composites), as a zip" ' +
            'onclick="window._vivFiguresFromCard(event,\'' + _esc(iset.name) + '\');return false;" ' +
            'style="color:#3b82f6;text-decoration:none;white-space:nowrap">↓ figures</a>' : '') +
        '</div>' +
        '<div class="iset-studies-detail" style="display:' + (full ? 'block' : 'none') + ';margin-top:8px;border-top:1px solid #f1f5f9;padding-top:6px">' + (studyRows || '<span class="muted" style="font-size:0.85em">No studies.</span>') + '</div>' +
        // "Run this investigation in your terminal" chip (like the composite/process card).
        _runCmdChip(iset.run_command || ('vwb run investigation ' + iset.name)) +
      '</div>';
    }

    var GRID = 'display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:12px;margin:6px 0 14px';
    var _isetFull = (window._isetZoom === 'full');
    function _groupHtml(label, items) {
      if (!items.length) return '';
      return '<div class="iset-group" data-group-label="' + label + '">' +
        '<h3 class="iset-group-head" style="font-size:0.9em;color:#475569;font-weight:700;margin:10px 0 2px;text-transform:uppercase;letter-spacing:0.04em">' +
          label + ' <span class="iset-group-count" style="color:#94a3b8;font-weight:600">(' + items.length + ')</span></h3>' +
        '<div class="investigations-grid" style="' + GRID + '">' +
          items.map(function (iset) { return _isetCardHtml(iset, _isetFull); }).join('') +
        '</div>' +
      '</div>';
    }

    var active = [], closedItems = [];
    ordered.forEach(function(pair) {
      var iset = pair[0];
      if (iset.status === 'archived' || iset.status === 'closed') closedItems.push(iset);
      else active.push(iset);
    });

    // User sort (applied WITHIN the Active/Closed grouping so the split stays).
    var _sortMode = window._isetSort || 'default';
    var _statusRank = { running: 0, in_progress: 1, planning: 2, failed: 3, complete: 4, closed: 5, archived: 5 };
    function _isetUpdatedAt(iset) {
      // Most-recent member-study run time (empty for never-run).
      var t = '';
      _isetStudyObjs(iset).forEach(function(s) {
        var st = s && (s.last_run || s.updated_at || '');
        if (st && st > t) t = st;
      });
      return t;
    }
    function _sortIsets(items) {
      if (_sortMode === 'default') return items;
      return items.slice().sort(function(a, b) {
        if (_sortMode === 'name')
          return String(a.title || a.name).localeCompare(String(b.title || b.name));
        if (_sortMode === 'status')
          return (_statusRank[a.effective_status || a.status] ?? 9) -
                 (_statusRank[b.effective_status || b.status] ?? 9) ||
                 String(a.title || a.name).localeCompare(String(b.title || b.name));
        if (_sortMode === 'studies_desc') return (b.n_studies || 0) - (a.n_studies || 0);
        if (_sortMode === 'studies_asc') return (a.n_studies || 0) - (b.n_studies || 0);
        if (_sortMode === 'recent') return _isetUpdatedAt(b).localeCompare(_isetUpdatedAt(a));
        return 0;
      });
    }

    list.innerHTML =
      _groupHtml('Active', _sortIsets(active)) +
      _groupHtml('Closed', _sortIsets(closedItems)) +
      '<p id="investigations-empty" class="empty-state" style="display:none">No investigations match the filter.</p>';

    _applyIsetCols();
    _filterInvestigations();

    var _ic = document.getElementById('iset-tab-inv-count');
    if (_ic) _ic.textContent = (window._isetIndex || []).length || '';
    var _sc = document.getElementById('iset-tab-study-count');
    if (_sc) _sc.textContent = (window._investigations || []).length || '';
  }

  // Member study objects for an investigation (from the client studies index).
  function _isetStudyObjs(iset) {
    return ((iset && iset.studies) || [])
      .map(function(slug) {
        return (window._investigations || []).find(function(s) { return s.name === slug; })
          || { name: slug };
      });
  }

  function _setIsetSort(value) {
    window._isetSort = value;
    _renderInvestigationSets();
  }
  window._setIsetSort = _setIsetSort;
  window._isetStudyObjs = _isetStudyObjs;

  // Flip the browse grid between the Investigations and the flat Studies view,
  // sharing the same search + sort + card look.
  function _setIsetBrowseTab(tab) {
    window._isetBrowseTab = tab;
    document.querySelectorAll('.iset-browse-tab').forEach(function (b) {
      // Styling comes from the shared .registry-tab / .registry-tab.active CSS
      // (same as the Modules/Registry tabs) — just toggle the class.
      b.classList.toggle('active', b.getAttribute('data-browse') === tab);
    });
    var createBtn = document.getElementById('iset-browse-create');
    if (createBtn) createBtn.textContent = (tab === 'studies') ? '+ Study' : '+ Investigation';
    // The zoom toolbar (#iset-zoom-toolbar) is always visible on both tabs —
    // only the "click a card's studies count" tip is Studies-only.
    var tip = document.getElementById('iset-list-tip');
    if (tip) tip.style.display = (tab === 'studies') ? 'none' : '';
    var invCount = document.getElementById('iset-tab-inv-count');
    var studyCount = document.getElementById('iset-tab-study-count');
    if (invCount) invCount.textContent = (window._isetIndex || []).length || '';
    if (studyCount) studyCount.textContent = (window._investigations || []).length || '';
    _syncIsetToolbar();
    _renderInvestigationSets();
  }
  window._setIsetBrowseTab = _setIsetBrowseTab;

  // Two-surface split: #iset-explore (browse) vs #iset-workspace (viewing an
  // investigation + its studies). Toggle hides/shows — never destroys — each
  // surface's DOM so workspace state (open study, tab, scroll) survives a
  // round trip back to Explore.
  function _showExplore() {
    var ex = document.getElementById('iset-explore');
    var ws = document.getElementById('iset-workspace');
    if (ex) ex.style.display = '';
    if (ws) ws.style.display = 'none';
    // #investigations-list is the shared card grid for both the Investigations
    // and Studies tabs of Explore. _openInvestigationDetail hides it (legacy
    // single-surface behavior); restore it here so the "All investigations"
    // back button doesn't land on a blank grid. Cards are still in the DOM
    // (only display was toggled), so a display restore is sufficient — no
    // re-render needed.
    var list = document.getElementById('investigations-list');
    if (list) list.style.display = '';
  }
  window._showExplore = _showExplore;

  function _showWorkspace() {
    // The workspace surface (#iset-workspace / #ws-context) lives inside
    // #page-investigations. Activate that host page first — mirrors
    // _railOpenInvestigationDetail's manual page/menu activation — so callers
    // that land here from another page (e.g. the rail's study sublinks, or a
    // leftover _switchPage('studies') call) don't render the workspace onto
    // a hidden page.
    document.querySelectorAll('.page').forEach(function (s) { s.classList.remove('active'); });
    document.querySelectorAll('.menu-link').forEach(function (a) { a.classList.remove('active'); });
    var hostPage = document.getElementById('page-investigations');
    var hostLink = document.querySelector('.menu-link[data-page="investigations"]');
    if (hostPage) hostPage.classList.add('active');
    if (hostLink) hostLink.classList.add('active');

    var ex = document.getElementById('iset-explore');
    var ws = document.getElementById('iset-workspace');
    if (ex) ex.style.display = 'none';
    if (ws) ws.style.display = '';
  }
  window._showWorkspace = _showWorkspace;

  // Investigation context toggle: expanded (#ws-context — graph + objective)
  // vs the slim collapsed bar (#ws-context-bar, "▸ Investigation: <name>").
  // Toggle via style.display; DOM is retained either way. Clicking the slim
  // bar (wired in the template) re-expands via collapsed=false.
  function _setInvestigationContextCollapsed(collapsed) {
    window._wsContextCollapsed = !!collapsed;
    var ctx = document.getElementById('ws-context');
    var bar = document.getElementById('ws-context-bar');
    var name = document.getElementById('ws-context-bar-name');
    if (ctx) ctx.style.display = collapsed ? 'none' : '';
    if (bar) bar.style.display = collapsed ? '' : 'none';
    if (name) name.textContent = window._wsInvestigation || '';
  }
  window._setInvestigationContextCollapsed = _setInvestigationContextCollapsed;

  // Study-tabs manager: accumulating, closeable study tabs inside the
  // investigation workspace (#ws-study-tabs bar -> #ws-study-panel/#ws-study-frame
  // porthole). Opening a study collapses the investigation context down to the
  // slim bar; closing the last open tab returns to graph-only.
  window._wsStudyTabs = { investigation: null, openTabs: [], active: null };

  function _wsResetStudyTabs(investigation) {
    window._wsStudyTabs = { investigation: investigation, openTabs: [], active: null };
    _wsRenderStudyTabs();
    var panel = document.getElementById('ws-study-panel');
    if (panel) panel.style.display = 'none';
    _setInvestigationContextCollapsed(false);   // fresh investigation -> graph expanded
  }
  window._wsResetStudyTabs = _wsResetStudyTabs;

  function _wsRenderStudyTabs() {
    var bar = document.getElementById('ws-study-tabs');
    if (!bar) return;
    var st = window._wsStudyTabs;
    if (!st.openTabs.length) { bar.style.display = 'none'; bar.innerHTML = ''; return; }
    bar.style.display = '';
    bar.innerHTML = st.openTabs.map(function (slug) {
      var on = slug === st.active;
      return '<span class="ws-study-tab" data-ws-tab="' + _esc(slug) + '" ' +
        'style="display:inline-flex;align-items:center;gap:6px;padding:6px 10px;cursor:pointer;' +
        'border-bottom:2px solid ' + (on ? '#3b82f6' : 'transparent') + ';' +
        'color:' + (on ? '#0f172a' : '#64748b') + ';font-weight:' + (on ? '600' : '400') + ';margin-bottom:-1px">' +
        '<span onclick="_wsOpenStudyTab(\'' + _esc(slug) + '\')">' + _esc(slug) + '</span>' +
        '<span onclick="event.stopPropagation();_wsCloseStudyTab(\'' + _esc(slug) + '\')" ' +
        'title="close" style="color:#94a3b8;font-weight:700">×</span></span>';
    }).join('');
  }
  window._wsRenderStudyTabs = _wsRenderStudyTabs;

  function _wsOpenStudyTab(slug, tab) {
    var st = window._wsStudyTabs;
    if (st.openTabs.indexOf(slug) === -1) st.openTabs.push(slug);
    st.active = slug;
    _wsRenderStudyTabs();
    var panel = document.getElementById('ws-study-panel');
    var frame = document.getElementById('ws-study-frame');
    if (panel) panel.style.display = '';
    if (frame) {
      var href = _studyHref(slug);
      if (tab) href += (href.indexOf('?') >= 0 ? '&' : '?') + 'tab=' + encodeURIComponent(tab);
      frame.src = href;
    }
    // Keep the investigation context (overview + knowledge graph) EXPANDED above
    // the study instead of collapsing it to the slim bar, and grow the study
    // porthole to its content height, so the whole workspace is one continuous
    // scroll: land on the study, scroll up into the graph and past it to the
    // overview (user request).
    _setInvestigationContextCollapsed(false);
    // Open a landing window BEFORE sizing: while active, content-driven refits
    // skip their scroll-restore so they can't cancel the scroll-to-study below.
    var _HOLD_MS = 1800;
    window._embedLandingUntil = Date.now() + _HOLD_MS;
    // Floor the study porthole at the scroll container's visible height so the
    // study FILLS the view on open instead of sitting short under the (often
    // tall) investigation graph. With a full-viewport porthole, landing on the
    // study scrolls the graph fully off the top — scroll up to bring it back.
    var _scroller = document.querySelector('.viv-content');
    var _vh = (_scroller && _scroller.clientHeight) || window.innerHeight || 800;
    var _floor = Math.max(560, _vh - 8);
    if (typeof _fitEmbedToContent === 'function') _fitEmbedToContent(frame, _floor);
    else if (typeof _fitEmbedToViewport === 'function') _fitEmbedToViewport(frame, panel, _floor);
    // Land on the study AND actively HOLD it there. A one-shot smooth scroll
    // wasn't enough: the investigation graph / About block re-renders (and the
    // iframe refits) AFTER the scroll, springing the view back up to the top.
    // So for a short window we re-land whenever the study gets pushed back down
    // the page — releasing immediately on the first genuine user scroll so the
    // user can freely scroll up into the graph.
    if (panel && panel.scrollIntoView) {
      var _released = false, _lastAssert = 0, _holdUntil = Date.now() + _HOLD_MS;
      var _release = function () { _released = true; };
      window.addEventListener('wheel', _release, { passive: true, once: true });
      window.addEventListener('touchmove', _release, { passive: true, once: true });
      window.addEventListener('keydown', function (e) {
        if (['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].indexOf(e.key) !== -1) _release();
      }, { once: true });
      var _tick = function () {
        if (_released || !panel.isConnected) return;
        var now = Date.now();
        // panel.top well below the viewport top ⇒ the view sprang back up to the
        // graph; re-land (throttled so smooth animations don't stack).
        if (panel.getBoundingClientRect().top > 96 && now - _lastAssert > 240) {
          _lastAssert = now;
          try { panel.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (_) {}
        }
        if (now < _holdUntil) requestAnimationFrame(_tick);
        else {
          window.removeEventListener('wheel', _release);
          window.removeEventListener('touchmove', _release);
        }
      };
      var _landed = false;
      var _land = function () {
        if (_landed || !panel.isConnected) return;
        _landed = true;
        try { panel.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (_) {}
        requestAnimationFrame(_tick);   // start the hold once we've landed
      };
      if (frame) frame.addEventListener('load', function () { setTimeout(_land, 80); }, { once: true });
      setTimeout(_land, 600);   // fallback if load already fired or never fires
      // After the hold window, one final content-fit (suppressed during it) so
      // the porthole ends at its true height, preserving the landed position.
      if (frame) setTimeout(function () { if (frame._refit) frame._refit(); }, _HOLD_MS + 150);
    }
  }
  window._wsOpenStudyTab = _wsOpenStudyTab;

  function _wsCloseStudyTab(slug) {
    var st = window._wsStudyTabs;
    var wasActive = (st.active === slug);
    var i = st.openTabs.indexOf(slug);
    if (i !== -1) st.openTabs.splice(i, 1);
    if (wasActive) st.active = st.openTabs[Math.max(0, i - 1)] || st.openTabs[0] || null;
    _wsRenderStudyTabs();
    if (!wasActive) return;                      // background tab closed -> porthole untouched
    if (st.active) {
      _wsOpenStudyTab(st.active);                // re-focus nearest remaining tab
    } else {
      var panel = document.getElementById('ws-study-panel');
      if (panel) panel.style.display = 'none';
      _setInvestigationContextCollapsed(false);  // last tab closed -> graph-only
    }
  }
  window._wsCloseStudyTab = _wsCloseStudyTab;

  // Task 5: render an investigation's graph + objective into an arbitrary mount
  // (the workspace #ws-context). Reuses the v3 detail renderer verbatim by
  // relocating the shared #investigation-detail-view subtree (title/objective
  // "About" block, needs-attention, and the interactive study DAG) into the
  // mount, then letting the existing async _openInvestigationDetail populate it
  // in place. This is the graph + objective source for BOTH v3 AND v2-shape
  // specs — it is NEVER the legacy #investigation-detail icon-tab view.
  function _renderInvestigationDetailInto(name, mountEl) {
    if (!name || !mountEl) return;
    var view = document.getElementById('investigation-detail-view');
    if (view) {
      if (view.parentNode !== mountEl) mountEl.appendChild(view);
      view.style.display = '';
      // The workspace header (#ws-title/#ws-status/#ws-actions) is the single
      // chrome now; hide the relocated view's duplicate back-link + title row.
      var back = view.querySelector('.iset-back-link');
      if (back) back.style.display = 'none';
      var titleEl = document.getElementById('investigation-detail-title');
      if (titleEl && titleEl.parentNode) titleEl.parentNode.style.display = 'none';
    }
    if (typeof _openInvestigationDetail === 'function') _openInvestigationDetail(name);
  }
  window._renderInvestigationDetailInto = _renderInvestigationDetailInto;

  // Report / Notebook actions in the workspace header (#ws-actions), moved off
  // the detail-view header (hidden by _renderInvestigationDetailInto).
  function _wsSetInvestigationActions() {
    var actions = document.getElementById('ws-actions');
    if (!actions) return;
    var isSnapshot = (window.__DASH_CONFIG__ || {}).mode === 'snapshot';
    var name = window._wsInvestigation || window._currentIset || '';
    // Match the investigation CARD's ↓ actions (↓ report / ↓ notebook / ↓ figures)
    // instead of the old emoji buttons. ↓ figures ALWAYS shows here (so the
    // affordance is discoverable) but starts DISABLED/greyed; the async summary
    // upgrades it to an active download when the investigation actually has
    // figures (same n_figures signal as the card).
    var _figuresDisabled =
      ' <button class="btn-mini" disabled ' +
        'title="No figures yet — run this investigation\'s studies to generate them" ' +
        'style="opacity:0.5;cursor:not-allowed">↓ figures</button>';
    actions.innerHTML =
      '<button class="btn-mini" onclick="_downloadInvestigationReport()" ' +
        'title="Download the shareable HTML report">↓ report</button> ' +
      '<button class="btn-mini" onclick="_downloadInvestigationNotebook()" ' +
        'title="Download a self-contained Jupyter notebook">↓ notebook</button>' +
      '<span id="ws-actions-figures">' + _figuresDisabled + '</span>' +
      (isSnapshot ? '' :
      ' <button class="btn-mini" onclick="_rerunInvestigation()" ' +
        'title="Re-run every member study\'s CURRENT baseline spec (re-derives from each study\'s study.yaml)">▶ Run current spec</button>');
    if (name) {
      fetch(_api('/api/investigation-summaries'), {headers: {Accept: 'application/json'}})
        .then(function (r) { return r.json(); })
        .then(function (j) {
          var me = ((j && j.investigations) || []).filter(function (i) { return i.name === name; })[0];
          var host = document.getElementById('ws-actions-figures');
          if (me && me.n_figures && host) {
            host.innerHTML = ' <button class="btn-mini" ' +
              'onclick="window._vivFiguresFromCard(event,\'' + _esc(name) + '\')" ' +
              'title="Download all figures (studies figures + post-study composites) as a zip">↓ figures</button>';
          }
          // else: leave the disabled/greyed ↓ figures in place.
        }).catch(function () {});
    }
  }
  window._wsSetInvestigationActions = _wsSetInvestigationActions;

  // Task 5: the investigation workspace render — replacement for the legacy
  // focus-mode icon view. Always shows the study's OWN investigation graph +
  // objective, expanded, with an empty study-tabs bar and a hidden porthole.
  function _showInvestigationWorkspace(name) {
    if (!name) return;
    window._wsInvestigation = name;
    _showWorkspace();
    var title = document.getElementById('ws-title');
    if (title) title.textContent = 'Investigation: ' + name;
    var status = document.getElementById('ws-status');
    if (status) { status.textContent = ''; status.className = ''; }
    // Always the graph + objective detail — NOT the legacy "Study:<inv>" icon view.
    var ctx = document.getElementById('ws-context');
    if (ctx) _renderInvestigationDetailInto(name, ctx);
    _wsResetStudyTabs(name);   // graph expanded, no study open
    _wsSetInvestigationActions();   // Report/Notebook actions in #ws-actions
  }
  window._showInvestigationWorkspace = _showInvestigationWorkspace;

  // Prompt-first create: a free-text description scaffolds a real investigation /
  // study seeded with that as the question, name auto-derived (editable).
  // Open the reproducibility audit report (latest cached; the page itself has a
  // "Re-run audit" link → ?rerun=1). New tab so it doesn't disturb the SPA.
  function _openAuditReport() {
    // window.open() bypasses the fetch/XHR/EventSource base-path shim — prefix
    // explicitly (same gap as sim-table.js's per-run artifact links).
    window.open((window.__BASE_PATH__ || "") + '/api/audit-report', '_blank', 'noopener');
  }
  window._openAuditReport = _openAuditReport;

  // mode: 'investigation' | 'study' (explicit from the header cluster); falls
  // back to the active browse tab when omitted.
  function _openBrowseCreate(mode) {
    var isStudy = mode ? (mode === 'study') : (window._isetBrowseTab === 'studies');
    window._browseCreateMode = isStudy ? 'study' : 'investigation';
    document.getElementById('browse-create-title').textContent = isStudy ? 'New study' : 'New investigation';
    document.getElementById('browse-create-submit').textContent = isStudy ? 'Create study' : 'Create investigation';
    document.getElementById('browse-create-prompt-label').textContent = isStudy
      ? 'Describe the study — the question you want to answer'
      : 'Describe the investigation — the question you want to answer';
    document.getElementById('browse-create-inv-row').style.display = isStudy ? '' : 'none';
    if (isStudy) {
      var sel = document.getElementById('browse-create-inv');
      sel.innerHTML = (window._isetIndex || []).map(function (i) {
        return '<option value="' + _esc(i.name) + '">' + _esc(i.title || i.name) + '</option>';
      }).join('');
    }
    var form = document.getElementById('form-browse-create');
    form.reset();
    var nameInput = form.querySelector('[name=name]');
    if (nameInput) nameInput._touched = false;
    form.querySelector('.form-error').textContent = '';
    openModal('modal-browse-create');
  }
  window._openBrowseCreate = _openBrowseCreate;

  function _browseCreateSuggestName(ta) {
    var nameInput = ta.form.querySelector('[name=name]');
    if (!nameInput || nameInput._touched) return;   // don't clobber a manual edit
    nameInput.value = String(ta.value).toLowerCase()
      .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
      .split('-').filter(Boolean).slice(0, 6).join('-');
  }
  window._browseCreateSuggestName = _browseCreateSuggestName;

  function _submitBrowseCreate(form) {
    var data = new FormData(form);
    var name = String(data.get('name') || '').trim();
    var prompt = String(data.get('prompt') || '').trim();
    var errEl = form.querySelector('.form-error');
    if (!name) { errEl.textContent = 'Name required.'; return; }
    var post = function (url, body) {
      return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
        .then(function (r) { return r.json().then(function (j) { return [r.ok, j]; }); });
    };
    if (window._browseCreateMode === 'investigation') {
      post('/api/investigation-create', { name: name, overview: prompt, question: prompt })
        .then(function (p) {
          if (!p[0]) { errEl.textContent = p[1].error || 'Create failed.'; return; }
          closeModal('modal-browse-create');
          window._investigationsLoaded = false;
          if (typeof _loadInvestigations === 'function') _loadInvestigations();
          if (typeof _vivOpenInvestigationFromRail === 'function') _vivOpenInvestigationFromRail(name);
        });
    } else {
      var inv = String(data.get('investigation') || '');
      post('/api/study-create', { name: name, investigation: inv, question: prompt })
        .then(function (p) {
          if (!p[0]) { errEl.textContent = p[1].error || 'Create failed.'; return; }
          var created = (p[1] && p[1].name) || name;
          // Seed the question on the scaffolded study (best-effort).
          post('/api/study-narrative-set', { study: created, path: 'purpose.question', value: prompt })
            .catch(function () {}).then(function () {
              closeModal('modal-browse-create');
              window._investigationsLoaded = false;
              if (typeof _loadInvestigations === 'function') _loadInvestigations();
              _openStudyEmbeddedNewTab(created);
            });
        });
    }
  }
  window._submitBrowseCreate = _submitBrowseCreate;

  // Status dot vocab shared by the study cards + breakdowns.
  var _STUDY_DOT = {
    complete: ['#16a34a', 'done'], ran: ['#16a34a', 'done'],
    running: ['#2563eb', 'running'], in_progress: ['#d97706', 'in progress'],
    failed: ['#dc2626', 'failed'], planning: ['#94a3b8', 'planned'],
    planned: ['#94a3b8', 'planned'],
  };
  function _studyDotMeta(st) { return _STUDY_DOT[st] || _STUDY_DOT.planned; }

  function _studyBrowseCardHtml(s, full) {
    var status = s.effective_status || s.status || 'planned';
    var m = _studyDotMeta(status);
    var inv = _investigationForStudy(s.name);
    var q = s.question || s.objective || '';
    var qText = String(q).split('\n')[0];
    var nRuns = (s.n_runs !== undefined) ? s.n_runs
              : (s.n_simulations !== undefined ? s.n_simulations : 0);
    var cardStyle = 'background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:14px 16px;cursor:pointer;transition:box-shadow 0.1s,border-color 0.1s;';
    var partOf = (s.investigations && s.investigations.length)
      ? '<div style="font-size:0.78em;color:#94a3b8;margin:0 0 6px">part of: ' + _esc(s.investigations.join(', ')) + '</div>'
      : '';
    return '<div class="investigation-set-card' + (full ? ' iset-card-full' : '') + (s.read_only ? ' federated-readonly' : '') + '" onclick="_openStudyEmbeddedNewTab(\'' + _esc(s.name) + '\')" ondblclick="_isetZoomIn()" ' +
           'title="' + _esc(s.name) + '" ' +
           'data-iset-title="' + _esc(String(s.title || s.name).toLowerCase()) + '" ' +
           'data-iset-slug="' + _esc(String(s.name).toLowerCase()) + '" ' +
           'data-iset-status="' + _esc(String(status).toLowerCase()) + '" ' +
           'style="' + cardStyle + '">' +
      '<div style="display:flex;align-items:baseline;gap:6px 10px;flex-wrap:wrap;margin-bottom:6px;">' +
        '<strong style="font-size:1.02em;flex:1 1 100%">' + _esc(s.title || s.name) + '</strong>' +
        '<span style="font-size:0.72em;border-radius:9999px;padding:1px 9px;white-space:nowrap;' +
          'background:' + m[0] + '22;color:' + m[0] + ';border:1px solid ' + m[0] + '55">' + _esc(m[1]) + '</span>' +
        _originBadge(s.origin_repo) +
      '</div>' +
      (inv ? '<div style="font-size:0.78em;color:#94a3b8;margin:0 0 6px"><span style="color:#cbd5e1">▪</span> ' + _esc(inv) + '</div>' : '') +
      partOf +
      (q ? '<p style="margin:0 0 8px 0;font-size:0.9em;color:#334155"><span style="color:#94a3b8;font-weight:600">Q</span> ' + _esc(full ? qText : qText.slice(0, 180)) + '</p>' : '') +
      '<div style="display:flex;align-items:center;gap:12px;font-size:0.85em;color:#64748b">' +
        '<span style="flex:1"><strong>' + nRuns + '</strong> run' + (nRuns === 1 ? '' : 's') + '</span>' +
        '<span style="color:#3b82f6">open ↗</span>' +
      '</div>' +
      // "Run this study in your terminal" chip (like the composite/process card).
      _runCmdChip(s.run_command || ('vwb run study ' + s.name)) +
    '</div>';
  }

  function _renderStudyBrowseCards(list, full) {
    var studies = (window._investigations || []).slice();
    if (!studies.length) {
      list.innerHTML = '<p class="empty-state">No studies in this workspace yet.</p>';
      return;
    }
    var sort = window._isetSort || 'default';
    var rank = { running: 0, in_progress: 1, planning: 2, planned: 2, failed: 3, complete: 4, ran: 4 };
    var byStatus = function (s) { return rank[s.effective_status || s.status] ?? 9; };
    var cmp = function (a, b) {
      var an = String(a.title || a.name), bn = String(b.title || b.name);
      if (sort === 'status') return byStatus(a) - byStatus(b) || an.localeCompare(bn);
      if (sort === 'studies_desc' || sort === 'recent') return (b.n_runs || 0) - (a.n_runs || 0) || an.localeCompare(bn);
      if (sort === 'studies_asc') return (a.n_runs || 0) - (b.n_runs || 0) || an.localeCompare(bn);
      return an.localeCompare(bn);
    };
    // Bucket studies by their investigation (ordered by _isetIndex; leftovers last).
    var groups = {};
    studies.forEach(function (s) {
      var inv = _investigationForStudy(s.name) || '__ungrouped__';
      (groups[inv] = groups[inv] || []).push(s);
    });
    var order = (window._isetIndex || []).map(function (i) { return i.name; })
      .filter(function (n) { return groups[n]; });
    if (groups.__ungrouped__) order.push('__ungrouped__');
    var GRID = 'display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px;margin:6px 0 14px';
    var titleFor = function (inv) {
      if (inv === '__ungrouped__') return 'Ungrouped';
      var it = (window._isetIndex || []).find(function (i) { return i.name === inv; });
      return (it && (it.title || it.name)) || inv;
    };
    list.innerHTML = order.map(function (inv) {
      var items = groups[inv].slice().sort(cmp);
      return '<div class="iset-group" data-study-group="' + _esc(inv) + '">' +
        '<h3 class="iset-group-head" style="font-size:0.9em;color:#475569;font-weight:700;margin:10px 0 2px;text-transform:uppercase;letter-spacing:0.04em">' +
        _esc(titleFor(inv)) + ' <span style="color:#94a3b8;font-weight:600">(' + items.length + ')</span></h3>' +
        '<div class="investigations-grid" style="' + GRID + '">' +
        items.map(function (s) { return _studyBrowseCardHtml(s, full); }).join('') + '</div></div>';
    }).join('') +
      '<p id="investigations-empty" class="empty-state" style="display:none">No studies match the filter.</p>';
    _applyIsetCols();
    _filterInvestigations();
  }

  // Investigation title for a slug (Studies table's Investigation column).
  function _isetTitleForSlug(inv) {
    if (!inv) return 'Ungrouped';
    var it = (window._isetIndex || []).find(function (i) { return i.name === inv; });
    return (it && (it.title || it.name)) || inv;
  }
  function _fmtStudyDate(iso) {
    if (!iso) return '<span style="color:#cbd5e1">—</span>';
    return _esc(String(iso).slice(0, 10));   // YYYY-MM-DD
  }

  // Studies TABLE view — one row per study, sortable by ANY column (including a
  // real last-run date), row-click opens the study in the workspace. This is the
  // flat, globally-sortable counterpart to the investigation-grouped cards.
  function _renderStudyBrowseTable(list) {
    var studies = (window._investigations || []).slice();
    if (!studies.length) { list.innerHTML = '<p class="empty-state">No studies in this workspace yet.</p>'; return; }
    var sort = window._isetTableSort || { col: 'investigation', dir: 1 };
    var runsOf = function (s) { return (s.n_runs != null) ? s.n_runs : (s.n_simulations || 0); };
    var val = function (s, col) {
      if (col === 'investigation') return _investigationForStudy(s.name) || 'zzz~ungrouped';
      if (col === 'status') return String(s.effective_status || s.status || '');
      if (col === 'phase') return String(s.phase || '');
      if (col === 'runs') return runsOf(s);
      if (col === 'last_run') return String(s.last_run || '');
      if (col === 'composite') return String(s.composite || '');
      return String(s.title || s.name || '');
    };
    studies.sort(function (a, b) {
      var av = val(a, sort.col), bv = val(b, sort.col), r;
      r = (typeof av === 'number') ? (av - bv) : String(av).localeCompare(String(bv));
      if (r === 0) r = String(a.title || a.name).localeCompare(String(b.title || b.name));
      return r * sort.dir;
    });
    var cols = [['name', 'Study'], ['investigation', 'Investigation'], ['status', 'Status'],
                ['phase', 'Phase'], ['runs', 'Runs'], ['last_run', 'Last run'], ['composite', 'Composite']];
    var th = cols.map(function (c) {
      var arrow = sort.col === c[0] ? (sort.dir > 0 ? ' ▲' : ' ▼') : '';
      var alignR = (c[0] === 'runs') ? 'text-align:right;' : 'text-align:left;';
      return '<th onclick="_setStudyTableSort(\'' + c[0] + '\')" style="' + alignR +
        'position:sticky;top:0;background:#f8fafc;padding:7px 10px;cursor:pointer;font-size:0.78em;' +
        'text-transform:uppercase;letter-spacing:0.03em;color:#475569;border-bottom:1px solid #e5e7eb;white-space:nowrap">' +
        _esc(c[1]) + arrow + '</th>';
    }).join('');
    var rows = studies.map(function (s) {
      var inv = _investigationForStudy(s.name) || '';
      var invTitle = _isetTitleForSlug(inv);
      var status = s.effective_status || s.status || 'planned';
      var m = _studyDotMeta(status);
      var runs = runsOf(s);
      var rowText = (String(s.title || s.name) + ' ' + inv + ' ' + invTitle + ' ' + status + ' ' + (s.phase || '')).toLowerCase();
      return '<tr data-row-text="' + _esc(rowText) + '" onclick="_openStudyEmbeddedNewTab(\'' + _esc(s.name) + '\')" ' +
        'style="cursor:pointer;border-bottom:1px solid #f1f5f9" ' +
        'onmouseover="this.style.background=\'#f8fafc\'" onmouseout="this.style.background=\'\'">' +
        '<td style="padding:7px 10px;font-weight:600;color:#1e293b">' + _esc(s.title || s.name) + '</td>' +
        '<td style="padding:7px 10px;color:#64748b">' + _esc(invTitle) + '</td>' +
        '<td style="padding:7px 10px;white-space:nowrap"><span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:' + m[0] + ';margin-right:5px"></span>' + _esc(m[1]) + '</td>' +
        '<td style="padding:7px 10px;color:#64748b">' + _esc(s.phase || '—') + '</td>' +
        '<td style="padding:7px 10px;text-align:right;color:' + (runs ? '#1e293b' : '#cbd5e1') + '">' + runs + '</td>' +
        '<td style="padding:7px 10px;color:#64748b;white-space:nowrap">' + _fmtStudyDate(s.last_run) + '</td>' +
        '<td style="padding:7px 10px;color:#94a3b8;font-family:ui-monospace,monospace;font-size:0.85em">' + _esc(s.composite || '—') + '</td>' +
        '</tr>';
    }).join('');
    list.innerHTML = '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:0.9em;' +
      'background:#fff;border:1px solid #e5e7eb;border-radius:8px;overflow:hidden">' +
      '<thead><tr>' + th + '</tr></thead><tbody>' + rows + '</tbody></table></div>';
    _filterInvestigations();
  }
  window._renderStudyBrowseTable = _renderStudyBrowseTable;

  function _setStudyTableSort(col) {
    var cur = window._isetTableSort || { col: 'investigation', dir: 1 };
    window._isetTableSort = { col: col, dir: (cur.col === col ? -cur.dir : 1) };
    _renderInvestigationSets();
  }
  window._setStudyTableSort = _setStudyTableSort;

  // Investigations TABLE view — dense, one row per investigation (same look as
  // _renderStudyBrowseTable). Row click opens the investigation workspace; the
  // report/notebook links mirror the card actions (_vivReportFromCard /
  // _vivNotebookFromCard already stopPropagation internally).
  var _ISET_TABLE_STATUS_META = {
    planning:    {label:'Planned',     bg:'#f1f5f9', fg:'#475569', bd:'#cbd5e1'},
    in_progress: {label:'In progress', bg:'#fef9c3', fg:'#854d0e', bd:'#fde047'},
    running:     {label:'Running now', bg:'#dbeafe', fg:'#1e40af', bd:'#93c5fd'},
    complete:    {label:'Complete',    bg:'#dcfce7', fg:'#166534', bd:'#86efac'},
    failed:      {label:'Failed',      bg:'#fee2e2', fg:'#991b1b', bd:'#fca5a5'}
  };
  function _renderInvestigationTable(isets, mountEl) {
    var items = (isets || []).slice();
    if (!items.length) {
      mountEl.innerHTML = '<p class="empty-state">No investigations declared. Author one at <code>investigations/&lt;name&gt;/investigation.yaml</code>.</p>';
      return;
    }
    var th = [['Name', 'left'], ['Status', 'left'], ['Studies', 'right'], ['Question', 'left'], ['Links', 'left']]
      .map(function (c) {
        return '<th style="text-align:' + c[1] + ';position:sticky;top:0;background:#f8fafc;padding:7px 10px;' +
          'font-size:0.78em;text-transform:uppercase;letter-spacing:0.03em;color:#475569;' +
          'border-bottom:1px solid #e5e7eb;white-space:nowrap">' + c[0] + '</th>';
      }).join('');
    var rows = items.map(function (iset) {
      var closed = (iset.status === 'archived' || iset.status === 'closed');
      var effStatus = iset.effective_status || iset.status || 'planning';
      var meta = _ISET_TABLE_STATUS_META[effStatus] || {label: effStatus, bg:'#f1f5f9', fg:'#475569', bd:'#cbd5e1'};
      var pillBase = 'font-size:0.72em;border-radius:9999px;padding:1px 9px;white-space:nowrap;';
      var statusPill = closed
        ? '<span class="status-pill" style="' + pillBase + 'background:#e5e7eb;color:#4b5563;border:1px solid #d1d5db">Closed</span>'
        : '<span class="status-pill" style="' + pillBase + 'background:' + meta.bg + ';color:' + meta.fg + ';border:1px solid ' + meta.bd + '">' + _esc(meta.label) + '</span>';
      var q = iset.question ? String(iset.question).split('\n')[0].slice(0, 140) : '';
      var rowText = (String(iset.title || iset.name) + ' ' + effStatus + ' ' + q).toLowerCase();
      return '<tr data-row-text="' + _esc(rowText) + '" onclick="_showInvestigationWorkspace(\'' + _esc(iset.name) + '\')" ' +
        'style="cursor:pointer;border-bottom:1px solid #f1f5f9' + (closed ? ';opacity:0.6' : '') + '" ' +
        'onmouseover="this.style.background=\'#f8fafc\'" onmouseout="this.style.background=\'\'">' +
        '<td style="padding:7px 10px;font-weight:600;color:#1e293b">' + _esc(iset.title || iset.name) + '</td>' +
        '<td style="padding:7px 10px;white-space:nowrap">' + statusPill + '</td>' +
        '<td style="padding:7px 10px;text-align:right;color:' + (iset.n_studies ? '#1e293b' : '#cbd5e1') + '">' + (iset.n_studies || 0) + '</td>' +
        '<td style="padding:7px 10px;color:#64748b">' + (q ? _esc(q) : '<span style="color:#cbd5e1">—</span>') + '</td>' +
        '<td style="padding:7px 10px;white-space:nowrap;font-size:0.85em">' +
          '<a href="#" title="Download the rendered HTML report for this investigation" ' +
            'onclick="window._vivReportFromCard(event,\'' + _esc(iset.name) + '\');return false;" ' +
            'style="color:#3b82f6;text-decoration:none;margin-right:10px">↓ report</a>' +
          '<a href="#" title="Download the runnable notebook for this investigation" ' +
            'onclick="window._vivNotebookFromCard(event,\'' + _esc(iset.name) + '\');return false;" ' +
            'style="color:#3b82f6;text-decoration:none">↓ notebook</a>' +
          (iset.n_figures ? '<a href="#" title="Download all figures for this investigation (studies figures + post-study composites), as a zip" ' +
            'onclick="window._vivFiguresFromCard(event,\'' + _esc(iset.name) + '\');return false;" ' +
            'style="color:#3b82f6;text-decoration:none;margin-left:10px">↓ figures</a>' : '') +
        '</td>' +
        '</tr>';
    }).join('');
    mountEl.innerHTML = '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:0.9em;' +
      'background:#fff;border:1px solid #e5e7eb;border-radius:8px;overflow:hidden">' +
      '<thead><tr>' + th + '</tr></thead><tbody>' + rows + '</tbody></table></div>';
  }
  window._renderInvestigationTable = _renderInvestigationTable;

  // Client-side filter for the Investigations landing list. UNIFIED with the
  // side-rail studies search (same _tokensMatch engine, same AND-first/OR-
  // fallback): an investigation card shows when the investigation itself OR any
  // of its member studies matches — so searching "basal" surfaces the
  // v2ecoli-vEcoli comparison investigation via its `basal` study. Updates
  // per-group counts, hides empty groups, toggles the "no matches" line.
  function _filterInvestigations() {
    var input = document.getElementById('investigations-filter');
    var tokens = _tokenize(input && input.value);
    // TABLE zoom (either tab): filter rows directly (each carries data-row-text).
    if (window._isetZoom === 'table') {
      document.querySelectorAll('#investigations-list tr[data-row-text]').forEach(function (tr) {
        tr.style.display = _tokensMatch(tr.getAttribute('data-row-text') || '', tokens) ? '' : 'none';
      });
      return;
    }
    var cards = document.querySelectorAll('#investigations-list .investigation-set-card');

    // iset slug -> member study objects, for study-aware matching.
    var studiesByIset = {};
    (window._isetIndex || []).forEach(function(iset) {
      studiesByIset[iset.name] = (iset.studies || [])
        .map(function(slug) {
          return (window._investigations || []).find(function(s) { return s.name === slug; });
        }).filter(Boolean);
    });

    function _cardMatches(card, requireAll) {
      var slug = card.getAttribute('data-iset-slug') || '';
      var title = card.getAttribute('data-iset-title') || '';
      var status = card.getAttribute('data-iset-status') || '';
      if (_tokensMatch(_searchHay([title, slug, status]), tokens, requireAll)) return true;
      return (studiesByIset[slug] || []).some(function(s) {
        return _tokensMatch(_studyHay(s, title), tokens, requireAll);
      });
    }

    // AND-first, OR-fallback across investigations AND their studies.
    var requireAll = !!tokens.length && Array.prototype.some.call(cards, function(c) {
      return _cardMatches(c, true);
    });

    var anyVisible = false;
    cards.forEach(function(card) {
      var show = !tokens.length || _cardMatches(card, requireAll);
      card.style.display = show ? '' : 'none';
      if (show) anyVisible = true;
    });
    document.querySelectorAll('#investigations-list .iset-group').forEach(function(group) {
      var n = 0;
      group.querySelectorAll('.investigation-set-card').forEach(function(c) {
        if (c.style.display !== 'none') n++;
      });
      var countEl = group.querySelector('.iset-group-count');
      if (countEl) countEl.textContent = '(' + n + ')';
      group.style.display = n ? '' : 'none';
    });
    var empty = document.getElementById('investigations-empty');
    if (empty) empty.style.display = anyVisible ? 'none' : '';
  }
  window._filterInvestigations = _filterInvestigations;

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

    fetch('/api/investigation-create', {
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

    fetch('/api/investigation-clone', {
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
      s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');   // *italic* (after **bold**)
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

  // (The at-a-glance study-card row was removed — the dependency DAG below shows
  // the same studies. Its render fn (_renderInvAtAGlance) + tile click handler
  // (_vivOpenAagTile) are gone with it; DAG nodes use _openStudyInsideInvestigation.)

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
  // Inquiry brief: an investigation IS a question, so lead with it as the
  // headline; the verdict answers it as a colored status line (not a box); depth
  // (how-to-read / biology / background / glossary) lives in one flat tab strip.
  // Reads the SAME canonical fields as the downloaded report's executive summary.
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
    var bio       = (d.biological_story || '').trim();
    var glossary  = Array.isArray(d.glossary) ? d.glossary : [];
    var howto     = d.how_to_read;  // string (prose) or array (tips) — both render.
    var hasHowto  = Array.isArray(howto) ? howto.length : String(howto || '').trim().length;

    // Legacy investigations with no structured content fall back to the lead.
    if (!whatIs && !verdict && !q && !hyp) {
      return leadProse
        ? '<div class="inv-brief"><div class="inv-brief-prose">' + _renderInvLeadMarkdown(leadProse) + '</div></div>'
        : '';
    }

    var key = String(vs).toLowerCase().replace(/[^a-z0-9]+/g, '-');
    var vsClass = ({ 'passed':'passed','complete':'passed','in-progress':'progress',
                     'blocked':'blocked','failed':'blocked','planning':'planning' })[key] || 'default';

    // The question/framing fields run long — keep the headline to the opening
    // sentence (through the first "?" for questions, else the first period) and
    // demote the remainder to a muted framing line.
    var headlineOf = function(t) {
      t = oneline(t);
      var qi = t.indexOf('?');
      if (qi !== -1) return t.slice(0, qi + 1);
      var m = t.match(/^.*?[.!](?=\s)/);
      return m ? m[0] : t;
    };
    var _invInline = function(s) {
      s = _escInv(s);
      s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
      s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
      s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
      return s;
    };
    // Headline = the investigation title (falls back to the driving question);
    // the full what_is_this renders below as markdown prose (the introduction).
    var headline = (d.title && d.title !== d.name) ? d.title
                   : (q ? headlineOf(q) : (d.name || ''));

    var H = [];
    H.push('<div class="inv-brief inv-vs-' + vsClass + '">');

    // Headline.
    if (headline) H.push('<h2 class="inv-brief-q">' + _invInline(headline) + '</h2>');

    // Verdict — a colored status line.
    if (verdict) {
      H.push('<div class="inv-brief-verdict">' +
        '<span class="inv-vs-pill">' + _esc(vs.toUpperCase()) + '</span>' +
        '<span class="inv-brief-verdict-label">Current verdict</span> ' +
        '<span class="inv-brief-verdict-text">' + _invInline(verdict) + '</span></div>');
    }

    // Introduction — the full what_is_this rendered as markdown prose.
    if (whatIs) H.push('<div class="inv-brief-prose inv-brief-intro">' + _renderInvLeadMarkdown(whatIs) + '</div>');

    // Meta — the driving question + hypothesis, one muted line each.
    var _meta = [];
    if (q) _meta.push('<span class="inv-brief-meta-item"><em>Question</em> ' + _invInline(oneline(q)) + '</span>');
    if (hyp) _meta.push('<span class="inv-brief-meta-item"><em>Hypothesis</em> ' + _invInline(hyp) + '</span>');
    if (_meta.length) H.push('<div class="inv-brief-meta">' + _meta.join('') + '</div>');

    // Depth — one flat tab strip; only tabs with content are shown.
    var tabs = [];
    if (hasHowto)
      tabs.push({ id:'howto', label:'How to read', html: Array.isArray(howto)
        ? '<ol class="inv-brief-howto">' + howto.map(function(x) {
            return '<li>' + _renderInvLeadMarkdown(String(typeof x === 'string' ? x : (x.text || x.tip || ''))).replace(/^<p>|<\/p>$/g, '') + '</li>';
          }).join('') + '</ol>'
        : '<div class="inv-brief-prose">' + _renderInvLeadMarkdown(String(howto)) + '</div>' });
    if (bio)
      tabs.push({ id:'biology', label:'Biology', html:
        '<div class="inv-brief-prose">' + _renderInvLeadMarkdown(bio) + '</div>' });
    if (leadProse)
      tabs.push({ id:'background', label:'Background', html:
        '<div class="inv-brief-prose">' + _renderInvLeadMarkdown(leadProse) + '</div>' });
    if (glossary.length)
      tabs.push({ id:'glossary', label:'Glossary', html:
        '<dl class="inv-brief-glossary">' + glossary.map(function(g) {
          return '<dt>' + _escInv(g.term || g.name || '') + '</dt><dd>' + _escInv(g.definition || g.def || '') + '</dd>';
        }).join('') + '</dl>' });

    if (tabs.length) {
      H.push('<div class="inv-brief-tabs" role="tablist">' + tabs.map(function(t, i) {
        return '<button type="button" class="inv-brief-tab' + (i === 0 ? ' active' : '') +
          '" onclick="_invBriefTab(this,\'' + t.id + '\')">' + _esc(t.label) + '</button>';
      }).join('') + '</div>');
      H.push('<div class="inv-brief-panels">' + tabs.map(function(t, i) {
        return '<div class="inv-brief-panel" data-panel="' + t.id + '"' + (i === 0 ? '' : ' style="display:none"') + '>' + t.html + '</div>';
      }).join('') + '</div>');
    }

    H.push('</div>');
    return H.join('');
  }

  // Flat tab switcher for the inquiry brief (scoped to the clicked brief).
  function _invBriefTab(btn, id) {
    var brief = btn.closest('.inv-brief');
    if (!brief) return;
    brief.querySelectorAll('.inv-brief-tab').forEach(function(b) { b.classList.toggle('active', b === btn); });
    brief.querySelectorAll('.inv-brief-panel').forEach(function(p) {
      p.style.display = (p.getAttribute('data-panel') === id) ? '' : 'none';
    });
  }
  window._invBriefTab = _invBriefTab;

  function _openInvestigationDetail(name) {
    window._currentIset = name;
    // Rerun POSTs to a live-only endpoint — hide it in a published read-only
    // snapshot (no backend to launch against). Report/Notebook stay visible
    // since they only read data, so they still work off the static bundle.
    var rerunBtn = document.getElementById('investigation-rerun');
    if (rerunBtn) {
      rerunBtn.style.display = (window.__DASH_CONFIG__ || {}).mode === 'snapshot' ? 'none' : '';
    }
    // Opening an investigation is an explicit context switch → re-scope the
    // Simulations DB to it. Clearing the sticky manual pick lets the next visit
    // default to this investigation (see _populateSimFilters / _simCurrent).
    window._simInvChosen = false;
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

    // Route through DataSource so snapshot mode reads api/investigation/<name>.json from
    // the static bundle instead of hitting the live /api/investigation/<name> endpoint
    // (which would 404 in a hosted read-only bundle). Direct-fetch fallback keeps
    // local-server mode identical — the ternary branch only triggers under snapshot.
    var _isetDetailFetch = (window.DataSource && window.DataSource.loadInvestigation)
      ? window.DataSource.loadInvestigation(name)
      : fetch('/api/investigation/' + encodeURIComponent(name), {headers: {Accept: 'application/json'}})
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
        // The inquiry brief renders the full opening — headline, verdict, meta,
        // and the how-to-read / biology / background / glossary tabs — from `d`.
        leadEl.innerHTML = _renderInvOpening(d);

        // Phase B4: render today's study graph (unchanged), then layer each
        // study's typed evidence chain into its card. Falls back to the plain
        // study graph on any fetch failure (graceful — identical to before).
        (function () {
          var slug = d.slug || d.name || name;
          if (!slug) { _renderInvestigationDag(d.studies || []); return; }
          _dagInvSlug = slug;
          _dagTriggerBySlug = {};
          // Layer-4 cached/compute badges: fetch the per-study trigger status in
          // parallel with the graph. Live-server only (the query-string endpoint
          // 404s in the published bundle) and best-effort — a failure just leaves
          // the badges off; the graph still renders.
          var _isSnap = (window.__DASH_CONFIG__ || {}).mode === 'snapshot';
          var _statusP = _isSnap ? Promise.resolve(null) :
            fetch('/api/investigation-trigger-status?investigation=' + encodeURIComponent(slug))
              .then(function (r) { return r.ok ? r.json() : null; })
              .catch(function () { return null; });
          // Snapshot-aware: DataSource resolves to /api/investigation-graph/<slug>.json
          // in the published read-only (a raw fetch of the query-string endpoint
          // 404s there, dropping the evidence chains from every card).
          var _graphP = (window.DataSource && window.DataSource.loadInvestigationGraph
            ? window.DataSource.loadInvestigationGraph(slug)
            : fetch('/api/investigation-graph?investigation=' + encodeURIComponent(slug))
                .then(function (r) { if (!r.ok) throw new Error('graph ' + r.status); return r.json(); })
          );
          _statusP.then(function (status) {
            var by = {};
            ((status && status.nodes) || []).forEach(function (n) { if (n && n.slug) by[n.slug] = n; });
            _dagTriggerBySlug = by;
            return _graphP;
          }, function () { return _graphP; })
            .then(function (graph) {
              _renderInvestigationDag(d.studies || [], (graph && graph.chains) || {}, (graph && graph.study_edges) || []);
            })
            .catch(function () { _renderInvestigationDag(d.studies || []); });
        })();
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
      // Human-readable name + which study tab a click should land on, per kind.
      // These are follow-ups the deterministic scan flags — NOT app updates.
      var _naKindMeta = {
        diagnostic_branch_needed: {label: 'Diagnostic study needed', tab: 'conclusions'},
        next_action_ready:        {label: 'Next action ready',        tab: 'conclusions'},
        invariant_regression:     {label: 'Invariant regression',     tab: 'conclusions'},
        uncovered_ac:             {label: 'Acceptance criterion uncovered', tab: 'tests'},
        open_feedback:            {label: 'Open expert feedback',      tab: 'overview'}
      };
      var items = (d.items || []).map(function(it) {
        var st = _naSeverityStyle(it.severity);
        var ref = (it.study || it.ref || '').toString();
        var kindRaw = (it.kind || '').toString();
        var meta = _naKindMeta[kindRaw] || {label: kindRaw.replace(/_/g, ' '), tab: 'conclusions'};
        var refHtml = ref ? '<code>' + _esc(ref) + '</code>' : '<span class="muted">—</span>';
        var hint = it.action_hint ? ' &nbsp;·&nbsp; ' + _esc(it.action_hint.toString()) : '';
        var titleLine = it.title
          ? '<div style="font-size:0.9em;margin-top:2px">' + _esc(it.title.toString()) + '</div>'
          : '';
        // Clickable when the item names a study: open it at the relevant tab
        // (verdict-based items → the conclusions/Decide tab).
        var open = ref
          ? ' role="button" tabindex="0" onclick="_openStudyInsideInvestigation(\'' + _esc(ref) + '\',\'' + meta.tab + '\')"'
            + ' onkeydown="if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();this.click();}"'
            + ' title="Open study &quot;' + _esc(ref) + '&quot; at its verdict"'
          : '';
        var arrow = ref ? '<span style="float:right;opacity:.55;font-size:0.9em">open →</span>' : '';
        return '<li class="na-item' + (ref ? ' na-item-click' : '') + '"' + open
          + ' style="margin-top:7px;padding:5px 8px 6px 10px;border-left:3px solid ' + st.bd + ';border-radius:4px'
          + (ref ? ';cursor:pointer' : '') + '">'
          + arrow
          + '<span style="color:' + st.dot + ';font-weight:700">●</span> '
          + '<strong style="font-size:0.9em">' + _esc(meta.label) + '</strong> &nbsp;·&nbsp; ' + refHtml + hint
          + titleLine + '</li>';
      }).join('');
      var byKind = d.summary.by_kind || {};
      var breakdown = Object.keys(byKind).sort(function(a, b) {
        return (byKind[b] || 0) - (byKind[a] || 0);
      }).map(function(k) {
        var m = _naKindMeta[k];
        return (byKind[k] || 0) + '× ' + _esc(m ? m.label : k.replace(/_/g, ' '));
      }).join(' &nbsp;·&nbsp; ');
      var sev = _naSeverityStyle(high ? 'high' : 'medium');
      // Plain-language explainer so the panel is self-describing (the user asked
      // "what ARE these?"): they're automated follow-ups, and each is clickable.
      var explain = '<div class="muted" style="font-size:0.84em;margin:2px 14px 4px;line-height:1.45">'
        + 'Automated checks (no AI) that flag studies needing a follow-up — e.g. a study whose '
        + 'verdict is <em>failed</em> or <em>needs-calibration</em> but has no diagnostic study seeded '
        + 'to investigate why. Not app updates. <strong>Click any item</strong> to open that study at the verdict.</div>';
      container.innerHTML =
        '<details class="needs-attention-banner" style="margin:10px 0 14px 0;background:' + sev.bg
        + ';border:1px solid ' + sev.bd + ';border-left-width:5px;border-radius:6px;color:' + sev.col + '">'
        + '<summary style="padding:10px 14px;cursor:pointer;list-style:none;outline:none">'
        + '<strong>' + head + '</strong>'
        + '<span class="na-toggle-hint" style="opacity:.6;font-style:italic;font-size:0.85em;margin-left:8px">— click to expand</span>'
        + (breakdown ? '<div class="muted" style="font-size:0.82em;margin-top:5px">' + breakdown + '</div>' : '')
        + '</summary>'
        + explain
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

  // "Run current spec" (investigation-level) — force-relaunch every member
  // study's CURRENT baseline spec, RE-DERIVING each from its own study.yaml
  // (ignores the unblocked-gate; explicit user action). This is the
  // investigation-level counterpart to the study header's "Run current
  // spec" button (reproducible-rerun-spine Task 4 / G2) — an investigation-
  // level "Reproduce" (DAG-ordered manifest replay across member studies) is
  // explicitly OUT of scope here; that's Task 7. POSTs to
  // /api/investigation-rerun, toasts the launched count, then re-renders the
  // run-progress panel with per-study results and refreshes the detail view
  // so new runs show up in charts/Simulations.
  function _rerunInvestigation() {
    var name = window._currentIset;
    if (!name) return;
    if (!confirm('Re-run every study in this investigation? Launches a fresh baseline run per study.')) return;
    var btn = document.getElementById('investigation-rerun');
    var panel = document.getElementById('investigation-run-progress');
    if (btn) { btn.disabled = true; btn.textContent = '… launching'; }
    if (panel) { panel.style.display = ''; panel.innerHTML = '<div class="inv-run-progress-banner">Launching reruns…</div>'; }
    fetch('/api/investigation-rerun', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ investigation: name }),
    }).then(function(r) {
      return r.json().then(function(j) { return { ok: r.ok, body: j, status: r.status }; });
    }).then(function(res) {
      if (btn) { btn.disabled = false; btn.textContent = '▶ Run current spec'; }
      if (!res.ok) {
        var errMsg = 'Rerun failed: ' + ((res.body && res.body.error) || res.status);
        if (typeof _showToast === 'function') _showToast(errMsg); else alert(errMsg);
        if (panel) panel.innerHTML = '<div class="inv-run-progress-banner inv-run-error">' + _h(errMsg) + '</div>';
        return;
      }
      var body = res.body || {};
      var launched = body.launched || [], errors = body.errors || [];
      var msg = (body.count || launched.length) + ' runs launched';
      if (errors.length) msg += ', ' + errors.length + ' failed';
      if (typeof _showToast === 'function') _showToast(msg); else alert(msg);
      if (panel) {
        var items = launched.map(function(it) {
          return '<div class="inv-run-item inv-run-done"><span class="inv-run-icon">✓</span>' +
            '<code>' + _h(it.study) + '</code> <span class="inv-run-arrow">›</span> run <code>' + _h(it.run_id) + '</code></div>';
        }).concat(errors.map(function(it) {
          return '<div class="inv-run-item inv-run-failed"><span class="inv-run-icon">✗</span>' +
            '<code>' + _h(it.study) + '</code><span class="inv-run-err"> ' + _h(String(it.error)) + '</span></div>';
        })).join('');
        panel.innerHTML = '<div class="inv-run-progress-banner"><strong>' + _h(msg) + '</strong></div>' +
          '<div class="inv-run-list">' + items + '</div>';
      }
      // Refresh the investigation detail so newly-launched runs surface.
      if (typeof _refreshInvestigationDetail === 'function') {
        setTimeout(_refreshInvestigationDetail, 500);
      } else if (typeof _openInvestigationDetail === 'function') {
        setTimeout(function() { _openInvestigationDetail(name); }, 500);
      }
    }).catch(function(err) {
      if (btn) { btn.disabled = false; btn.textContent = '▶ Run current spec'; }
      var netMsg = 'Network error: ' + err;
      if (typeof _showToast === 'function') _showToast(netMsg); else alert(netMsg);
      if (panel) panel.innerHTML = '<div class="inv-run-progress-banner inv-run-error">' + _h(netMsg) + '</div>';
    });
  }
  window._rerunInvestigation = _rerunInvestigation;

  function _vivPollRunProgress(jobId) {
    if (_vivRunUnblockedTimer) clearTimeout(_vivRunUnblockedTimer);
    // Plan §A3′ option (c): an item gated behind an unfinished prerequisite is
    // parked `waiting` and its worker RETURNS, rather than holding a thread for
    // the life of a Batch job. Something has to come back and release it once
    // the prerequisite lands, and this poll is the natural caller — it is
    // already here, already watching the same job.
    //
    // Fired on CHANGE, not every tick. The status GET resolves `submitted`
    // items against viva-api, so a prerequisite completing on Batch shows up
    // here as `progress.done` increasing; that edge is exactly when a redrive
    // can accomplish something. Polling it blindly every 2s would spawn a
    // worker thread per tick for the whole life of a multi-hour campaign, each
    // one re-parking the same items.
    var lastDone = -1;
    function maybeRedrive(job) {
      var prog = job.progress || {};
      if (!prog.waiting) { lastDone = (prog.done || 0); return; }
      if ((prog.done || 0) === lastDone) return;   // nothing settled since last look
      lastDone = (prog.done || 0);
      fetch(_api('/api/investigation-run-redrive'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: jobId })
      }).catch(function() { /* best-effort: the next change re-tries */ });
    }
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
          maybeRedrive(res.body);
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
      // `submitted` (A2′) and `waiting` (A3′) both post-date this map, so both
      // rendered as '?' — a dispatched Batch run and a gated dependent looked
      // like a bug rather than the two normal states they are.
      var icon = ({queued: '⋯', running: '▶', done: '✓', failed: '✗',
                   blocked: '⛔', skipped: '—', submitted: '☁', waiting: '⏸'})[it.status] || '?';
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
                 (prog.running ? ' · ' + prog.running + ' in flight' : '') +
                 (prog.submitted ? ' · ' + prog.submitted + ' on Batch' : '') +
                 (prog.waiting ? ' · ' + prog.waiting + ' waiting on prerequisites' : '');
    }
    panel.innerHTML = '<div class="inv-run-progress-banner">' + headline + '</div>'
                    + '<div class="inv-run-list">' + items + '</div>';
  }

  // Manual refresh: re-fetch /api/investigation/<current> + re-render. Use after editing
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
  // Two orientations, chosen by chooseGraphOrientation() (aig-graph.js) or a
  // manual per-investigation localStorage override:
  //   LR (left->right): depth -> x (columns), within-depth index -> y (rows).
  //   TB (top->bottom):  depth -> y (rows),    within-depth index -> x (columns).
  // Cards as absolute-positioned <div>s; edges as SVG cubic-Bezier paths.
  // ── Layer-4 pull-or-compute affordances on the investigation DAG ─────────
  //
  // Each study node gets a cached/compute badge (does its output artifact
  // already exist in .pbg/artifacts/?) and, on the live server only, two
  // buttons: "Run this study" (compute it, pulling any cached upstream) and
  // "Continue from here" (same call — the label reflects that it reuses cached
  // upstream when the study has ancestors). Both POST /api/investigation-trigger
  // with the node as target. The buttons carry class `js-authoring` so they
  // hide automatically in the published snapshot and live read-only modes.

  function _dagCacheBadgeHtml(slug) {
    var st = _dagTriggerBySlug[slug];
    if (!st) return '';
    var cached = !!st.cached;
    var bg = cached ? '#dcfce7' : '#f1f5f9';
    var fg = cached ? '#166534' : '#475569';
    var label = cached ? '● cached' : '○ compute';
    var tip = cached
      ? 'Output artifact is in the store — this study is pulled, not recomputed'
      : 'No cached artifact — this study computes when triggered';
    return '<span class="dag-cache-badge" title="' + _esc(tip) + '" ' +
      'style="display:inline-block;font-size:0.62em;font-weight:700;padding:1px 7px;' +
      'border-radius:9999px;background:' + bg + ';color:' + fg + '">' + label + '</span>';
  }

  function _dagTriggerControlsHtml(slug) {
    // Launch actions (Run this study / Continue from here) intentionally do NOT
    // appear on the graph study cards — running lives on the full study tab
    // (▶ Run current spec / ↻ Reproduce), matching the download group below and
    // the investigation card study rows. The cards stay a browse + download
    // surface. (Kept as a no-op so existing call sites need no change.)
    return '';
  }

  // Download affordances for a graph study card: ↓ figures (this study's own
  // figures) + ↓ notebook (the parent investigation's runnable notebook). Unlike
  // the run/continue controls these are NOT authoring-gated — they survive into
  // the read-only snapshot so shared links can still grab figures. Deliberately
  // no ▶ run here: the small card stays uncluttered; running lives on the full
  // study tab.
  function _dagDownloadControlsHtml(slug) {
    var lnk = 'font-size:0.66em;color:#3b82f6;text-decoration:none;white-space:nowrap';
    // Show "↓ figures" only when the study actually has downloadable figures.
    // The per-study status (from /api/investigation-trigger-status) carries
    // has_figures; hide the link ONLY on an explicit false so that when the
    // status is unavailable (snapshot bundle / fetch failed → no entry) we keep
    // showing it rather than hiding a real download. ↓ notebook is always
    // generatable, so it stays unconditional.
    var _st = _dagTriggerBySlug[slug];
    var _figures = (!_st || _st.has_figures !== false)
      ? '<a href="#" title="Download this study\'s figures (and embedded HTML reports) as a zip" ' +
          'onclick="window._vivStudyFiguresFromCard(event,\'' + _esc(slug) + '\');return false;" ' +
          'style="' + lnk + '">↓ figures</a>'
      : '';
    return '<div class="dag-download-controls" style="display:flex;gap:12px;flex-wrap:wrap;margin-top:6px">' +
      _figures +
      '<a href="#" title="Download this study\'s own runnable notebook (composite + parameters + figures)" ' +
        'onclick="window._vivStudyNotebookFromCard(event,\'' + _esc(slug) + '\',\'' + _esc(_dagInvSlug || '') + '\');return false;" ' +
        'style="' + lnk + '">↓ notebook</a>' +
      '</div>';
  }

  function _triggerStudy(slug, onMissing, btnEl) {
    if (!_dagInvSlug) return;
    var original = btnEl ? btnEl.textContent : '';
    if (btnEl) { btnEl.disabled = true; btnEl.textContent = 'triggering…'; }
    fetch('/api/investigation-trigger', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        investigation: _dagInvSlug, target_study: slug, on_missing: onMissing,
      }),
    }).then(function (r) {
      return r.json().then(function (j) { return { ok: r.ok, status: r.status, body: j }; });
    }).then(function (res) {
      if (btnEl) { btnEl.disabled = false; btnEl.textContent = original; }
      if (!res.ok) {
        // 409 = an uncached prerequisite under on_missing=error; the message
        // names it. Offer to compute the whole chain instead.
        if (res.status === 409 &&
            window.confirm((res.body && res.body.error) + '\n\nCompute the missing upstream too?')) {
          _triggerStudy(slug, 'compute', btnEl);
          return;
        }
        window.alert('Trigger failed: ' + ((res.body && res.body.error) || res.status));
        return;
      }
      var rep = res.body.report || {};
      var msg = 'Triggered ' + slug + '\n' +
        'pulled (reused from cache): ' + ((rep.pulled || []).join(', ') || 'none') + '\n' +
        'computed (running): ' + ((rep.computed || []).join(', ') || 'none') + '\n' +
        'pruned (not needed): ' + ((rep.pruned || []).join(', ') || 'none');
      var run = res.body.run || {};
      if (run.run_id) msg += '\n\nrun: ' + run.run_id;
      window.alert(msg);
      // Refresh badges so newly-cached studies flip to "cached".
      if (typeof _refreshDagTriggerStatus === 'function') _refreshDagTriggerStatus();
    }).catch(function (err) {
      if (btnEl) { btnEl.disabled = false; btnEl.textContent = original; }
      window.alert('Trigger request failed: ' + err);
    });
  }

  function _refreshDagTriggerStatus() {
    if (!_dagInvSlug) return;
    fetch('/api/investigation-trigger-status?investigation=' + encodeURIComponent(_dagInvSlug))
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (status) {
        if (!status) return;
        var by = {};
        (status.nodes || []).forEach(function (n) { if (n && n.slug) by[n.slug] = n; });
        _dagTriggerBySlug = by;
        if (_lastDagArgs) _renderInvestigationDag(_lastDagArgs[0], _lastDagArgs[1], _lastDagArgs[2]);
      })
      .catch(function () {});
  }

  function _renderInvestigationDag(studies, chainsBySlug, studyEdges) {
    _lastDagArgs = [studies, chainsBySlug, studyEdges];
    // Prefer the server's computed study_edges for dependency layout: in the
    // member-interface model the per-study `parent_studies` / `pipeline_gate`
    // (what _dagEdges reads) is empty because prerequisites are declared as
    // interface `inputs` (from:<study>), which the server resolves into
    // study_edges. Without this the graph collapses to a flat, depth-0 row.
    var _edgesByChild = {};
    (studyEdges || []).forEach(function (e) {
      var src = String(e.source || '').replace(/^study\//, '');
      var tgt = String(e.target || '').replace(/^study\//, '');
      if (!src || !tgt) return;
      (_edgesByChild[tgt] = _edgesByChild[tgt] || []).push({
        study: src,
        condition: e.condition || 'tests-passed',
        relation: e.artifact ? 'model-input' : 'leads-to',
        artifact: e.artifact,
      });
    });
    function dagEdgesFor(s) {
      var fromServer = s && _edgesByChild[s.name];
      if (fromServer && fromServer.length) return fromServer;
      return _dagEdges(s);
    }
    var _opts = window._layoutOptsForBand(aigBand);
    var shellEl = document.getElementById('investigation-dag-shell');
    if (shellEl) { shellEl.classList.remove('aig-zoom-far','aig-zoom-mid','aig-zoom-near'); shellEl.classList.add(_opts.cls); }


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
      dagEdgesFor(s).forEach(function(p) {
        var pn = p.study;
        if (children[pn]) children[pn].push(s.name);
      });
    });

    // BFS depth from roots. A "root" is a study with no prerequisite AMONG THE
    // STUDIES IN THIS INVESTIGATION. External prereqs (e.g. `parca`, which is not a
    // study node in this graph) must NOT disqualify a root — otherwise a chain whose
    // head depends on an external node is never reached by the BFS and every study
    // falls through to the depth-0 default, collapsing the whole chain into a single
    // column (vertical stack) instead of flowing left->right by dependency depth.
    var depth = {};
    var queue = [];
    studies.forEach(function(s) {
      var inParents = dagEdgesFor(s).filter(function(p) { return byName[p.study]; });
      if (!inParents.length) { depth[s.name] = 0; queue.push(s.name); }
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

    // Orientation: auto-pick from the graph's shape (wide/shallow -> TB,
    // deep/narrow -> LR) unless the user manually toggled it for this
    // investigation, in which case the stored choice wins.
    var depthCounts = {};
    Object.keys(byDepth).forEach(function(d) { depthCounts[d] = byDepth[d].length; });
    var _storedOrient = _getStoredGraphOrientation(window._currentIset);
    var orient = _storedOrient ||
      (window.chooseGraphOrientation ? window.chooseGraphOrientation(depthCounts) : 'LR');
    if (shellEl) { shellEl.classList.remove('aig-orient-lr', 'aig-orient-tb'); shellEl.classList.add(orient === 'TB' ? 'aig-orient-tb' : 'aig-orient-lr'); }
    _syncGraphOrientToggleUI(orient, !!_storedOrient);

    // Card HEIGHT is NOT fixed: each card grows to fit its full text. We render
    // once, measure each card, then stack + center by the measured heights
    // (two passes) so nothing is clipped.
    //   - DEPTH_GAP (semantic-zoom-controlled, _opts.xGap): gap along the
    //     dependency-depth axis — horizontal columns in LR, vertical rows in TB.
    //   - BREADTH_GAP (fixed): gap along the same-depth axis — vertical stack in
    //     LR, horizontal row in TB.
    var CARD_W = _opts.cardW;
    var DEPTH_GAP = _opts.xGap, BREADTH_GAP = 22;
    var PAD_X = 24, PAD_Y = 16;
    var svgNS = 'http://www.w3.org/2000/svg';
    var pos = {};
    var depths = Object.keys(byDepth).map(Number).sort(function(a, b) { return a - b; });

    // Breadth index (position within its own depth level, alphabetical order —
    // same ordering byDepth[d] was already sorted into above).
    var breadthIndex = {};
    depths.forEach(function(d) {
      byDepth[d].forEach(function(s, i) { breadthIndex[s.name] = i; });
    });

    // TB only: each depth level is a ROW, laid out left->right by breadth index
    // using the fixed CARD_W (rows don't need measurement to know their width,
    // unlike their height). Precompute row start-x (centered within the widest
    // row) so Pass 1 below can place cards immediately, mirroring how LR places
    // columns immediately from depth alone.
    var rowStartX = {}, canvasW_TB = 180;
    if (orient === 'TB') {
      var rowWidths = {}, maxRowWidth = 0;
      depths.forEach(function(d) {
        var n = byDepth[d].length;
        rowWidths[d] = n > 0 ? n * (CARD_W + BREADTH_GAP) - BREADTH_GAP : 0;
        if (rowWidths[d] > maxRowWidth) maxRowWidth = rowWidths[d];
      });
      canvasW_TB = Math.max(PAD_X * 2 + maxRowWidth, 180);
      depths.forEach(function(d) {
        rowStartX[d] = PAD_X + Math.max(0, (canvasW_TB - PAD_X * 2 - rowWidths[d]) / 2);
      });
    }

    // -- Pass 1: build every card at its x (LR: by depth; TB: by breadth-in-row),
    //    top TBD, append, measure --
    studies.forEach(function(s) {
      var liveStatus = s.effective_status || s.status || 'planned';
      // Derive confidence from the spine's gate_status VERDICT first, so the badge
      // tracks the computed verdict rather than the drift-prone hand-set `status`
      // (a stale `status: in_progress` on a passed study used to mis-show
      // "Investigating"). Fall back to lifecycle status only when no gate verdict.
      var gateV = String(s.gate_status || '').trim().toLowerCase();
      var confidence = s.confidence || (function() {
        if (gateV === 'passed' || gateV === 'pass') return 'Accepted';
        if (gateV === 'partial' || gateV === 'needs_calibration') return 'Investigating';
        if (gateV === 'failed' || gateV === 'failed_evaluation' || gateV === 'refuted' || gateV === 'blocked') return 'Refuted';
        if (liveStatus === 'completed' || liveStatus === 'complete' || liveStatus === 'ran') return 'Accepted';
        if (liveStatus === 'in_progress' || liveStatus === 'running') return 'Investigating';
        if (liveStatus === 'failed' || liveStatus === 'invalid') return 'Refuted';
        return 'Planned';
      })();
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
      // Question + claim teaser. The claim can be an entire research-log
      // paragraph (e.g. pdmp-01), which blows the card up to fill the canvas —
      // so it's CSS line-clamped to a few lines below; the full text lives one
      // click away on the study page (and on hover via the node title).
      var asks = (s.question || '').replace(/\s+/g, ' ').split(/[.?]/)[0].trim();
      var findings = _asFindings(s.findings);
      var claim = (s.claim ||
        (findings[0] && (findings[0].summary || findings[0].statement || findings[0].id)) || ''
      ).replace(/\s+/g, ' ').trim();
      var moreN = findings.length > 1 ? findings.length - 1 : 0;
      // Shared line-clamp style — keeps every DAG card compact regardless of
      // how long its question/claim text is.
      var _clamp = function(lines) {
        return 'display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:' +
          lines + ';line-clamp:' + lines + ';overflow:hidden;';
      };

      var node = document.createElement('div');
      node.className = 'iset-dag-node';
      // Single click opens the full study view directly — no quick-look
      // side-card, no double-click.
      node.onclick = function() {
        _openStudyInsideInvestigation(s.name);
      };
      node.title = s.name + ' — ' + confidence + (claim ? '\n\nFinds: ' + claim : '') +
        '\n\nClick to open the study';
      var x = (orient === 'TB')
        ? rowStartX[depth[s.name]] + breadthIndex[s.name] * (CARD_W + BREADTH_GAP)
        : PAD_X + depth[s.name] * (CARD_W + DEPTH_GAP);
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
        // Meta row: icon (left) + status badge (right). The badge is alone on
        // this row with space-between, so a nowrap label can never overflow the
        // card. The title then spans the FULL card width on its own line below
        // (no longer squeezed into a thin flex column beside the badge).
        '<div style="display:flex;align-items:center;justify-content:space-between;gap:6px;margin-bottom:5px">' +
          '<span style="color:' + ss.color + ';font-size:1.05em;line-height:1;flex:none">' + ss.icon + '</span>' +
          '<span class="aig-status-badge" role="button" tabindex="0" title="Why: open this study\'s finding & evidence" ' +
            'style="font-size:0.62em;font-weight:700;color:' + ss.color + ';white-space:nowrap;cursor:pointer;text-decoration:underline dotted;flex:none">' +
            _esc(confidence) + '</span>' +
        '</div>' +
        '<strong style="display:block;font-size:0.85em;line-height:1.3;color:#1e293b">' + _esc(prettyTitle) + '</strong>' +
        (_opts.asks && asks
          ? '<div style="font-size:0.72em;margin-top:7px;line-height:1.35;color:#64748b;' + _clamp(2) + '">' +
              '<span style="font-weight:600;color:#475569">Asks:</span> ' + _esc(asks) + '</div>'
          : '') +
        (_opts.finds
          ? '<div style="font-size:0.72em;margin-top:5px;line-height:1.35;color:#64748b;' + _clamp(5) + '">' +
              '<span style="font-weight:600;color:#475569">Finds:</span> ' +
              (claim ? _esc(claim) : '<em style="color:#94a3b8">pending evidence</em>') +
            '</div>'
          : '') +
        (_opts.finds && moreN
          ? '<div title="' + _esc(findings.slice(1).map(function (f) {
                return '• ' + ((f.summary || f.statement || f.id || '').replace(/\s+/g, ' ').trim());
              }).join('\n')) + '" ' +
            'style="font-size:0.72em;margin-top:2px;color:#94a3b8;cursor:help">+' + moreN +
            ' more finding' + (moreN === 1 ? '' : 's') + '</div>'
          : '') +
        (_opts.followups ? followUpsChip : '') +
        (_opts.chain && chainsBySlug && typeof window._chainBlockHtml === 'function'
          ? window._chainBlockHtml(chainsBySlug[s.name]) : '') +
        // Layer-4: cached/compute badge + downloads (↓figures/↓notebook, all
        // modes) + run/continue buttons (live only).
        _dagCacheBadgeHtml(s.name) +
        _dagDownloadControlsHtml(s.name) +
        _dagTriggerControlsHtml(s.name);
      node._followUps = followUps;
      nodesHost.appendChild(node);
      // Wire the pull-or-compute buttons (stopPropagation so the card's own
      // click-to-open handler doesn't also fire).
      node.querySelectorAll('.dag-trigger-run').forEach(function (b) {
        b.addEventListener('click', function (ev) {
          ev.stopPropagation();
          _triggerStudy(b.getAttribute('data-slug'), 'error', b);
        });
      });
      node.querySelectorAll('.dag-trigger-continue').forEach(function (b) {
        b.addEventListener('click', function (ev) {
          ev.stopPropagation();
          _triggerStudy(b.getAttribute('data-slug'), 'error', b);
        });
      });
      var _badge = node.querySelector('.aig-status-badge');
      if (_badge) {
        var _openReason = function (ev) {
          ev.stopPropagation();
          // The quick-look side-card is gone — the verdict badge opens the full
          // study (its findings/evidence live there).
          _openStudyInsideInvestigation(s.name);
        };
        _badge.addEventListener('click', _openReason);
        _badge.addEventListener('keydown', function (ev) { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); _openReason(ev); } });
      }
      if (chainsBySlug && window._groupClaims && window._openInvestigationDrawer) {
        (function (study, chain) {
          var claims = window._groupClaims(chain);
          node.querySelectorAll('.aig-claim-row').forEach(function (row) {
            row.addEventListener('click', function (ev) {
              ev.stopPropagation();
              _openStudyInsideInvestigation(study.name);
            });
          });
        })(s, chainsBySlug[s.name]);
      }
      pos[s.name] = { x: x, node: node, depth: depth[s.name] };
    });

    // Measure now that content is in the DOM (container is already visible).
    studies.forEach(function(s) { pos[s.name].h = pos[s.name].node.offsetHeight || 120; });

    // -- Pass 2: position the measured axis, then compute the canvas size --
    var canvasW, canvasH;
    if (orient === 'TB') {
      // Depth is now the ROW (y) axis. A row's height is the tallest card in
      // it (cards within a row sit side-by-side, not stacked), so rows must
      // be sequenced top->bottom using DEPTH_GAP between them. Each card is
      // then vertically centered within its own row's height.
      var rowHeights = {};
      depths.forEach(function(d) {
        var maxH = 0;
        byDepth[d].forEach(function(s) { if (pos[s.name].h > maxH) maxH = pos[s.name].h; });
        rowHeights[d] = maxH;
      });
      var rowY = {};
      var yc = PAD_Y;
      depths.forEach(function(d) {
        rowY[d] = yc;
        yc += rowHeights[d] + DEPTH_GAP;
      });
      canvasH = Math.max(yc - DEPTH_GAP + PAD_Y, 180);
      depths.forEach(function(d) {
        byDepth[d].forEach(function(s) {
          var y = rowY[d] + Math.max(0, (rowHeights[d] - pos[s.name].h) / 2);
          pos[s.name].y = y;
          pos[s.name].node.style.top = y + 'px';
        });
      });
      canvasW = canvasW_TB;
    } else {
      // Depth is the COLUMN (x) axis (already positioned in Pass 1). Breadth
      // is the y axis: stack same-depth cards vertically by measured height,
      // then center each column within the tallest column's total height.
      var colTotals = {};
      depths.forEach(function(d) {
        var sum = 0;
        byDepth[d].forEach(function(s) { sum += pos[s.name].h; });
        colTotals[d] = sum + Math.max(0, byDepth[d].length - 1) * BREADTH_GAP;
      });
      var maxCol = 0;
      depths.forEach(function(d) { if (colTotals[d] > maxCol) maxCol = colTotals[d]; });
      canvasH = Math.max(PAD_Y * 2 + maxCol, 180);
      depths.forEach(function(d) {
        var yc2 = PAD_Y + Math.max(0, (canvasH - PAD_Y * 2 - colTotals[d]) / 2);
        byDepth[d].forEach(function(s) {
          pos[s.name].y = yc2;
          pos[s.name].node.style.top = yc2 + 'px';
          yc2 += pos[s.name].h + BREADTH_GAP;
        });
      });
      canvasW = PAD_X * 2 + (depths.length ? depths[depths.length - 1] : 0) * (CARD_W + DEPTH_GAP) + CARD_W;
    }

    nodesHost.style.width = canvasW + 'px';
    nodesHost.style.height = canvasH + 'px';
    edgesSvg.setAttribute('width', canvasW);
    edgesSvg.setAttribute('height', canvasH);
    edgesSvg.style.width = canvasW + 'px';
    edgesSvg.style.height = canvasH + 'px';
    // Loom-like viewport: remember the canvas dims and apply the current zoom.
    // The shell keeps its CSS/user height (resize:vertical) instead of being
    // forced to fit — content scrolls/zooms inside it.
    var shellSize = document.getElementById('investigation-dag-shell');
    if (shellSize) { shellSize.dataset.canvasW = canvasW; shellSize.dataset.canvasH = canvasH; }
    _applyAigZoom();

    // Edges (drawn after positions are known), using measured heights.
    edgesSvg.innerHTML =
      '<defs><marker id="dag-arrowhead" viewBox="0 0 10 10" refX="9" refY="5" ' +
      'markerWidth="7" markerHeight="7" orient="auto-start-reverse">' +
      '<path d="M 0 0 L 10 5 L 0 10 z" fill="#94a3b8"/></marker></defs>';
    studies.forEach(function(s) {
      dagEdgesFor(s).forEach(function(p) {
        var pn = p.study;
        if (!pos[pn] || !pos[s.name]) return;
        // Endpoints follow the flow direction: LR connects parent's right edge
        // to child's left edge (both vertically centered); TB connects
        // parent's bottom edge to child's top edge (both horizontally
        // centered). The Bezier control offset is along that same flow axis
        // so the curve still reads as "leads to" in either orientation.
        var x1, y1, x2, y2, path_d;
        if (orient === 'TB') {
          x1 = pos[pn].x + CARD_W / 2;
          y1 = pos[pn].y + pos[pn].h;
          x2 = pos[s.name].x + CARD_W / 2;
          y2 = pos[s.name].y;
          var dy = Math.max(28, (y2 - y1) / 2);
          path_d = 'M ' + x1 + ' ' + y1 +
                   ' C ' + x1 + ' ' + (y1 + dy) +
                   ', ' + x2 + ' ' + (y2 - dy) +
                   ', ' + x2 + ' ' + y2;
        } else {
          x1 = pos[pn].x + CARD_W;
          y1 = pos[pn].y + pos[pn].h / 2;
          x2 = pos[s.name].x;
          y2 = pos[s.name].y + pos[s.name].h / 2;
          var dx = Math.max(28, (x2 - x1) / 2);
          path_d = 'M ' + x1 + ' ' + y1 +
                   ' C ' + (x1 + dx) + ' ' + y1 +
                   ', ' + (x2 - dx) + ' ' + y2 +
                   ', ' + x2 + ' ' + y2;
        }
        var rel = p.relation || 'leads-to';
        var st = _dagRelStyle(rel);
        var path = document.createElementNS(svgNS, 'path');
        path.setAttribute('d', path_d);
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
        // Offset the label perpendicular to the flow axis (up for LR's
        // horizontal flow, sideways for TB's vertical flow) so it doesn't sit
        // directly on top of the line.
        if (orient === 'TB') {
          label.setAttribute('x', (x1 + x2) / 2 + 8);
          label.setAttribute('y', (y1 + y2) / 2);
          label.setAttribute('text-anchor', 'start');
        } else {
          label.setAttribute('x', (x1 + x2) / 2);
          label.setAttribute('y', (y1 + y2) / 2 - 6);
          label.setAttribute('text-anchor', 'middle');
        }
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

  // ── Investigation-graph viewport: continuous zoom / pan / fit / fullscreen ──
  var aigZoom = 1;
  function _applyAigZoom() {
    var shell = document.getElementById('investigation-dag-shell');
    var nodes = document.getElementById('investigation-dag-nodes');
    var edges = document.getElementById('investigation-dag-edges');
    if (!shell || !nodes || !edges) return;
    var z = aigZoom;
    var cw = parseFloat(shell.dataset.canvasW) || nodes.offsetWidth || 0;
    var ch = parseFloat(shell.dataset.canvasH) || nodes.offsetHeight || 0;
    [nodes, edges].forEach(function (el) {
      el.style.transformOrigin = '0 0';
      el.style.transform = 'scale(' + z + ')';
    });
    // A spacer sized to the SCALED content so the shell's scroll extent matches
    // (transforms don't affect scrollWidth/Height on their own).
    var spacer = shell.querySelector('.aig-spacer');
    if (!spacer) {
      spacer = document.createElement('div');
      spacer.className = 'aig-spacer';
      spacer.style.cssText = 'position:absolute;top:0;left:0;pointer-events:none';
      shell.appendChild(spacer);
    }
    spacer.style.width = (cw * z) + 'px';
    spacer.style.height = (ch * z) + 'px';
  }
  function _aigZoomBy(f) { aigZoom = Math.max(0.3, Math.min(2.2, aigZoom * f)); _applyAigZoom(); }
  function _aigFit() {
    var shell = document.getElementById('investigation-dag-shell');
    if (!shell) return;
    var cw = parseFloat(shell.dataset.canvasW) || 1, ch = parseFloat(shell.dataset.canvasH) || 1;
    var z = Math.min((shell.clientWidth - 10) / cw, (shell.clientHeight - 10) / ch);
    aigZoom = Math.max(0.3, Math.min(1.5, z || 1));
    _applyAigZoom();
    shell.scrollLeft = 0; shell.scrollTop = 0;
  }
  function _aigFullscreen() {
    var shell = document.getElementById('investigation-dag-shell');
    if (!shell) return;
    if (document.fullscreenElement) { document.exitFullscreen(); }
    else if (shell.requestFullscreen) { shell.requestFullscreen().then(function () { setTimeout(_aigFit, 150); }); }
  }
  window._aigZoomBy = _aigZoomBy;
  window._aigFit = _aigFit;
  window._aigFullscreen = _aigFullscreen;
  // Drag anywhere on the shell background to pan (card clicks still work).
  document.addEventListener('pointerdown', function (e) {
    var shell = e.target.closest && e.target.closest('#investigation-dag-shell');
    if (!shell || (e.target.closest && e.target.closest('.iset-dag-node'))) return;
    var sx = e.clientX, sy = e.clientY, sl = shell.scrollLeft, st0 = shell.scrollTop, moved = false;
    shell.style.cursor = 'grabbing';
    function mv(ev) { moved = true; shell.scrollLeft = sl - (ev.clientX - sx); shell.scrollTop = st0 - (ev.clientY - sy); }
    function up() {
      document.removeEventListener('pointermove', mv);
      document.removeEventListener('pointerup', up);
      shell.style.cursor = '';
    }
    document.addEventListener('pointermove', mv);
    document.addEventListener('pointerup', up);
  });

  function _setAigBand(b) {
    var nb = Math.max(0, Math.min(2, b | 0));
    var sl = document.getElementById('aig-zoom-slider');
    if (sl && String(sl.value) !== String(nb)) sl.value = String(nb);
    // No band change → keep the slider synced (above) but skip the re-render.
    if (nb === aigBand) return;
    aigBand = nb;
    if (_lastDagArgs) _renderInvestigationDag(_lastDagArgs[0], _lastDagArgs[1], _lastDagArgs[2]);
  }
  window._setAigBand = _setAigBand;

  // Semantic zoom is driven ONLY by the top-right zoom slider (_setAigBand).
  // Scroll-to-zoom-into-a-card was removed intentionally — hijacking the wheel
  // over cards was confusing; the wheel now always scrolls the page normally.

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
        window.location.href = (window.__BASE_PATH__ || '') + '/studies/' +
          encodeURIComponent(res.body.new_study_name);
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

  function _drawerStudyHtml(s) {
    var q = (s.question || '').replace(/\s+/g, ' ').trim();
    return '<div style="font-weight:700;color:#0f172a">' + _esc(s.title || s.name) + '</div>' +
      '<div style="font-size:0.78em;color:#64748b;margin:2px 0 8px">' + _esc(s.effective_status || s.status || '') + '</div>' +
      (q ? '<div style="margin:6px 0"><span style="font-weight:600;color:#475569">Asks: </span>' + _esc(q) + '</div>' : '') +
      '<button class="drawer-open-study" data-study="' + _esc(s.name) + '" style="margin-top:10px;cursor:pointer">Open full study →</button>';
  }

  function _drawerBlock(label, node, extra) {
    if (!node) return '';
    return '<div style="margin:9px 0;padding:8px 10px;border:1px solid #e5e7eb;border-radius:8px">' +
      '<div style="font-size:0.72em;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:#64748b">' +
      label + (node.lifecycle_state ? ' · ' + _esc(node.lifecycle_state) : '') + (extra || '') + '</div>' +
      '<div style="margin-top:3px;color:#1e293b">' + _esc(node.statement || node.label || '') + '</div></div>';
  }

  function _drawerClaimHtml(claim, study) {
    var P = claim.parts || {};
    var dec = P.decision ? _drawerBlock('▣ Decision', P.decision, P.decision.outcome ? ' · ' + _esc(P.decision.outcome) : '') : '';
    var prov = claim.source
      ? 'Derived from ' + _esc(study ? study.name : '') + ' · ' + _esc(claim.source)
      : 'Authored' + (study ? ' in ' + _esc(study.name) : '');
    return '<div style="font-weight:700;color:#0f172a;line-height:1.3">' + _esc(claim.claimText) + '</div>' +
      '<div style="font-size:0.8em;color:#64748b;margin:2px 0 8px">' + _esc(claim.status) + '</div>' +
      _drawerBlock('● Finding', P.finding) +
      _drawerBlock('◆ Evidence', P.evidence) +
      dec +
      _drawerBlock('★ Conclusion', P.conclusion) +
      '<div style="margin-top:10px;font-size:0.74em;color:#94a3b8">' + prov + '</div>' +
      (study ? '<button class="drawer-open-study" data-study="' + _esc(study.name) + '" style="margin-top:10px;cursor:pointer">Open full study →</button>' : '');
  }

  function _openInvestigationDrawer(kind, data) {
    var drawer = document.getElementById('investigation-detail-drawer');
    var body = document.getElementById('investigation-detail-drawer-body');
    if (!drawer || !body) return;
    if (kind === 'claim') body.innerHTML = _drawerClaimHtml(data.claim, data.study);
    else if (kind === 'study') body.innerHTML = _drawerStudyHtml(data);
    else return;
    drawer.style.display = 'block';
    var btn = body.querySelector('.drawer-open-study');
    if (btn) btn.addEventListener('click', function () {
      drawer.style.display = 'none';
      _openStudyInsideInvestigation(btn.getAttribute('data-study'));
    });
  }
  window._openInvestigationDrawer = _openInvestigationDrawer;

  // Click a DAG node → load the full study in an in-page iframe BELOW the
  // DAG (no jump to the legacy Studies tab). The iframe is the same
  // /studies/<name> route the standalone embed uses.
  function _openStudyInsideInvestigation(name, tab) {
    // Unified with every other study-open entry point: route through the single
    // workspace router so a graph node / ref link / needs-attention button opens
    // the study exactly like clicking a study card — the investigation context
    // collapses to the slim bar and the study opens as a tab in the porthole.
    // (Previously this embedded a separate panel BELOW the graph with its own
    // Pop-out/×, leaving the context expanded — the inconsistency this fixes.)
    _openStudyEmbeddedNewTab(name, tab);
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
  // The investigation report is generated server-side (GET
  // /api/investigation-report/<slug>) — a deterministic, data-only, fully
  // self-contained document. In a static bundle (no server) publish.py
  // pre-renders it to reports/investigation-<slug>.html and this opens that
  // file instead. (Replaced the old client-side fan-out builder, now removed.)
  function _investigationReportUrl(name) {
    var cfg = window.__DASH_CONFIG__ || {};
    var base = cfg.basePath || '';
    return (cfg.mode === 'snapshot')
      ? base + '/reports/investigation-' + encodeURIComponent(name) + '.html'
      : '/api/investigation-report/' + encodeURIComponent(name);
  }
  function _downloadInvestigationReport(name) {
    name = name || window._wsInvestigation || window._currentIset;
    if (!name) {
      console.warn('_downloadInvestigationReport: no current investigation');
      return;
    }
    var cfg = window.__DASH_CONFIG__ || {};
    // Live: hit the endpoint with ?download=1 so the server sends
    // Content-Disposition: attachment and the browser saves it. Snapshot: the
    // pre-rendered static file, downloaded via the anchor's `download` attr.
    var url = _investigationReportUrl(name) + (cfg.mode === 'snapshot' ? '' : '?download=1');
    var a = document.createElement('a');
    a.href = url;
    a.download = 'investigation-' + name + '.html';
    a.rel = 'noopener';
    document.body.appendChild(a);
    a.click();
    a.remove();
  }
  window._downloadInvestigationReport = _downloadInvestigationReport;

  // Per-card actions on the Investigations LIST (don't require opening the
  // investigation) — open the report for the named investigation directly.
  window._vivReportFromCard = function (ev, name) {
    if (ev) ev.stopPropagation();
    _downloadInvestigationReport(name);
  };
  window._vivNotebookFromCard = function (ev, name) {
    if (ev) ev.stopPropagation();
    var c = window.__DASH_CONFIG__ || {};
    var base = c.basePath || '';
    var url = (c.mode === 'snapshot')
      ? base + '/investigation-notebooks/' + encodeURIComponent(name) + '.ipynb'
      : '/api/investigation-notebook/' + encodeURIComponent(name);
    var a = document.createElement('a');
    a.href = url; a.download = name + '.ipynb';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
  };
  // A SINGLE study's runnable notebook (just that study's cells). Live: the
  // server generates it on demand at /api/study/<slug>/notebook. Snapshot: a
  // static export has no per-study artifact, so fall back to the investigation
  // notebook (which includes this study) via _vivNotebookFromCard.
  window._vivStudyNotebookFromCard = function (ev, slug, invName) {
    if (ev) ev.stopPropagation();
    var c = window.__DASH_CONFIG__ || {};
    if (c.mode === 'snapshot') {
      return window._vivNotebookFromCard(ev, invName || slug);
    }
    var url = '/api/study/' + encodeURIComponent(slug) + '/notebook';
    var a = document.createElement('a');
    a.href = url; a.download = slug + '.ipynb';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
  };
  // Download the FULL figure archive for an investigation (every study's figures
  // + the post-study composites) as a zip. Snapshot: a prebuilt static zip under
  // figures/<slug>/; live: the server builds it on demand.
  window._vivFiguresFromCard = function (ev, name) {
    if (ev) ev.stopPropagation();
    var c = window.__DASH_CONFIG__ || {};
    var base = c.basePath || '';
    var url = (c.mode === 'snapshot')
      ? base + '/figures/' + encodeURIComponent(name) + '/figures.zip'
      : '/api/investigation/' + encodeURIComponent(name) + '/figures.zip';
    var a = document.createElement('a');
    a.href = url; a.download = name + '-figures.zip';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
  };
  // A single study's OUTPUTS (its image figures + any embedded HTML report /
  // dashboard and its viz assets) as a zip.
  window._vivStudyFiguresFromCard = function (ev, slug) {
    if (ev) ev.stopPropagation();
    var c = window.__DASH_CONFIG__ || {};
    var base = c.basePath || '';
    var url = (c.mode === 'snapshot')
      ? base + '/figures/studies/' + encodeURIComponent(slug) + '.zip'
      : '/api/study/' + encodeURIComponent(slug) + '/outputs.zip';
    // Probe before downloading. The ↓ visualizations button renders whenever a
    // study declares any `visualizations`, but the figures zip only contains
    // declared IMAGE files (svg/png/gif). A study whose visualizations are all
    // native/embed panels therefore has no zip — and in a snapshot the file is
    // simply absent (404). A bare `<a download>` to a 404 silently does nothing,
    // which reads as a broken button. Fetch first: download the blob when it
    // exists, otherwise tell the user why there's nothing to grab.
    function _notify(msg) {
      if (typeof _showToast === 'function') _showToast(msg); else window.alert(msg);
    }
    fetch(url).then(function (r) {
      if (!r.ok) {
        _notify('No downloadable figures for "' + slug + '" '
          + '(no figures or embedded HTML reports).');
        return null;
      }
      return r.blob();
    }).then(function (blob) {
      if (!blob) return;
      var href = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = href; a.download = slug + '-figures.zip';
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      window.setTimeout(function () { URL.revokeObjectURL(href); }, 1000);
    }).catch(function (e) {
      _notify('Figures download failed: ' + e);
    });
  };
  // A study's ↓ notebook is its parent investigation's runnable notebook (there
  // is no per-study notebook). In the graph the parent is the open investigation.
  window._vivStudyNotebookFromCard = function (ev, slug) {
    if (ev) ev.stopPropagation();
    var inv = window._wsInvestigation || window._currentIset || '';
    if (inv && window._vivNotebookFromCard) window._vivNotebookFromCard(ev, inv);
  };
  // ▶ run — launch a study's CURRENT baseline spec as a new run (live only).
  window._vivRunStudyFromRow = function (ev, slug) {
    if (ev) ev.stopPropagation();
    if ((window.__DASH_CONFIG__ || {}).mode === 'snapshot') return;
    if (!confirm("Run this study's current baseline spec as a new run?")) return;
    fetch('/api/study-run-baseline', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({study: slug}),
    }).then(function (r) { return r.json(); }).then(function (j) {
      var id = j && (j.run_id || j.simulation_id);
      var msg = id ? ('Run launched — ' + id) : ('Run: ' + ((j && j.error) || 'done'));
      if (typeof _showToast === 'function') _showToast(msg); else alert(msg);
    }).catch(function (e) { alert('Run failed: ' + e); });
  };
  // ↻ reproduce — replay a study's most recent run's recorded manifest (live
  // only). Resolves the latest run id from /api/simulations first.
  window._vivReproduceStudyFromRow = function (ev, slug) {
    if (ev) ev.stopPropagation();
    if ((window.__DASH_CONFIG__ || {}).mode === 'snapshot') return;
    fetch('/api/simulations?study=' + encodeURIComponent(slug))
      .then(function (r) { return r.json(); })
      .then(function (j) {
        var rows = (j && (j.simulations || j.runs)) || [];
        var latest = rows[0] && (rows[0].run_id || rows[0].id || rows[0].name);
        if (!latest) { alert('No run to reproduce yet for ' + slug + '.'); return; }
        return fetch('/api/study-reproduce', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({study: slug, run_id: latest}),
        }).then(function (r) { return r.json(); }).then(function (res) {
          var id = res && res.run_id;
          var msg = id ? ('Reproduce launched — ' + id) : ('Reproduce: ' + ((res && res.error) || 'done'));
          if (typeof _showToast === 'function') _showToast(msg); else alert(msg);
        });
      }).catch(function (e) { alert('Reproduce failed: ' + e); });
  };

  // Download the coder-facing notebook for the current investigation. In a
  // published (snapshot) bundle this is a static file under
  // investigation-notebooks/; in local mode the server generates it on demand.
  // Optional fmt === 'py' fetches the matching script instead of the .ipynb.
  function _downloadInvestigationNotebook(fmt) {
    var name = window._currentIset;
    if (!name) {
      console.warn('_downloadInvestigationNotebook: no current investigation');
      return;
    }
    var c = window.__DASH_CONFIG__ || {};
    var base = c.basePath || '';
    var ext = fmt === 'py' ? '.py' : '.ipynb';
    var url = (c.mode === 'snapshot')
      ? base + '/investigation-notebooks/' + encodeURIComponent(name) + ext
      : '/api/investigation-notebook/' + encodeURIComponent(name) + (fmt === 'py' ? '?format=py' : '');
    var a = document.createElement('a');
    a.href = url;
    a.download = name + ext;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }
  window._downloadInvestigationNotebook = _downloadInvestigationNotebook;

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
  var _TRACK_COLORS = {
    PASS: ['#dcfce7', '#166534'], PARTIAL: ['#fef3c7', '#92400e'],
    FAIL: ['#fee2e2', '#991b1b'], GAP: ['#f1f5f9', '#475569'], PENDING: ['#f1f5f9', '#475569']
  };
  // ── Shared run/outcome helpers (bug-fix: pills + decision read the run that
  // actually CARRIES outcomes, not blindly runs[last]) ───────────────────────
  // A study's recorded test outcomes live on its canonical/grade run, which is
  // often NOT the last run in the list (composite/sim runs land after it with no
  // outcomes). Selecting runs[last] made every pill render ⏳ PENDING even though
  // outcomes were recorded. Pick the latest run that actually has outcomes (or a
  // canonical run), falling back to the last run for run-identity displays.
  function _runWithOutcomes(runs) {
    if (!runs || !runs.length) return null;
    var i;
    for (i = runs.length - 1; i >= 0; i--) {
      var r = runs[i];
      if (r && ((r.outcomes && Object.keys(r.outcomes).length) ||
                (r.computed_outcomes && Object.keys(r.computed_outcomes).length))) {
        return r;
      }
    }
    for (i = runs.length - 1; i >= 0; i--) {
      if (runs[i] && runs[i].canonical) return runs[i];
    }
    return runs[runs.length - 1];
  }
  // Normalize a single recorded outcome value into an object with a `result`.
  // Outcomes may be authored as a bare UPPERCASE string ("PASS"/"FAIL"/...) OR
  // as an object {result, detail, ...}. A bare string fed to Object.assign({})
  // becomes {0:'P',1:'A',...} with no `.result`, which silently read as PENDING.
  function _normOutcome(v) {
    if (v == null) return null;
    if (typeof v === 'string') return { result: v.trim().toUpperCase() };
    if (typeof v === 'object') return Object.assign({}, v);
    return { result: String(v) };
  }
  // Map an authored tests[].status (passed/failed/partial/skipped) onto the
  // UPPERCASE result vocabulary the pills use, so a recorded status surfaces even
  // when no per-run outcome is present.
  function _testStatusToResult(st) {
    if (st == null) return null;
    return ({ passed: 'PASS', pass: 'PASS', ok: 'PASS',
              failed: 'FAIL', fail: 'FAIL', error: 'FAIL',
              partial: 'PARTIAL', mixed: 'PARTIAL',
              skipped: 'SKIP', skip: 'SKIP' })[String(st).trim().toLowerCase()] || null;
  }
  // The study's declared tests as an ARRAY. `tests:` may be authored as a dict
  // (e.g. {auto_discover: true}) while the real list lives on `behavior_tests:`,
  // so pick the first array-valued field (a dict has no .forEach/.length).
  function _studyTests(s) {
    if (!s || typeof s !== 'object') return [];
    var cands = [s.tests, s.behavior_tests, s.expected_behavior];
    for (var i = 0; i < cands.length; i++) {
      if (Array.isArray(cands[i])) return cands[i];
    }
    return [];
  }
  // Is this study a descriptive/informational reference (no hypothesis test)?
  // Such studies have no pass/fail gate; the planning/not-run framing and the ⚠
  // "needs work" verdict pill are wrong for them.
  function _isInformationalStudy(s) {
    if (!s || typeof s !== 'object') return false;
    var verdict = String(((s.report || {}).verdict) || s.verdict || '').trim().toLowerCase();
    if (verdict === 'informational' || verdict === 'descriptive') return true;
    var gate = String(s.gate_status || '').trim().toLowerCase();
    var nTests = _studyTests(s).length;
    if ((gate === 'not_applicable' || gate === 'n/a' || gate === 'na') && !nTests) return true;
    return false;
  }
  // Format an evidence.observed value into display-SAFE HTML (callers must NOT
  // re-wrap in _h). Scalars/strings are escaped and passed through; arrays join
  // with commas; dicts render as readable "key: value" pairs (was String(obj) →
  // the literal "[object Object]"). Recursion returns already-escaped HTML.
  function _fmtObserved(v) {
    if (v == null) return '';
    if (typeof v === 'number') return _h(String(Math.round(v * 1000) / 1000));
    if (typeof v === 'string') return _h(v);
    if (Array.isArray(v)) {
      return v.map(function(x) { return _fmtObserved(x); }).join(', ');
    }
    if (typeof v === 'object') {
      var pairs = Object.keys(v).map(function(k) {
        return _h(k) + ': <strong>' + _fmtObserved(v[k]) + '</strong>';
      });
      return pairs.length ? pairs.join(' · ') : '';
    }
    return _h(String(v));
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
  // `findings` is authored EITHER as a list, OR as a {entries:[...]} object
  // (e.g. mbp-06-gap-analysis), OR a dict keyed by id. Normalize to an array so
  // consumers never call .filter/.forEach/.map on a non-array (which throws and
  // aborts the whole report generation).
  function _asFindings(v) {
    if (Array.isArray(v)) return v;
    if (v && typeof v === 'object') return Array.isArray(v.entries) ? v.entries : Object.values(v);
    return [];
  }
  function _conclusionVerdictsHtml(s, slug) {
    var cv = (s.derived || {}).conclusion_verdicts || {
      biological_validation: { result: 'PENDING' },
      regression_compatibility: { result: 'PENDING' },
      explanatory_gain: { result: 'GAP' }
    };
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
    var findings = _asFindings(s.findings).filter(function(f) { return f && typeof f === 'object'; });
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
  // The investigation a study belongs to (from the iset index), or '' if none.
  function _investigationForStudy(slug) {
    var iset = (window._isetIndex || []).find(function(i) {
      return (i.studies || []).indexOf(slug) !== -1;
    });
    return iset ? iset.name : '';
  }

  // Is `slug` a member of investigation `invName`? Used so opening a study from
  // a graph node keeps the investigation you clicked from (a study can belong to
  // several investigations; _investigationForStudy returns only the FIRST).
  function _studyInInvestigation(slug, invName) {
    if (!invName) return false;
    var iset = (window._isetIndex || []).find(function(i) { return i.name === invName; });
    return !!(iset && (iset.studies || []).indexOf(slug) !== -1);
  }
  window._studyInInvestigation = _studyInInvestigation;
  window._investigationForStudy = _investigationForStudy;

  function _openStudyEmbeddedNewTab(name, tab) {
    // Single router: show the study's OWN investigation workspace, then
    // open/focus its study tab. Never the legacy icon view, never full-window nav.
    // Optional `tab` deep-links the porthole to a study sub-tab (e.g. conclusions).
    // Prefer the investigation you're already viewing if this study belongs to it,
    // so opening a study from investigation B's graph doesn't reroute to the study's
    // primary (first) investigation A. Fall back to the first-membership lookup.
    var _cur = window._wsInvestigation;
    var inv = _studyInInvestigation(name, _cur) ? _cur : _investigationForStudy(name);
    if (inv) {
      if (window._wsInvestigation !== inv) _showInvestigationWorkspace(inv);
      else _showWorkspace();
      _wsOpenStudyTab(name, tab);
    } else {
      // Ungrouped study: minimal workspace (no graph), just the study tab.
      window._wsInvestigation = null;
      _showWorkspace();
      // #ws-context may host the relocated shared #investigation-detail-view;
      // HIDE it rather than wipe innerHTML (which would destroy the node).
      var ctx = document.getElementById('ws-context');
      var view = document.getElementById('investigation-detail-view');
      if (view && view.parentNode === ctx) view.style.display = 'none';
      else if (ctx) ctx.innerHTML = '';
      var t = document.getElementById('ws-title'); if (t) t.textContent = 'Study: ' + name;
      var a = document.getElementById('ws-actions'); if (a) a.innerHTML = '';
      _wsResetStudyTabs(null);
      _wsOpenStudyTab(name, tab);
    }
    _selectStudyInRail(name);
  }
  window._openStudyEmbeddedNewTab = _openStudyEmbeddedNewTab;

  // Reflect the open study in the sidebar: highlight its leaf + reveal its
  // investigation group. Tolerant if the rail hasn't rendered the leaf yet.
  function _selectStudyInRail(name) {
    document.querySelectorAll('.viv-rail-sublink.rail-study-active').forEach(function (a) {
      a.classList.remove('rail-study-active'); a.style.background = '';
    });
    var sel = '.viv-rail-sublink[data-study-name="' +
      (window.CSS && CSS.escape ? CSS.escape(name) : name) + '"]';
    var leaf = document.querySelector(sel);
    if (!leaf) return;
    leaf.classList.add('rail-study-active');
    leaf.style.background = '#eef2ff';
    // If its investigation group is collapsed, expand it so the leaf is visible.
    var grp = leaf.closest('[data-rail-group], .viv-rail-investigations-group');
    if (grp && grp.classList.contains('collapsed') &&
        typeof _vivToggleInvGroup === 'function') {
      var hdr = grp.querySelector('.viv-rail-investigations-group-header');
      if (hdr) _vivToggleInvGroup(hdr);
    }
    try { leaf.scrollIntoView({ block: 'nearest' }); } catch (e) { /* ignore */ }
  }
  window._selectStudyInRail = _selectStudyInRail;

  // Sidebar grouping: studies-by-investigation, collapsible.
  // Replaces the existing flat-list render in #viv-rail-investigations.
  // Map a study's free-form status string to a small colored dot. Keeps the
  // rail rows readable: the study NAME gets the full row width, the dot is a
  // glanceable status, the full status text is shown in the title tooltip.
  function _railStatusColor(status) {
    var s = String(status || '').toLowerCase();
    if (s.indexOf('fail') !== -1 || s.indexOf('invalid') !== -1 || s.indexOf('blocked') !== -1) return '#ef4444';   // red
    if (s.indexOf('pending') !== -1 || s.indexOf('refresh') !== -1 || s.indexOf('needs') !== -1) return '#f59e0b';// amber
    if (s.indexOf('inconclusive') !== -1 || s.indexOf('partial') !== -1) return '#d97706'; // dark amber
    if (s.indexOf('running') === 0) return '#3b82f6';                                // blue
    // 'pass' covers the gate verdict 'passed' as well as 'passing'/'passes'.
    if (s.indexOf('done') === 0 || s.indexOf('ran') === 0 || s.indexOf('complete') !== -1
        || s.indexOf('evaluated') !== -1 || s.indexOf('confirmed') !== -1 || s.indexOf('pass') !== -1
        || s.indexOf('accept') !== -1 || s.indexOf('decided') !== -1
        || s.indexOf('-wins') !== -1 || s.indexOf('in-band') !== -1) return '#16a34a'; // green
    if (s.indexOf('evaluate') === 0) return '#6366f1';                               // indigo (mid-pass action)
    return '#9ca3af';                                                                // gray (planned/unknown)
  }

  // Pinned studies: a per-user convenience, kept in localStorage (no workspace
  // write). A pinned study is duplicated into a "Pinned" strip at the top of the
  // STUDIES rail for quick access while still appearing in its own group.
  function _loadPinnedStudies() {
    try {
      var raw = window.localStorage.getItem('viv.pinnedStudies');
      window._pinnedStudies = raw ? JSON.parse(raw) : [];
    } catch (e) { window._pinnedStudies = []; }
    if (!Array.isArray(window._pinnedStudies)) window._pinnedStudies = [];
    return window._pinnedStudies;
  }
  function _isStudyPinned(name) {
    if (!window._pinnedStudies) _loadPinnedStudies();
    return window._pinnedStudies.indexOf(name) !== -1;
  }
  function _toggleStudyPin(name) {
    if (!window._pinnedStudies) _loadPinnedStudies();
    var i = window._pinnedStudies.indexOf(name);
    if (i === -1) window._pinnedStudies.push(name);
    else window._pinnedStudies.splice(i, 1);
    try { window.localStorage.setItem('viv.pinnedStudies', JSON.stringify(window._pinnedStudies)); } catch (e) { /* private mode */ }
    if (typeof _renderRailInvestigationGroups === 'function') _renderRailInvestigationGroups();
  }
  window._toggleStudyPin = _toggleStudyPin;

  // Single-row per study: [dot] name [pin]. Full status string in tooltip. The
  // pin toggle sits at the right; clicking it pins/unpins without opening the
  // study (stopPropagation). Used by the grouped, pinned, and ungrouped layouts.
  function _railStudyItem(s, opts) {
    opts = opts || {};
    var status = s.status || 'planned';
    var color = _railStatusColor(status);
    var indent = opts.indent ? '28px' : '12px';
    var fontSize = opts.indent ? '0.85em' : '0.86em';
    var nameColor = opts.indent ? '#64748b' : '#374151';
    var pinned = _isStudyPinned(s.name);
    var tip = _esc(s.name) + ' — ' + _esc(status) + (s.blocked ? ' (blocked)' : '');
    var pinBtn = '<span class="viv-rail-pin' + (pinned ? ' pinned' : '') + '" role="button" tabindex="0" ' +
           'aria-label="' + (pinned ? 'Unpin study' : 'Pin study to top') + '" ' +
           'title="' + (pinned ? 'Unpin' : 'Pin to top') + '" ' +
           'onclick="event.preventDefault();event.stopPropagation();_toggleStudyPin(\'' + _esc(s.name) + '\');return false;">📌</span>';
    return '<a class="viv-rail-sublink' + (pinned ? ' viv-rail-sublink-pinned' : '') + '" data-study-name="' + _esc(s.name) + '" ' +
           'onclick="event.preventDefault();_openStudyEmbeddedNewTab(\'' + _esc(s.name) + '\');return false;" ' +
           'href="#" title="' + tip + '" ' +
           'style="display:flex;align-items:center;gap:8px;padding:4px 14px 4px ' + indent + ';color:' + nameColor + ';text-decoration:none;font-size:' + fontSize + ';">' +
             '<span aria-hidden="true" style="flex:none;width:8px;height:8px;border-radius:50%;background:' + color + ';display:inline-block"></span>' +
             '<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1">' + _esc(s.name) + '</span>' +
             pinBtn +
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
    // Show EVERY study in the repo, grouped by investigation. The active
    // investigation (window._currentIsetSlug, which follows the current
    // branch/context) is rendered first and expanded; all other
    // investigations follow as collapsed groups. Studies that belong to no
    // investigation collect in a final "Ungrouped" group so nothing is hidden.
    var currentSlug = window._currentIsetSlug || '';
    var railDepthMap = window._investigationsDepth || {};

    // Order: pinned first (in pin order), then most-recently-opened, then the
    // never-opened rest by topological depth then title. The active
    // investigation was just opened so MRU floats it to the top of the unpinned.
    var _mru = _loadInvMru();
    var _pins = _loadPinnedInvestigations();
    var ordered = groups.slice().sort(function(a, b) {
      var ap = _pins.indexOf(a.name), bp = _pins.indexOf(b.name);
      var aP = ap !== -1, bP = bp !== -1;
      if (aP !== bP) return aP ? -1 : 1;
      if (aP && bP) return ap - bp;
      var am = _mru[a.name] || 0, bm = _mru[b.name] || 0;
      if (am !== bm) return bm - am;   // most-recently-opened first
      var da = railDepthMap[a.name] || 0, db = railDepthMap[b.name] || 0;
      return da - db || String(a.title || a.name).localeCompare(String(b.title || b.name));
    });

    // Studies not a member of any investigation. These render as a plain flat
    // list at the BOTTOM of the rail (no collapsible group wrapper) rather than
    // inside an "Ungrouped" dropdown, so loose studies are always visible.
    var ungrouped = window._investigations.filter(function(s) { return !seen[s.name]; });
    ungrouped.sort(function(a, b) { return String(a.name).localeCompare(String(b.name)); });

    if (!window._pinnedStudies) _loadPinnedStudies();
    var hasActive = ordered.some(function(g) { return g.name === currentSlug; });

    function _railGroupHtml(g, forceOpen) {
      var isActive = g.name === currentSlug;
      // Collapsed unless active, or the caller forces it open (first group when
      // there is no active investigation), so the rail opens on something.
      var collapsed = (isActive || forceOpen) ? '' : ' collapsed';
      var activeCls = isActive ? ' rail-iset-active' : '';
      var clickName = g._ungrouped
        ? ''
        : ' onclick="window._railOpenInvestigationDetail(\'' + _esc(g.name) + '\');event.stopPropagation();"';
      var nameStyle = g._ungrouped ? '' : 'cursor:pointer;';
      return '<div class="viv-rail-investigations-group' + collapsed + activeCls + '" data-iset="' + _esc(g.name) + '">'
        + '<div class="viv-rail-investigations-group-header" onclick="_vivToggleInvGroup(this)"'
        + ' title="' + _esc(g.title || g.name) + (g._ungrouped ? '' : ' — open investigation') + '">'
        + '<span class="viv-rail-investigations-group-arrow viv-arrow">▾</span>'
        + '<span class="viv-rail-investigations-group-name" style="' + nameStyle + '"' + clickName + '>'
        + _esc(g.title || g.name) + '</span>'
        + (g._ungrouped ? '' :
            '<span class="viv-rail-pin viv-rail-inv-pin' + (_isInvestigationPinned(g.name) ? ' pinned' : '') + '"'
            + ' role="button" tabindex="0"'
            + ' title="' + (_isInvestigationPinned(g.name) ? 'Unpin investigation' : 'Pin investigation to top') + '"'
            + ' onclick="event.preventDefault();event.stopPropagation();_toggleInvestigationPin(\'' + _esc(g.name) + '\');return false;">📌</span>')
        + '<span class="viv-rail-investigations-group-count">' + g.studies.length + '</span>'
        + '</div>'
        + '<div class="viv-rail-investigations-group-items">'
        + (g.studies.length
            ? g.studies.map(function(s) { return _railStudyItem(s, { indent: true }); }).join('')
            : '<div class="viv-rail-empty" style="font-size:0.82em;color:#94a3b8;'
              + 'padding:4px 14px 4px 28px;font-style:italic">No studies</div>')
        + '</div>'
        + '</div>';
    }

    // Live study-search filter (the #viv-rail-study-search input). Token-AND
    // over a broad per-study haystack (name/title/objective/tags + the group
    // title), so e.g. "basal simulation" finds the `basal` study in the
    // v2ecoli-vEcoli comparison investigation. While searching, non-matching
    // groups are hidden and matching groups are force-expanded so hits show.
    var q = (window._railStudyQuery || '').trim().toLowerCase();
    var tokens = q ? q.split(/\s+/) : [];
    var searching = tokens.length > 0;

    // AND-first, OR-fallback. Prefer studies matching EVERY token (precise); but
    // if nothing matches all tokens, fall back to matching ANY token so a natural
    // phrase like "basal simulation" still surfaces the `basal` study even when
    // "simulation" appears in none of its fields. Consider grouped + ungrouped.
    var requireAll = searching && (
      ordered.some(function(g) {
        return g.studies.some(function(s) { return _studyMatchesQuery(s, g.title, tokens, true); });
      }) ||
      ungrouped.some(function(s) { return _studyMatchesQuery(s, 'Ungrouped', tokens, true); })
    );

    // Pinned strip (top): duplicates of pinned studies for quick access. Hidden
    // while searching so results stay clean. A pinned study still shows in its
    // own group/ungrouped list below.
    var pinnedHtml = '';
    if (!searching && (window._pinnedStudies || []).length) {
      var pinnedStudies = window._pinnedStudies
        .map(function(name) {
          return window._investigations.find(function(s) { return s.name === name; });
        })
        .filter(Boolean);
      if (pinnedStudies.length) {
        pinnedHtml = '<div class="viv-rail-pinned-section">'
          + '<div class="viv-rail-section-subheader">Pinned</div>'
          + pinnedStudies.map(function(s) { return _railStudyItem(s, {}); }).join('')
          + '</div>';
      }
    }

    // Investigation groups (middle).
    var groupsHtml = ordered.map(function(g, i) {
      var studies = g.studies;
      if (searching) {
        studies = g.studies.filter(function(s) {
          return _studyMatchesQuery(s, g.title, tokens, requireAll);
        });
        if (!studies.length) return '';   // hide groups with no match
        g = { name: g.name, title: g.title, studies: studies };
      }
      // While searching, force groups open so matches are visible. Otherwise:
      // with no active investigation, open the first group so the rail isn't
      // entirely collapsed on load.
      return _railGroupHtml(g, searching || (!hasActive && i === 0));
    }).join('');

    // Ungrouped studies (bottom): a collapsible "Ungrouped" folder, like the
    // investigation groups (collapsed by default; force-open while searching).
    var ungroupedList = searching
      ? ungrouped.filter(function(s) { return _studyMatchesQuery(s, 'Ungrouped', tokens, requireAll); })
      : ungrouped;
    var ungroupedHtml = '';
    if (ungroupedList.length) {
      ungroupedHtml = _railGroupHtml(
        { name: '__ungrouped__', title: 'Ungrouped', studies: ungroupedList, _ungrouped: true },
        searching
      );
    }

    var html = pinnedHtml + groupsHtml + ungroupedHtml;

    if (!html && searching) {
      html = '<div class="viv-rail-empty" style="font-size:0.85em;color:#94a3b8;'
           + 'padding:6px 14px;font-style:italic">No studies match “' + _esc(q) + '”.</div>';
    }

    host.innerHTML = html
      || '<div class="viv-rail-empty" style="font-size:0.85em;color:#94a3b8;'
       + 'padding:6px 14px;font-style:italic">No studies yet.</div>';
  }

  // A study matches the rail search when EVERY whitespace-delimited token of the
  // query is a substring of its combined searchable text (study fields + the
  // investigation/group title it's rendered under). Broad + forgiving on purpose.
  // ── Shared search engine ────────────────────────────────────────────
  // ONE implementation for every search box (side-rail studies, Investigations
  // tab, Studies grid) so behaviour never diverges. Token match with the caller
  // deciding AND (requireAll) vs OR across its candidate set — enabling the
  // AND-first/OR-fallback pattern used everywhere.
  function _tokenize(q) {
    q = String(q || '').trim().toLowerCase();
    return q ? q.split(/\s+/) : [];
  }
  function _searchHay(parts) {
    return parts.filter(Boolean).map(String).join(' ').toLowerCase();
  }
  function _tokensMatch(hay, tokens, requireAll) {
    if (!tokens || !tokens.length) return true;
    return requireAll
      ? tokens.every(function(t) { return hay.indexOf(t) !== -1; })
      : tokens.some(function(t) { return hay.indexOf(t) !== -1; });
  }
  // A study's searchable haystack (+ optional extra text, e.g. its group title).
  function _studyHay(s, extra) {
    return _searchHay([s.name, s.title, s.slug, s.objective, s.question,
      s.description, s.summary, s.status,
      Array.isArray(s.tags) ? s.tags.join(' ') : '', extra]);
  }

  function _studyMatchesQuery(s, groupTitle, tokens, requireAll) {
    return _tokensMatch(_studyHay(s, groupTitle), tokens, requireAll);
  }

  // Study-search input handler: store the query and re-render the rail groups.
  window._filterRailStudies = function(value) {
    window._railStudyQuery = String(value || '');
    _renderRailInvestigationGroups();
  };

  // Per-workspace localStorage key for the remembered investigation. The URL
  // path differs per hosted workspace (base-path), so it namespaces cleanly.
  function _railIsetKey() {
    return 'viv:rail-iset:' + (window.location.pathname || '/');
  }

  // Build the investigation <select> shown at the top of the STUDIES rail.
  function _railInvestigationPicker(currentSlug) {
    var isets = (window._isetIndex || []).slice().sort(function(a, b) {
      return String(a.title || a.name).localeCompare(String(b.title || b.name));
    });
    var opts = ['<option value="">Choose an investigation…</option>'];
    isets.forEach(function(i) {
      var sel = i.name === currentSlug ? ' selected' : '';
      opts.push('<option value="' + _esc(i.name) + '"' + sel + '>'
        + _esc(i.title || i.name) + '</option>');
    });
    return '<select class="rail-iset-picker" style="width:calc(100% - 24px);'
      + 'margin:2px 12px 6px;padding:3px 6px;font-size:0.82em;color:#374151;'
      + 'border:1px solid #e5e7eb;border-radius:4px;background:#fff;cursor:pointer;"'
      + ' onchange="window._railSelectInvestigation(this.value)">'
      + opts.join('') + '</select>';
  }

  // Picker onchange: set the current investigation, persist it, re-render.
  window._railSelectInvestigation = function(name) {
    window._currentIsetSlug = name || '';
    try { window.localStorage.setItem(_railIsetKey(), name || ''); } catch (_) { /* ignore */ }
    _renderRailInvestigationGroups();
  };
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
    // BFS depth from roots — root = no parent AMONG the nodes in this set. Ignore
    // external prereqs (e.g. `parca`) that aren't in childrenMap, else a chain whose
    // head has an external parent collapses to depth 0 (same bug as the DAG render).
    var depthMap = {};
    var queue = [];
    all.forEach(function(inv) {
      var inParents = (inv.parent_studies || []).filter(function(p) {
        return childrenMap[_parentName(p)] !== undefined;
      });
      if (!inParents.length) {
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
    var dag = _buildInvestigationDag(window._investigations);
    window._investigationsChildren = dag.children;
    window._investigationsDepth = dag.depth;
    // Same shared engine + AND-first/OR-fallback as the rail / Investigations tab.
    var tokens = _tokenize(f.search);
    var requireAll = !!tokens.length && window._investigations.some(function(inv) {
      return _tokensMatch(_studyHay(inv), tokens, true);
    });
    var filtered = window._investigations.filter(function(inv) {
      if (tokens.length && !_tokensMatch(_studyHay(inv), tokens, requireAll)) return false;
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
      return '<a onclick="event.stopPropagation(); _openStudyEmbeddedNewTab(\'' + _esc(name) + '\')" ' +
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

    return '<div class="investigation-card" onclick="_openStudyEmbeddedNewTab(\'' + _esc(inv.name) + '\')">' +
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
    fetch('/api/study-create', {
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
        '<hr style="margin:20px 0;border:none;border-top:1px solid #eee">' +
        '<p class="panel-lead">Analyses to run at dispatch time — one <code>v2ecoli.workflow.analysis.' +
          'ANALYSIS_REGISTRY</code> name per line (e.g. <code>doubling_time_distribution</code>). Translated ' +
          'into <code>analysis_options</code> for remote (sms-api) dispatch and the local post-run pipeline ' +
          'alike.</p>' +
        '<textarea id="inv-analyses-list" rows="3" style="width:100%;font-family:monospace;font-size:0.9em" ' +
          'placeholder="doubling_time_distribution"></textarea>' +
        '<button class="action-btn js-authoring" onclick="_saveAnalyses()">Save analyses</button>' +
        '<div id="inv-analyses-status" style="margin-top:8px;font-size:0.9em;color:#555"></div>' +
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
      _loadInvAnalyses(window._currentInvestigation);
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

  // ── Investigation Analyses tab handlers ───────────────────────────────────
  // Lives in the same "observables" panel — both configure what a dispatch
  // records/computes for this study, and neither needed its own top-level tab.

  function _loadInvAnalyses(invName) {
    // Pre-fill from the current spec.yaml.analyses[].name — same naive-scrape
    // approach _loadInvObservables already uses for observables, so this
    // doesn't need a new read endpoint.
    fetch('/investigations/' + encodeURIComponent(invName) + '/spec.yaml').then(function(r) {
      return r.ok ? r.text() : '';
    }).then(function(specText) {
      var names = [];
      var m = specText.match(/^analyses:\s*\n([\s\S]*?)(?=^[a-zA-Z_]|\s*$)/m);
      if (m) {
        m[1].split(/\r?\n/).forEach(function(line) {
          var p = line.match(/name:\s*["']?([\w.-]+)["']?/);
          if (p) names.push(p[1]);
        });
      }
      var el = document.getElementById('inv-analyses-list');
      if (el) el.value = names.join('\n');
    });
  }
  window._loadInvAnalyses = _loadInvAnalyses;

  function _saveAnalyses() {
    var invName = window._currentInvestigation || '';
    var el = document.getElementById('inv-analyses-list');
    var names = ((el && el.value) || '').split(/[\n,]/)
      .map(function(s) { return s.trim(); }).filter(Boolean);
    var analyses = names.map(function(n) { return {name: n, params: {}}; });
    fetch('/api/study-set-analyses', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({investigation: invName, analyses: analyses}),
    }).then(function(r) { return r.json().then(function(j) { return [r.ok, j]; }); })
      .then(function(parts) {
        var status = document.getElementById('inv-analyses-status');
        if (!status) return;
        status.textContent = parts[0]
          ? 'Saved ' + analyses.length + ' analysis/analyses'
          : 'Save failed: ' + ((parts[1] || {}).error || '');
      });
  }
  window._saveAnalyses = _saveAnalyses;

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
    }).then(function(r) { return r.json().then(function(j) { return [r.ok, j, r.status]; }); })
      .then(function(parts) {
        var ok = parts[0], j = parts[1], code = parts[2];
        // §A5: a v3 investigation is now delegated server-side to the SAME
        // background job machinery "Run unblocked" uses, so this answers
        // 202 + job_id instead of blocking until every simulation finishes.
        // Hand it to the existing progress poll rather than inventing a second
        // async UX — that poll already renders items, resolves Batch dispatches
        // and drives the prerequisite re-drive.
        //
        // This is also what makes the button usable on a gateway-fronted
        // deployment at all: the synchronous shape could not outlive the ALB's
        // idle timeout regardless of where the work ran.
        if (code === 202 && j && j.job_id) {
          if (typeof _vivPollRunProgress === 'function') _vivPollRunProgress(j.job_id);
          if (btn) { btn.disabled = false; btn.textContent = 'Run'; }
          _openInvestigation(name);
          return;
        }
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

  function _simStatusChip(status) { return window.SimTable.statusChip(status); }

  // Emitter-type pill, keyed by the API's emitter_type ("SQLite"/"Parquet"/
  // "XArray"). Colors live in CSS classes emitter-sqlite/parquet/xarray.
  function _simEmitterPill(emitterType) { return window.SimTable.emitterPill(emitterType); }

  // Single source for the Origin column's text — used by BOTH the pill and the
  // sort key so they can't diverge. `remote_origin` is an OBJECT
  // ({deployment, simulation_id, …}) or null; it is never a bare string.
  function _simOriginLabel(row) { return window.SimTable.originLabel(row); }

  function _simOriginPill(row) { return window.SimTable.originPill(row); }

  // Format an epoch-seconds timestamp as a readable local time.
  function _simFmtTime(sec) { return window.SimTable.fmtTime(sec); }

  // Module-scope cache. _simRows = all runs from the API (the {simulations}
  // shape from simulations_index.list_simulations); _simCurrent = the current
  // investigation slug (default filter target, may be null).
  window._simRows = [];
  window._simCurrent = null;

  // Investigation/study come from the index's *_slug fields; the study slug
  // falls back to the first cross-referenced study name.
  function _simInvestigation(row) { return window.SimTable.investigation(row); }
  function _simStudy(row) { return window.SimTable.study(row); }
  // Where the run's data lives: the native store (zarr/parquet dir or s3 uri)
  // when present, else the runs.db SQLite at db_path. Shows a compact tail with
  // the full path on hover.
  function _simLocation(row) { return window.SimTable.location(row); }

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

  /** Open a Simulations-DB row: the associated STUDY when the run has one, else
   *  the Composite Explorer (bigraph-loom) seeded to this run's results.
   *  Study-associated runs NAVIGATE to the study page — not _openStudyEmbedded,
   *  whose embed panel lives in a different page section that is hidden while the
   *  Simulations page is active, so it silently did nothing here. */
  function _openSimulation(row) {
    if (!row) return;
    var study = _simStudy(row);
    if (study) { window.location = _studyHref(study); return; }
    if (row.run_id && row.spec_id) { _openSimulationInExplorer(row.run_id, row.spec_id); }
  }
  window._openSimulation = _openSimulation;

  // Extract a composite-parameter override dict from a Simulations-DB row's
  // saved config, so opening the run in the Composite Explorer pre-fills the
  // Configure form with the values that produced it (Run → reproduces the run).
  function _runConfigToOverrides(row) {
    var c = row && row.config;
    if (!c || typeof c !== 'object') return {};
    var ov = {};
    if (c.config_overrides && typeof c.config_overrides === 'object') {
      Object.keys(c.config_overrides).forEach(function (k) { ov[k] = c.config_overrides[k]; });
    }
    Object.keys(c).forEach(function (k) {
      if (k === 'config_overrides') return;              // already merged above
      if (c[k] !== null && typeof c[k] !== 'object') ov[k] = c[k];  // scalar params (seed, n_cells, …)
    });
    return ov;
  }
  // Open a run in the Composite Explorer (in-app, left nav preserved) with its
  // saved config seeded into the Configure form so Run reproduces the run.
  function _openCompositeFromRun(row) {
    if (!row || !row.spec_id) return;
    window._ceIncomingOverrides = _runConfigToOverrides(row);
    _openCompositeCardView(row.spec_id);
  }
  window._openCompositeFromRun = _openCompositeFromRun;

  // Open a registered composite in the NEW full composite-card view (Modules →
  // Composites tab, Full zoom), focused on it with its Explore/loom opened —
  // instead of the old standalone bigraph-loom composite-explore page.
  function _openCompositeCardView(spec_id) {
    if (!spec_id) return;
    if (typeof _openCompositesTab === 'function') _openCompositesTab();
    window._registryZoom = 'full';
    try { localStorage.setItem('viv.registryZoom', 'full'); } catch (e) { /* private mode */ }
    if (typeof _syncRegistryToolbar === 'function') _syncRegistryToolbar();
    var esc = (window.CSS && CSS.escape) ? CSS.escape(spec_id) : spec_id;
    var reveal = function () {
      if (typeof _renderRegistryComposites === 'function') _renderRegistryComposites();
      var card = document.querySelector('.pcard-composite[data-address="' + esc + '"]');
      if (!card) return false;
      try { card.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (e) { /* ignore */ }
      var sec = card.querySelector('.pcard-sec-explore');
      if (sec && !sec.classList.contains('pcard-sec-open')) {
        var h = sec.querySelector('.pcard-sec-head'); if (h) _pcardToggleSec(h);
      }
      return true;
    };
    // Composites may not be loaded yet on a cold Modules page — load, then reveal.
    if (window._composites && window._composites.length) {
      setTimeout(function () { reveal(); }, 60);
    } else if (typeof _loadComposites === 'function') {
      _loadComposites();
      var tries = 0;
      var poll = setInterval(function () {
        tries++;
        if (reveal() || tries > 20) clearInterval(poll);
      }, 250);
    } else {
      setTimeout(function () { reveal(); }, 200);
    }
  }
  window._openCompositeCardView = _openCompositeCardView;

  // Reveal a truncated ".sim-loc" cell's full path in place and copy it.
  function _revealAndCopyLoc(el) {
    if (!el) return;
    var full = el.getAttribute('data-loc') || el.textContent || '';
    if (!full) return;
    el.textContent = full;
    el.style.whiteSpace = 'normal';
    el.style.wordBreak = 'break-all';
    el.style.overflow = 'visible';
    el.style.textOverflow = 'clip';
    el.title = full;
    var done = function (ok) {
      var badge = document.createElement('span');
      badge.textContent = ok ? '  ✓ copied' : '  (copy failed)';
      badge.style.cssText = 'color:' + (ok ? '#16a34a' : '#b91c1c') + ';font-size:10px;white-space:nowrap';
      el.appendChild(badge);
      setTimeout(function () { if (badge.parentNode) badge.parentNode.removeChild(badge); }, 1800);
    };
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(full).then(function () { done(true); }, function () { done(false); });
      } else {
        var ta = document.createElement('textarea');
        ta.value = full; document.body.appendChild(ta); ta.select();
        var ok = false; try { ok = document.execCommand('copy'); } catch (e2) { ok = false; }
        document.body.removeChild(ta); done(ok);
      }
    } catch (e3) { done(false); }
  }
  window._revealAndCopyLoc = _revealAndCopyLoc;

  // Popover showing a run's FULL config as formatted JSON, with a Copy button.
  // Anchored to the clicked ".sim-config" cell; dismissed on outside-click/Esc.
  function _showConfigPopover(el) {
    var existing = document.getElementById('sim-config-popover');
    if (existing) existing.remove();
    var json = el.getAttribute('data-config') || '{}';
    var pop = document.createElement('div');
    pop.id = 'sim-config-popover';
    pop.style.cssText = 'position:fixed;z-index:3000;min-width:300px;max-width:min(560px,92vw);' +
      'background:#fff;border:1px solid #cbd5e1;border-radius:10px;box-shadow:0 10px 34px rgba(15,23,42,.20);padding:10px 12px';
    var head = document.createElement('div');
    head.style.cssText = 'display:flex;align-items:center;gap:10px;margin-bottom:6px';
    head.innerHTML = '<strong style="font-size:0.82em;text-transform:uppercase;letter-spacing:0.06em;color:#334155;flex:1">Run config</strong>';
    var copyBtn = document.createElement('button');
    copyBtn.type = 'button'; copyBtn.className = 'btn-mini'; copyBtn.textContent = '⧉ Copy JSON';
    copyBtn.onclick = function (ev) {
      ev.stopPropagation();
      var orig = copyBtn.textContent;
      var ok = function (good) { copyBtn.textContent = good ? '✓ Copied' : '✗ Failed'; setTimeout(function () { copyBtn.textContent = orig; }, 1400); };
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(json).then(function () { ok(true); }, function () { ok(false); });
        else { var ta = document.createElement('textarea'); ta.value = json; document.body.appendChild(ta); ta.select(); var g = false; try { g = document.execCommand('copy'); } catch (e) { g = false; } document.body.removeChild(ta); ok(g); }
      } catch (e) { ok(false); }
    };
    var closeBtn = document.createElement('button');
    closeBtn.type = 'button'; closeBtn.className = 'btn-mini'; closeBtn.textContent = '✕';
    closeBtn.title = 'Close'; closeBtn.onclick = function (ev) { ev.stopPropagation(); pop.remove(); };
    head.appendChild(copyBtn); head.appendChild(closeBtn);
    var pre = document.createElement('pre');
    pre.textContent = json;
    pre.style.cssText = 'margin:0;font-size:11.5px;line-height:1.45;color:#1f2937;white-space:pre;' +
      'max-height:min(60vh,420px);overflow:auto;background:#f8fafc;border:1px solid #eef2f7;border-radius:7px;padding:8px 10px';
    pop.appendChild(head); pop.appendChild(pre);
    document.body.appendChild(pop);
    // Position below the cell, clamped to the viewport.
    var r = el.getBoundingClientRect();
    var w = pop.offsetWidth, h = pop.offsetHeight;
    var left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8));
    var top = (r.bottom + 6 + h > window.innerHeight) ? Math.max(8, r.top - h - 6) : r.bottom + 6;
    pop.style.left = left + 'px'; pop.style.top = top + 'px';
    // Dismiss on outside click / Esc.
    var onDoc = function (ev) { if (!pop.contains(ev.target) && ev.target !== el) { cleanup(); } };
    var onKey = function (ev) { if (ev.key === 'Escape') cleanup(); };
    function cleanup() { pop.remove(); document.removeEventListener('mousedown', onDoc, true); document.removeEventListener('keydown', onKey, true); }
    setTimeout(function () { document.addEventListener('mousedown', onDoc, true); document.addEventListener('keydown', onKey, true); }, 0);
  }
  window._showConfigPopover = _showConfigPopover;

  function _renderSimRow(row) { return window.SimTable.renderRow(row, { scope: 'full' }); }

  // Client-side column sort for the Simulations DB table. Purely a rendering
  // concern on top of the server-ordered (newest-first) _simRows — clicking a
  // sortable <th> toggles asc/desc and re-runs _applySimFilter, which applies
  // _sortSimRows to the filtered rows before rendering.
  let _simSortState = { key: null, dir: 'desc' };

  function _simSortValue(row, key) {
    if (key === 'time') return row.completed_at || row.started_at || 0;
    if (key === 'emitter_type') return String(row.emitter_type || '').toLowerCase();
    // remote_origin is an OBJECT — read the same label the pill renders. A bare
    // `.toLowerCase()` on it threw and aborted the whole re-sort (Origin looked
    // like it "didn't sort"). String() on every branch keeps a stray non-string
    // value from ever breaking the comparator again.
    if (key === 'origin') return _simOriginLabel(row).toLowerCase();
    if (key === 'study') return String(_simStudy(row) || '').toLowerCase();
    if (key === 'investigation') return String(_simInvestigation(row) || '').toLowerCase();
    if (key === 'run') return String(row.sim_name || row.label || row.run_id || '').toLowerCase();
    if (key === 'composite') return String(row.spec_id || '').toLowerCase();
    if (key === 'source') {
      var _s = row.source_ref || {};
      return ((_s.repo || '') + ' ' + (_s.commit_short || '')).toLowerCase();
    }
    if (key === 'status') return String(row.status || '').toLowerCase();
    if (key === 'location') return String(row.store_path || row.db_path || '').toLowerCase();
    if (key === 'config') {
      var c = row.config || {};
      return Object.keys(c).length ? JSON.stringify(c).toLowerCase() : '';
    }
    // Tools: sort by the matched tool's label so runs that have a tool (e.g. the
    // atlas run → "HRA Computational Model Atlas") group together and, on the
    // first (ascending) click, rise to the TOP; tool-less runs get a high
    // sentinel so they sink. Clicking Tools thus surfaces the tool-linked runs.
    if (key === 'tools') {
      var mt = row.matched_tools || [];
      return mt.length ? String(mt[0].label || mt[0].id || '').toLowerCase() : '\uffff';
    }
    return '';
  }

  function _sortSimRows(rows, key, dir) {
    if (!key) return rows;
    const s = rows.slice().sort(function (a, b) {
      var va = _simSortValue(a, key), vb = _simSortValue(b, key);
      if (va < vb) return -1;
      if (va > vb) return 1;
      return 0;
    });
    return dir === 'desc' ? s.reverse() : s;
  }

  function _onSimHeaderClick(th) {
    var key = th.getAttribute('data-sort-key');
    if (!key) return;
    if (_simSortState.key === key) {
      _simSortState.dir = _simSortState.dir === 'asc' ? 'desc' : 'asc';
    } else {
      _simSortState = { key: key, dir: 'asc' };
    }
    document.querySelectorAll('#page-simulations th[data-sort-key]')
      .forEach(function (h) { h.removeAttribute('data-sort-dir'); });
    th.setAttribute('data-sort-dir', _simSortState.dir);   // CSS ::after renders ▲/▼
    _applySimFilter();
  }
  window._onSimHeaderClick = _onSimHeaderClick;

  // Populate the Study + Emitter dropdowns from the data (preserving any
  // current selection), then render rows through the active filters.
  function _applySimFilter() {
    var rows = window._simRows || [];
    var invSel    = document.getElementById('sim-inv-filter');
    var studySel  = document.getElementById('sim-study-filter');
    var emitterSel = document.getElementById('sim-emitter-filter');

    var invVal = invSel ? invSel.value : '';
    var studyVal = studySel ? studySel.value : '';
    var emitterVal = emitterSel ? emitterSel.value : '';

    // Dropdown filters first (Investigation / Study / Emitter).
    var visible = rows.filter(function (r) {
      if (invVal && _simInvestigation(r) !== invVal) return false;
      if (studyVal && _simStudy(r) !== studyVal) return false;
      if (emitterVal && (r.emitter_type || 'SQLite') !== emitterVal) return false;
      return true;
    });

    // Free-text search (#sim-text-filter) over each run's searchable fields
    // (study, investigation, run name/label/id, spec, status, emitter, origin),
    // combined with the dropdowns above. AND-first, OR-fallback: prefer runs
    // matching EVERY token; if none do, match ANY token so a natural phrase
    // still surfaces relevant runs.
    var textEl = document.getElementById('sim-text-filter');
    var textTokens = textEl && textEl.value.trim()
      ? textEl.value.trim().toLowerCase().split(/\s+/) : [];
    if (textTokens.length) {
      var _simHay = function (r) {
        return [
          _simStudy(r), _simInvestigation(r), r.status, r.emitter_type,
          _simOriginLabel(r), r.sim_name, r.label, r.run_id, r.spec_id
        ].filter(Boolean).join(' ').toLowerCase();
      };
      var andRows = visible.filter(function (r) {
        var h = _simHay(r);
        return textTokens.every(function (t) { return h.indexOf(t) !== -1; });
      });
      visible = andRows.length ? andRows : visible.filter(function (r) {
        var h = _simHay(r);
        return textTokens.some(function (t) { return h.indexOf(t) !== -1; });
      });
    }

    visible = _sortSimRows(visible, _simSortState.key, _simSortState.dir);

    var tbody = document.getElementById('sim-tbody');
    var table = document.getElementById('sim-table');
    var empty = document.getElementById('sim-empty');
    if (tbody) tbody.innerHTML = visible.map(_renderSimRow).join('');
    // Row click opens the run (delegated once, survives re-renders); the
    // download links/buttons keep their own behaviour.
    if (tbody && !tbody._simClickWired) {
      tbody._simClickWired = true;
      tbody.addEventListener('click', function (e) {
        // Composite link → the NEW full composite-card view (not the old
        // bigraph-loom composite-explore page).
        var clink = e.target.closest('.sim-composite-link');
        if (clink) {
          e.stopPropagation();
          var crid = clink.getAttribute('data-run-id');
          var crow = (window._simRows || []).filter(function (r) { return String(r.run_id) === crid; })[0];
          if (crow && window._openCompositeFromRun) window._openCompositeFromRun(crow);
          return;
        }
        // Location → reveal the full path (wrap) and copy it to the clipboard.
        var loc = e.target.closest('.sim-loc');
        if (loc) { e.stopPropagation(); _revealAndCopyLoc(loc); return; }
        // Config → popover with the full config JSON + Copy JSON.
        var cfg = e.target.closest('.sim-config');
        if (cfg) { e.stopPropagation(); _showConfigPopover(cfg); return; }
        if (e.target.closest('a, button, .action-btn')) return;
        var tr = e.target.closest('tr[data-run-id]');
        if (!tr) return;
        var rid = tr.getAttribute('data-run-id');
        var row = (window._simRows || []).filter(function (r) { return String(r.run_id) === rid; })[0];
        if (row) _openSimulation(row);
      });
    }
    if (table) table.style.display = visible.length ? '' : 'none';
    if (empty) empty.style.display = visible.length ? 'none' : '';

    // Drag-resizable columns for the Simulations DB table. The thead is static
    // (rendered once in index.html.j2) and only the tbody re-renders, so wire
    // the grips a single time; stored widths persist across filters/reloads.
    if (table && window.ColResize && !table._colResizeWired) {
      table._colResizeWired = true;
      window.ColResize.apply(table, 'sim-global');
    }

    var note = document.getElementById('sim-scope-note');
    if (note) {
      if (invVal) {
        var isCurrent = (invVal === window._simCurrent);
        note.textContent = 'Scoped to ' + invVal +
          (isCurrent ? ' (current branch)' : '') +
          ' — ' + visible.length + ' runs. Pick "All" to widen.';
      } else {
        note.textContent = 'Showing all investigations — ' + visible.length + ' runs.';
      }
    }
  }

  // Exposed for the #sim-text-filter input's inline oninput handler.
  window._applySimFilter = _applySimFilter;

  // Rebuild the Study + Emitter <select> option lists from the current data.
  function _populateSimFilters() {
    var rows = window._simRows || [];
    var studies = {}, emitters = {}, invs = {};
    rows.forEach(function (r) {
      var st = _simStudy(r);
      if (st) studies[st] = true;
      emitters[r.emitter_type || 'SQLite'] = true;
      var inv = _simInvestigation(r);
      if (inv) invs[inv] = true;
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

    // Investigation dropdown: same fill, but on first load default to the
    // current investigation (branch slug or dashboard focus) when it has runs.
    // Once the user picks one (window._simInvChosen), preserve their choice
    // across auto-refreshes instead of snapping back to current.
    var invSel = document.getElementById('sim-inv-filter');
    if (invSel) {
      var invKeys = Object.keys(invs);
      var prev = invSel.value;
      var opts = ['<option value="">All</option>'];
      invKeys.sort().forEach(function (v) {
        opts.push('<option value="' + _escSim(v) + '">' + _escSim(v) + '</option>');
      });
      invSel.innerHTML = opts.join('');
      if (window._simInvChosen) {
        if (invKeys.indexOf(prev) >= 0) invSel.value = prev;
      } else {
        var def = window._simCurrent;
        invSel.value = (def && invs[def]) ? def : '';
      }
    }
  }

  // quiet=true → background auto-refresh: skip the "Loading…" flash and leave
  // the existing table in place on transient errors (don't clobber good data).
  function _initSimulations(quiet) {
    var loading = document.getElementById('sim-loading');
    var empty   = document.getElementById('sim-empty');
    var table   = document.getElementById('sim-table');
    if (!quiet) {
      if (loading) loading.style.display = '';
      if (empty)   empty.style.display = 'none';
      if (table)   table.style.display = 'none';
    }

    window.DataSource.loadSimulations()
      .then(function (data) {
        if (data.error) {
          if (quiet) return;
          if (loading) loading.innerHTML =
            '<span style="color:#c00;">Could not load simulations: ' +
            _escSim(data.error) + ' <button class="action-btn" ' +
            'onclick="_initSimulations()">Retry</button></span>';
          return;
        }
        window._simRows = data.simulations || [];
        // Scope target, most-specific first: the investigation currently open
        // in the detail view (_currentIsetSlug, set by _openInvestigationDetail),
        // else the git-branch investigation slug, else whatever investigation the
        // dashboard is focused on. Null → All.
        window._simCurrent = window._currentIsetSlug || data.current ||
          window._currentInvestigation || null;
        if (loading) loading.style.display = 'none';
        _populateSimFilters();
        _applySimFilter();
        _pollNonTerminalRemoteRuns();
      })
      .catch(function (err) {
        if (quiet) return;
        if (loading) loading.innerHTML =
          '<span style="color:#c00;">Network error: ' + _escSim(String(err)) +
          ' <button class="action-btn" onclick="_initSimulations()">Retry</button></span>';
      });
  }
  window._initSimulations = _initSimulations;

  // Backlog item 84: a UI-dispatched remote run's row shows "running" from
  // the moment PR #922's pending-dispatch placeholder lands until someone
  // explicitly clicks "Land Results" -- runs.db is never otherwise touched,
  // so without this the row is frozen at "running" even long after the real
  // AWS Batch campaign finished. Piggybacks on the auto-refresh cadence
  // _startSimAutoRefresh already drives (every 15s while this page is open)
  // rather than adding a second timer. For each currently-rendered remote
  // row still showing "running", does ONE live check via the same
  // GET /api/remote-run-poll?simulation_id=<id> endpoint item 6/81's own
  // active-dispatch progress bar already uses (remote_run_status --
  // on-demand, no in-process state) and, if the real phase is terminal,
  // swaps just that row's chip in place. Deliberately does NOT write to
  // runs.db or auto-land -- landing (the actual data pull) stays an
  // explicit user action; this only keeps what's ON SCREEN honest while
  // waiting for that click. Analysis-side staleness (item 84's own filing:
  // GET /analyses/{id}/status is also pull-based) is a separate, still-open
  // follow-on -- no analysis_id is tracked per-row today to poll against.
  // Page-session-scoped: once a poll confirms a simulation_id's real terminal
  // phase, remember it here. Required because this poller never writes to
  // runs.db (landing stays the explicit user action) -- without this cache,
  // every 15s auto-refresh re-renders every row from the raw DB value (still
  // "running" until landed), silently erasing the chip this function just
  // set, and the very next tick would re-poll and flip it right back --
  // running/completed/running/completed forever for as long as the tab
  // stays open on a real, finished-but-unlanded remote campaign. Caught live
  // by watching more than one refresh cycle, not by a single before/after
  // check.
  window._remoteTerminalCache = window._remoteTerminalCache || {};

  function _pollNonTerminalRemoteRuns() {
    if ((window.__DASH_CONFIG__ || {}).mode === 'snapshot') return;  // no live backend
    var rows = document.querySelectorAll('tr[data-remote-sim-id]');
    if (!rows.length) return;
    var checked = 0;
    for (var i = 0; i < rows.length; i++) {
      var tr = rows[i];
      var chipHost = tr.querySelector('.run-status-live');
      if (!chipHost) continue;
      var simId = tr.getAttribute('data-remote-sim-id');
      if (!simId) continue;

      // Already known terminal from an earlier poll this page session --
      // reapply immediately (no request, no visible flicker) instead of
      // leaving this tick's fresh-from-DB "running" render stand until the
      // next poll gets around to it.
      var cachedPhase = window._remoteTerminalCache[simId];
      if (cachedPhase) {
        chipHost.innerHTML = window.SimTable.statusChip(cachedPhase);
        continue;
      }

      if (!/running/i.test(chipHost.textContent)) continue;
      if (checked >= 20) continue;  // defensive cap, not expected to bind in practice
      checked++;
      (function (host, id) {
        fetch('/api/remote-run-poll?simulation_id=' + encodeURIComponent(id))
          .then(function (r) { return r.json(); })
          .then(function (body) {
            var phase = body && body.phase;
            if (phase === 'done') {
              window._remoteTerminalCache[id] = 'completed';
              host.innerHTML = window.SimTable.statusChip('completed');
            } else if (phase === 'failed') {
              window._remoteTerminalCache[id] = 'failed';
              host.innerHTML = window.SimTable.statusChip('failed');
            }
            // running / queued / unreachable: leave the chip as-is, the next
            // 15s auto-refresh tick will check again.
          })
          .catch(function () { /* transient -- next tick retries */ });
      })(chipHost, simId);
    }
  }

  // Auto-refresh: while the Simulations DB page is open, re-pull every 15s so
  // the table stays current with newly persisted / remote-landed runs without
  // a manual Refresh. Stopped on page switch (see _switchPage).
  function _startSimAutoRefresh() {
    _stopSimAutoRefresh();
    window._simRefreshTimer = setInterval(function () {
      var page = document.getElementById('page-simulations');
      if (!page || !page.classList.contains('active')) { _stopSimAutoRefresh(); return; }
      if (document.hidden) return;   // skip while tab is backgrounded
      _initSimulations(true);
    }, 15000);
  }
  function _stopSimAutoRefresh() {
    if (window._simRefreshTimer) { clearInterval(window._simRefreshTimer); window._simRefreshTimer = null; }
  }
  window._startSimAutoRefresh = _startSimAutoRefresh;
  window._stopSimAutoRefresh = _stopSimAutoRefresh;

  // Wire the investigation/study/emitter filters + refresh button (once).
  function _wireSimulationsUiOnce() {
    // The investigation filter is special: a user pick is sticky (survives
    // auto-refresh) instead of snapping back to the current-branch default.
    var invSel = document.getElementById('sim-inv-filter');
    if (invSel && !invSel.dataset.wired) {
      invSel.addEventListener('change', function () {
        window._simInvChosen = true;
        _applySimFilter();
      });
      invSel.dataset.wired = '1';
    }
    [['sim-study-filter', 'change'],
     ['sim-emitter-filter', 'change']].forEach(function (pair) {
      var el = document.getElementById(pair[0]);
      if (el && !el.dataset.wired) {
        el.addEventListener(pair[1], _applySimFilter);
        el.dataset.wired = '1';
      }
    });
    var r = document.getElementById('sim-refresh');
    if (r && !r.dataset.wired) {
      r.addEventListener('click', function () { _initSimulations(); });
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
        fetch(_api('/api/composite-run/' + encodeURIComponent(run_id) + '/status'))
          .then(function(r) {
            if (r.status === 404) return { _gone: true };
            return r.json();
          }),
        fetch(_api('/api/composite-run/' + encodeURIComponent(run_id)))
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
            nf += _asFindings(s.findings).length;
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
      _asFindings(s.findings).forEach(function (f) { allFindings.push({study: s.name, f: f}); });
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
                 ' | ' + (_asFindings(s.findings).length) + ' | ' + ((s.follow_up_studies || []).length) + ' |');
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
    // The report is generated server-side (or pre-rendered in a bundle); fetch
    // its self-contained HTML to attach to a PR.
    return fetch(_investigationReportUrl(name))
      .then(function (r) { return r.ok ? r.text() : null; })
      .catch(function () { return null; });
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
      // Legacy single-line banner is superseded by the per-row "Workspace
      // repository" detail (_renderGitStatusRows); keep it permanently hidden
      // so it doesn't duplicate that section.
      var box = document.getElementById('viv-git-status');
      if (box) { box.hidden = true; }
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
    var _retry = ' <a href="#" id="viv-gh-org-retry" style="color:#2563eb">Retry</a>';
    function _bindRetry() {
      var a = document.getElementById('viv-gh-org-retry');
      if (a) a.onclick = function (e) { e.preventDefault(); _loadGithubOrgs(); };
    }
    fetch('/api/auth/github/orgs').then(function (r) {
      if (r.status === 401) {
        sel.innerHTML = '<option value="">Sign in to load orgs…</option>';
        if (hint) hint.textContent = 'Sign in above to pick a default org.';
        return;
      }
      if (!r.ok) {
        // Backend now degrades gracefully, so a hard non-OK here is unusual
        // (network/proxy). Keep the picker usable + offer a retry.
        sel.innerHTML = '<option value="">Could not load orgs</option>';
        if (hint) { hint.innerHTML = 'GitHub request failed (HTTP ' + r.status + ').' + _retry; _bindRetry(); }
        return;
      }
      return r.json().then(function (data) {
        // API shape: {login, orgs: [{name, kind}], warning?: "orgs_lookup_failed"}
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
          if (data && data.warning) {
            // Org list couldn't be fetched, but the personal namespace is
            // available — the user isn't blocked.
            hint.innerHTML = 'Showing your personal namespace — couldn’t list orgs right now.' + _retry;
            _bindRetry();
          } else {
            hint.textContent = saved
              ? 'Default: ' + saved + ' (saved in this browser).'
              : 'Pick one to use as the default for new-repo flows.';
          }
        }
      });
    }).catch(function () {
      sel.innerHTML = '<option value="">Network error</option>';
      if (hint) { hint.innerHTML = 'Network error reaching the workbench.' + _retry; _bindRetry(); }
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
  // No outer closure state is used (cache lives on the function object), a
  // property retained from when the investigation report baked an exact copy of
  // this function via `.toString()`.
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
        _asFindings(j.findings).forEach(function (f) {
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
