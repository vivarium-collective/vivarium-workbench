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
    host.innerHTML = '<p class="muted" style="padding:10px;font-size:0.85em">Resolving composite (this can take a moment)…</p>';
    var apiUrl = (window.DataSource && window.DataSource.apiUrl) ? window.DataSource.apiUrl.bind(window.DataSource) : function (p) { return p; };
    var tabParam = det.getAttribute('data-view') ? '&tab=' + encodeURIComponent(det.getAttribute('data-view')) : '';
    var liveInner = document.body.classList.contains('snapshot')
      ? '' : '&id=' + encodeURIComponent(id) + '&live=1';
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
