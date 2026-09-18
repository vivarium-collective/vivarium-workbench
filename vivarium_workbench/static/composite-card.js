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
// (_popoutCard), the composite JSON viewer (_toggleCompositeJson/
// _copyCompositeJson) and Share-link (_shareCompositeFromHeader), and the
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
    var url = apiUrl('/bigraph-loom/index.html') + '?id=' + encodeURIComponent(id);
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

  function _positionMaximizedCard(card) {
    // The card's fixed geometry is CSS-driven (see .pcard-maximized) off a single
    // CSS var so a card re-render can't strip inline positioning. Here we only
    // (a) publish the rail's right edge so the card clears the menu bar, and
    // (b) grow the embedded loom to fill from its top to the bottom of the pane.
    // In an embed IFRAME (Study→Model) there is no rail (it's in the parent,
    // covered by the full-window iframe), so the card fills from the left edge.
    var inIframe = !!(window.parent && window.parent !== window);
    var rail = document.querySelector('.viv-rail');
    var railRight = inIframe ? 0 : (rail ? rail.getBoundingClientRect().right : 240);
    document.documentElement.style.setProperty('--vw-rail-right', railRight + 'px');
    var frame = card.querySelector('.ccard-loom-frame');
    if (frame) {
      var fr = frame.getBoundingClientRect();
      frame.style.height = Math.max(360, window.innerHeight - fr.top - 16) + 'px';
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
      // Make sure the Explore section is open so the loom is actually visible.
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
      // Re-fit once the Explore section has finished expanding.
      setTimeout(function () { if (card.classList.contains('pcard-maximized')) _positionMaximizedCard(card); }, 120);
    } else {
      btn.title = 'Fill the pane — maximize (Esc to exit)';
      ['position', 'top', 'left', 'width', 'height', 'zIndex'].forEach(function (p) { card.style[p] = ''; });
      var frame = card.querySelector('.ccard-loom-frame');
      if (frame) { frame.style.height = ''; frame.style.maxHeight = ''; }
      if (card._maxReposition) window.removeEventListener('resize', card._maxReposition);
      if (card._maxEsc) document.removeEventListener('keydown', card._maxEsc);
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

  // "{ } JSON" — reveal the composite's full resolved JSON spec. The onclick
  // references _toggleCompositeJson (Modules-page-only) — inert elsewhere.
  function _compositeJsonBtn() {
    return '<button class="pcard-json-btn" type="button" title="View the full composite JSON spec" ' +
      'onclick="event.stopPropagation();_toggleCompositeJson(this)">{ } JSON</button>';
  }
  window._compositeJsonBtn = _compositeJsonBtn;

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
    if (!open) return;
    var card = head.closest('.registry-entry-full');
    // Process cards lazy-load resolved config/input fields; composites don't.
    // (_loadFullRunFields is walkthrough.js's process-card-only concern — a
    // composite card never reaches this branch, so its absence here is safe.)
    if (card && !card.classList.contains('pcard-composite') && typeof _loadFullRunFields === 'function') _loadFullRunFields(card);
    // Explore section: mount the composite's loom bigraph on first open (only
    // available on pages that also load walkthrough.js's live-loom glue).
    var embed = sec.querySelector('.ccard-loom-embed');
    if (embed && typeof _openCompositeLoomInline === 'function') _openCompositeLoomInline(embed);
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
    var desc = (opts.description || '').trim();
    return '<div class="cfg-row"' + (desc ? ' title="' + _esc(desc) + '"' : '') + '>' +
        '<div class="cfg-row-name">' +
          '<span class="cfg-key">' + _esc(key) + '</span>' +
          (typeLabel ? '<span class="cfg-type">' + _esc(typeLabel) + '</span>' : '') +
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
          '<span class="muted pcard-out-obs-hint">Loading declared observables…</span>' +
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
    var wsPill = c.workspace_local ? '<span class="composite-ws-tag">📦 workspace</span>' : '';
    var stats = _regStatsHtml(c);
    var selCls = (window._registrySelected && window._registrySelected === c.id) ? ' reg-selected' : '';
    var idA = _esc(c.id);
    // A little more info on the card: process + parameter counts, and tags.
    var np = (c.parameters && typeof c.parameters === 'object') ? Object.keys(c.parameters).length : 0;
    var nproc = (c.requires && c.requires.processes) ? c.requires.processes.length : 0;
    var metaBits = [];
    if (nproc) metaBits.push(nproc + ' process' + (nproc === 1 ? '' : 'es'));
    if (np) metaBits.push(np + ' param' + (np === 1 ? '' : 's'));
    var meta = metaBits.length
      ? '<div class="reg-card-meta" style="font-size:11px;color:#6b7280;margin:2px 0 4px">' + metaBits.join(' · ') + '</div>' : '';
    var tags = Array.isArray(c.tags) ? c.tags.slice(0, 3) : [];
    var tagHtml = tags.length
      ? '<div class="reg-card-tags" style="display:flex;flex-wrap:wrap;gap:4px;margin-top:5px">' +
          tags.map(function (t) { return '<span style="font-size:10px;color:#6d28d9;background:#f5f3ff;border:1px solid #e9d5ff;border-radius:4px;padding:1px 6px">' + _esc(t) + '</span>'; }).join('') +
        '</div>' : '';
    var actions = '<div class="reg-card-actions" style="display:flex;gap:6px;margin-top:9px;flex-wrap:wrap">' +
      '<button type="button" onclick="event.stopPropagation();_enterMaxcardMode(\'' + idA + '\',\'composite\')" ' +
        'title="Open maximized with the interactive bigraph (Explore) pinned at the top" ' +
        'style="height:26px;padding:0 11px;font-size:12px;font-weight:600;background:#2563eb;color:#fff;border:1px solid #2563eb;border-radius:5px;cursor:pointer">🔍 Explore</button>' +
      '<button type="button" onclick="event.stopPropagation();_cardPopout(this,\'' + idA + '\',\'composite\')" ' +
        'title="Pop out into its own window" ' +
        'style="height:26px;padding:0 9px;font-size:12px;background:#fff;color:#374151;border:1px solid #d1d5db;border-radius:5px;cursor:pointer">⧉ Pop out</button>' +
      '<button type="button" onclick="event.stopPropagation();_setRegistryZoom(\'full\')" ' +
        'title="Open the full card (Configure · Inputs · Run)" ' +
        'style="height:26px;padding:0 9px;font-size:12px;background:#fff;color:#374151;border:1px solid #d1d5db;border-radius:5px;cursor:pointer">Full card</button>' +
    '</div>';
    return '<div class="registry-card' + selCls + '" data-address="' + idA + '" data-kind="composite"' +
        ' onclick="_selectRegistryEntry(\'' + idA + '\')" ondblclick="_enterMaxcardMode(\'' + idA + '\',\'composite\')"' +
        ' title="Double-click to Explore (maximized bigraph)">' +
      '<div class="reg-card-row">' +
        '<div class="reg-card-main">' +
          '<div class="reg-card-head"><strong class="reg-card-name">' + _esc(c.name) + '</strong>' + _compositeBadge() + _compositeTierBadge(c) + wsPill + '</div>' +
          '<code class="reg-card-addr">' + _esc(addr) + '</code>' +
          meta +
          (short ? '<p class="reg-card-desc">' + _esc(short) + '</p>' : '') +
          tagHtml +
          _runCmdChip(c.run_command) +
          actions +
        '</div>' +
        '<div class="reg-card-stats">' + stats + '</div>' +
      '</div>' +
    '</div>';
  }
  window._renderCompositeCardGrid = _renderCompositeCardGrid;

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
    var pKeys = Object.keys(params), nCfg = pKeys.length;
    var desc = (c.description || '').trim();
    var sel = (window._registrySelected && window._registrySelected === c.id) ? ' reg-selected' : '';
    var wsPill = c.workspace_local ? '<span class="composite-ws-tag">📦 workspace</span>' : '';
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
          return _runField(k, ('default' in pv) ? pv.default : null, { type: pv.type, description: pv.description });
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
        '" data-address="' + _esc(c.id) + '" data-kind="composite">' +
      '<div class="loom-card loom-card-stack loom-card-composite">' +
        '<div class="pcard-top">' +
          '<div class="pcard-header pcard-title" onclick="_pinCardTop(this)" ondblclick="event.stopPropagation();_maximizeCardFromHeader(this)" title="Click to pin to top · double-click to maximize">' +
            '<span class="loom-name">' + _esc(c.name) + '</span>' + _compositeBadge() + _compositeTierBadge(c) + wsPill + roPill +
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
            _cardMaximizeBtn() +
            _cardPopoutBtn(c.id, 'composite') +
          '</div>' +
          // Thin restore strip — shown only while the header is collapsed
          // (pcard-hdr-hidden); click to bring the bar back.
          '<button class="pcard-hdr-restore" type="button" onclick="_toggleCardHeader(this)" title="Show the composite bar">▸ ' + _esc(c.name) + '</button>' +
          '<div class="pcard-summary">' +
            '<div class="pcard-desc-col">' +
              '<div class="pcard-contract-meta" data-role="contract-meta">composite · <strong>' + nCfg + '</strong> param' + (nCfg === 1 ? '' : 's') + '</div>' +
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
        '</div>' +
      '</div>' +
    '</div>';
  }
  window._renderCompositeCardFull = _renderCompositeCardFull;
})();
