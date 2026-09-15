// loom-embed.js — the SINGLE source for the composite-card loom EMBED glue,
// installed as window globals. Loaded in BOTH the main SPA (before walkthrough.js,
// which now calls these globals instead of carrying its own copy) and the
// study-detail IFRAME (which loads composite-card.js but NOT walkthrough.js).
//
// composite-card.js's _pcardToggleSec mounts a composite's loom via
// `_openCompositeLoomInline`. This file owns that function (and _compositeStateUrl
// + the auto-height wiring); walkthrough.js used to duplicate them byte-for-byte,
// which drifted — now there is one copy here.
(function () {
  "use strict";

  function _esc(s) {
    return String(s == null ? '' : s).replace(/[<>&"]/g, function (c) {
      return { '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c];
    });
  }

  // ── Cloud-run tracking (parent-owned) ─────────────────────────────────────
  // A composite-card Cloud Run dispatches the selected build's PRE-BUILT image
  // via sms-api run_simulation (plan B). That dispatch POST is inherently slow
  // (~15-25s while sms-api registers the whole-cell run over the SSM tunnel) and
  // the loom's own run bar times out during it, mislabelling a run that actually
  // landed on GovCloud. So the loom hands the PARENT the sms-api simulation id
  // (postMessage explore:remote-dispatched) and the parent owns the tracking:
  // a patient chip driven by the SAME robust sim-status endpoint the Runs tab
  // uses (/api/composite-run/remote-sim-<id>/status), with backoff, terminating
  // only on a real completed/failed — never a false "complete".

  // Map a /api/composite-run/remote-sim-<id>/status body to a chip phase.
  // A missing/error body (slow tunnel, sim not yet registered) returns null so
  // the poller keeps waiting rather than ever flipping to a false terminal state.
  function _cloudPhaseFromStatus(body) {
    if (!body || typeof body !== 'object') return null;   // transient — keep polling
    var st = body.status;
    if (st === 'completed') return 'completed';
    if (st === 'failed' || st === 'orphaned') return 'failed';
    if (st === 'running') {
      var raw = String(body.raw_status || '').toLowerCase();
      return /queued|pending|submitted|created/.test(raw) ? 'queued' : 'running';
    }
    return null;   // unknown status — keep polling
  }

  // Pure chip HTML for a cloud-run phase. `state` = {phase, simId, buildSim, error}.
  // phase ∈ dispatching | queued | running | completed | failed | dispatch-failed.
  function _cloudRunChipHtml(state) {
    state = state || {};
    var phase = state.phase, simId = state.simId;
    var pal = {
      dispatching: ['#e6f0fb', '#1e5fa4'], queued: ['#e5e7eb', '#374151'],
      running: ['#dbeafe', '#1e40af'], completed: ['#dcfce7', '#166534'],
      failed: ['#fee2e2', '#991b1b'], 'dispatch-failed': ['#fee2e2', '#991b1b'],
    }[phase] || ['#e5e7eb', '#374151'];
    var label;
    if (phase === 'dispatching') {
      label = '☁ Dispatching to Cloud build #' +
        _esc(state.buildSim != null ? state.buildSim : '?') + '…';
    } else if (phase === 'dispatch-failed') {
      label = '☁ Cloud dispatch failed';
    } else {
      var word = { queued: 'queued', running: 'running',
        completed: '✓ completed', failed: '✗ failed' }[phase] || _esc(phase);
      label = '☁ Cloud run #' + _esc(simId != null ? simId : '?') + ' · ' + word;
    }
    var chip = '<span class="pcard-cloud-chip" style="display:inline-flex;align-items:center;gap:6px;' +
      'background:' + pal[0] + ';color:' + pal[1] + ';padding:2px 9px;border-radius:10px;' +
      'font-size:12px;font-weight:600">' + label + '</span>';
    var link = (simId != null && phase !== 'dispatching' && phase !== 'dispatch-failed')
      ? ' <a href="#simulations" class="pcard-cloud-link" onclick="return _viewCloudRunInRuns(' +
          Number(simId) + ')" style="font-size:12px;margin-left:8px">View in Runs DB →</a>'
      : '';
    var err = (phase === 'dispatch-failed' && state.error)
      ? '<div class="muted" style="font-size:11px;margin-top:3px">' + _esc(state.error) + '</div>' : '';
    return '<div style="display:flex;align-items:center;flex-wrap:wrap;gap:4px;margin:6px 0">' +
      chip + link + '</div>' + err;
  }

  // Render/refresh the cloud-run chip on a card (creating the host if the card's
  // markup predates the data-role="cloud-run" container).
  function _renderCloudChip(card, state) {
    if (!card) return;
    var host = card.querySelector('[data-role="cloud-run"]');
    if (!host) {
      host = document.createElement('div');
      host.className = 'pcard-cloud-run';
      host.setAttribute('data-role', 'cloud-run');
      var bar = card.querySelector('.pcard-graph-bar');
      if (bar && bar.parentNode) bar.parentNode.insertBefore(host, bar);
      else card.appendChild(host);
    }
    host.hidden = false;
    host.innerHTML = _cloudRunChipHtml(state);
  }

  // Poll the robust remote sim-status endpoint (same source the Runs tab uses)
  // with backoff. Terminates only on a real completed/failed; a transient error
  // just retries — so a slow tunnel never produces a false "complete".
  var _CLOUD_POLL_DELAYS = [2000, 3000, 5000, 8000];
  var _CLOUD_POLL_MAX_TICKS = 130;   // generous cap (~16 min at 8s) so no zombie timer
  function _pollCloudRun(card, simId) {
    if (!card || simId == null) return;
    if (card._cloudRunPoll) { clearTimeout(card._cloudRunPoll); card._cloudRunPoll = null; }
    var apiUrl = (window.DataSource && window.DataSource.apiUrl)
      ? window.DataSource.apiUrl.bind(window.DataSource) : function (p) { return p; };
    var i = 0;
    function schedule() {
      var d = _CLOUD_POLL_DELAYS[Math.min(i, _CLOUD_POLL_DELAYS.length - 1)];
      i++;
      card._cloudRunPoll = setTimeout(tick, d);
    }
    function tick() {
      card._cloudRunPoll = null;
      if (!document.body.contains(card)) return;   // card re-rendered/removed
      if (i > _CLOUD_POLL_MAX_TICKS) return;         // give up quietly, keep last chip
      fetch(apiUrl('/api/composite-run/remote-sim-' + encodeURIComponent(simId) + '/status'))
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (body) {
          var phase = _cloudPhaseFromStatus(body);
          if (phase === 'completed' || phase === 'failed') {
            _renderCloudChip(card, { phase: phase, simId: simId });
            return;   // terminal — stop polling
          }
          if (phase) _renderCloudChip(card, { phase: phase, simId: simId });
          schedule();
        })
        .catch(function () { schedule(); });   // transient — never false-complete
    }
    tick();
  }

  // "View in Runs DB" — open the Simulations/Runs tab focused on this run.
  function _focusRemoteRow(simId) {
    var tries = 0;
    (function look() {
      var row = document.querySelector('tr[data-remote-sim-id="' + simId + '"]');
      if (row) {
        try { row.scrollIntoView({ block: 'center' }); } catch (e) { /* ignore */ }
        var prev = row.style.boxShadow;
        row.style.boxShadow = 'inset 0 0 0 2px #2563eb';
        setTimeout(function () { row.style.boxShadow = prev; }, 2200);
        return;
      }
      if (tries++ < 25) setTimeout(look, 400);
    })();
  }
  function _viewCloudRunInRuns(simId) {
    if (typeof window._switchPage === 'function') {
      window._switchPage('simulations');
      _focusRemoteRow(simId);
    } else {
      // Study-detail iframe / no SPA driver: navigate the top window to Runs.
      try { (window.top || window).location.hash = 'simulations'; } catch (e) { /* cross-origin */ }
    }
    return false;
  }
  window._viewCloudRunInRuns = _viewCloudRunInRuns;
  window._cloudRunChipHtml = _cloudRunChipHtml;
  window._cloudPhaseFromStatus = _cloudPhaseFromStatus;
  window._renderCloudChip = _renderCloudChip;
  window._pollCloudRun = _pollCloudRun;

  function _compositeStateUrl(id, overrides) {
    var apiUrl = (window.DataSource && window.DataSource.apiUrl)
      ? window.DataSource.apiUrl.bind(window.DataSource) : function (p) { return p; };
    if (document.body.classList.contains('snapshot')) {
      return apiUrl('/api/composite-state/' + encodeURIComponent(id) + '.json');
    }
    return apiUrl('/api/composite-resolve?id=' + encodeURIComponent(id)) +
      (overrides ? '&overrides=' + encodeURIComponent(overrides) : '');
  }

  // Mount a composite's loom into its .ccard-loom-embed container.
  function _openCompositeLoomInline(det) {
    if (!det || det._loomLoaded) return;
    if (det.tagName === 'DETAILS' && !det.open) return;
    det._loomLoaded = true;
    var id = det.getAttribute('data-id');
    var host = det.querySelector('.ccard-loom-frame');
    if (!host) return;
    // Build-error chip (PR #1111 degrade): if this composite's wiring came back
    // stale/degraded, show the amber warning chip in the card header. Lazy (only
    // on loom mount, so no ParCa-heavy build on list load), fire-and-forget, and
    // hits the same TTL-cached composite-state the loom itself resolves.
    var _card = det.closest ? det.closest('.registry-entry-full') : null;
    if (_card && id && typeof window._loadCompositeBuildWarn === 'function') {
      window._loadCompositeBuildWarn(_card, id, det._overrides);
    }
    host.innerHTML = '<p class="muted" style="padding:10px;font-size:0.85em">Resolving composite (this can take a moment)…</p>';
    var apiUrl = (window.DataSource && window.DataSource.apiUrl) ? window.DataSource.apiUrl.bind(window.DataSource) : function (p) { return p; };
    var tabParam = det.getAttribute('data-view') ? '&tab=' + encodeURIComponent(det.getAttribute('data-view')) : '';
    var liveInner = document.body.classList.contains('snapshot')
      ? '' : '&id=' + encodeURIComponent(id) + '&live=1';
    var fullSurface = det.getAttribute('data-surface') === 'full';
    var isSnapshot = document.body.classList.contains('snapshot');
    var chromeParam = fullSurface ? '&header=off' : '&chrome=off';
    // Dynamic run-target: if the Environment scope is Cloud and a build resolves,
    // tell the loom to dispatch this Run to the cloud against that build (the loom
    // forwards run_target + build into the composite-test-run request; the backend
    // runs against git+repo@commit — no local push). Cloud-but-no-build passes
    // run_target with no build, and the loom blocks the Run (Q3).
    var rtParam = '';
    try {
      if (window.VivEnv && window.VivEnv.isCloud()) {
        var b = window.VivEnv.runBuild();
        rtParam = b
          ? '&run_target=deployment&build_sim=' + encodeURIComponent(b.simulator_id) +
            '&build_repo=' + encodeURIComponent(b.repo_url || '') +
            '&build_commit=' + encodeURIComponent(b.commit || '')
          : '&run_target=deployment';
      }
    } catch (e) { /* VivEnv unavailable → default local behavior */ }
    var loomUrl = (det._loomLive || (fullSurface && !isSnapshot))
      ? apiUrl('/bigraph-loom/index.html') + '?id=' + encodeURIComponent(id) +
          (det._overrides ? '&overrides=' + encodeURIComponent(det._overrides) : '') + chromeParam + tabParam + rtParam
      : apiUrl('/bigraph-loom/index.html') + '?static=1&stateUrl=' +
          encodeURIComponent(_compositeStateUrl(id, det._overrides)) + liveInner + chromeParam + tabParam;
    var f = document.createElement('iframe');
    f.className = 'ccard-loom-iframe' + (fullSurface ? ' ccard-loom-iframe-full' : '');
    f.setAttribute('title', 'Loom — ' + id);
    f.src = loomUrl;
    host.innerHTML = '';
    if (fullSurface) {
      // Auto-height: the full surface reports its natural content height via
      // explore:autoheight and we size the frame to it (see _wireLoomAutoHeight).
      // It mounts GRAPH-COLLAPSED (run + outputs lead), so start at that compact
      // height — NOT a tall box — so opening a card goes straight to run/outputs
      // with no tall "loading the loom" flash before it settles.
      host.style.height = '128px';
    } else {
      var savedH = 0;
      try { savedH = parseInt(localStorage.getItem('viv.loomFrameH') || '', 10) || 0; } catch (e) { /* private mode */ }
      if (savedH) host.style.height = Math.max(220, Math.min(Math.round(window.innerHeight * 0.92), savedH)) + 'px';
    }
    host.appendChild(f);
  }

  // Find the .ccard-loom-embed card whose iframe sent a message (by contentWindow).
  function _cardForLoomMessage(ev) {
    var frames = document.querySelectorAll('.ccard-loom-iframe');
    for (var i = 0; i < frames.length; i++) {
      if (frames[i].contentWindow === ev.source) return frames[i];
    }
    return null;
  }

  // The .registry-entry-full card that owns the loom iframe a message came from.
  function _cardFromMsg(ev) {
    var ifr = _cardForLoomMessage(ev);
    return ifr ? ifr.closest('.registry-entry-full') : null;
  }

  // Handle messages from full-surface loom iframes: (a) auto-height — size the
  // frame to the loom's content so it grows/shrinks with the graph instead of
  // scrolling inside a fixed frame; (b) collapse-card — the loom's bottom bar was
  // double-clicked, so fully collapse the card back to its pre-mount strip. Wired
  // once per page; matches the sending iframe by contentWindow.
  function _wireLoomAutoHeight() {
    if (window._loomAutoHeightWired) return;
    window._loomAutoHeightWired = true;
    window.addEventListener('message', function (ev) {
      var d = ev.data;
      if (!d) return;
      if (d.type === 'explore:autoheight' && typeof d.height === 'number') {
        var iframe = _cardForLoomMessage(ev);
        if (!iframe) return;
        var host = iframe.closest('.ccard-loom-frame') || iframe.parentElement;
        // +4px covers the frame's border (border-box) so the loom's content never
        // overflows into a hairline inner scrollbar.
        if (host) host.style.height = Math.max(120, Math.min(2600, Math.round(d.height) + 4)) + 'px';
      } else if (d.type === 'explore:collapse-card') {
        var ifr = _cardForLoomMessage(ev);
        var card = ifr && ifr.closest('.registry-entry-full');
        var bar = card && card.querySelector('.pcard-graph-bar');
        if (bar && typeof window._toggleLoomCard === 'function') window._toggleLoomCard(bar);
      } else if (d.type === 'explore:remote-dispatching') {
        // The ~20s Cloud dispatch POST is in flight — show a patient chip so the
        // card never mislabels a run that is still being registered on GovCloud.
        var cd = _cardFromMsg(ev);
        if (cd) _renderCloudChip(cd, { phase: 'dispatching', buildSim: d.build_sim });
      } else if (d.type === 'explore:remote-dispatched') {
        // 202 carrying the sms-api simulation id — the SAME id the Runs tab
        // tracks. Own the chip + robust poll here, in the parent (vanilla JS),
        // independent of the loom bar's own per-run polling.
        var cx = _cardFromMsg(ev);
        if (cx && d.simulation_id != null) {
          _renderCloudChip(cx, { phase: 'queued', simId: d.simulation_id });
          _pollCloudRun(cx, d.simulation_id);
        }
      } else if (d.type === 'explore:remote-dispatch-failed') {
        var cf = _cardFromMsg(ev);
        if (cf) _renderCloudChip(cf, { phase: 'dispatch-failed', error: d.error });
      }
    });
  }
  _wireLoomAutoHeight();

  // Single source of truth for the loom embed glue — used by BOTH the main SPA
  // (this file is loaded before walkthrough.js) and the study-detail iframe
  // (walkthrough.js absent). walkthrough.js no longer carries its own copy, so
  // the byte-identical-duplication drift this used to have is gone.
  window._compositeStateUrl = _compositeStateUrl;
  window._openCompositeLoomInline = _openCompositeLoomInline;
})();
