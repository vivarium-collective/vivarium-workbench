// composite-card.js — shared composite "card" renderer, extracted from
// walkthrough.js (study-spine-reorg Task 6) so the Modules/Composites view
// AND the Study Detail Model tab render the SAME rich card — full semantic
// detail (description, parameters/config schema, declared observables) at
// the "Full" loom zoom level — instead of forking the markup.
//
// Scope of the extraction: the pure HTML-string renderers
// (_renderCompositeCardFull / _renderCompositeCardGrid / _regPortColumn) plus
// every function they call directly (the shared "ProcessCard" chrome —
// _pcardSection/_pcardInfoRow/_pcardRunBar/_cfgJsonTools/_cfgJsonToggle/
// _runField/_cardMaximizeBtn/_cardPopoutBtn/_shareCompositeBtn/
// _compositeJsonBtn/_compositeBadge/_regStatsHtml/_runCmdChip/_successCell)
// and the lightweight, self-contained accordion/expand-collapse interactions
// (_pcardToggleSec/_pcardJumpSec/_pcardToggleDesc/_pcardSecGripDown/
// _pcardSecGripFull/_toggleCardMaximize/_maximizeCardFromHeader/
// _syncOutEmitter/_loadCompositeObservables/_copyRunCmd) needed so a card
// mounted on ANY page can actually expand its Configure/Inputs/Outputs
// sections and show its full config schema + declared observables.
//
// Deliberately NOT moved (stay Modules-page-only, in walkthrough.js): the
// deep interactive subsystems tied to Registry/Modules page state — inline
// Run (_runComposite/_pollCompositeRun/_collectCardConfig/
// _applyCompositeConfig/_resetCompositeConfig), pop-out window
// (_popoutCard), Share-link (_shareCompositeFromHeader), and the
// live bigraph-loom embed (_openCompositeLoomInline). Those buttons remain
// in a rendered card wherever it's mounted, but on a page that doesn't also
// load walkthrough.js (e.g. Study Detail) clicking them is an inert no-op
// (console error only, never a page-breaking exception) — the Explore
// section shows its static "Resolving…" placeholder instead of the live
// bigraph. The page's own dedicated "🧬 explore & run ↗" action is the
// sanctioned way to reach the interactive loom.
//
// Load this script BEFORE walkthrough.js wherever both are used (e.g. the
// Modules page) so walkthrough.js's own bare references to these names keep
// resolving via the global scope chain — walkthrough.js no longer defines
// them itself.
(function () {
  "use strict";

  // Local escape/API-prefix helpers — trivial, intentionally NOT shared with
  // walkthrough.js's own copies (which stay put; walkthrough.js already
  // carries more than one internal _esc). Avoids touching ~500+ unrelated
  // call sites elsewhere in walkthrough.js for a four-line utility.
  function _esc(s) {
    return String(s || '').replace(/[<>&"]/g, function (c) {
      return { '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c];
    });
  }
  function _api(p) {
    return (window.DataSource && window.DataSource.apiUrl) ? window.DataSource.apiUrl(p) : p;
  }

  // "Composite" kind pill (a composite IS a process — sits alongside Temporal/Step).
  function _compositeBadge() {
    return '<span class="proc-kind-badge proc-kind-composite" title="Composite — a Process assembled from other processes/steps; open Explore for its bigraph">Composite</span>';
  }
  window._compositeBadge = _compositeBadge;

  // contract-audit badge + panel (Phase 4B). Renders the 4A `contract_audit`.
  // Pure string helpers; empty string when there is no contract_audit (emitters,
  // pre-4A snapshots). All dynamic text goes through window._esc.
  function _contractBadge(ca) {
    if (!ca || !ca.status) return '';
    var esc = _esc;
    var map = {
      pass:         { cls: 'contract-pass',       glyph: '✓', label: 'contract' },
      fail:         { cls: 'contract-fail',       glyph: '✗', label: 'contract' },
      incomplete:   { cls: 'contract-incomplete', glyph: '◐', label: 'contract' },
      'not-declared': { cls: 'contract-none',     glyph: '—', label: 'no contract' },
      unavailable:  { cls: 'contract-none',       glyph: '—', label: 'contract n/a' },
      error:        { cls: 'contract-none',       glyph: '—', label: 'contract' }
    };
    var m = map[ca.status] || map.error;
    var n = (ca.findings || []).filter(function (f) { return f && f.severity === 'error'; }).length;
    var count = (ca.status === 'fail' && n) ? ' (' + n + ')' : '';
    var title = 'Contract audit: ' + esc(ca.status) +
      (typeof ca.grade === 'number' ? ' · ' + Math.round(ca.grade * 100) + '% declared' : '');
    return '<span class="contract-badge ' + m.cls + '" title="' + title + '">' +
      m.glyph + ' ' + esc(m.label) + count + '</span>';
  }

  function _contractPanelBody(ca) {
    if (!ca || !ca.status) return '';
    var esc = _esc;
    var rows = [];
    rows.push('<div class="contract-status-line">Status: <b>' + esc(ca.status) + '</b>' +
      (typeof ca.grade === 'number' ? ' · completeness ' + Math.round(ca.grade * 100) + '%' : '') + '</div>');
    // Explain the grade: typed ports (75%) + declared predicate conditions (25%),
    // with the concrete pieces still missing. Mirrors the composite panel.
    var c = ca.completeness;
    if (c) {
      rows.push('<div class="contract-sub muted">completeness = typed ports (75%) + declared predicate conditions (25%)</div>');
      var parts = [];
      if (typeof c.ports_total === 'number') {
        var ok = c.ports_total && c.ports_typed === c.ports_total;
        parts.push('<span class="cmpl-part ' + (ok ? 'is-ok' : 'is-gap') + '">ports ' +
          c.ports_typed + '/' + c.ports_total + ' typed</span>');
      }
      parts.push('<span class="cmpl-part ' + (c.has_predicates ? 'is-ok' : 'is-gap') + '">predicates ' +
        (c.n_predicates || 0) + '</span>');
      rows.push('<div class="contract-cmpl">' + parts.join(' · ') + '</div>');
      if ((c.missing || []).length) {
        rows.push('<div class="contract-cmpl-missing">to reach 100%: ' +
          c.missing.map(function (m) { return esc(m); }).join('; ') + '</div>');
      }
    }
    function portRows(side, ports) {
      var ks = Object.keys(ports || {});
      if (!ks.length) return '';
      return '<div class="contract-ports"><div class="contract-sub">' + esc(side) + '</div>' +
        ks.map(function (p) {
          var s = ports[p] || {}; var b = [];
          if (s._min != null || s._max != null) b.push('[' + esc(s._min == null ? '' : s._min) + ', ' + esc(s._max == null ? '' : s._max) + ']');
          if (s._units) b.push(esc(s._units));
          return '<div class="contract-port"><code>' + esc(p) + '</code> ' + esc(s._type || '') + ' ' + b.join(' ') + '</div>';
        }).join('') + '</div>';
    }
    if (ca.ports) { rows.push(portRows('inputs', ca.ports.inputs)); rows.push(portRows('outputs', ca.ports.outputs)); }
    if ((ca.conditions || []).length) {
      rows.push('<div class="contract-conditions"><div class="contract-sub">conditions</div>' +
        ca.conditions.map(function (c) {
          return '<div class="contract-cond"><b>' + esc(c.kind) + '</b> ' + esc(c.name || '') +
            ': <code>' + esc(c.expr || '') + '</code></div>';
        }).join('') + '</div>');
    }
    // Drop the library's own "completeness grade" info finding — the breakdown
    // above already shows it, more usefully.
    var findings = (ca.findings || []).filter(function (f) { return f.code !== 'completeness'; });
    if (findings.length) {
      rows.push('<div class="contract-findings"><div class="contract-sub">findings</div>' +
        findings.map(function (f) {
          return '<div class="contract-finding is-' + esc(f.severity) + '">' +
            esc(f.severity) + ' <code>' + esc(f.code) + '</code> ' + esc(f.where || '') + ' — ' + esc(f.message || '') + '</div>';
        }).join('') + '</div>');
    }
    return rows.filter(Boolean).join('');
  }

  // Render the REAL composite audit (from /api/composite-contract-audit):
  // overall status, the Phase-2 wiring findings, then a per-process roll-up.
  // Shape: {status, grade, n_processes, processes:[{name,status,grade,findings}],
  //         wiring:{status, findings:[{severity,code,where,message}]}, notice?}.
  function _compositeContractPanelBody(a) {
    if (!a || !a.status) return '<div class="contract-status-line muted">No audit data.</div>';
    var esc = _esc;
    function findingRows(fs) {
      return (fs || []).map(function (f) {
        return '<div class="contract-finding is-' + esc(f.severity) + '">' +
          esc(f.severity) + ' <code>' + esc(f.code || '') + '</code> ' +
          esc(f.where || '') + ' — ' + esc(f.message || '') + '</div>';
      }).join('');
    }
    // Per-process completeness is 75% typed ports + 25% declared predicates;
    // render it as that arithmetic so the grade is self-explaining, and drop the
    // library's own "completeness grade" info finding (we show it better here).
    function completenessLine(c) {
      if (!c) return '';
      var parts = [];
      if (typeof c.ports_total === 'number') {
        var ok = c.ports_total && c.ports_typed === c.ports_total;
        parts.push('<span class="cmpl-part ' + (ok ? 'is-ok' : 'is-gap') + '">ports ' +
          c.ports_typed + '/' + c.ports_total + ' typed</span>');
      }
      parts.push('<span class="cmpl-part ' + (c.has_predicates ? 'is-ok' : 'is-gap') + '">predicates ' +
        (c.n_predicates || 0) + '</span>');
      var line = '<div class="contract-cmpl">' + parts.join(' · ') + '</div>';
      if ((c.missing || []).length) {
        line += '<div class="contract-cmpl-missing">to reach 100%: ' +
          c.missing.map(function (m) { return esc(m); }).join('; ') + '</div>';
      }
      return line;
    }
    function nonCompletenessFindings(fs) {
      return (fs || []).filter(function (f) { return f.code !== 'completeness'; });
    }
    var rows = [];
    rows.push('<div class="contract-status-line">Status: <b>' + esc(a.status) + '</b>' +
      (typeof a.grade === 'number' ? ' · completeness ' + Math.round(a.grade * 100) + '%' : '') +
      (typeof a.n_processes === 'number' ? ' · ' + a.n_processes + ' process' + (a.n_processes === 1 ? '' : 'es') : '') +
      '</div>');
    rows.push('<div class="contract-sub muted">completeness = typed ports (75%) + declared predicate conditions (25%)</div>');
    if (a.notice) rows.push('<div class="contract-sub muted">' + esc(a.notice) + '</div>');
    // Composite-level "to improve" — the concrete, deduplicated actions.
    if ((a.improve || []).length) {
      rows.push('<div class="contract-improve"><div class="contract-sub">to improve</div>' +
        a.improve.map(function (it) {
          return '<div class="contract-improve-item">▸ ' + esc(it.action) +
            ' <span class="muted">(' + it.n_processes + ' process' + (it.n_processes === 1 ? '' : 'es') + ')</span></div>';
        }).join('') + '</div>');
    }
    // Phase 2 — wiring / interface
    var w = a.wiring || {};
    var wf = w.findings || [];
    rows.push('<div class="contract-ports"><div class="contract-sub">wiring: <b>' +
      esc(w.status || 'n/a') + '</b></div>' +
      (wf.length ? findingRows(wf)
                 : '<div class="contract-port muted">all shared stores type-consistent</div>') +
      '</div>');
    // Phase 1 — per-process roll-up
    if ((a.processes || []).length) {
      rows.push('<div class="contract-findings"><div class="contract-sub">processes</div>' +
        a.processes.map(function (p) {
          var extra = nonCompletenessFindings(p.findings);
          return '<div class="contract-proc is-' + esc(p.status) + '">' +
            '<code>' + esc(p.name) + '</code> <b>' + esc(p.status) + '</b>' +
            (typeof p.grade === 'number' ? ' · ' + Math.round(p.grade * 100) + '%' : '') +
            completenessLine(p.completeness) +
            (extra.length ? findingRows(extra) : '') +
            '</div>';
        }).join('') + '</div>');
    }
    return rows.filter(Boolean).join('');
  }

  window._contractBadge = _contractBadge;
  window._contractPanelBody = _contractPanelBody;
  window._compositeContractPanelBody = _compositeContractPanelBody;

  // TIER of a figure composite — draft interface / executable compilation / live
  // topology rewrite — inferred from its display name (see the meta-modelers
  // naming). A small colored badge makes the role scannable at a glance.
  function _compositeTier(c) {
    var n = (c && c.name) || '', id = (c && c.id) || '';
    if (/live topology/i.test(n) || /-rewrite\b/i.test(id))    return { id: 'live', label: 'Live' };
    if (/executable/i.test(n)    || /-executable\b/i.test(id)) return { id: 'exec', label: 'Executable' };
    // Any other FIGURE composite is a draft interface (typed ports, no dynamics).
    if (/\.fig\d/i.test(id))                                   return { id: 'draft', label: 'Draft' };
    return null;   // not a figure composite → no tier badge
  }
  function _compositeTierBadge(c) {
    var t = _compositeTier(c);
    if (!t) return '';
    var titles = {
      live:  'Live topology — a genuine runtime place-graph rewrite; animates the bigraph when run',
      exec:  'Executable — the draft compiled to conforming Process handlers (runnable dynamics)',
      draft: 'Draft interface — typed, unit-bearing ports + a behavior contract, no dynamics',
    };
    return '<span class="ccard-tier ccard-tier-' + t.id + '" title="' + titles[t.id] + '">' + t.label + '</span>';
  }
  // FIGURE grouping key from the composite id (…composites.fig10-1-rewrite → 10).
  function _compositeFigure(c) {
    var id = (c && c.id) || '';
    var m = id.match(/\.fig0*(\d+)/i);
    if (!m) return null;
    var num = parseInt(m[1], 10);
    return { num: num, label: 'Fig ' + num };
  }
  window._compositeTier = _compositeTier;
  window._compositeTierBadge = _compositeTierBadge;
  window._compositeFigure = _compositeFigure;

  // A card header "pop out" control (⧉ = its own window) — opens the whole card
  // (Explore/loom and all) in its own focused window. Routed through _cardPopout
  // so it works on EVERY page, not just Modules.
  function _cardPopoutBtn(address, kind) {
    return '<button class="pcard-popout" type="button" title="Pop out this card into its own window" ' +
      'onclick="event.stopPropagation();_cardPopout(this,\'' + _esc(address) + '\',\'' + _esc(kind) + '\')">⧉</button>';
  }
  window._cardPopoutBtn = _cardPopoutBtn;

  // Robust pop-out. On the Modules page walkthrough.js provides the rich
  // ?popcard= handshake (_popoutCard). On any other page (Study→Model embed, a
  // loom viewer) that function isn't loaded — and inside an embed IFRAME the
  // ?popcard= reload targets the wrong document — so fall back to opening the
  // composite's standalone loom in a new window. Either way, pop-out opens a
  // window (that's its job); maximize (⛶) fills the pane in place.
  function _cardPopout(btn, address, kind) {
    var card = (btn && btn.closest) ? btn.closest('.registry-entry-full, .registry-card') : null;
    var id = address || (card && card.getAttribute('data-address'));
    var inIframe = !!(window.parent && window.parent !== window);
    if (!inIframe && typeof window._popoutCard === 'function') {
      window._popoutCard(id, kind || 'composite');
      return;
    }
    if (!id) return;
    var apiUrl = (window.DataSource && window.DataSource.apiUrl)
      ? window.DataSource.apiUrl.bind(window.DataSource) : function (p) { return p; };
    // Pop-out lands on the graph too. The standalone loom already opens with the
    // graph visible (no ?header=off), but pass ?graph=open explicitly so the intent
    // survives any change to that default.
    var url = apiUrl('/bigraph-loom/index.html') + '?id=' + encodeURIComponent(id) + '&graph=open';
    try { url = new URL(url, window.location.href).href; } catch (e) { /* keep relative */ }
    window.open(url, '_blank',
      'width=1180,height=940,menubar=no,toolbar=no,location=no,resizable=yes,scrollbars=yes');
  }
  window._cardPopout = _cardPopout;

  // "⛶" — maximize this card into the content area, in-place. Toggles again /
  // Esc to restore. Self-contained DOM/CSS manipulation — safe on any page.
  function _cardMaximizeBtn() {
    return '<button class="pcard-maximize" type="button" title="Fill the pane — maximize (Esc to exit)" ' +
      'onclick="event.stopPropagation();_toggleCardMaximize(this)">⛶</button>';
  }
  window._cardMaximizeBtn = _cardMaximizeBtn;

  // Publish ONLY the rail's right edge. The maximized card's left edge (and hence
  // width) then tracks the rail purely via CSS (left: calc(var(--vw-rail-right) …)).
  // Cheap: one rail-rect read + one var write, NO loom-iframe geometry read — so it
  // is safe to run on every frame of a rail resize (see the rail ResizeObserver).
  // In an embed IFRAME (Study→Model) there is no rail, so the card fills from x=0.
  function _publishRailRight() {
    var inIframe = !!(window.parent && window.parent !== window);
    var rail = document.querySelector('.viv-rail');
    var railRight = inIframe ? 0 : (rail ? rail.getBoundingClientRect().right : 240);
    document.documentElement.style.setProperty('--vw-rail-right', railRight + 'px');
  }
  function _positionMaximizedCard(card) {
    // (a) track the rail (cheap) and (b) grow the embedded loom to fill from its top
    // to the bottom of the pane. The loom HEIGHT depends on the viewport + bottom
    // docks, NOT on the rail WIDTH, so a rail resize only needs (a) — that heavy
    // frame.getBoundingClientRect() reflow must stay off the per-frame rail path.
    _publishRailRight();
    var frame = card.querySelector('.ccard-loom-frame');
    if (frame) {
      var fr = frame.getBoundingClientRect();
      var cs = getComputedStyle(document.documentElement);
      // Leave room for a bottom-docked panel — chat OR the process-code rail.
      var aiBottom = parseFloat(cs.getPropertyValue('--viv-ai-bottom')) || 0;
      var codeBottom = parseFloat(cs.getPropertyValue('--viv-code-bottom')) || 0;
      frame.style.height = Math.max(360, window.innerHeight - fr.top - 16 - aiBottom - codeBottom) + 'px';
      frame.style.maxHeight = 'none';
    }
  }
  function _toggleCardMaximize(btn) {
    var card = btn.closest('.registry-entry-full');
    if (!card) return;
    // Maximize in place so the card fills the content pane. The card goes
    // position:fixed (see .pcard-maximized); inside an embed IFRAME (Study→Model)
    // that's fixed to the iframe's viewport, which IS the pane the user sees, so
    // it fills the pane there too (_positionMaximizedCard's inIframe branch drops
    // the rail offset). To pop the composite into its OWN window, use ⧉ pop-out.
    var on = card.classList.toggle('pcard-maximized');
    document.body.classList.toggle('pcard-maximized', on);
    if (on) {
      btn.title = 'Restore (Esc)';
      // Fill-the-pane lands on the graph, not on the compact run+outputs strip:
      // mark the embed so the loom lazy-mounts with its graph expanded
      // (loom-embed.js turns data-graph="open" into ?graph=open). Then open the
      // Explore section so the loom is actually visible.
      var _lembed = card.querySelector('.ccard-loom-embed');
      if (_lembed) _lembed.setAttribute('data-graph', 'open');
      var explore = card.querySelector('.pcard-sec-explore');
      if (explore && !explore.classList.contains('pcard-sec-open')) {
        var head = explore.querySelector('.pcard-sec-head');
        if (head) head.click();
      }
      _positionMaximizedCard(card);
      card._maxReposition = function () { _positionMaximizedCard(card); };
      card._maxEsc = function (e) { if (e.key === 'Escape') _toggleCardMaximize(btn); };
      window.addEventListener('resize', card._maxReposition);
      document.addEventListener('keydown', card._maxEsc);
      // The card's left edge clears the left rail via --vw-rail-right, but that var is
      // only republished here — a window resize doesn't fire when the RAIL itself is
      // resized (drag) or collapsed, so the maximized card would stop tracking the menu.
      // Observe the rail and track it, but DON'T reflow the heavy maximized content
      // (loom iframe + config panel) every frame — that's what made the drag stutter.
      // During a rail-size change we only apply a compositor-only transform so the
      // card's left edge follows live (no reflow); once the rail SETTLES we drop the
      // transform and do the real width/loom re-fit exactly once.
      var railEl = document.querySelector('.viv-rail');
      if (railEl && window.ResizeObserver) {
        var _settle = 0, _base = null;
        card._maxRailRO = new ResizeObserver(function () {
          var inIframe = !!(window.parent && window.parent !== window);
          var rr = inIframe ? 0 : railEl.getBoundingClientRect().right;
          if (_base === null) {
            _base = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--vw-rail-right')) || rr;
          }
          card.style.transform = 'translateX(' + Math.round(rr - _base) + 'px)';   // cheap, no reflow
          if (_settle) clearTimeout(_settle);
          _settle = setTimeout(function () {
            _settle = 0; _base = null;
            card.style.transform = '';
            if (card.classList.contains('pcard-maximized')) _publishRailRight();    // real re-fit, once
          }, 90);
        });
        card._maxRailRO.observe(railEl);
      }
      // Re-fit once the Explore section has finished expanding.
      setTimeout(function () { if (card.classList.contains('pcard-maximized')) _positionMaximizedCard(card); }, 120);
    } else {
      btn.title = 'Fill the pane — maximize (Esc to exit)';
      ['position', 'top', 'left', 'width', 'height', 'zIndex', 'transform'].forEach(function (p) { card.style[p] = ''; });
      var frame = card.querySelector('.ccard-loom-frame');
      if (frame) { frame.style.height = ''; frame.style.maxHeight = ''; }
      if (card._maxReposition) window.removeEventListener('resize', card._maxReposition);
      if (card._maxEsc) document.removeEventListener('keydown', card._maxEsc);
      if (card._maxRailRO) { card._maxRailRO.disconnect(); card._maxRailRO = null; }
      card._maxReposition = card._maxEsc = null;
      card.scrollIntoView({ block: 'nearest' });
    }
  }
  window._toggleCardMaximize = _toggleCardMaximize;

  // Double-clicking a composite card's header also maximizes it.
  function _maximizeCardFromHeader(headerEl) {
    var card = headerEl.closest('.registry-entry-full');
    if (!card) return;
    var btn = card.querySelector('.pcard-maximize');
    if (btn) _toggleCardMaximize(btn);
  }
  window._maximizeCardFromHeader = _maximizeCardFromHeader;

  // "{ } JSON" — reveal the composite's full resolved JSON spec. _toggleCompositeJson
  // now lives in this file (below), so the button works on every page the card
  // mounts on (Modules page AND the Study Detail Model tab).
  function _compositeJsonBtn() {
    return '<button class="pcard-json-btn" type="button" title="View the full composite JSON spec" ' +
      'onclick="event.stopPropagation();_toggleCompositeJson(this)">{ } JSON</button>';
  }
  window._compositeJsonBtn = _compositeJsonBtn;

  // "§ Contract" — reveal the contract-audit panel (status/ports/conditions/
  // findings) in a hidden inline view, toggled from the header exactly like the
  // JSON button. Replaces the always-open accordion section. Rendered only when
  // the card carries a contract_audit; the small header badge still shows the
  // status at a glance. Inert where _toggleCompositeContract isn't defined.
  function _compositeContractBtn() {
    return '<button class="pcard-json-btn pcard-contract-btn" type="button" title="View this composite\'s contract audit" ' +
      'onclick="event.stopPropagation();_toggleCompositeContract(this)">§ Contract</button>';
  }
  window._compositeContractBtn = _compositeContractBtn;

  // "</> Code" — open the code rail for this card. Header-styled variant, used by
  // the full cards; the grid/table views inline their own smaller buttons.
  function _compositeCodeBtn(id) {
    return '<button class="pcard-json-btn" type="button" title="View / edit this composite\'s source" ' +
      'onclick="event.stopPropagation();_openCompositeCode(\'' + _esc(id) + '\')">&lt;/&gt; Code</button>';
  }
  window._compositeCodeBtn = _compositeCodeBtn;
  function _processCodeBtn(address) {
    if (!address) return '';
    return '<button class="pcard-json-btn" type="button" title="View / edit this process\'s source" ' +
      'onclick="event.stopPropagation();window.ProcessCode&&ProcessCode.open(\'' + _esc(address) + '\')">&lt;/&gt; Code</button>';
  }
  window._processCodeBtn = _processCodeBtn;

  // "§ Contract" — reveal the process's contract-audit panel from the header,
  // exactly like the composite card's § Contract button, replacing the always-
  // open CONTRACT accordion section. The per-process audit is already inline on
  // the card (p.contract_audit), so the toggle just shows a pre-rendered panel —
  // no fetch. Inert where _toggleProcessContract isn't defined.
  function _processContractBtn() {
    return '<button class="pcard-json-btn pcard-contract-btn" type="button" title="View this process\'s contract audit" ' +
      'onclick="event.stopPropagation();_toggleProcessContract(this)">§ Contract</button>';
  }
  window._processContractBtn = _processContractBtn;

  // "🔗 Share" — copy a shareable link to this composite's interactive
  // bigraph view. The onclick references _shareCompositeFromHeader
  // (Modules-page-only) — inert elsewhere.
  function _shareCompositeBtn() {
    return '<button class="pcard-json-btn" type="button" title="Copy a shareable link to this composite\'s view" ' +
      'onclick="event.stopPropagation();_shareCompositeFromHeader(this)">🔗 Share</button>';
  }
  window._shareCompositeBtn = _shareCompositeBtn;

  // Usage stats chips — shared by grid + full cards, for processes AND
  // composites: "used in N composites", "requires N processes" (composites),
  // "N studies · X% passed". Returns '' when there's nothing to show.
  function _regStatsHtml(p) {
    var esc = _esc;
    function stat(glyph, n, singular, plural, title) {
      return '<span class="reg-stat" title="' + esc(title) + '"><span class="reg-stat-glyph">' + glyph +
        '</span><strong>' + n + '</strong> ' + (n === 1 ? singular : plural) + '</span>';
    }
    var stats = [];
    if (p.composite_uses) stats.push(stat('▦', p.composite_uses, 'composite', 'composites', 'Used in this many composite generators'));
    if (p.requires && p.requires.processes && p.requires.processes.length)
      stats.push(stat('⚙', p.requires.processes.length, 'process', 'processes', 'Requires this many process/step classes'));
    // Study participation / % success is meaningful only for runnable process
    // kinds and composites — not emitters/visualizations/analyses/types.
    var noStudies = /^(emitter|visualization|analysis|type|report_card)$/.test(p.kind || '');
    var sp = noStudies ? null : (p.study_participation || p.studies);   // composites carry `studies`
    if (sp && sp.studies) {
      var succ = (sp.success_pct != null && sp.total)
        ? ' <span class="reg-succ-inline ' + (sp.success_pct >= 80 ? 'reg-succ-hi' : (sp.success_pct >= 50 ? 'reg-succ-mid' : 'reg-succ-lo')) + '">' + sp.success_pct + '%</span>'
        : '';
      stats.push('<span class="reg-stat reg-stat-part" title="Participates in ' + sp.studies +
        ' stud' + (sp.studies === 1 ? 'y' : 'ies') + '; ' +
        (sp.pass != null ? sp.pass + '/' + sp.total + ' report-card outcomes passed' : '') + '"><span class="reg-stat-glyph">◆</span><strong>' +
        sp.studies + '</strong> stud' + (sp.studies === 1 ? 'y' : 'ies') + succ + '</span>');
    } else if (p.study_uses) {
      stats.push(stat('⌥', p.study_uses, 'study', 'studies', 'Referenced by this many study runner scripts'));
    }
    return stats.join('');
  }
  window._regStatsHtml = _regStatsHtml;

  // Shared "how to run this in your terminal" chip: a copy-pasteable one-line
  // command + a copy button, rendered on composite/process cards and the
  // investigation graph.
  function _runCmdChip(cmd) {
    if (!cmd) return '';
    var full = _esc(cmd);
    return '<div class="run-cmd-chip" onclick="event.stopPropagation()" ' +
        'style="display:flex;align-items:center;gap:6px;margin-top:8px;padding:4px 6px;' +
        'background:#f8fafc;border:1px solid #e2e8f0;border-radius:5px;font-size:0.72em;min-width:0">' +
      '<span aria-hidden="true" style="color:#94a3b8;flex:none;font-family:ui-monospace,monospace">$</span>' +
      '<code title="' + full + '" style="flex:1 1 auto;min-width:0;overflow:hidden;' +
        'text-overflow:ellipsis;white-space:nowrap;color:#334155;' +
        'font-family:ui-monospace,SFMono-Regular,Menlo,monospace">' + full + '</code>' +
      '<button type="button" class="run-cmd-copy" data-cmd="' + full + '" ' +
        'onclick="event.stopPropagation();_copyRunCmd(this)" title="Copy command" ' +
        'style="flex:none;font-size:0.95em;cursor:pointer;border:1px solid #cbd5e1;' +
        'background:#fff;border-radius:4px;padding:1px 6px;color:#475569">copy</button>' +
    '</div>';
  }
  window._runCmdChip = _runCmdChip;

  function _copyRunCmd(btn) {
    var cmd = btn && btn.getAttribute('data-cmd');
    if (!cmd) return;
    var done = function () {
      var prev = btn.getAttribute('data-label') || 'copy';
      btn.textContent = 'copied'; btn.style.color = '#047857';
      setTimeout(function () { btn.textContent = prev; btn.style.color = '#475569'; }, 1200);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(cmd).then(done, done);
    } else { done(); }
  }
  window._copyRunCmd = _copyRunCmd;

  // Percent-success cell/chip from a study_participation stat (pass/total of
  // report-card outcomes across participating studies). '—' when none ran.
  function _successCell(sp) {
    if (!sp || sp.success_pct == null || !sp.total) return '<span class="muted">—</span>';
    var pct = sp.success_pct;
    var cls = pct >= 80 ? 'reg-succ-hi' : (pct >= 50 ? 'reg-succ-mid' : 'reg-succ-lo');
    return '<span class="reg-succ ' + cls + '" title="' + sp.pass + ' / ' + sp.total +
      ' report-card outcomes passed across participating studies">' + pct + '%</span>';
  }
  window._successCell = _successCell;

  // ── Shared ProcessCard building blocks (used by process + composite cards) ──
  // Left info-panel row → open + scroll the matching accordion section.
  function _pcardInfoRow(target, label, n) {
    return '<button type="button" class="pcard-info-row" data-target="' + target +
      '" onclick="_pcardJumpSec(this)" title="Open the ' + label + ' section">' +
      '<span class="pcard-info-label">' + _esc(label) + '</span>' +
      '<span class="pcard-info-n">' + n + '</span></button>';
  }
  window._pcardInfoRow = _pcardInfoRow;

  // One accordion section: header (caret · name · summary [· extra]) + body.
  function _pcardSection(key, name, summary, body, opts) {
    opts = opts || {};
    var open = !!opts.open;
    var caret = open ? '▾' : '▸';
    var cls = 'pcard-sec pcard-sec-' + key + (open ? ' pcard-sec-open' : '') +
      (opts.wide ? ' pcard-sec-wide' : '') + (opts.resizable ? ' pcard-sec-resizable' : '') +
      (opts.feature ? ' pcard-sec-feature' : '');
    // Resizable sections get a drag grip: drag to set height, double-click to fit.
    var grip = opts.resizable
      ? '<div class="pcard-sec-grip" title="Drag to resize · double-click to fit contents" ' +
        'onmousedown="_pcardSecGripDown(event,this)" ontouchstart="_pcardSecGripDown(event,this)" ' +
        'ondblclick="_pcardSecGripFull(event,this)"></div>'
      : '';
    return '<div class="' + cls + '" data-sec="' + key + '">' +
      '<div class="pcard-sec-head" role="button" tabindex="0" onclick="_pcardToggleSec(this)" onkeydown="if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();_pcardToggleSec(this);}">' +
        '<span class="pcard-sec-caret">' + caret + '</span>' +
        '<span class="pcard-sec-name">' + _esc(name) + '</span>' +
        '<span class="pcard-sec-sum">' + summary + '</span>' +
        (opts.headExtra || '') +
      '</div>' +
      '<div class="pcard-sec-body">' + body + '</div>' +
      grip +
    '</div>';
  }
  window._pcardSection = _pcardSection;

  // Persistent (non-collapsible) Run bar — always visible at the card's top level.
  function _pcardRunBar(inner) {
    return '<div class="pcard-runbar">' + inner + '</div>';
  }
  window._pcardRunBar = _pcardRunBar;

  // Expand / collapse a clamped description in place.
  function _pcardToggleDesc(el) { if (el) el.classList.toggle('pcard-desc-open'); }
  window._pcardToggleDesc = _pcardToggleDesc;

  // Resize a section body by dragging its grip; double-click fits to contents.
  function _pcardSecGripDown(e, grip) {
    if (e && e.cancelable) e.preventDefault();
    var sec = grip.closest('.pcard-sec'); if (!sec) return;
    var body = sec.querySelector('.pcard-sec-body'); if (!body) return;
    sec.classList.remove('pcard-sec-full');
    var y0 = (e.touches && e.touches[0]) ? e.touches[0].clientY : e.clientY;
    var h0 = body.getBoundingClientRect().height;
    document.body.classList.add('pcard-sec-resizing');
    function move(ev) {
      var y = (ev.touches && ev.touches[0]) ? ev.touches[0].clientY : ev.clientY;
      body.style.setProperty('--pch', Math.max(56, h0 + (y - y0)) + 'px');
      if (ev.cancelable) ev.preventDefault();
    }
    function up() {
      document.removeEventListener('mousemove', move); document.removeEventListener('mouseup', up);
      document.removeEventListener('touchmove', move); document.removeEventListener('touchend', up);
      document.body.classList.remove('pcard-sec-resizing');
    }
    document.addEventListener('mousemove', move); document.addEventListener('mouseup', up);
    document.addEventListener('touchmove', move, { passive: false }); document.addEventListener('touchend', up);
  }
  window._pcardSecGripDown = _pcardSecGripDown;
  function _pcardSecGripFull(e, grip) {
    if (e) { e.preventDefault(); e.stopPropagation(); }
    var sec = grip.closest('.pcard-sec'); if (!sec) return;
    var body = sec.querySelector('.pcard-sec-body'); if (body) body.style.removeProperty('--pch');
    sec.classList.toggle('pcard-sec-full');
  }
  window._pcardSecGripFull = _pcardSecGripFull;

  // Toggle one accordion section (Configure / Inputs / Run / Outputs). Opening
  // Configure or Inputs makes sure the lazily-resolved fields are loaded.
  function _pcardToggleSec(head) {
    var sec = head.closest('.pcard-sec'); if (!sec) return;
    var open = sec.classList.toggle('pcard-sec-open');
    var caret = head.querySelector('.pcard-sec-caret'); if (caret) caret.textContent = open ? '▾' : '▸';
    var card = head.closest('.registry-entry-full');
    // Keep the card-level `.pcard-loom-open` flag in sync no matter HOW the
    // explore (loom) section is toggled — the "run · outputs · graph" bar, a
    // direct info-panel jump, or "open maximized". Only `_toggleLoomCard` used to
    // set it, so opening the loom by any other path left the flag off and the
    // collapsed "▸ run · outputs · graph" strip (hidden via .pcard-loom-open in
    // CSS) lingered stacked over the already-mounted loom. Restore the header on
    // close too, so no orphaned max-view state survives collapsing the loom.
    if (card && sec.querySelector('.ccard-loom-embed')) {
      card.classList.toggle('pcard-loom-open', open);
      if (!open) card.classList.remove('pcard-hdr-hidden');
    }
    if (!open) return;
    // Process cards lazy-load resolved config/input fields; composites don't.
    // (_loadFullRunFields is walkthrough.js's process-card-only concern — a
    // composite card never reaches this branch, so its absence here is safe.)
    if (card && !card.classList.contains('pcard-composite') && typeof _loadFullRunFields === 'function') _loadFullRunFields(card);
    // Explore section: mount the composite's loom bigraph on first open (only
    // available on pages that also load walkthrough.js's live-loom glue).
    var embed = sec.querySelector('.ccard-loom-embed');
    if (embed && typeof _openCompositeLoomInline === 'function') _openCompositeLoomInline(embed);
    // Fill the face process-count badge when a composite card is first expanded.
    if (card && card.classList.contains('pcard-composite')) _ensureCompositeProcCount(card);
    // Outputs section: lazy-fill the declared-observables checklist on first open.
    if (card && card.classList.contains('pcard-composite') && sec.getAttribute('data-sec') === 'outputs') {
      _loadCompositeObservables(card);
    }
  }
  window._pcardToggleSec = _pcardToggleSec;

  // Open/close a composite card's loom. The single "graph" bar (.pcard-graph-bar)
  // is the control; once open, the loom's OWN graph grip takes over and this bar
  // is hidden (CSS, keyed on .pcard-loom-open). Reuses _pcardToggleSec so the loom
  // lazy-mounts on first open, then syncs a card-level class for styling.
  function _toggleLoomCard(btn) {
    var card = btn.closest('.registry-entry-full'); if (!card) return;
    var sec = card.querySelector('.pcard-sec-explore'); if (!sec) return;
    var head = sec.querySelector('.pcard-sec-head'); if (!head) return;
    _pcardToggleSec(head);
    var open = sec.classList.contains('pcard-sec-open');
    card.classList.toggle('pcard-loom-open', open);
    // Collapsing the loom also restores the header (no orphaned max-view state).
    if (!open) card.classList.remove('pcard-hdr-hidden');
  }
  window._toggleLoomCard = _toggleLoomCard;

  // Collapse / restore the composite bar (header + summary) while the loom is
  // open, to maximize the viewing area. A thin restore strip takes its place.
  function _toggleCardHeader(btn) {
    var card = btn.closest('.registry-entry-full'); if (!card) return;
    // If the loom isn't open yet, open it first (collapsing the bar over an
    // empty card would be pointless).
    if (!card.classList.contains('pcard-loom-open')) {
      var exp = card.querySelector('.pcard-graph-bar');
      if (exp) _toggleLoomCard(exp);
    }
    card.classList.toggle('pcard-hdr-hidden');
  }
  window._toggleCardHeader = _toggleCardHeader;

  // Info-panel click → open the matching section and scroll it into view.
  function _pcardJumpSec(btn) {
    var target = btn.getAttribute('data-target');
    var card = btn.closest('.registry-entry-full'); if (!card) return;
    var sec = card.querySelector('.pcard-sec[data-sec="' + target + '"]'); if (!sec) return;
    if (!sec.classList.contains('pcard-sec-open')) {
      var head = sec.querySelector('.pcard-sec-head'); if (head) _pcardToggleSec(head);
    }
    try { sec.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); } catch (e) { /* ignore */ }
  }
  window._pcardJumpSec = _pcardJumpSec;

  // "Set via JSON" panel — paste a JSON object to override matching config
  // fields in one shot. Shared by process + composite Configure sections. The
  // onclick references _applyConfigJson (Modules-page-only) — inert elsewhere.
  function _cfgJsonTools() {
    return '<div class="cfg-json" hidden data-role="cfg-json">' +
        '<textarea class="cfg-json-box" spellcheck="false" rows="4" ' +
          'placeholder=\'{ "param": value, … }  — overrides the matching fields above\'></textarea>' +
        '<div class="cfg-json-actions">' +
          '<button class="btn-mini" type="button" onclick="_applyConfigJson(this)">Load into fields</button>' +
          '<span class="cfg-json-status muted"></span>' +
        '</div>' +
      '</div>';
  }
  window._cfgJsonTools = _cfgJsonTools;
  // The onclick references _toggleConfigJson (Modules-page-only) — inert elsewhere.
  function _cfgJsonToggle() {
    return '<button class="btn-mini cfg-json-toggle" type="button" onclick="_toggleConfigJson(this)" ' +
      'title="Paste a JSON object to set several parameters at once">{ } Set via JSON</button>';
  }
  window._cfgJsonToggle = _cfgJsonToggle;

  // One config parameter as an organized ROW: name · declared type · value
  // field (prefilled with the default). `opts.type` is the declared type label,
  // `opts.description` a hover/explainer line. The input carries the vtype the
  // collectors (_collectCardConfig / run) read back.
  function _runField(key, value, opts) {
    opts = opts || {};
    var t = (value === null) ? 'null' : (Array.isArray(value) ? 'json' : typeof value);
    var attr = 'class="loom-cfg-field" data-key="' + _esc(key) + '" data-vtype="';
    var input;
    if (t === 'boolean') {
      input = '<input type="checkbox" ' + attr + 'boolean"' + (value ? ' checked' : '') + '>';
    } else if (t === 'number') {
      input = '<input type="number" step="any" ' + attr + 'number" value="' + _esc(String(value)) + '">';
    } else if (t === 'string') {
      input = '<input type="text" ' + attr + 'string" value="' + _esc(value) + '">';
    } else {
      var jv = ''; try { jv = JSON.stringify(value); } catch (e) { jv = ''; }
      input = '<input type="text" ' + attr + 'json" value="' + _esc(jv) + '" placeholder="JSON / null">';
    }
    var typeLabel = opts.type || '';
    var units = opts.units || '';   // physical unit (from the param/port _units)
    var desc = (opts.description || '').trim();
    return '<div class="cfg-row"' + (desc ? ' title="' + _esc(desc) + '"' : '') + '>' +
        '<div class="cfg-row-name">' +
          '<span class="cfg-key">' + _esc(key) + '</span>' +
          (typeLabel ? '<span class="cfg-type">' + _esc(typeLabel) + '</span>' : '') +
          (units ? '<span class="cfg-units" title="unit: ' + _esc(units) + '">' + _esc(units) + '</span>' : '') +
        '</div>' +
        '<div class="cfg-row-input">' + input + '</div>' +
        (desc ? '<div class="cfg-row-desc">' + _esc(desc) + '</div>' : '') +
      '</div>';
  }
  window._runField = _runField;

  // Outputs before any run this session.
  function _compositeOutIdle() {
    return '<div class="pcard-out-empty">' +
      '<p class="pcard-out-empty-title">No run yet</p>' +
      '<p class="muted">Set <strong>Steps</strong> and hit <strong>▶ Run</strong> above — this shows the run\'s progress, then its visualizations. Past runs live under ' +
        '<a href="#simulations" onclick="_switchPage(\'simulations\');return false;">Runs</a>.</p>' +
    '</div>';
  }
  window._compositeOutIdle = _compositeOutIdle;

  // Outputs controls: the emitter (observation sink) + the observables to emit.
  // The emitter mirrors the composite's `emitter` config param (choices), and
  // on change syncs back to the Configure field so runs + Explore re-resolve
  // stay consistent. The observables checklist is lazy-filled on first open
  // (see _loadCompositeObservables) from the composite's declared emit paths.
  function _compositeOutControls(c) {
    var params = (c.parameters && typeof c.parameters === 'object') ? c.parameters : {};
    var em = params.emitter || {};
    var emVal = ('default' in em) ? em.default : null;
    var emChoices = Array.isArray(em.choices) ? em.choices : null;
    var emHint = em.description ? String(em.description).split('.')[0] : '';
    var emitterRow = '';
    if (emChoices) {
      emitterRow =
        '<div class="pcard-out-ctl-row">' +
          '<span class="pcard-out-ctl-lbl">Emitter</span>' +
          '<select class="pcard-out-emitter" data-role="out-emitter-sel" onchange="_syncOutEmitter(this)" title="Observation sink — where this run\'s outputs are written">' +
            emChoices.map(function (ch) { return '<option value="' + _esc(String(ch)) + '"' + (ch === emVal ? ' selected' : '') + '>' + _esc(String(ch)) + '</option>'; }).join('') +
          '</select>' +
          (emHint ? '<span class="pcard-out-ctl-hint muted">' + _esc(emHint) + '</span>' : '') +
        '</div>';
    } else if (emVal != null) {
      emitterRow =
        '<div class="pcard-out-ctl-row">' +
          '<span class="pcard-out-ctl-lbl">Emitter</span>' +
          '<code class="pcard-out-emitter-static">' + _esc(String(emVal)) + '</code>' +
          '<span class="pcard-out-ctl-hint muted">set in Configure</span>' +
        '</div>';
    }
    return '<div class="pcard-out-controls" data-role="out-controls">' +
      emitterRow +
      '<div class="pcard-out-ctl-row pcard-out-obs-row">' +
        '<span class="pcard-out-ctl-lbl">Observables</span>' +
        '<div class="pcard-out-obs" data-role="out-observables">' +
          (window.ProgressTrack ? window.ProgressTrack.loadingHtml('Loading declared observables…') : '<span class="muted pcard-out-obs-hint">Loading declared observables…</span>') +
        '</div>' +
      '</div>' +
    '</div>';
  }
  window._compositeOutControls = _compositeOutControls;

  // Mirror the Outputs emitter <select> back to the Configure `emitter` field so
  // there is a single authoritative value at run time (_collectCardConfig reads
  // Configure) and the Explore re-resolve uses the same emitter.
  function _syncOutEmitter(sel) {
    var card = sel.closest('.registry-entry-full'); if (!card) return;
    var cfg = card.querySelector('.loom-cfg-field[data-key="emitter"]');
    if (cfg) cfg.value = sel.value;
  }
  window._syncOutEmitter = _syncOutEmitter;

  // Count the PROCESS/STEP instances inside a resolved composite state (the
  // bigraph the Explore graph renders). Emitters are excluded — the registry
  // treats them as a separate kind (their own tab), not model processes. A
  // process/step node is counted as a unit (we don't descend into its ports/
  // config). The count is read off the RESOLVED state, so it reflects the
  // applied config (a config that structurally adds/removes a process updates it).
  function _countCompositeProcs(state) {
    var n = 0;
    (function walk(node) {
      if (!node || typeof node !== 'object') return;
      if (Array.isArray(node)) { node.forEach(walk); return; }
      var t = node._type;
      if (t === 'process' || t === 'step') {
        if (!/emitter/i.test(String(node.address || node.instance || ''))) n++;
        return;   // count the process as one unit; its children are ports/config
      }
      for (var k in node) {
        if (!Object.prototype.hasOwnProperty.call(node, k) || k.charAt(0) === '_') continue;
        walk(node[k]);
      }
    })(state);
    return n;
  }
  window._countCompositeProcs = _countCompositeProcs;

  // Fill/update a composite card's process count, in the meta line next to the
  // param count: "composite · N params · M processes".
  function _setCompositeProcCount(card, n) {
    if (!card || typeof n !== 'number') return;
    var span = card.querySelector('[data-role="proc-count"]');
    if (span) span.innerHTML = ' · <strong>' + n + '</strong> process' + (n === 1 ? '' : 'es');
  }
  window._setCompositeProcCount = _setCompositeProcCount;

  // ` data-nproc="N"` for a card whose registry record carried a server-counted
  // process count (env_worker → discover_all_composites → c.n_processes); empty
  // otherwise, so the card falls back to the lazy composite-resolve count.
  function _nprocAttr(c) {
    return (c && typeof c.n_processes === 'number') ? ' data-nproc="' + c.n_processes + '"' : '';
  }
  window._nprocAttr = _nprocAttr;

  // Resolve the composite ONCE (per card) just to count its processes and fill the
  // face badge. The composites LIST carries no process count (these are live
  // generators with no committed state), and a full resolve is too costly to run
  // for every list row — so this fires lazily, when the card's graph is first
  // shown (and again on Apply via _applyCompositeConfig's re-resolve).
  function _ensureCompositeProcCount(card) {
    if (!card || card._procCountLoaded) return;
    card._procCountLoaded = true;
    // Prefer the server-precomputed count (registry's n_processes, baked into the
    // card as data-nproc at render). It needs no resolve round-trip, so the count
    // also shows in the published static bundle, where /api/composite-resolve is
    // absent and the fetch below would simply no-op.
    var pre = card.getAttribute('data-nproc');
    if (pre !== null && pre !== '' && !isNaN(+pre)) { _setCompositeProcCount(card, +pre); return; }
    var id = card.getAttribute('data-address'); if (!id) { card._procCountLoaded = false; return; }
    fetch(_api('/api/composite-resolve?id=' + encodeURIComponent(id)))
      .then(function (r) { return r.json(); })
      .then(function (d) { var st = (d && d.state) ? d.state : d; _setCompositeProcCount(card, _countCompositeProcs(st)); })
      .catch(function () { card._procCountLoaded = false; });   // allow a retry
  }
  window._ensureCompositeProcCount = _ensureCompositeProcCount;

  // Auto-fill each composite card's "· N processes" (next to the param count) as
  // soon as the card is on screen — without needing to open a section. An
  // IntersectionObserver resolves only cards that actually become visible (so the
  // whole composite list is never mass-resolved), and a MutationObserver picks up
  // cards from every render path (full card, popout, re-render). _ensureCompositeProcCount
  // is idempotent (its own _procCountLoaded guard), so this never double-resolves.
  (function _autoCompositeProcCount() {
    if (typeof IntersectionObserver === 'undefined' || typeof MutationObserver === 'undefined') return;
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { io.unobserve(e.target); _ensureCompositeProcCount(e.target); }
      });
    }, { rootMargin: '150px' });
    // Both the full card (.pcard-composite) and the grid card (.registry-card,
    // data-kind=composite) carry data-address; resolve the count for either.
    var SEL = '.pcard-composite[data-address], .registry-card[data-kind="composite"][data-address]';
    function observe(card) { if (card && !card._procCountObserved) { card._procCountObserved = true; io.observe(card); } }
    function scan(root) {
      if (!root || root.nodeType !== 1) return;
      if (root.matches && root.matches(SEL)) observe(root);
      if (root.querySelectorAll) root.querySelectorAll(SEL).forEach(observe);
    }
    function start() {
      scan(document.body);
      new MutationObserver(function (muts) {
        muts.forEach(function (m) { m.addedNodes.forEach(scan); });
      }).observe(document.body, { childList: true, subtree: true });
    }
    if (document.body) start(); else document.addEventListener('DOMContentLoaded', start);
  })();

  // Lazy-fill the Outputs observables checklist from the composite's DECLARED
  // emit paths (composite-resolve → state._declared_emit_paths). All checked by
  // default; global_time is always emitted (time axis) so it's shown pinned/
  // disabled, not a toggle. Runs pass a subset as emit_paths (see _runComposite).
  function _loadCompositeObservables(card) {
    if (!card || card._obsLoaded) return;
    card._obsLoaded = true;
    var box = card.querySelector('[data-role="out-observables"]'); if (!box) return;
    var id = card.getAttribute('data-address');
    fetch(_api('/api/composite-resolve?id=' + encodeURIComponent(id)))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var st = (d && d.state) ? d.state : d;
        // Fill the card's "· N processes" meta from the resolved bigraph (initial load).
        try { _setCompositeProcCount(card, _countCompositeProcs(st)); } catch (e) {}
        var paths = (st && Array.isArray(st._declared_emit_paths)) ? st._declared_emit_paths : [];
        var toggles = paths.filter(function (p) { return p && p !== 'global_time'; });
        if (!toggles.length) {
          box.innerHTML = '<span class="muted">This composite declares no selectable observables — the run emits its default set.</span>';
          return;
        }
        box.innerHTML =
          (paths.indexOf('global_time') >= 0
            ? '<label class="pcard-obs-item pcard-obs-fixed" title="Always emitted — the trajectory time axis"><input type="checkbox" checked disabled> global_time</label>'
            : '') +
          toggles.map(function (p) {
            return '<label class="pcard-obs-item"><input type="checkbox" class="pcard-obs-cb" value="' + _esc(p) + '" checked> ' + _esc(p) + '</label>';
          }).join('');
      })
      .catch(function () {
        card._obsLoaded = false;  // allow a retry on next open
        box.innerHTML = '<span class="muted pcard-apply-err">Could not load declared observables.</span>';
      });
  }
  window._loadCompositeObservables = _loadCompositeObservables;

  // ── Build-error warning chip ──────────────────────────────────────────────
  // PR #1111 made GET /api/composite-state degrade to a 200 with a `build_error`
  // object (+ kind: static-fallback/last-good/skeleton, optional stale_overrides)
  // instead of 400ing when a generator build fails (stale/absent ParCa cache,
  // env-probe drift, an import error). This renders a small, non-blocking amber
  // chip near the composite header so the user knows the shown wiring is stale/
  // degraded. It is informational only — it never blocks the Run button or the
  // view (the card already renders the best-available wiring).
  //
  // Given a composite-state response object, return {text, title} for the chip,
  // or null when the wiring is healthy (no chip).
  function _buildErrorChipInfo(d) {
    if (!d || typeof d !== 'object') return null;
    var be = (d.build_error && typeof d.build_error === 'object') ? d.build_error : null;
    var kind = d.kind || '';
    var unavailable = (kind === 'skeleton' || d.wiring_status === 'unavailable');
    // Healthy build (kind generator/spec, no build_error) → no chip.
    if (!be && kind !== 'last-good' && !unavailable && !d.stale_overrides) return null;
    var detail = be ? String(be.detail || '') : String(d.notice || '');
    var shortDetail = detail.length > 160 ? detail.slice(0, 160) + '…' : detail;
    var msg = (be && be.notice) ? be.notice : shortDetail;
    var label;
    if (unavailable) {
      label = 'wiring preview unavailable';
    } else if (kind === 'last-good') {
      label = 'showing last-known-good wiring';
    } else if (be && (be.kind === 'stale-cache' || be.remote_no_cache)) {
      label = 'wiring may be stale';
    } else if (kind === 'static-fallback' || be) {
      label = 'showing default wiring — live build failed';
    } else {
      label = 'wiring may be degraded';
    }
    var text = '⚠ ' + label;   // ⚠
    if (msg && msg !== label) text += ' — ' + msg;   // — msg
    if (d.stale_overrides) {
      text += ' · Config → Apply didn’t render (showing default wiring)';
    }
    return { text: text, title: detail || msg || label };
  }
  window._buildErrorChipInfo = _buildErrorChipInfo;

  // Fill (or clear) a card's build-warning chip from a composite-state response.
  function _renderCompositeBuildWarn(cardEl, d) {
    if (!cardEl) return;
    var chip = cardEl.querySelector('[data-role="build-warn"]');
    if (!chip) return;
    var info = _buildErrorChipInfo(d);
    // Toggle style.display too: the chip's inline style sets a display, which
    // overrides the `hidden` attribute's UA display:none — without this the empty
    // amber pill lingers visible when the wiring is healthy.
    if (!info) { chip.hidden = true; chip.style.display = 'none'; chip.textContent = ''; chip.removeAttribute('title'); return; }
    chip.textContent = info.text;
    chip.title = info.title || info.text;
    chip.hidden = false;
    chip.style.display = 'inline-block';
  }
  window._renderCompositeBuildWarn = _renderCompositeBuildWarn;

  // Composite-state URL. `build_error` lives on /api/composite-state — NOT on the
  // /api/composite-resolve the card payload came from — so the chip needs its own
  // lookup. Snapshot bundles bake it at <base>/api/composite-state/<id>.json.
  function _compositeBuildStateUrl(id, overridesJson) {
    var apiUrl = (window.DataSource && window.DataSource.apiUrl)
      ? window.DataSource.apiUrl.bind(window.DataSource) : function (p) { return p; };
    if (document.body.classList.contains('snapshot')) {
      return apiUrl('/api/composite-state/' + encodeURIComponent(id) + '.json');
    }
    return apiUrl('/api/composite-state?ref=' + encodeURIComponent(id)) +
      (overridesJson ? '&overrides=' + encodeURIComponent(overridesJson) : '');
  }

  // Lazily fetch composite-state for a mounted card and render the build-warning
  // chip if the wiring came back degraded. Fire-and-forget: never throws, never
  // blocks the card/Run — a fetch failure just leaves the chip hidden. The build
  // is ParCa-heavy so this only runs on demand (loom mount), once per card, and
  // the backend TTL-caches the same lookup the loom itself makes.
  function _loadCompositeBuildWarn(cardEl, id, overridesJson) {
    if (!cardEl || !id || cardEl._buildWarnLoaded) return;
    cardEl._buildWarnLoaded = true;
    try {
      fetch(_compositeBuildStateUrl(id, overridesJson))
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) { if (d) _renderCompositeBuildWarn(cardEl, d); })
        .catch(function () { /* informational only — leave the chip hidden */ });
    } catch (e) { /* never break the card */ }
  }
  window._loadCompositeBuildWarn = _loadCompositeBuildWarn;

  // ── Composite ProcessCard ────────────────────────────────────────────────
  // A composite IS a process (§ unified idea): same card, same accordion, plus
  // an EXPLORE section (the wide loom bigraph) between Inputs and Run. A
  // composite is mostly top-level, so Inputs/Outputs are informational; the
  // value is Configure (its parameters) + Explore (its internal wiring).
  function _compositeLoomExplore(c) {
    // The card body IS the full stacked loom surface — Configure/Inputs, bigraph,
    // Run/Step, and Outputs all live inside the loom now (data-surface="full").
    // The card no longer re-implements those sections around it.
    return '<div class="ccard-loom-embed pcard-loom" data-surface="full" data-id="' + _esc(c.id) + '">' +
      '<div class="ccard-loom-frame"><p class="muted" style="padding:10px;font-size:0.85em">Resolving composite &amp; rendering the surface…</p></div>' +
    '</div>';
  }
  window._compositeLoomExplore = _compositeLoomExplore;

  // One port column (Inputs or Outputs): port name → type, from a schema dict.
  function _regPortColumn(title, schema) {
    var keys = schema && typeof schema === 'object' ? Object.keys(schema) : null;
    var body;
    if (keys === null) {
      body = '<div class="reg-port-na" title="Ports depend on a configured instance and can\'t be introspected statically.">—</div>';
    } else if (!keys.length) {
      body = '<div class="reg-port-na">(none)</div>';
    } else {
      body = '<ul class="reg-port-list">' + keys.map(function (k) {
        var t = (typeof _regTypeLabel === 'function') ? _regTypeLabel(schema[k]) : '';
        return '<li><code class="reg-port-name">' + _esc(k) + '</code>' +
          (t ? ' <span class="reg-port-type">' + _esc(t) + '</span>' : '') + '</li>';
      }).join('') + '</ul>';
    }
    return '<div class="reg-port-col"><div class="reg-port-title">' + title + '</div>' + body + '</div>';
  }
  window._regPortColumn = _regPortColumn;

  // Compact composite card (Cards / medium zoom) — mirrors the process grid
  // card: name · badge · address · short desc · usage stats.
  function _renderCompositeCardGrid(c) {
    var addr = c.module ? (c.module + '.' + c.name) : c.id;
    var desc = (c.description || '').trim(), short = desc ? desc.split('\n')[0] : '';
    var stats = _regStatsHtml(c);
    var selCls = (window._registrySelected && window._registrySelected === c.id) ? ' reg-selected' : '';
    var idA = _esc(c.id);
    // Param count + the live process count next to it. c.requires.processes is empty
    // for generators, so the real count is filled from the resolved bigraph by the
    // IntersectionObserver (data-role="proc-count") when the card is visible.
    var np = (c.parameters && typeof c.parameters === 'object') ? Object.keys(c.parameters).length : 0;
    var meta = '<div class="reg-card-meta" style="font-size:11px;color:#6b7280;margin:2px 0 4px">' +
      (np ? '<strong>' + np + '</strong> param' + (np === 1 ? '' : 's') : 'composite') +
      '<span data-role="proc-count"></span></div>';
    var tags = Array.isArray(c.tags) ? c.tags.slice(0, 3) : [];
    var tagHtml = tags.length
      ? '<div class="reg-card-tags" style="display:flex;flex-wrap:wrap;gap:4px;margin-top:5px">' +
          tags.map(function (t) { return '<span style="font-size:10px;color:#6d28d9;background:#f5f3ff;border:1px solid #e9d5ff;border-radius:4px;padding:1px 6px">' + _esc(t) + '</span>'; }).join('') +
        '</div>' : '';
    var actions = '<div class="reg-card-actions" style="display:flex;gap:6px;margin-top:9px;flex-wrap:wrap">' +
      '<button type="button" class="reg-explore-btn" onclick="event.stopPropagation();_showRegistryCard(\'' + idA + '\',\'composite\')" ' +
        'title="Open the full card in the Process view">🔍 Explore</button>' +
      '<button type="button" onclick="event.stopPropagation();_openCompositeCode(\'' + idA + '\')" ' +
        'title="View / edit this composite\'s source (spec YAML or its generator)" ' +
        'style="height:26px;padding:0 9px;font-size:12px;background:#fff;color:#374151;border:1px solid #d1d5db;border-radius:5px;cursor:pointer">&lt;/&gt; Code</button>' +
    '</div>';
    return '<div class="registry-card' + selCls + '" data-address="' + idA + '" data-kind="composite"' + _nprocAttr(c) +
        ' onclick="_selectRegistryEntry(\'' + idA + '\')" ondblclick="_showRegistryCard(\'' + idA + '\',\'composite\')"' +
        ' title="Double-click to Explore (open the full card)">' +
      '<div class="reg-card-row">' +
        '<div class="reg-card-main">' +
          '<div class="reg-card-head"><strong class="reg-card-name">' + _esc(c.name) + '</strong></div>' +
          '<code class="reg-card-addr">' + _esc(addr) + '</code>' +
          meta +
          (desc ? '<p class="reg-card-desc pcard-desc-clamp" onclick="event.stopPropagation();_pcardToggleDesc(this)" title="Click to read the full description">' + _esc(desc) + '</p>' : '') +
          tagHtml +
          _runCmdChip(c.run_command) +
          actions +
        '</div>' +
        '<div class="reg-card-stats">' + stats + '</div>' +
      '</div>' +
    '</div>';
  }
  window._renderCompositeCardGrid = _renderCompositeCardGrid;

  // Open the code panel for a composite: a spec composite → its YAML file, a
  // generator composite → its @composite_generator module. Looks the record up
  // in the composites cache so we pass structured fields, not embedded strings.
  function _openCompositeCode(id) {
    var c = (window._compositesById || {})[id] || { id: id };
    if (!window.ProcessCode) return;
    window.ProcessCode.openComposite({
      id: c.id || id,
      name: c.name,
      module: c.module || '',
      source_path: c.source || '',   // spec relpath; empty ⇒ resolve via module
    });
  }
  window._openCompositeCode = _openCompositeCode;

  // --- Run-target badge -----------------------------------------------------
  // Show whether a composite ▶ Run will execute Local or on the Cloud (GovCloud)
  // deployment. This is resolve_run_target(workspace) — per WORKSPACE, not per card,
  // and NOT the same as the Local/Cloud *scope* selector (which binds the source).
  // That distinction is exactly what confuses: scope can say "remote" while a Run
  // still executes locally. One preflight fetch per composites render fills every
  // card's badge.
  function _computeRunTarget(pf) {
    // The Environment scope (dynamic run-target) wins when Cloud is active — a Run
    // then dispatches to the selected build. Otherwise the badge reflects the
    // workspace's resolve_run_target (the preflight).
    try {
      if (window.VivEnv && window.VivEnv.isCloud()) {
        var b = window.VivEnv.runBuild();
        // Action-oriented affordance: make it obvious that ▶ Run dispatches to a
        // SPECIFIC Cloud build, not just that the workspace "runs on cloud".
        // Green/cloud styling separates the ready-to-dispatch state from the
        // amber "no build" blocker and the grey Local state.
        if (b) return { label: '▶ Run → ☁ Cloud · build #' + b.simulator_id, bg: '#e7f6ec', fg: '#1a7f4b',
          tip: '▶ Run dispatches to the Cloud (GovCloud) against build #' + b.simulator_id +
               (b.commit ? ' (' + String(b.commit).slice(0, 7) + ')' : '') +
               '. It runs the build’s committed code — local edits not in that build won’t apply. ' +
               'The dispatch itself takes ~15-25s (sms-api registers the run over the SSM tunnel); the card tracks it robustly once it lands.' };
        return { label: '☁ Cloud · no build ⚠', bg: '#fdf0e3', fg: '#a15c12',
          tip: 'Cloud is active but no build is selected — a Run is blocked. Pick or build one, or switch to Local.' };
      }
    } catch (e) { /* VivEnv unavailable → fall through to preflight */ }
    var known = !!(pf && pf.target);
    var cloud = known && pf.target === 'deployment';
    if (!known) return { label: 'Runs: —', bg: '#eef1f4', fg: '#8a97a4', tip: 'Could not determine where a Run will execute.' };
    if (cloud) return { label: 'Runs: Cloud', bg: '#e6f0fb', fg: '#1e5fa4',
      tip: pf.message || 'This workspace runs on the Cloud (GovCloud) deployment — ▶ Run dispatches remotely.' };
    return { label: 'Runs: Local', bg: '#eef1f4', fg: '#667085',
      tip: (pf.message || 'This workspace runs locally.') +
        ' Switch the Environment scope to Cloud (with a build selected) to dispatch a Run remotely instead.' };
  }
  // Per-run local/remote switch: the run-target badge is a CLICKABLE chip when
  // VivEnv is available (live mode) — clicking flips the Environment scope
  // Local↔Cloud in place, so you switch where a Run executes without leaving the
  // card for the Source panel's toggle. Snapshot/static mode (no VivEnv) leaves
  // the badge as a passive label.
  function _runTargetClickable() {
    return !!(window.VivEnv && typeof window.VivEnv.setScope === 'function');
  }
  function _applyRunTargetBadges(pf) {
    var badges = document.querySelectorAll('.pcard-runtarget[data-role="runtarget"]');
    if (!badges.length) return;
    if (pf !== undefined) window._lastRunTargetPreflight = pf;  // cache for scope-change re-apply
    var t = _computeRunTarget(pf !== undefined ? pf : window._lastRunTargetPreflight);
    var clickable = _runTargetClickable();
    badges.forEach(function (b) {
      b.textContent = t.label;
      b.title = t.tip + (clickable ? ' — click to switch Local ⇄ Cloud.' : '');
      b.style.background = t.bg; b.style.color = t.fg;
      b.style.cursor = clickable ? 'pointer' : '';
      b.setAttribute('data-clickable', clickable ? '1' : '0');
    });
  }
  window._applyRunTargetBadges = _applyRunTargetBadges;
  // Re-reflect the badge live when the Environment scope / selected build changes
  // (branch-source.js dispatches viv:envchange) — no re-fetch, re-reads VivEnv.
  window.addEventListener('viv:envchange', function () { _applyRunTargetBadges(); });
  // One delegated handler for the clickable chip: flip scope to the OTHER target.
  // Registered once; badges are re-created per render, so delegation (not per-badge
  // listeners) avoids duplicates/leaks. viv:envchange then re-applies every badge.
  document.addEventListener('click', function (ev) {
    var chip = ev.target && ev.target.closest && ev.target.closest('.pcard-runtarget[data-role="runtarget"]');
    if (!chip || chip.getAttribute('data-clickable') !== '1') return;
    if (!_runTargetClickable()) return;
    ev.preventDefault(); ev.stopPropagation();
    try { window.VivEnv.setScope(window.VivEnv.isCloud() ? 'local' : 'remote'); }
    catch (e) { /* VivEnv gone → no-op */ }
  });

  var _runTargetScheduled = false;
  function _scheduleRunTargetBadges() {
    if (_runTargetScheduled) return;   // debounce: one fetch per render batch, not per card
    _runTargetScheduled = true;
    setTimeout(function () {
      _runTargetScheduled = false;
      var BP = window.__BASE_PATH__ || '';
      fetch(BP + '/api/remote-dispatch-preflight')
        .then(function (r) { return r.ok ? r.json() : null; })
        .catch(function () { return null; })
        .then(_applyRunTargetBadges);
    }, 0);
  }

  function _renderCompositeCardFull(c) {
    _scheduleRunTargetBadges();
    var params = (c.parameters && typeof c.parameters === 'object') ? c.parameters : {};
    // The raw config_schema (from the env worker) still carries each param's
    // _units even when the parameters mapping dropped it — read it as a fallback
    // so the Registry card shows the unit beside the type.
    var cfgSchema = (c.config_schema && typeof c.config_schema === 'object') ? c.config_schema : {};
    var pKeys = Object.keys(params), nCfg = pKeys.length;
    var desc = (c.description || '').trim();
    var sel = (window._registrySelected && window._registrySelected === c.id) ? ' reg-selected' : '';
    var roPill = c.read_only ? '<span class="tag-pill" style="background:#fef2f2;color:#b91c1c;margin-left:6px">read-only</span>' : '';

    var chip = function (k) { return '<code class="pcard-chip">' + _esc(k) + '</code>'; };
    var cfgChips = nCfg
      ? pKeys.slice(0, 6).map(chip).join('') + (nCfg > 6 ? ' <span class="muted pcard-chip-more">+' + (nCfg - 6) + '</span>' : '')
      : '<span class="muted">none</span>';

    // Editable config fields from each parameter's default; title carries its
    // description so hovering a field explains the parameter.
    var cfgFields = nCfg
      ? pKeys.map(function (k) {
          var pv = params[k] || {};
          var sch = cfgSchema[k] || {};
          return _runField(k, ('default' in pv) ? pv.default : null, { type: pv.type, units: pv.units || pv._units || sch._units, description: pv.description });
        }).join('')
      : '<p class="muted" style="font-size:0.82em">No parameters.</p>';

    var infoPanel =
      '<div class="pcard-infopanel">' +
        _pcardInfoRow('configure', 'Config', nCfg) +
        _pcardInfoRow('inputs', 'Inputs', 0) +
        _pcardInfoRow('outputs', 'Outputs', 0) +
      '</div>';

    var configBody =
      '<div class="cfg-list" data-role="cfg">' + cfgFields + '</div>' +
      _cfgJsonTools() +
      '<div class="pcard-config-actions">' +
        '<button class="btn-mini pcard-apply" type="button" onclick="_applyCompositeConfig(this)" title="Apply parameters &amp; re-resolve the Explore bigraph">✓ Apply</button>' +
        '<button class="btn-mini" type="button" onclick="_resetCompositeConfig(this)" title="Reset to declared defaults">↺ Reset</button>' +
        _cfgJsonToggle() +
        '<span class="pcard-apply-status muted" data-role="apply-status"></span>' +
      '</div>';

    var addr = c.module ? (c.module + '.' + c.name) : c.id;

    return '<div class="registry-entry registry-entry-full loom-runnable pcard pcard-accordion pcard-composite' + sel +
        '" data-address="' + _esc(c.id) + '" data-kind="composite"' + _nprocAttr(c) + '>' +
      '<div class="loom-card loom-card-stack loom-card-composite">' +
        '<div class="pcard-top">' +
          '<div class="pcard-header pcard-title" onclick="_pinCardTop(this)" ondblclick="event.stopPropagation();_maximizeCardFromHeader(this)" title="Click to pin to top · double-click to maximize">' +
            // Clean face: the title, then only the read-only pill. The "Composite"
            // kind badge, the composite-tier badge, the inline contract marker, and the
            // "workspace" tag were removed as redundant/obvious here; the process count
            // now sits next to the param count in the meta line below.
            '<span class="loom-name">' + _esc(c.name) + '</span>' + roPill +
            '<span class="pcard-runtarget" data-role="runtarget" title="checking where a Run will execute…" ' +
              'style="display:inline-block;margin-left:8px;padding:1px 8px;border-radius:10px;font-size:11px;font-weight:600;' +
              'background:#eef1f4;color:#8a97a4;vertical-align:middle">Runs: …</span>' +
            '<code class="loom-addr">' + _esc(addr) + '</code>' +
            // Build-error warning chip (PR #1111 degrade). Hidden until a
            // composite-state fetch reports the shown wiring is stale/degraded
            // (_loadCompositeBuildWarn fires when the loom mounts). Amber, matching
            // the workbench's status-pill convention; informational, non-blocking.
            '<span class="pcard-build-warn" data-role="build-warn" hidden ' +
              'style="display:none;margin-left:8px;padding:1px 9px;border-radius:10px;' +
              'font-size:11px;font-weight:600;background:#fef3c7;color:#92400e;border:1px solid #fde68a;' +
              'vertical-align:middle;max-width:520px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"></span>' +
            '<button class="pcard-hdr-collapse" type="button" onclick="event.stopPropagation();_toggleCardHeader(this)" title="Collapse this bar to maximize the view">⌃</button>' +
            _shareCompositeBtn() +
            _compositeJsonBtn() +
            (c.contract_audit ? _compositeContractBtn() : '') +
            _compositeCodeBtn(c.id) +
            _cardMaximizeBtn() +
            _cardPopoutBtn(c.id, 'composite') +
          '</div>' +
          // Thin restore strip — shown only while the header is collapsed
          // (pcard-hdr-hidden); click to bring the bar back.
          '<button class="pcard-hdr-restore" type="button" onclick="_toggleCardHeader(this)" title="Show the composite bar">▸ ' + _esc(c.name) + '</button>' +
          '<div class="pcard-summary">' +
            '<div class="pcard-desc-col">' +
              '<div class="pcard-contract-meta" data-role="contract-meta">composite · <strong>' + nCfg + '</strong> param' + (nCfg === 1 ? '' : 's') + '<span data-role="proc-count"></span></div>' +
              (function () { var s = _regStatsHtml(c); return s ? '<div class="reg-card-stats pcard-usage">' + s + '</div>' : ''; })() +
              (desc ? '<p class="loom-desc pcard-desc-clamp" onclick="_pcardToggleDesc(this)" title="Click to expand / collapse">' + _esc(desc) + '</p>' : '') +
              // Same copy-pasteable "how to run this" chip as the grid card, so
              // the high-zoom (full-card) list also surfaces the terminal command.
              _runCmdChip(c.run_command) +
            '</div>' +
          '</div>' +
          '<div class="pcard-json-view" data-role="composite-json" hidden>' +
            '<div class="pcard-json-body"></div>' +
          '</div>' +
          // Contract-audit panel — hidden until the header "§ Contract" button
          // toggles it (mirrors the JSON view). The REAL composite audit (a
          // per-process roll-up + wiring check) is expensive — it builds the
          // composite's core — so it's fetched lazily on first open from
          // /api/composite-contract-audit, not baked into the card. The card's
          // own c.contract_audit is the coreless stub ("unavailable").
          (c.contract_audit
            ? '<div class="pcard-json-view pcard-contract-view" data-role="composite-contract" hidden>' +
                '<div class="pcard-contract-body"></div>' +
              '</div>'
            : '') +
        '</div>' +
        '<div class="pcard-acc">' +
          // Card-owned Cloud-run status chip. When a Run dispatches to the Cloud
          // the loom emits explore:remote-dispatching / -dispatched / -failed
          // messages (see loom-embed.js); the PARENT owns a patient "Dispatching
          // to Cloud build #N…" → "☁ Cloud run #<simid> · queued → running →
          // completed" chip here, polling the robust sim-status endpoint the Runs
          // tab uses — instead of the loom bar's timeout-prone per-run polling.
          '<div class="pcard-cloud-run" data-role="cloud-run" hidden></div>' +
          // ONE surface: the card body is just the lazily-mounted loom. The
          // full-width "graph" bar is the single control (it replaces the old
          // header Explore/Collapse button AND the duplicated static run/outputs
          // strip). It looks and sits exactly like the loom's own collapsed graph
          // grip, so opening is seamless: click to lazy-mount + open the loom
          // (Configure · bigraph · run · outputs — the loom owns all of it). The
          // loom is heavy to resolve, so the list stays fast by mounting only on
          // click; once open, the loom's own graph grip takes over and this bar is
          // hidden (CSS, keyed on .pcard-loom-open) — one continuous bar.
          // The single control. Clicking it lazy-mounts the loom — which owns the
          // run + outputs (and the graph). The loom mounts with its GRAPH COLLAPSED,
          // so the first thing shown is the compact run + outputs strip (identical
          // to every other state, since it IS the loom); the same bar then expands
          // the graph. Once mounted, the loom's own grip takes over and this
          // card-level bar hides (CSS, keyed on .pcard-loom-open).
          '<button class="pcard-graph-bar" type="button" onclick="event.stopPropagation();_toggleLoomCard(this)" title="Open the composite — run · outputs · graph">' +
            '<span class="pcard-graph-bar-handle"></span>' +
            '<span class="pcard-graph-bar-label">▸ run · outputs · graph</span>' +
          '</button>' +
          // The loom — the FULL stacked surface (Configure/Inputs · bigraph ·
          // Run/Step · Outputs), lazy-mounted on first open. Graph collapsed at
          // first so run + outputs lead.
          _pcardSection('explore', 'Explore', '<span class="pcard-sec-hint">◆ Configure · run · outputs — click to open</span>', _compositeLoomExplore(c), { wide: true, feature: true }) +
          // Contract audit now lives in a header-toggled panel (the "§ Contract"
          // button + .pcard-contract-view above), not an always-open accordion.
        '</div>' +
      '</div>' +
    '</div>';
  }
  window._renderCompositeCardFull = _renderCompositeCardFull;

  // ---------------------------------------------------------------------------
  // Composite JSON viewer — MOVED here from walkthrough.js so the "{ } JSON"
  // header button works on EVERY page the card mounts on (the Study Detail
  // Model tab doesn't load walkthrough.js, where these used to live, so the
  // button was a dead no-op there). composite-card.js loads before
  // walkthrough.js, so walkthrough.js's own bare references to these names
  // keep resolving via the global scope chain.
  // ---------------------------------------------------------------------------
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
  window._jsonViewer = _jsonViewer;

  function _toggleCompositeJson(btn) {
    var card = btn.closest('.registry-entry-full'); if (!card) return;
    var panel = card.querySelector('[data-role="composite-json"]'); if (!panel) return;
    var show = panel.hidden;
    panel.hidden = !show;
    btn.classList.toggle('active', show);
    if (!show || panel._loaded) return;
    panel._loaded = true;
    var id = card.getAttribute('data-address');
    if (id) panel.setAttribute('data-cid', id);   // so the fill-screen modal / download can name itself
    var body = panel.querySelector('.pcard-json-body');
    if (body) body.innerHTML = '<span class="muted" style="font-size:0.85em">Resolving composite JSON…</span>';
    var url = (typeof _compositeStateUrl === 'function')
      ? _compositeStateUrl(id)
      : _api('/api/composite-resolve?id=' + encodeURIComponent(id));
    fetch(url).then(function (r) { return r.json(); }).then(function (j) {
      var doc = (j && j.state) ? j.state : j;
      if (body) {
        var live = !document.body.classList.contains('snapshot');
        body.innerHTML =
          '<div class="pcard-json-toolbar">' +
            '<button class="btn-mini" type="button" onclick="_copyCompositeJson(this)">⧉ Copy</button>' +
            '<button class="btn-mini" type="button" onclick="_downloadCompositeJson(this)">⬇ Download</button>' +
            '<a class="btn-mini" href="' + _esc(url) + '" target="_blank" rel="noopener">Raw ↗</a>' +
            '<button class="btn-mini" type="button" onclick="_cfgJsonExpandAll(this,true)">⊕ Expand all</button>' +
            '<button class="btn-mini" type="button" onclick="_cfgJsonExpandAll(this,false)">⊖ Collapse all</button>' +
            '<button class="btn-mini pcard-json-max-btn" type="button" onclick="_toggleCompositeJsonMax(this)" title="Expand to fill the view (Esc to exit)">⛶ Fill screen</button>' +
            (live ? '<button class="btn-mini pcard-json-state-btn" type="button" onclick="_loadCompositeInitialState(this)" ' +
              'title="Run the composite 1 step and load the real state (lattice dims/fields, cell positions/types, immune agents) — the declared document shows these empty at rest">▸ Load initial state</button>' : '') +
            '<span class="pcard-json-state-note muted" style="font-size:0.78em;align-self:center"></span>' +
          '</div>' +
          _jsonViewer(doc) +
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

  // "⬇ Download" — save the currently-shown JSON (the declared document, or the
  // loaded post-run state) as a .json file.
  function _downloadCompositeJson(btn) {
    var panel = btn.closest('[data-role="composite-json"]'); if (!panel) return;
    var raw = panel.querySelector('.pcard-json-raw');
    var txt = (panel._json != null) ? JSON.stringify(panel._json, null, 2) : (raw ? raw.textContent : '');
    if (!txt) return;
    var card = btn.closest('.registry-entry-full');
    var id = (card && card.getAttribute('data-address')) || 'composite';
    var base = String(id).split('.').pop() || 'composite';
    var name = base + (panel._stateLoaded ? '-state' : '') + '.json';
    try {
      var blob = new Blob([txt], { type: 'application/json' });
      var href = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = href; a.download = name; a.style.display = 'none';
      document.body.appendChild(a); a.click();
      setTimeout(function () { document.body.removeChild(a); URL.revokeObjectURL(href); }, 150);
      var o = btn.textContent; btn.textContent = '✓ Saved'; setTimeout(function () { btn.textContent = o; }, 1200);
    } catch (e) { /* download blocked — Raw ↗ remains available */ }
  }
  window._downloadCompositeJson = _downloadCompositeJson;

  // "⛶ Fill screen" — expand the JSON to fill the CONTENT AREA (the column to
  // the right of the workbench rail), from its top down to the viewport bottom,
  // with the rail still on the side. NOT a modal, NOT OS fullscreen.
  //
  // Why it's built in the TOP document: the Model tab renders inside a study
  // "porthole" iframe that a ResizeObserver sizes to its CONTENT height. A
  // position:fixed overlay inside that iframe is out of flow, so the porthole
  // never grows (and resets any height we set) → the overlay fills only a short
  // box with whitespace below. So instead the child (same-origin) builds the
  // overlay in window.top.document, positioned over its own iframe's rect and
  // extending to the viewport bottom — independent of the porthole's height.
  // Everything is inline-styled + self-injected CSS, so it's immune to a stale
  // cached stylesheet.
  function _topCtx() {
    var fe = null; try { fe = window.frameElement; } catch (e) { fe = null; }
    try {
      if (fe && fe.ownerDocument && fe.ownerDocument.body) {
        var win = fe.ownerDocument.defaultView || window;
        void win.innerHeight;  // force the cross-origin check
        return { doc: fe.ownerDocument, win: win, fe: fe };
      }
    } catch (e) { /* cross-origin — fall back */ }
    return { doc: document, win: window, fe: null };
  }
  function _ensureTreeCss(doc) {
    if (doc.getElementById('cfg-json-tree-style')) return;
    var css = [
      '#cfg-json-top-overlay .json-viewer{max-height:none;height:auto;overflow:visible;background:transparent;border:0;border-radius:0;padding:0;margin:0;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px;line-height:1.55;color:#1f2328;}',
      '#cfg-json-top-overlay .json-viewer .jt-row,#cfg-json-top-overlay .json-viewer .jt-branch>summary{display:flex;gap:4px;align-items:baseline;padding:1px 0;}',
      '#cfg-json-top-overlay .json-viewer .jt-children{padding-left:14px;border-left:1px solid #eceff2;margin-left:3px;}',
      '#cfg-json-top-overlay .json-viewer .jt-key{color:#57606a;font-weight:600;}',
      '#cfg-json-top-overlay .json-viewer .jt-colon{color:#8a94a0;}',
      '#cfg-json-top-overlay .json-viewer .jt-num{color:#0550ae;}',
      '#cfg-json-top-overlay .json-viewer .jt-str{color:#0a7d33;}',
      '#cfg-json-top-overlay .json-viewer .jt-bool{color:#8250df;}',
      '#cfg-json-top-overlay .json-viewer .jt-null{color:#8a94a0;font-style:italic;}',
      '#cfg-json-top-overlay .json-viewer .jt-punct{color:#8a94a0;}',
      '#cfg-json-top-overlay .json-viewer .jt-count{font-size:10px;color:#8a94a0;background:#eef1f4;border-radius:9px;padding:0 6px;margin:0 2px;}',
      '#cfg-json-top-overlay .json-viewer .jt-branch>summary{cursor:pointer;list-style:none;}',
      '#cfg-json-top-overlay .json-viewer details>summary::-webkit-details-marker{display:none;}',
    ].join('\n');
    var st = doc.createElement('style'); st.id = 'cfg-json-tree-style'; st.textContent = css;
    (doc.head || doc.documentElement).appendChild(st);
  }
  function _closeTopOverlay() {
    // search this doc AND the top doc
    [document, (function () { try { return window.top.document; } catch (e) { return null; } })()].forEach(function (doc) {
      if (!doc) return;
      var el = doc.getElementById('cfg-json-top-overlay');
      if (el) { if (el._reflow && el._win) el._win.removeEventListener('resize', el._reflow); if (el._esc && el._edoc) el._edoc.removeEventListener('keydown', el._esc); if (el.parentNode) el.parentNode.removeChild(el); }
    });
    _syncMaxBtns();
  }
  function _syncMaxBtns() {
    var open = false;
    try { open = !!(window.top.document.getElementById('cfg-json-top-overlay')); } catch (e) { open = !!document.getElementById('cfg-json-top-overlay'); }
    document.querySelectorAll('.pcard-json-max-btn').forEach(function (b) {
      b.textContent = open ? '⤡ Exit' : '⛶ Fill screen'; b.classList.toggle('active', open);
    });
  }
  function _openTopOverlay(data, name, panel) {
    var ctx = _topCtx(), doc = ctx.doc, win = ctx.win, fe = ctx.fe;
    _closeTopOverlay();
    _ensureTreeCss(doc);
    var cur = data;   // current data shown (swapped in when initial state loads)
    var text = function () { try { return JSON.stringify(cur, null, 2); } catch (e) { return String(cur); } };
    // composite address for the "Load initial state" run (card attr, then data-cid)
    var cardEl = panel && panel.closest ? panel.closest('.registry-entry-full') : null;
    var cid = (cardEl && cardEl.getAttribute('data-address')) || (panel && panel.getAttribute && panel.getAttribute('data-cid')) || name;
    var live = !!(panel && panel.querySelector && panel.querySelector('.pcard-json-state-btn'));
    var ov = doc.createElement('div'); ov.id = 'cfg-json-top-overlay';
    ov.style.cssText = 'position:fixed;z-index:2147483000;background:#fbfcfe;border-left:1px solid #e1e4e8;' +
      'box-shadow:-3px 0 16px rgba(0,0,0,.10);display:flex;flex-direction:column;overflow:hidden;';
    var place = function () {
      var left = 0, top = 0, width = win.innerWidth, h = win.innerHeight;
      if (fe) { var r = fe.getBoundingClientRect(); left = Math.max(0, r.left); top = Math.max(0, r.top); width = r.width; h = win.innerHeight - top; }
      ov.style.left = left + 'px'; ov.style.top = top + 'px'; ov.style.width = width + 'px'; ov.style.height = Math.max(300, h) + 'px';
    };
    place();
    var bar = doc.createElement('div');
    bar.style.cssText = 'display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:8px 12px;border-bottom:1px solid #e1e4e8;background:#f6f8fa;font-family:system-ui,sans-serif;';
    var title = doc.createElement('strong'); title.style.cssText = 'font-size:13px;margin-right:6px;'; title.textContent = name + ' — JSON';
    bar.appendChild(title);
    function mk(label, fn) {
      var b = doc.createElement('button'); b.type = 'button'; b.textContent = label;
      b.style.cssText = 'font-size:12px;padding:3px 9px;border:1px solid #e2e8f0;border-radius:6px;background:#fff;cursor:pointer;color:#334155;';
      b.addEventListener('click', fn); bar.appendChild(b); return b;
    }
    mk('⧉ Copy', function () { var s = this, o = s.textContent, d = function () { s.textContent = '✓ Copied'; setTimeout(function () { s.textContent = o; }, 1200); }; if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text()).then(d).catch(d); else d(); });
    mk('⬇ Download', function () { var s = this, o = s.textContent; try { var bl = new Blob([text()], { type: 'application/json' }); var u = URL.createObjectURL(bl); var a = doc.createElement('a'); a.href = u; a.download = name.split('.').pop() + '.json'; a.style.display = 'none'; doc.body.appendChild(a); a.click(); setTimeout(function () { doc.body.removeChild(a); URL.revokeObjectURL(u); }, 150); s.textContent = '✓ Saved'; setTimeout(function () { s.textContent = o; }, 1200); } catch (e) {} });
    mk('⊕ Expand all', function () { ov.querySelectorAll('.json-viewer details').forEach(function (d) { d.open = true; }); });
    mk('⊖ Collapse all', function () { ov.querySelectorAll('.json-viewer details').forEach(function (d) { d.open = false; }); });
    var stateNote = doc.createElement('span'); stateNote.style.cssText = 'font-size:11px;color:#64748b;';
    if (live) {
      var loaded = !!(panel && panel._stateLoaded);
      var sbtn = mk(loaded ? '✓ initial state' : '▸ Load initial state', function () {
        var self = this; if (self._busy) return; self._busy = true;
        var o0 = self.textContent; self.disabled = true; self.textContent = 'running 1 step…';
        stateNote.textContent = 'building the composite core…';
        _fetchInitialState(cid, function (t) { stateNote.textContent = t || ''; }).then(function (state) {
          cur = state;
          bodyEl.innerHTML = (typeof _jsonViewer === 'function') ? _jsonViewer(state) : '<pre>' + _esc(JSON.stringify(state, null, 2)) + '</pre>';
          _applyInitialState(panel, state);
          self.disabled = false; self._busy = false; self.textContent = '✓ initial state'; self.classList.add('active');
          stateNote.textContent = '● loaded state after 1 step';
        }).catch(function (e) {
          self.disabled = false; self._busy = false; self.textContent = o0;
          stateNote.textContent = String(e && e.message || e);
        });
      });
      if (loaded) sbtn.classList.add('active');
      sbtn.title = 'Run the composite 1 step and load the real state (lattice dims/fields, cell positions/types, immune agents) — the declared document shows these empty at rest';
      bar.appendChild(stateNote);
    }
    var spacer = doc.createElement('span'); spacer.style.cssText = 'flex:1 1 auto'; bar.appendChild(spacer);
    mk('⤡ Exit', _closeTopOverlay);
    var bodyEl = doc.createElement('div');
    bodyEl.style.cssText = 'flex:1 1 auto;min-height:0;overflow:auto;padding:12px 16px;';
    bodyEl.innerHTML = (typeof _jsonViewer === 'function') ? _jsonViewer(cur) : '<pre>' + _esc(text()) + '</pre>';
    ov.appendChild(bar); ov.appendChild(bodyEl);
    doc.body.appendChild(ov);
    ov._reflow = place; ov._win = win; win.addEventListener('resize', place);
    var escFn = function (e) { if (e.key === 'Escape') _closeTopOverlay(); };
    ov._esc = escFn; ov._edoc = doc; doc.addEventListener('keydown', escFn);
    _syncMaxBtns();
  }
  function _toggleCompositeJsonMax(btn) {
    var panel = btn.closest('[data-role="composite-json"]'); if (!panel) return;
    var open = false;
    try { open = !!(window.top.document.getElementById('cfg-json-top-overlay')); } catch (e) { open = !!document.getElementById('cfg-json-top-overlay'); }
    if (open) { _closeTopOverlay(); return; }
    var data = (panel._json != null) ? panel._json : null;
    if (data == null) { var raw = panel.querySelector('.pcard-json-raw'); try { data = raw ? JSON.parse(raw.textContent) : null; } catch (e) { data = null; } }
    _openTopOverlay(data, panel.getAttribute('data-cid') || 'composite', panel);
  }
  window._toggleCompositeJsonMax = _toggleCompositeJsonMax;

  // Expand / collapse every <details> node in a JSON panel's tree at once.
  function _cfgJsonExpandAll(btn, open) {
    var panel = btn.closest('[data-role="composite-json"]'); if (!panel) return;
    panel.querySelectorAll('.json-viewer details').forEach(function (d) { d.open = open; });
  }
  window._cfgJsonExpandAll = _cfgJsonExpandAll;

  // "▸ Load initial state" — the declared composite document shows its stores
  // empty at rest (process-bigraph fills lattice dims/fields, cell positions,
  // etc. only on the first process step). This runs the composite ONE step via
  // the loom run API (composite-test-run → poll status → fetch the emitted
  // observables = the loaded per-store state) and swaps the viewer to show the
  // real, populated values. Runs the BASE composite (the same document the JSON
  // viewer shows) — the study's run knobs (replicas/steps/fracs/…) are
  // ensemble-driver params, not composite parameters, so they are NOT passed
  // (build_generator rejects unknown parameter names).
  // Core: run the BASE composite one step via the loom run API, poll to
  // completion, and resolve to the emitted observables (= the loaded per-store
  // state). onPhase(text) reports progress. Shared by the inline card button and
  // the fill-screen overlay button.
  function _fetchInitialState(id, onPhase) {
    onPhase = onPhase || function () {};
    return new Promise(function (resolve, reject) {
      var req = { id: id, steps: 1, label: 'model-tab-initial-state' };
      fetch(_api('/api/composite-test-run'), {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(req),
      }).then(function (r) { return r.json(); }).then(function (j) {
        if (!j || !j.run_id) throw new Error((j && j.error) || 'run did not start');
        var runId = j.run_id, tries = 0;
        (function poll() {
          fetch(_api('/api/composite-run/' + encodeURIComponent(runId) + '/status'))
            .then(function (r) { return r.json(); })
            .then(function (s) {
              var st = s && s.status;
              if (st === 'completed') {
                onPhase('loading state…');
                fetch(_api('/api/composite-run/' + encodeURIComponent(runId) + '/artifact/observables'))
                  .then(function (r) { if (!r.ok) throw new Error('no observables artifact'); return r.json(); })
                  .then(resolve)
                  .catch(function () { reject(new Error('run finished but no state artifact')); });
              } else if (st === 'failed' || st === 'orphaned') {
                var errLines = (s && s.error ? String(s.error).split('\n') : []).filter(function (l) { return l.trim(); });
                reject(new Error('run failed: ' + (errLines.length ? errLines[errLines.length - 1].slice(0, 200) : 'see Runs tab')));
              } else if (tries++ < 180) {
                onPhase((st || 'running') + '… step ' + (s && s.progress_step != null ? s.progress_step : '?'));
                setTimeout(poll, 1000);
              } else { reject(new Error('timed out waiting for the run')); }
            })
            .catch(function () { if (tries++ < 180) setTimeout(poll, 1000); else reject(new Error('timed out')); });
        })();
      }).catch(function (e) { reject(e instanceof Error ? e : new Error(String(e && e.message || e))); });
    });
  }

  // Swap a composite-json panel's tree/raw/state to the loaded state and mark it.
  function _applyInitialState(panel, state) {
    if (!panel) return;
    var viewer = panel.querySelector('.json-viewer');
    if (viewer) viewer.outerHTML = _jsonViewer(state);
    var raw = panel.querySelector('.pcard-json-raw'); if (raw) raw.textContent = JSON.stringify(state, null, 2);
    panel._json = state; panel._stateLoaded = true;
    var ibtn = panel.querySelector('.pcard-json-state-btn');
    if (ibtn) { ibtn.classList.add('active'); ibtn.textContent = '✓ initial state'; ibtn.disabled = false; }
  }

  function _loadCompositeInitialState(btn) {
    var panel = btn.closest('[data-role="composite-json"]'); if (!panel) return;
    var card = btn.closest('.registry-entry-full'); if (!card) return;
    var id = card.getAttribute('data-address');
    var note = panel.querySelector('.pcard-json-state-note');
    var orig = btn.textContent;
    var setNote = function (t) { if (note) note.textContent = t || ''; };
    var reset = function (label, ms) { btn.textContent = label || orig; btn.disabled = false; if (label && ms) setTimeout(function () { btn.textContent = orig; }, ms); };
    btn.disabled = true; btn.textContent = 'running 1 step…'; setNote('building the composite core…');
    _fetchInitialState(id, setNote).then(function (state) {
      _applyInitialState(panel, state);
      reset('✓ initial state'); btn.classList.add('active');
      setNote('● loaded state after 1 step');
    }).catch(function (e) {
      var msg = String(e && e.message || e);
      reset(/^run failed|^timed out/.test(msg) ? msg.split(':')[0] : 'could not run', 3500);
      setNote(msg);
    });
  }
  window._loadCompositeInitialState = _loadCompositeInitialState;
})();
