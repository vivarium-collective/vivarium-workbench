// study-detail.js — wires the six-card Study Detail page to /api/study-* routes.
(function() {
  // ── G3: shared outcome vocabulary (Fable §10.1, §14.1(4)) ────────────────
  // JS mirror of vivarium_workbench/lib/study_page.py's outcome_label/_class/
  // _glyph — SAME token map, so client-rendered outcomes (e.g. verdict pills
  // filled from /api/study-* JSON) read identically to server-rendered ones.
  // Display-only remap; never touches a stored token. Confirmed token
  // families: test/verdict PASS/FAIL/PARTIAL/SKIP/PENDING/GAP, report-card
  // within_tol/drift/mismatch/ungraded, and acceptance-criterion
  // passing/failing/passing-with-caveats/in-progress (case-insensitive).
  // Unknown/missing tokens degrade to "not assessable" — never blank, never
  // throws.
  var _OUTCOME_TOKEN_MAP = {
    PASS: 'met', FAIL: 'not met', PARTIAL: 'conditional-pass',
    SKIP: 'not assessable', PENDING: 'not assessable', GAP: 'not assessable',
    WITHIN_TOL: 'met', DRIFT: 'conditional-pass', MISMATCH: 'not met',
    UNGRADED: 'not assessable',
    PASSING: 'met', FAILING: 'not met', 'PASSING-WITH-CAVEATS': 'conditional-pass',
    'IN-PROGRESS': 'not assessable'
  };
  var _OUTCOME_CLASS = {
    'met': 'met', 'conditional-pass': 'conditional',
    'not met': 'not-met', 'not assessable': 'not-assessable'
  };
  var _OUTCOME_GLYPH = {
    'met': '✓', 'conditional-pass': '◐',
    'not met': '✗', 'not assessable': '○'
  };
  function outcomeLabel(token) {
    var key = (token === null || token === undefined) ? '' : String(token).trim().toUpperCase();
    var v = _OUTCOME_TOKEN_MAP[key];
    return v === undefined ? 'not assessable' : v;
  }
  function outcomeClass(token) { return _OUTCOME_CLASS[outcomeLabel(token)]; }
  function outcomeGlyph(token) { return _OUTCOME_GLYPH[outcomeLabel(token)]; }
  window.outcomeLabel = outcomeLabel;
  window.outcomeClass = outcomeClass;
  window.outcomeGlyph = outcomeGlyph;

  // ── G7: honest attribution from existing fields (Fable §11.2, §14.1(5)) ──
  // JS mirror of vivarium_workbench/lib/study_page.py's actor_kind/_glyph/
  // attribution_text — SAME never-guess-a-name rule, so client-rendered
  // attribution (the feedback_tracked panel below) reads identically to any
  // server-rendered attribution. Only a known LLM/automation naming TOKEN
  // (claude, gpt-4o, ci, ...) flips a recorded name to "agent" — the same
  // category of signal viva_superpowers.investigation_close.derive_contributors
  // already uses for git co-authors (there: an email's "noreply@anthropic.com"
  // / "bot" / "ci" substring). Every other non-empty name defaults to "human"
  // — a documented DEFAULT, not a claim about who that person is. Empty/None
  // -> "unattributed" (never blank).
  var _KNOWN_AGENT_NAME_TOKENS = {
    claude: 1, gpt: 1, chatgpt: 1, codex: 1, copilot: 1, gemini: 1, llama: 1,
    mistral: 1, deepseek: 1, qwen: 1, grok: 1, bot: 1, ci: 1
  };
  function actorKind(actor) {
    var s = (actor === null || actor === undefined) ? '' : String(actor).trim();
    if (!s) return 'unattributed';
    var low = s.toLowerCase();
    var firstToken = low.split(/[\s\-_/]+/)[0];
    if (_KNOWN_AGENT_NAME_TOKENS[low] || _KNOWN_AGENT_NAME_TOKENS[firstToken]) return 'agent';
    return 'human';
  }
  var _ACTOR_KIND_GLYPH = {human: '◇', agent: '⚙', unattributed: '○'};
  function actorGlyph(actor) { return _ACTOR_KIND_GLYPH[actorKind(actor)]; }
  function attributionText(actor, when) {
    if (actorKind(actor) === 'unattributed') return 'unattributed';
    var label = String(actor).trim();
    var whenS = when ? String(when).trim() : '';
    return whenS ? ('by ' + label + ' · ' + whenS) : ('by ' + label);
  }
  window.actorKind = actorKind;
  window.actorGlyph = actorGlyph;
  window.attributionText = attributionText;

  function api(method, path, body) {
    return fetch(path, {
      method: method,
      headers: body ? {'Content-Type': 'application/json'} : {},
      body: body ? JSON.stringify(body) : null,
    }).then(function(r) {
      return r.json().then(function(d) { return {status: r.status, body: d}; });
    });
  }

  // --- Tab navigation ---

  // The `.study-pillar` buttons ARE the tabs (one level, no pillar/member
  // indirection) — each drives _setStudyTab(kind) directly via its data-kind.
  function _setStudyTab(kind) {
    document.querySelectorAll('.study-pillar').forEach(function (b) {
      b.classList.toggle('active', b.dataset.kind === kind);
    });
    document.querySelectorAll('.study-tab-panel').forEach(function (p) {
      p.classList.toggle('active', p.dataset.kind === kind);
    });
    if (kind === 'tests') { _loadTestsPanel(window._study); }
    if (kind === 'readouts') { _loadReadouts(); _loadReadoutsDownloadPointer(); }
    if (kind === 'visualize') { _loadCharts('viz-charts-panel'); _loadNativeGallery(); }
    if (kind === 'compose') { _loadModelConfig(); }
    // Task E2: the study-artifacts strip (analysis-files zip + raw-data
    // bulk) moved from the Exports (data) tab onto Simulations, so its
    // loaders now trigger here instead.
    if (kind === 'simulate') { _loadStudySims(); _loadAnalysisOutputs(); _loadRawData(); }
    // Textareas measured 0 while their tab was hidden; re-fit the now-visible
    // panel's auto-grow boxes so they show all content without a scrollbar.
    if (window._autoGrowTextareas) window._autoGrowTextareas();
  }
  window._setStudyTab = _setStudyTab;

  // Cross-tab link helper (Fable §6 #15, Task C1): a link on any tab can
  // point at an anchor that lives inside a DIFFERENT, currently-hidden tab
  // panel (e.g. an Overview finding's "via test <a>" citing a Tests-tab
  // #bt-<id> row). A plain href="#anchor" silently fails there because the
  // target is inside a display:none panel. Reuses _setStudyTab for the
  // actual show/hide (no duplicated switch logic) and then scrolls once the
  // panel is visible. C2 (findings ledger) wires the primary callers.
  function _gotoStudyTab(kind, anchor) {
    _setStudyTab(kind);
    if (!anchor) return;
    var el = document.getElementById(anchor);
    if (!el || !el.scrollIntoView) return;
    try { el.scrollIntoView({behavior: 'smooth', block: 'start'}); } catch (e) {}
  }
  window._gotoStudyTab = _gotoStudyTab;

  // ── Readouts table (emit plan + authored annotations) ───────────────────────
  // Fetch /api/study-readouts and render the table async (the composite build is
  // ~3s, TTL-cached). Tolerates failure (leaves the loading message).
  var _readoutsLoaded = false;
  function _loadReadouts() {
    if (_readoutsLoaded) return;
    _readoutsLoaded = true;
    var host = document.getElementById('readouts-table');
    if (!host) return;
    var slug = host.getAttribute('data-study') || studyName();
    if (!slug) return;
    fetch('/api/study-readouts?study=' + encodeURIComponent(slug),
          {headers: {Accept: 'application/json'}})
      .then(function(r) { return r.ok || r.status === 422 ? r.json() : null; })
      .then(function(j) {
        if (!j || !Array.isArray(j.rows)) {
          host.innerHTML = '<p class="empty-message">Readouts unavailable.</p>';
          return;
        }
        host.innerHTML = _renderReadoutsTable(j);
      })
      .catch(function() {
        host.innerHTML = '<p class="empty-message">Readouts unavailable.</p>';
      });
  }

  // ── Readouts tab: pointer to the raw-data downloads that live under Simulations ──
  // Simulations (data-kind="simulate") is the single "get the data" tab — it
  // folds in every run's raw emitter store (see _loadRawData below) plus the
  // analysis result files (Task E2 moved both off the old Exports tab).
  // Readouts used to render its OWN full download widget here (every run's
  // raw store, one ⬇ each), duplicating those same links. Task C4 replaced
  // that widget with one pointer that jumps to the raw-data group via C1's
  // _gotoStudyTab (E2 repointed it from the 'data' tab to 'simulate'). Uses
  // the SAME /api/simulations fetch + (store_path || db_path) filter
  // _loadRawData uses, so the pointer only shows up when there's actually
  // something to show — never pointing at an empty tab.
  var _readoutsDownloadPointerLoaded = false;
  function _loadReadoutsDownloadPointer(force) {
    var host = document.getElementById('readouts-download');
    if (!host) return;
    if (_readoutsDownloadPointerLoaded && !force) return;
    _readoutsDownloadPointerLoaded = true;
    var slug = studyName();
    if (!slug) { host.innerHTML = ''; return; }
    fetch('/api/simulations?study=' + encodeURIComponent(slug), { headers: { Accept: 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        var sims = (j && j.simulations) || [];
        var withData = sims.filter(function (s) { return s.run_id && (s.store_path || s.db_path); });
        host.innerHTML = withData.length
          ? '<p class="muted">⬇ Download this study\'s raw run data → '
            + '<a href="#" onclick="_gotoStudyTab(\'simulate\',\'exports-downloads\');return false;">Simulations</a></p>'
          : '';
      })
      .catch(function () { host.innerHTML = ''; });
  }
  window._loadReadoutsDownloadPointer = _loadReadoutsDownloadPointer;

  // --- Data tab: downloadable Analysis result files (CSV/TSV) ---
  var _analysisOutputsLoaded = false;
  function _fmtBytes(n) {
    if (!n && n !== 0) return '';
    if (n < 1024) return n + ' B';
    var u = ['KB', 'MB', 'GB'], i = -1, v = n;
    do { v /= 1024; i++; } while (v >= 1024 && i < u.length - 1);
    return (v >= 10 ? Math.round(v) : v.toFixed(1)) + ' ' + u[i];
  }
  function _renderAnalysisOutputs(j) {
    var e = escapeHtmlForTests;
    var files = (j && j.files) || [];
    if (!files.length) {
      return '<p class="empty-message">No result files yet. Analysis steps write '
        + '<code>.csv</code>/<code>.tsv</code> files here once this study has run.</p>';
    }
    // Group by parent dir so ptools/ and per-run analysis tables read cleanly.
    var groups = {}, order = [];
    files.forEach(function (f) {
      var g = f.dir || '(study root)';
      if (!groups[g]) { groups[g] = []; order.push(g); }
      groups[g].push(f);
    });
    var html = '';
    order.forEach(function (g) {
      html += '<div class="data-group" style="margin-bottom:14px">'
        + '<div class="muted" style="font-family:ui-monospace,monospace;font-size:0.82em;'
        + 'margin:0 0 4px 0">' + e(g) + '/</div>'
        + '<table class="data-files-table" style="width:100%;border-collapse:collapse;font-size:0.9em">';
      groups[g].forEach(function (f) {
        html += '<tr style="border-top:1px solid #eef2f6">'
          + '<td style="padding:5px 8px"><a href="' + e(f.download_url) + '">'
          + e(f.name) + '</a></td>'
          + '<td style="padding:5px 8px;text-align:right;color:#64748b;white-space:nowrap">'
          + e(_fmtBytes(f.size)) + '</td></tr>';
      });
      html += '</table></div>';
    });
    return html;
  }
  function _loadAnalysisOutputs() {
    if (_analysisOutputsLoaded) return;
    _analysisOutputsLoaded = true;
    var host = document.getElementById('data-files');
    if (!host) return;
    var slug = host.getAttribute('data-study') || studyName();
    if (!slug) return;
    fetch('/api/study-analysis-outputs?study=' + encodeURIComponent(slug),
          {headers: {Accept: 'application/json'}})
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (!j || !Array.isArray(j.files)) {
          host.innerHTML = '<p class="empty-message">Result files unavailable.</p>';
          return;
        }
        host.innerHTML = _renderAnalysisOutputs(j);
        var dl = document.getElementById('data-download-all');
        if (dl) dl.style.display = j.files.length ? '' : 'none';
      })
      .catch(function () {
        host.innerHTML = '<p class="empty-message">Result files unavailable.</p>';
      });
  }

  function _emitStatusBadge(status) {
    var e = escapeHtmlForTests;
    var styles = {
      emitted:          {bg: '#d1fae5', fg: '#065f46', bd: '#6ee7b7', glyph: '✓', label: 'emitted'},
      not_in_emit_plan: {bg: '#fee2e2', fg: '#991b1b', bd: '#fca5a5', glyph: '✗', label: 'not in emit plan'},
      derived:          {bg: '#f1f5f9', fg: '#475569', bd: '#cbd5e1', glyph: '⏳', label: 'derived'},
    };
    var s = styles[status] || styles.derived;
    return '<span style="display:inline-block;padding:2px 8px;border-radius:9999px;background:'
      + s.bg + ';color:' + s.fg + ';border:1px solid ' + s.bd + '">' + s.glyph + ' ' + e(s.label) + '</span>';
  }

  function _renderReadoutsTable(j) {
    var e = escapeHtmlForTests;
    var note = j.note ? '<p class="muted" style="color:#92400e">' + e(j.note) + '</p>' : '';
    var rows = j.rows || [];
    var idxHtml = function (o) {
      return o.index_by ? '<code style="font-size:0.85em;">' + e(o.index_by.type) + '=' + e(o.index_by.value) + '</code>'
                         : '<span class="muted">—</span>';
    };
    // Column defs — `html` is the same accessor used to render the cell, so
    // dropEmptyColumns() (Fable A #2 / spec R3) can judge emptiness from the
    // exact rendered content. Name/Store path/Emitted? have no accessor and
    // always stay; Indexed by/Units/Description are the columns that go
    // empty for studies that don't populate them.
    var cols = [
      { id: 'name', label: 'Name' },
      { id: 'store_path', label: 'Store path' },
      { id: 'emitted', label: 'Emitted?' },
      { id: 'indexed_by', label: 'Indexed by', html: idxHtml },
      { id: 'units', label: 'Units', html: function (o) { return e(o.units || ''); } },
      { id: 'description', label: 'Description', html: function (o) { return e(o.description || ''); } },
    ];
    var dropEmptyColumns = (window.SimTable && window.SimTable.dropEmptyColumns) || function (r, c) { return c; };
    cols = dropEmptyColumns(rows, cols);
    var keep = {};
    cols.forEach(function (c) { keep[c.id] = true; });
    var head = '<table class="observables-table" style="width:100%; border-collapse: collapse;"><thead><tr>'
      + cols.map(function(c) {
          return '<th style="text-align:left; padding:6px; border-bottom:1px solid #e2e8f0;">' + c.label + '</th>';
        }).join('') + '</tr></thead><tbody>';
    var body = rows.map(function(o) {
      var tds = '';
      if (keep.name) tds += '<td style="padding:6px; vertical-align:top;"><code>' + e(o.name) + '</code></td>';
      if (keep.store_path) tds += '<td style="padding:6px; vertical-align:top;"><code style="font-size:0.85em;">' + e(o.store_path || '') + '</code></td>';
      if (keep.emitted) tds += '<td style="padding:6px; vertical-align:top; font-size:0.75em;">' + _emitStatusBadge(o.emit_status) + '</td>';
      if (keep.indexed_by) tds += '<td style="padding:6px; vertical-align:top;">' + idxHtml(o) + '</td>';
      if (keep.units) tds += '<td style="padding:6px; vertical-align:top; font-size:0.9em;">' + e(o.units || '') + '</td>';
      if (keep.description) tds += '<td style="padding:6px; vertical-align:top; max-width:380px; font-size:0.9em;">' + e(o.description || '') + '</td>';
      return '<tr style="border-bottom:1px solid #f1f5f9;" data-readout="' + e(o.name) + '">' + tds + '</tr>';
    }).join('');
    return note + head + body + '</tbody></table>';
  }

  // ── Charts panel: inline SVGs from /api/study-charts ─────────────────────
  // Lives in the Visualizations tab only. Memoized per panel id.
  // Merges two sources returned by the server:
  //   live   — generated from runs.db at request time
  //   static — pre-rendered SVGs under studies/<name>/charts/
  var _chartsLoadedFor = {};
  // Task E3 (per-run hub): small caches so _showRunDetail can FILTER the
  // study's already-fetched figure sources to one run, instead of firing a
  // new per-row request. Populated by _loadNativeGallery/_loadCharts once
  // their (memoized) fetches settle; `undefined` means "not fetched yet".
  //   _nativeGalleryRunId — build_study_native_gallery attaches ONE run_id to
  //     the whole gallery (the study's latest completed run); null when none.
  //   _chartsCache — the study-charts payload's `charts` array, each item's
  //     `run_id` populated only when genuinely derivable (V3).
  var _nativeGalleryRunId;
  var _chartsCache;
  // The run row currently shown in #study-run-detail, or null when closed —
  // lets a late-arriving async figure fetch refresh an already-open panel.
  var _currentRunDetailRow = null;
  // Fable §4.5 (Task V2/V3): one `.figure-card` shell shared with the native
  // gallery / embed sources — a bordered figure container + a muted
  // caption-row footer (source chip + title + optional run link), not the
  // old boxed `.chart-card` with its own title bar. `c.run_id` is populated
  // by build_study_charts_payload only when genuinely derivable (a static
  // chart's stamped meta sidecar) — this render is conditional on it so a
  // chart with no recorded provenance omits the link rather than fabricate
  // one (Task V3).
  // Auto-height resizer (Task V6): byte-identical logic to the
  // embed_visualizations iframe's onload handler in
  // templates/study-detail.html — grows an iframe to its content's
  // scrollHeight (or a CSS-pinned overflow:hidden height) so a three.js
  // canvas / self-contained HTML figure isn't clipped inside a fixed box,
  // without giving it a scrollbar. Reused rather than re-derived so the two
  // iframe call sites can't drift.
  var _FIGURE_IFRAME_ONLOAD =
    "(function(f){try{var d=f.contentDocument;if(!d)return;var b=d.body,e=d.documentElement;" +
    "var bStyle=b&&d.defaultView&&d.defaultView.getComputedStyle?d.defaultView.getComputedStyle(b):null;" +
    "var pinnedH=0;if(bStyle&&(bStyle.overflow||'').indexOf('hidden')>=0){" +
    "var hm=(bStyle.height||'').match(/^(\\d+(?:\\.\\d+)?)px$/);if(hm)pinnedH=Math.round(parseFloat(hm[1]));}" +
    "var h=pinnedH>0?pinnedH:Math.max(e?e.scrollHeight:0,b?b.scrollHeight:0);" +
    "if(h>0)f.style.height=(h+24)+'px';}catch(e){}})(this)";

  function _renderChartCard(c) {
    // SVG records carry inline markup in c.svg; PNG/GIF records carry a
    // self-contained data-URI in c.img (rendered as <img>). A declared
    // threejs:/html: figure (Task V6, study_charts.discover_declared_figure_
    // charts) carries neither — just an `iframe_url` pointing at a self-
    // contained HTML file — and renders as an iframe, reusing the
    // embed_visualizations iframe pattern: same trust model (a same-origin
    // `src` iframe, no `sandbox` attribute beyond what embeds already use)
    // and the same auto-height onload resizer.
    var title = c.title || c.key || 'figure';
    var media = c.iframe_url
      ? '<iframe src="' + escapeHtmlForTests(c.iframe_url) + '" '
        + 'class="figure-media-frame figure-media-frame--embed" '
        + 'loading="lazy" title="' + escapeHtmlForTests(title) + '" '
        + 'onload="' + _FIGURE_IFRAME_ONLOAD + '"'
        + '></iframe>'
      : (c.img
        ? '<img class="chart-img figure-media" src="' + c.img + '" alt="' + (c.key || 'chart') + '" loading="lazy">'
        : (c.svg || ''));
    var desc = c.caption ? '<div class="chart-caption">' + c.caption + '</div>' : '';
    var runLink = c.run_id
      ? '<a href="#" class="figure-run-link" data-run-id="' + escapeHtmlForTests(String(c.run_id)) + '">from run '
        + escapeHtmlForTests(String(c.run_id)) + ' ↗</a>'
      : '';
    return '<div class="figure-card">' + media + desc
      + '<div class="figure-caption-row">'
      + '<span class="figure-source-chip">chart</span>'
      + (c.title ? '<span class="figure-title">' + (c.iframe_url ? escapeHtmlForTests(c.title) : c.title) + '</span>' : '')
      + runLink
      + '</div></div>';
  }
  // Figures tab (Fable A #3): the empty state is computed over the UNION of
  // the three figure sources — native gallery, embed_visualizations iframes
  // (server-rendered, present in the DOM from page load), and latest-run
  // charts — instead of each source painting its own "no figures" text.
  // _loadNativeGallery used to write "No figures yet." into its own panel
  // whenever ITS fetch came back empty, even when embeds/charts below it had
  // real content. Each async loader now reports whether it produced content;
  // the shared #figures-empty-message only appears once both async sources
  // have reported AND neither they nor the (synchronous) embeds have any.
  var _figuresSourceState = { native: null, charts: null };
  function _figuresHasEmbeds() {
    return !!document.querySelector('#visualize-section .embed-viz-card');
  }
  function _updateFiguresEmptyState() {
    var msg = document.getElementById('figures-empty-message');
    if (!msg) return;
    var allReported = _figuresSourceState.native !== null && _figuresSourceState.charts !== null;
    var allEmpty = allReported && !_figuresSourceState.native && !_figuresSourceState.charts && !_figuresHasEmbeds();
    msg.style.display = allEmpty ? '' : 'none';
  }

  // Figure caption run-links (Fable §4.5, Task V2): a `.figure-card`'s
  // caption row carries a `from run <id> ↗` link, built by each source's
  // card markup as `<a class="figure-run-link" data-run-id="...">` when a
  // run_id is available. Wiring the click via a delegated listener AFTER
  // innerHTML is set (rather than an inline onclick with the id baked into
  // the attribute string) avoids round-tripping the id through HTML
  // attribute parsing before it reaches JS.
  function _wireFigureRunLinks(container) {
    if (!container) return;
    container.querySelectorAll('.figure-run-link[data-run-id]').forEach(function (a) {
      a.addEventListener('click', function (e) {
        e.preventDefault();
        _gotoStudyTab('simulate', 'run-' + a.getAttribute('data-run-id'));
      });
    });
  }

  // Task E3: figures for ONE run, reusing the three existing figure sources
  // (never forking a new card renderer) filtered to `run_id`:
  //   - embeds (study.embed_visualizations) — server-rendered synchronously
  //     into #visualize-section at page load, so no "not loaded yet" state;
  //     filtered by the run-link each card already carries (V3).
  //   - native gallery — async, ONE run_id shared by every panel, so a match
  //     means the WHOLE rendered gallery panel belongs to this run; reuses
  //     the already-rendered #native-gallery-panel markup verbatim.
  //   - charts — async, per-item run_id (V3); reuses _renderChartCard(c).
  function _figureCardsForRun(runId) {
    var cards = [];
    if (!runId) return cards;
    document.querySelectorAll('#visualize-section .embed-viz-card').forEach(function (card) {
      var link = card.querySelector('.figure-run-link[data-run-id]');
      if (link && link.getAttribute('data-run-id') === String(runId)) cards.push(card.outerHTML);
    });
    if (_nativeGalleryRunId !== undefined && _nativeGalleryRunId !== null
        && String(_nativeGalleryRunId) === String(runId)) {
      var ngPanel = document.getElementById('native-gallery-panel');
      if (ngPanel && ngPanel.innerHTML) cards.push(ngPanel.innerHTML);
    }
    if (_chartsCache) {
      _chartsCache.forEach(function (c) {
        if (c && c.run_id != null && String(c.run_id) === String(runId)) cards.push(_renderChartCard(c));
      });
    }
    return cards;
  }

  // Renders the Figures sub-section for _showRunDetail. Cheap + lazy: the
  // native-gallery/charts sources are only fetched once (memoized), reused
  // across every row-open; if neither has settled yet, this kicks off the
  // SAME loaders the Visualizations tab uses (a redundant call is a no-op)
  // and shows a quiet pointer instead of blocking. Absent (not-yet-loaded)
  // is distinguished from empty (loaded, genuinely no match) so "no figures
  // for this run" is only shown once we actually know that.
  function _runDetailFiguresHtml(row) {
    var runId = row.run_id || '';
    var cards = _figureCardsForRun(runId);
    if (cards.length) {
      return {
        count: cards.length,
        html: '<div style="display:flex;flex-wrap:wrap;gap:10px">' + cards.join('') + '</div>',
      };
    }
    var settled = (_nativeGalleryRunId !== undefined) && (_chartsCache !== undefined);
    if (!settled) {
      _loadNativeGallery();
      _loadCharts('viz-charts-panel');
      return {
        count: 0,
        html: '<p class="muted" style="margin:0;font-size:0.85em">figures load on the Visualizations tab</p>',
      };
    }
    return {
      count: 0,
      html: '<p class="muted" style="margin:0;font-size:0.85em">no figures for this run</p>',
    };
  }

  // Once a late (async) native-gallery/charts fetch settles, refresh an
  // already-open run-detail panel in place — guarded on the mount still
  // being in the DOM (closing the panel clears #run-detail-figures, so a
  // stale notification after close is a harmless no-op, never a throw).
  function _notifyFigureDataAvailable() {
    if (!_currentRunDetailRow) return;
    var mount = document.getElementById('run-detail-figures');
    if (!mount) return;
    var r = _runDetailFiguresHtml(_currentRunDetailRow);
    mount.innerHTML = r.html;
    _wireFigureRunLinks(mount);
  }

  // Task E3: report cards are STUDY-level — report_card_urls is keyed by
  // card name (see _renderRichReportCard below), with no run_id anywhere in
  // its shape. So this never fabricates a per-run association; it's a
  // compact pointer to the Tests tab, where every card already renders
  // inline (C6, _bindReportCardRowExpanders).
  function _runDetailReportCardsHtml() {
    var urls = (window._study && window._study.report_card_urls) || {};
    var n = Object.keys(urls).length;
    if (!n) {
      return '<p class="muted" style="margin:0;font-size:0.85em">no report cards for this study</p>';
    }
    return '<p class="muted" style="margin:0;font-size:0.85em">' + n + ' report card'
      + (n === 1 ? '' : 's') + ' for this study — '
      + '<a href="#" onclick="_gotoStudyTab(\'tests\');return false;">Report cards → Tests</a></p>';
  }

  // Task E3: compact results/analysis line — surfaces values already known
  // (figure count just rendered above, whether a raw store exists, step
  // count already shown in the metadata block) rather than computing
  // anything new.
  function _runDetailResultsSummaryHtml(row, figureCount, hasData) {
    var e = window.SimTable.esc;
    var bits = [
      figureCount ? (figureCount + ' figure' + (figureCount === 1 ? '' : 's') + ' above') : 'no figures for this run',
      hasData ? 'raw store available (⬇ Data)' : 'no persisted store',
    ];
    if (row.n_steps != null) bits.push(row.n_steps + ' steps');
    return '<p class="muted" style="margin:0;font-size:0.85em">'
      + bits.map(function (b) { return e(String(b)); }).join(' · ') + '</p>';
  }

  // Baseline native-analysis gallery — the study's latest completed run's
  // viz.json panels (mass fractions, cell mass, replication, …). Each panel is
  // a self-contained Altair/Plotly doc, so it renders in its own srcdoc iframe
  // (innerHTML would not execute the embedded vega/plotly <script> tags).
  var _nativeGalleryLoaded = false;
  function _loadNativeGallery() {
    var host = document.getElementById('native-gallery-panel');
    if (!host || _nativeGalleryLoaded) return;
    _nativeGalleryLoaded = true;
    var slug = studyName();
    fetch('/api/study-native-gallery/' + encodeURIComponent(slug))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var panels = (d && d.panels) || {};
        var names = Object.keys(panels);
        if (!names.length) {
          // No message here — the shared empty state (below) speaks for the
          // whole Figures section once embeds/charts have also reported.
          host.innerHTML = '';
          _figuresSourceState.native = false;
          _updateFiguresEmptyState();
          _nativeGalleryLoaded = false;  // allow a retry after a run completes
          _nativeGalleryRunId = null;    // Task E3: settled — no run to match
          _notifyFigureDataAvailable();
          return;
        }
        function attr(s) { return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;'); }
        // build_study_native_gallery returns ONE run_id for the whole
        // gallery (the study's latest completed run) — every panel below
        // shares the same caption. Rendered conditionally: a study with no
        // completed run (run_id is None) omits the link instead of
        // fabricating provenance.
        var runId = d && d.run_id;
        var runCaption = runId
          ? '<a href="#" class="figure-run-link" data-run-id="' + attr(runId) + '">from run '
            + escapeHtmlForTests(String(runId)) + ' ↗</a>'
          : '';
        host.innerHTML = names.map(function (n) {
          return '<div class="figure-card">'
            + '<iframe srcdoc="' + attr(panels[n]) + '" loading="lazy" '
            + 'class="figure-media-frame figure-media-frame--native"></iframe>'
            + '<div class="figure-caption-row">'
            + '<span class="figure-source-chip">native</span>'
            + '<span class="figure-title">' + escapeHtmlForTests(n) + '</span>'
            + runCaption
            + '</div>'
            + '</div>';
        }).join('');
        _wireFigureRunLinks(host);
        _figuresSourceState.native = true;
        _updateFiguresEmptyState();
        _nativeGalleryRunId = runId || null;  // Task E3: settled
        _notifyFigureDataAvailable();
      })
      .catch(function () {
        host.innerHTML = '<p class="muted" style="padding:8px">Failed to load baseline figures.</p>';
        _figuresSourceState.native = false;
        _updateFiguresEmptyState();
        _nativeGalleryLoaded = false;
      });
  }

  // Exports tab: per-run raw emitter store downloads, folded in from the
  // Simulations DB so Exports is the single "get the data" tab.
  var _rawDataLoaded = false;
  function _loadRawData(force) {
    var mount = document.getElementById('raw-data-list');
    if (!mount) return;
    if (_rawDataLoaded && !force) return;
    _rawDataLoaded = true;
    var bulkBtn = document.getElementById('raw-data-download-all');
    var slug = studyName(), esc = window.SimTable ? window.SimTable.esc : function (x) { return String(x == null ? '' : x); };
    var path = '/api/simulations?study=' + encodeURIComponent(slug);
    var url = (window.DataSource && window.DataSource.apiUrl) ? window.DataSource.apiUrl(path) : path;
    fetch(url).then(function (r) { return r.text(); }).then(function (t) {
      var d = {}; try { d = t ? JSON.parse(t) : {}; } catch (e) {}
      var rows = d.simulations || [];
      if (!rows.length) {
        mount.innerHTML = '<p class="empty-message">No runs with persisted data yet.</p>';
        if (bulkBtn) bulkBtn.style.display = 'none';
        return;
      }
      var withDataCount = rows.filter(function (row) { return !!(row.store_path || row.db_path); }).length;
      if (bulkBtn) {
        bulkBtn.style.display = withDataCount ? '' : 'none';
        bulkBtn.textContent = '⬇ Download all raw data (' + withDataCount + ')';
      }
      mount.innerHTML = '<table style="width:100%;border-collapse:collapse;font-size:0.88em">' +
        rows.map(function (row) {
          var runId = row.run_id || '', hasData = !!(row.store_path || row.db_path);
          var label = row.sim_name || row.label || runId;
          var loc = window.SimTable ? window.SimTable.location(row) : esc(row.store_path || row.db_path || '');
          var dl = hasData
            ? '<a class="action-btn" download href="' + (window.__BASE_PATH__ || "") + '/api/simulation-run-download?run_id=' + encodeURIComponent(runId) + '">⬇ Data</a>'
            : '<span class="muted" style="font-size:0.82em">no store</span>';
          return '<tr style="border-bottom:1px solid #f3f4f6"><td style="padding:5px 8px"><code style="font-size:0.85em">' + esc(label) + '</code></td>' +
            '<td style="padding:5px 8px">' + loc + '</td>' +
            '<td style="padding:5px 8px;text-align:right">' + dl + '</td></tr>';
        }).join('') + '</table>';
    }).catch(function () {
      mount.innerHTML = '<p class="empty-message">Could not load runs.</p>';
      if (bulkBtn) bulkBtn.style.display = 'none';
    });
  }
  window._loadRawData = _loadRawData;

  // One-click "download all raw data": trigger every run's raw-emitter-store
  // download in sequence (browsers serialise multiple download navigations
  // from one user gesture). Restores the bulk convenience the old Readouts
  // widget's _downloadAllRawData offered — scoped here to Exports' raw-run
  // group (#raw-data-list a[download], the per-run links _loadRawData just
  // rendered); the analysis-file zip (#data-download-all) is untouched, it
  // already has its own single-click server-side zip download.
  function _downloadAllRawExports() {
    var mount = document.getElementById('raw-data-list');
    if (!mount) return;
    var links = Array.prototype.slice.call(mount.querySelectorAll('a[download]'));
    links.forEach(function (a, i) {
      setTimeout(function () {
        var t = document.createElement('a');
        t.href = a.getAttribute('href'); t.setAttribute('download', '');
        document.body.appendChild(t); t.click(); document.body.removeChild(t);
      }, i * 700);
    });
  }
  window._downloadAllRawExports = _downloadAllRawExports;

  // Model tab: for each baseline composite, fetch /api/composite-resolve and
  // render the RESOLVED config that actually runs (composite defaults overlaid
  // with this study's authored overrides). Loaded on demand when the tab opens.
  function _loadModelConfig(force) {
    var panel = document.getElementById('panel-compose');
    if (!panel) return;
    var esc = window.SimTable ? window.SimTable.esc : function (s) { return String(s == null ? '' : s); };
    panel.querySelectorAll('.cond-block[data-model-composite]').forEach(function (block) {
      var mount = block.querySelector('.model-config-mount');
      if (!mount || (mount._loaded && !force)) return;
      mount._loaded = true;
      var composite = block.getAttribute('data-model-composite');
      var overridesJson = block.getAttribute('data-model-overrides') || '{}';
      if (!composite) { mount.innerHTML = ''; return; }
      fetch('/api/composite-resolve?id=' + encodeURIComponent(composite) + '&overrides=' + encodeURIComponent(overridesJson))
        .then(function (r) { return r.json().then(function (b) { return { status: r.status, body: b }; }); })
        .then(function (res) {
          if (res.status !== 200 || !res.body || !res.body.parameters) {
            mount.innerHTML = '<p class="muted" style="font-size:0.85em;margin:0">No resolvable configuration for this composite.</p>';
            return;
          }
          var overrides = {}; try { overrides = JSON.parse(overridesJson); } catch (e) {}
          _renderModelConfig(mount, res.body.parameters, overrides, esc);
        }).catch(function () { mount.innerHTML = ''; });
    });
  }
  window._loadModelConfig = _loadModelConfig;

  function _renderModelConfig(mount, params, overrides, esc) {
    var keys = Object.keys(params);
    if (!keys.length) {
      mount.innerHTML = '<p class="muted" style="font-size:0.85em;margin:0">This composite takes no configurable parameters.</p>';
      return;
    }
    var effective = {};
    var rows = keys.map(function (k) {
      var def = params[k] || {};
      var overridden = overrides && (k in overrides);
      var val = overridden ? overrides[k] : def.default;
      effective[k] = val;
      var shown = (val === undefined || val === null) ? '—' : val;
      return '<tr' + (overridden ? ' style="background:#eff6ff"' : '') + '>' +
        '<td style="padding:3px 8px"><code>' + esc(k) + '</code></td>' +
        '<td style="padding:3px 8px;color:#6b7280">' + esc(def.type || '') + '</td>' +
        '<td style="padding:3px 8px"><code>' + esc(shown) + '</code>' +
        (overridden ? ' <span style="color:#2563eb;font-size:0.72em;font-weight:600">override</span>' : '') + '</td>' +
        '<td style="padding:3px 8px;color:#6b7280">' + esc(def.description || '') + '</td></tr>';
    }).join('');
    mount.innerHTML =
      '<div style="font-size:0.85em;color:#374151;margin-bottom:4px"><strong>Config that runs</strong> ' +
      '<span class="muted">— resolved parameters (composite defaults ⊕ this study\'s overrides)</span></div>' +
      '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:0.85em">' +
      '<thead><tr>' + ['Parameter', 'Type', 'Value', 'Description'].map(function (h) {
        return '<th style="text-align:left;padding:3px 8px;border-bottom:1px solid #e5e7eb;color:#6b7280;">' + h + '</th>';
      }).join('') + '</tr></thead><tbody>' + rows + '</tbody></table></div>' +
      '<details style="margin-top:6px"><summary class="muted" style="cursor:pointer;font-size:0.82em">Full resolved config (JSON)</summary>' +
      '<pre style="font-size:0.78em;background:#f8fafc;padding:8px;border-radius:4px;overflow-x:auto;margin:4px 0 0">' +
      esc(JSON.stringify(effective, null, 2)) + '</pre></details>';
  }

  // Simulations tab: the study's runs rendered with the SHARED Simulations-DB
  // table component (sim-table.js), filtered to this study via
  // /api/simulations?study=<slug>. One clean table (Run · Location · Origin ·
  // Emitter · Time · Status · ⬇Data/⬇Analysis) replacing the old bespoke
  // runs-table + baseline + simulation_set representations.
  var _studySimsLoaded = false;
  function _loadStudySims(force) {
    var mount = document.getElementById('study-sim-table');
    if (!mount || !window.SimTable) return;
    if (_studySimsLoaded && !force) return;
    _studySimsLoaded = true;
    var slug = studyName();
    mount.innerHTML = '<p class="muted" style="margin:0">Loading simulations…</p>';
    var path = '/api/simulations?study=' + encodeURIComponent(slug);
    var url = (window.DataSource && window.DataSource.apiUrl) ? window.DataSource.apiUrl(path) : path;
    fetch(url).then(function (r) { return r.text(); }).then(function (t) {
      var d = {}; try { d = t ? JSON.parse(t) : {}; } catch (e) { d = {}; }
      window.SimTable.renderTable(mount, d.simulations || [], { scope: 'study', onRowClick: _showRunDetail });
    }).catch(function () {
      window.SimTable.renderTable(mount, [], { scope: 'study' });
    });
  }
  window._loadStudySims = _loadStudySims;

  // Per-run detail panel (opened by clicking a row in the study Simulations
  // table): metadata + robust downloads + open-in-Composite-Explorer, PLUS
  // (Task E3) that run's figures/report-cards/results inline, so Simulations
  // is a real per-run hub — no new backend, reuses the run's store_path/
  // db_path/spec_id already on the row and existing endpoints/renderers.
  function _showRunDetail(row) {
    var host = document.getElementById('study-run-detail');
    if (!host || !row) return;
    var S = window.SimTable, e = S.esc;
    var runId = row.run_id || '';
    var hasData = !!(row.store_path || row.db_path);
    var slug = studyName();
    var BP = window.__BASE_PATH__ || "";
    var dl = hasData
      ? '<a class="action-btn" download href="' + BP + '/api/simulation-run-download?run_id=' + encodeURIComponent(runId) + '">⬇ Data (raw emitter)</a>'
      : '<span class="muted" style="font-size:0.85em">no persisted store</span>';
    var an = slug
      ? '<a class="action-btn" download href="' + BP + '/api/study-analysis-zip?study=' + encodeURIComponent(slug) + '">⬇ Analysis (figures / cards)</a>'
      : '';
    // Enforcement: the run opens in the Composite Explorer only when its
    // composite is a registered composite; otherwise we surface the gap.
    var explore = (runId && row.spec_id && row.composite_registered)
      ? '<a class="action-btn" href="/?focus=composite-explore&id=' + encodeURIComponent(row.spec_id) + '&run_id=' + encodeURIComponent(runId) + '#composite-explore">↗ Open run in Composite Explorer</a>'
      : '<span style="color:#b91c1c;font-size:0.85em">⚠ ' + (row.spec_id
          ? 'composite <code>' + e(row.spec_id) + '</code> is not registered — cannot open in the Explorer'
          : 'no composite associated with this run') + '</span>';
    var kv = function (k, v) {
      return '<div style="display:flex;gap:8px"><span class="muted" style="min-width:90px">' + e(k) + '</span><span>' + v + '</span></div>';
    };
    _currentRunDetailRow = row;
    var figs = _runDetailFiguresHtml(row);
    var rcHtml = _runDetailReportCardsHtml();
    var resultsHtml = _runDetailResultsSummaryHtml(row, figs.count, hasData);
    var sectionLabel = function (label) {
      return '<div class="muted" style="font-size:0.78em;text-transform:uppercase;letter-spacing:.04em;margin-bottom:4px">' + e(label) + '</div>';
    };
    host.innerHTML =
      '<div class="panel" style="padding:12px 14px">' +
        '<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">' +
          '<strong>' + e(row.sim_name || row.label || runId) + '</strong>' +
          S.statusChip(row.status) + S.emitterPill(row.emitter_type) + S.originPill(row) +
          '<button type="button" class="btn-mini" style="margin-left:auto" onclick="document.getElementById(\'study-run-detail\').innerHTML=\'\'">✕</button>' +
        '</div>' +
        '<div style="display:grid;gap:4px;font-size:0.88em;margin-bottom:10px">' +
          kv('Run ID', '<code>' + e(runId) + '</code>') +
          kv('Composite', S.composite(row)) +
          kv('Location', S.location(row)) +
          kv('Time', e(S.fmtTime(row.completed_at || row.started_at))) +
          (row.n_steps != null ? kv('Steps', e(row.n_steps)) : '') +
        '</div>' +
        '<div style="display:flex;flex-wrap:wrap;gap:8px">' + dl + ' ' + an + ' ' + explore + '</div>' +
        '<div style="margin-top:12px">' + sectionLabel('Figures') +
          '<div id="run-detail-figures">' + figs.html + '</div>' +
        '</div>' +
        '<div style="margin-top:10px">' + sectionLabel('Report cards') + rcHtml + '</div>' +
        '<div style="margin-top:10px">' + sectionLabel('Results') + resultsHtml + '</div>' +
      '</div>';
    _wireFigureRunLinks(host);
    host.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
  window._showRunDetail = _showRunDetail;

  function _loadCharts(panelId) {
    if (_chartsLoadedFor[panelId]) return;
    var panel = document.getElementById(panelId);
    if (!panel) return;
    _chartsLoadedFor[panelId] = true;
    // Both modes fetch the study-charts payload via DataSource: local mode
    // hits the live /api/study-charts/<slug> endpoint; snapshot mode reads the
    // /api/study-charts/<slug>.json the publisher base64-embedded at build
    // time (static charts only — live charts need a runs.db absent from the
    // snapshot). DataSource resolves the base-path-prefixed URL for either.
    var _cfg = window.__DASH_CONFIG__ || {};
    var _isSnapshot = _cfg.mode === 'snapshot';
    panel.innerHTML = '<p class="muted" style="margin:0">Loading charts…</p>';
    window.DataSource.loadStudyCharts(studyName())
      .then(function(d) {
        if (!d || !d.charts || !d.charts.length) {
          if (_isSnapshot) {
            panel.innerHTML = '<p class="muted" style="margin:0">No pre-rendered charts published for this study.</p>';
          } else {
            panel.innerHTML = (d && d.db_exists === false)
              ? '<p class="muted" style="margin:0">No run data or figures yet for this study.</p>'
              : '<p class="muted" style="margin:0">No chart data available for this study.</p>';
          }
          if (panelId === 'viz-charts-panel') {
            _figuresSourceState.charts = false;
            _updateFiguresEmptyState();
            _chartsCache = [];  // Task E3: settled — no charts to match a run
            _notifyFigureDataAvailable();
          }
          return;
        }
        // Render every pre-rendered chart — 'live' (runs.db), 'declared'
        // (study.yaml-registered viz, the common snapshot case), or unset —
        // except the checked-in 'static' charts, which get their own labeled
        // section below. (Previously only 'live'/unset rendered, so 'declared'
        // charts silently vanished in the published snapshot.)
        var live = d.charts.filter(function(c) { return c.source !== 'static'; });
        var stat = d.charts.filter(function(c) { return c.source === 'static'; });
        var html = '';
        if (live.length) {
          html += live.map(_renderChartCard).join('');
        }
        if (stat.length) {
          if (live.length) {
            html += '<h3 class="section-title" style="margin-top:24px">Pre-rendered charts <span class="muted" style="font-weight:400;font-size:0.85em">(checked-in under <code>studies/' + studyName() + '/charts/</code>)</span></h3>';
          }
          html += stat.map(_renderChartCard).join('');
        }
        panel.innerHTML = html;
        _wireFigureRunLinks(panel);
        if (panelId === 'viz-charts-panel') {
          _figuresSourceState.charts = true;
          _updateFiguresEmptyState();
          _chartsCache = d.charts;  // Task E3: settled — per-item run_id (V3)
          _notifyFigureDataAvailable();
        }
      })
      .catch(function(e) {
        panel.innerHTML = '<p class="muted" style="color:#dc2626">Chart load failed: ' + (e && e.message || e) + '</p>';
        if (panelId === 'viz-charts-panel') {
          _figuresSourceState.charts = false;
          _updateFiguresEmptyState();
        }
      });
  }

  // ── Seed a new study from a follow_up_studies[] entry ────────────────────
  function _seedFollowupStudy(parentStudyName, followupIdx) {
    if (!confirm('Seed a new study from this follow-up?\n\nA new study.yaml will be created under studies/<new-name>/ pre-populated with the follow-up context.')) {
      return;
    }
    api('POST', '/api/study-seed-followup', {parent: parentStudyName, followup_idx: followupIdx})
      .then(function(res) {
        if (res.status !== 200 || res.body.error) {
          alert('Seed failed: ' + (res.body.error || res.status));
          return;
        }
        alert('Created: ' + res.body.new_study_name + '\nOpening it now.');
        window.location.href = '/studies/' + encodeURIComponent(res.body.new_study_name);
      });
  }
  window._seedFollowupStudy = _seedFollowupStudy;

  // ── Seed a new study from a discovery_implications.followup_study_proposals
  // entry (by id). This is what the "➕ Add to investigation" buttons call;
  // it was previously undefined on the study-detail page (the button did
  // nothing). Delegates to the shared seed endpoint with {parent, proposal_id}.
  function _seedFollowupProposal(parentStudyName, proposalId) {
    if (!confirm('Spawn a new study from this follow-up proposal?\n\n'
        + 'A new study.yaml will be created under studies/<new-name>/ with a '
        + 'leads-to edge back to ' + parentStudyName + '.')) {
      return;
    }
    var body = {parent: parentStudyName};
    if (proposalId) body.proposal_id = proposalId;
    api('POST', '/api/study-seed-followup', body)
      .then(function(res) {
        if (res.status !== 200 || res.body.error) {
          alert('Seed failed: ' + (res.body.error || res.status));
          return;
        }
        alert('Created: ' + res.body.new_study_name + '\nOpening it now.');
        window.location.href = '/studies/' + encodeURIComponent(res.body.new_study_name);
      });
  }
  window._seedFollowupProposal = _seedFollowupProposal;

  // ── Pop out the bigraph-loom STATIC (read-only) view of a composite. Used by
  // the Build-tab Model block.
  //
  // Snapshot mode (the hosted read-only dashboard) serves pre-resolved composite
  // state as STATIC FILES at <basePath>/api/composite-state/<id>.json and the
  // loom entry point at <basePath>/bigraph-loom/ — BOTH must carry the configured
  // base path (e.g. /v2ecoli/dashboard on a GitHub Pages project site). The live
  // server instead answers the query form /api/composite-state?ref=<id> at the
  // origin root. Using the live form (or omitting the base path) in snapshot mode
  // 404s the pop-out — mirror walkthrough.js _loomStaticPopout here.
  function _openCompositeLoom(composite) {
    if (!composite) return;
    var cfg = (typeof window !== 'undefined' && window.__DASH_CONFIG__) || {};
    var isSnap = cfg.mode === 'snapshot';
    var origin = (typeof location !== 'undefined' && location.origin
                  && /^https?:/.test(location.origin)) ? location.origin : '';
    // basePath applies in BOTH modes now: snapshot (published subpath) and live
    // hosting under a prefix (e.g. /workbench). Empty in normal local serving.
    var base = origin + (cfg.basePath || '');
    var u;
    if (isSnap) {
      // Published bundle: no live backend → read-only wiring from a static snapshot.
      var stateUrl = base + '/api/composite-state/' + encodeURIComponent(composite) + '.json';
      u = base + '/bigraph-loom/index.html?static=1&stateUrl=' + encodeURIComponent(stateUrl);
    } else {
      // Live dashboard: full Setup & Run (loom self-hydrates via ?id= → /api/composite-state?ref=).
      u = base + '/bigraph-loom/index.html?id=' + encodeURIComponent(composite);
    }
    window.open(u, 'loom', 'width=1200,height=840');
  }
  window._openCompositeLoom = _openCompositeLoom;

  // --- Inline-edit (overview fields: objective, conclusion, question, hypothesis, status) ---
  function _saveOverviewField(field, value) {
    if (field === 'objective') {
      return api('POST', '/api/study-set-objective', {study: studyName(), text: value});
    }
    if (field === 'conclusion') {
      return api('POST', '/api/study-set-conclusion', {study: studyName(), text: value});
    }
    if (field === 'question' || field === 'hypothesis' || field === 'status') {
      var body = {investigation: studyName(), fields: {}};
      body.fields[field] = value;
      return api('POST', '/api/investigation-set-overview', body);
    }
    return Promise.resolve();
  }


  function makeEditable(el) {
    if (!el) return;
    var placeholder = el.dataset.placeholder || '';
    var field = el.dataset.field || el.id.replace(/-text$/, '');
    el.addEventListener('click', function() {
      if (el.querySelector('textarea')) return;
      var current = el.textContent.trim();
      var t = document.createElement('textarea');
      t.value = (current === placeholder) ? '' : current;
      t.rows = 4;
      t.style.width = '100%';
      el.innerHTML = '';
      el.appendChild(t);
      t.focus();
      t.addEventListener('blur', function() {
        _saveOverviewField(field, t.value).then(function() {
          el.textContent = t.value || placeholder;
        });
      });
    });
  }

  document.querySelectorAll('[data-editable="true"]').forEach(function(el) {
    makeEditable(el);
  });

  // --- v4 narrative-spine forms: report / study_card / biological_summary /
  // conclusion_verdicts. Every [data-narrative-path] input saves to the
  // generic /api/study-narrative-set on blur (text/textarea) or change
  // (select). The path is a dotted route into the v4 narrative-spine
  // sub-tree; the backend resolves it, creates parents as needed, and
  // atomically writes study.yaml.
  function _saveNarrative(el) {
    var path = el.dataset.narrativePath;
    if (!path) return;
    var value = el.value;
    el.classList.remove('narrative-saved', 'narrative-error');
    return api('POST', '/api/study-narrative-set', {
      study: studyName(),
      path: path,
      value: value,
    }).then(function(res) {
      // api() returns {status, body}. 200 + body.ok === success.
      if (res && res.status === 200 && res.body && res.body.ok) {
        el.classList.add('narrative-saved');
        setTimeout(function() { el.classList.remove('narrative-saved'); }, 700);
      } else {
        el.classList.add('narrative-error');
        var detail = (res && res.body && res.body.error) || (res && res.status) || 'unknown';
        el.title = 'Save failed: ' + detail;
      }
    }).catch(function(e) {
      el.classList.add('narrative-error');
      el.title = 'Network error: ' + (e && e.message || e);
    });
  }
  // Grow a textarea to fit its content so the caveat/conclusion/biology boxes
  // show all their text at once instead of a fixed 2–3 rows with an inner
  // scrollbar. Runs on init and on every keystroke.
  function _autoGrow(el) {
    if (!el || (el.tagName || '').toLowerCase() !== 'textarea') return;
    el.style.height = 'auto';
    el.style.height = Math.max(el.scrollHeight, 38) + 'px';
  }
  // Re-fit every auto-grow box (used after a tab becomes visible: hidden
  // textareas measure scrollHeight 0 and would otherwise stay at min height).
  window._autoGrowTextareas = function () {
    document.querySelectorAll('.narrative-textarea').forEach(_autoGrow);
  };
  document.querySelectorAll('[data-narrative-path]').forEach(function(el) {
    var tag = (el.tagName || '').toLowerCase();
    // Selects save on change (immediate, no need to wait for blur). Text
    // inputs + textareas save on blur so the user can type without round-
    // tripping per keystroke.
    var evt = (tag === 'select') ? 'change' : 'blur';
    el.addEventListener(evt, function() { _saveNarrative(el); });
    if (tag === 'textarea') {
      _autoGrow(el);                                            // size to initial content
      el.addEventListener('input', function() { _autoGrow(el); });
    }
  });
  // Re-fit on window resize: line-wrapping changes with width, so a full-width
  // box needs fewer rows than the same text at 90ch and vice-versa.
  window.addEventListener('resize', function() {
    document.querySelectorAll('.narrative-textarea').forEach(_autoGrow);
  });

  // Progressive disclosure: an empty optional narrative field renders a quiet
  // "+ Add …" button plus its editor pre-hidden (and already save-bound via the
  // [data-narrative-path] pass above). Clicking the button reveals the editor,
  // focuses it, and hides itself. No re-binding needed — the editor was always
  // in the DOM.
  document.querySelectorAll('.add-field-btn[data-reveal-field]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var path = btn.dataset.revealField;
      var ed = document.querySelector('[data-field-editor="' + path + '"]');
      if (!ed) return;
      ed.classList.remove('is-hidden');
      btn.classList.add('is-hidden');
      var field = ed.matches('textarea,input,select') ? ed : ed.querySelector('textarea,input,select');
      if (field) {
        field.focus();
        if ((field.tagName || '').toLowerCase() === 'textarea') _autoGrow(field);
      }
    });
  });

  // --- Helpers: attach a click handler to every button matching a CSS class ---
  function bindAll(selector, handler) {
    document.querySelectorAll(selector).forEach(function(btn) {
      btn.addEventListener('click', function(ev) { handler(btn, ev); });
    });
  }

  function studyName() { return window._studyName; }

  // --- Analyses (Model tab) ---
  // Reuses /api/study-set-analyses (lib.metadata_mutations.set_investigation_analyses,
  // which despite its name resolves any study by name via study_dir() — flat
  // studies/<name>/ preferred over legacy investigations/<name>/, so this works
  // for an ungrouped study exactly like a grouped one).
  function _saveStudyAnalyses() {
    var el = document.getElementById('study-analyses-list');
    var status = document.getElementById('study-analyses-status');
    if (!el) return;
    var names = el.value.split('\n').map(function (s) { return s.trim(); }).filter(Boolean);
    var analyses = names.map(function (n) { return {name: n, params: {}}; });
    if (status) status.textContent = 'Saving…';
    api('POST', '/api/study-set-analyses', {investigation: studyName(), analyses: analyses})
      .then(function (r) {
        if (status) {
          status.textContent = (r.status === 200)
            ? 'Saved.'
            : 'Error: ' + (r.body && r.body.error || r.status);
        }
      });
  }
  window._saveStudyAnalyses = _saveStudyAnalyses;

  // --- Header actions ---
  bindAll('.btn-rename', function() {
    var n = prompt('New name (lowercase + dashes):', studyName());
    if (!n) return;
    // study-rename handler (_post_study_rename_for_test) uses body key "study"
    api('POST', '/api/study-rename', {study: studyName(), new_name: n})
      .then(function(res) {
        if (res.status === 200) window.location = '/studies/' + n;
        else alert(res.body.error || 'Rename failed');
      });
  });

  bindAll('.btn-export', function() {
    // A location assignment bypasses the fetch/XHR/EventSource base-path shim.
    window.location = (window.__BASE_PATH__ || "") + '/api/study-export?study=' + encodeURIComponent(studyName());
  });

  // "Run current spec" — force-relaunch this study's baseline as a brand-new
  // run, RE-DERIVING spec_id/params/n_steps/emitter/etc. from the study's
  // CURRENT study.yaml (POST /api/study-run-baseline, same endpoint the
  // Baseline tab's Run button uses). This is one of TWO deliberately distinct
  // header actions (reproducible-rerun-spine Task 4 / G2) — the other,
  // "Reproduce" (below), replays a run's RECORDED manifest verbatim instead;
  // never conflate the two under one ambiguous "Rerun" button. Live-only: a
  // published read-only snapshot has no backend to launch against, so both
  // buttons are hidden there (see the snapshot-mode block near the end of
  // this file, mirroring the remote-run-panel hide).
  // Mode-aware dispatch: ONE button, the actual target decided by deployment
  // config, never a second button next to it. Items 18/19 exist specifically
  // to eliminate the "which button do I click" choice, not reintroduce it
  // under a new name. Remote-pinned deployments (VIVARIUM_WORKBENCH_REMOTE_PINNED,
  // e.g. the live smscdk prod deployment) dispatch to AWS Batch via
  // remote-run-submit; everything else keeps the existing local-engine path.
  var _CANCELLED = { status: 0, body: { cancelled: true } };

  function _dispatchCurrentSpecBaseline() {
    return api('GET', '/api/remote-run-config').then(function(cfgRes) {
      var cfg = (cfgRes.status === 200 && cfgRes.body) || {};
      if (cfg.pinned && cfg.simulator_id) return _dispatchRemotePinned(cfg);
      if (!confirm("Run this study's CURRENT baseline spec as a new run?")) return _CANCELLED;
      return api('POST', '/api/study-run-baseline', { study: studyName() });
    });
  }

  // item 20: the resolved target (repo/branch/commit/simulator id) is fetched
  // fresh via /api/remote-run-config immediately above -- never a stale
  // client-rendered label -- but nothing surfaced it to a human before this
  // function fired the actual AWS Batch dispatch. Show it and require an
  // explicit confirm, so a workspace-identity mismatch is caught here, before
  // money gets spent, not discovered afterward via aws batch describe-jobs.
  function _dispatchRemotePinned(cfg) {
    var msg = 'Dispatch to AWS Batch:\n\n' +
      '  repo:    ' + (cfg.repo_url || '(unknown)') + '\n' +
      '  branch:  ' + (cfg.branch || '(unknown)') + '\n' +
      '  commit:  ' + ((cfg.commit || '(unknown)').slice(0, 12)) + '\n' +
      '  simulator id: ' + cfg.simulator_id + '\n\n' +
      'Proceed?';
    if (!confirm(msg)) return _CANCELLED;
    var baseline = (window._study && window._study.baseline) || [];
    var params = (baseline[0] && baseline[0].params) || {};
    return api('POST', '/api/remote-run-submit', {
      study: studyName(),
      simulator_id: cfg.simulator_id,
      num_generations: params.n_generations || 1,
      num_seeds: params.n_seeds || 1,
    });
  }
  window._dispatchCurrentSpecBaseline = _dispatchCurrentSpecBaseline;

  bindAll('#study-run-current-spec', function(btn) {
    var orig = btn.textContent;
    btn.disabled = true;
    btn.textContent = '… running';
    _dispatchCurrentSpecBaseline()
      .then(function(res) {
        btn.disabled = false;
        btn.textContent = orig;
        if (res.body && res.body.cancelled) return;
        if (res.status === 200 || res.status === 202) {
          var runId = res.body && (res.body.run_id || res.body.simulation_id);
          var msg = 'Run launched' + (runId ? ' — new run ' + runId : '');
          if (typeof _showToast === 'function') _showToast(msg); else alert(msg);
          if (typeof _loadStudySims === 'function') _loadStudySims(true);
        } else {
          alert('Run failed: ' + (res.body && res.body.error || res.status));
        }
      })
      .catch(function(err) {
        btn.disabled = false;
        btn.textContent = orig;
        alert('Run failed: network error — ' + err);
      });
  });

  // "Reproduce" — replay this study's MOST RECENT run's recorded manifest
  // verbatim (POST /api/study-reproduce) rather than re-deriving from the
  // current study.yaml: a spec edit made after that run never changes what
  // this launches (reproducible-rerun-spine Task 4 / G2). Resolves the
  // latest run_id from /api/simulations?study=<slug> (already the source the
  // Simulations tab's table reads, newest-first) rather than requiring the
  // user to pick one — the per-row ↻ Rerun button (Simulations tab) already
  // covers reproducing an ARBITRARY older run.
  bindAll('#study-reproduce', function(btn) {
    var slug = studyName();
    var orig = btn.textContent;
    btn.disabled = true;
    btn.textContent = '… reproducing';
    fetch('/api/simulations?study=' + encodeURIComponent(slug))
      .then(function(r) { return r.json(); })
      .then(function(d) {
        var sims = (d && d.simulations) || [];
        var latest = sims.length ? (sims[0].run_id || '') : '';
        if (!latest) throw new Error('no runs recorded yet for this study');
        return api('POST', '/api/study-reproduce', { study: slug, run_id: latest });
      })
      .then(function(res) {
        btn.disabled = false;
        btn.textContent = orig;
        if (res.status === 200) {
          var msg = 'Reproduce launched' + (res.body && res.body.run_id ? ' — new run ' + res.body.run_id : '');
          if (typeof _showToast === 'function') _showToast(msg); else alert(msg);
          if (typeof _loadStudySims === 'function') _loadStudySims(true);
        } else {
          alert('Reproduce failed: ' + (res.body && res.body.error || res.status));
        }
      })
      .catch(function(err) {
        btn.disabled = false;
        btn.textContent = orig;
        alert('Reproduce failed: ' + (err && err.message ? err.message : err));
      });
  });

  // btn-delete has class "btn-delete danger" — selector ".btn-delete" still matches.
  // Handler _post_investigation_delete uses body key "name".
  bindAll('.btn-delete', function(btn) {
    // Guard: only the header delete button has data-study; variant/run deletes
    // use different class names so this handler won't fire for those.
    if (!btn.dataset.study) return;
    if (!confirm('Delete this study and all its runs?')) return;
    api('POST', '/api/study-delete', {name: studyName(), study: studyName()})
      .then(function() { window.location = '/studies'; });
  });

  // --- Baseline ---
  // Replace a baseline entry's composite ref: add-then-remove against the
  // existing (previously orphaned) endpoints, since there's no single
  // "replace" route. Order matters — study_baseline_remove refuses to leave
  // baseline[] empty (400), which a single-entry study (e.g. a fresh "+
  // Study" blank scaffold) always is; adding the replacement under a new
  // name FIRST means baseline[] never goes empty, then the old entry is
  // removed. The replacement keeps the original name only when it wasn't
  // already used (i.e. removal isn't blocked); otherwise it's suffixed to
  // avoid the add's own "already exists" 409. Params are dropped on
  // replace — a fresh composite ref starts from its own defaults, matching
  // what "+ Study" itself does.
  bindAll('.baseline-composite-set', function(btn) {
    var name = btn.dataset.baselineName;
    var input = document.querySelector('.baseline-composite-input[data-baseline-name="' + name + '"]');
    var status = document.querySelector('.baseline-composite-status[data-baseline-name="' + name + '"]');
    var composite = input ? input.value.trim() : '';
    if (!composite) { if (status) status.textContent = 'Enter a composite ref first.'; return; }
    if (status) status.textContent = 'Setting…';
    var newName = name + '-' + Date.now().toString(36);
    api('POST', '/api/study-baseline-add', {study: studyName(), name: newName, composite: composite, params: {}})
      .then(function (addResult) {
        if (addResult.status !== 200) throw addResult;
        return api('POST', '/api/study-baseline-remove', {study: studyName(), name: name});
      })
      .then(function (r) {
        if (r.status === 200) location.reload();
        else if (status) status.textContent = 'Error: ' + (r.body && r.body.error || r.status);
      })
      .catch(function (addResult) {
        if (status) status.textContent = 'Error: ' + (addResult.body && addResult.body.error || addResult.status);
      });
  });

  // --- Runs ---
  bindAll('.btn-view-run', function(btn) {
    // Per-run viewer: open THIS run's own store (zarr/parquet/sqlite) in the
    // Data Explorer standalone page. Prefer the run's provenance store_path
    // (data-store-path) so it works even when the store lives outside the
    // explorer's run-picker discovery; fall back to run_id (the explorer
    // resolves it via /api/explorer/runs).
    var row = btn.closest('tr');
    var runId = btn.dataset.runId || (row && row.dataset.runId) || '';
    var store = (row && row.dataset.storePath) || '';
    if (store || runId) {
      var u = '/assets/explorer.html?' +
        (store ? 'db=' + encodeURIComponent(store) + '&' : '') +
        'run=' + encodeURIComponent(runId);
      window.open(u, '_blank');
      return;
    }
    // No run identity → fall back to the study-level results view.
    _setStudyTab('visualize');
    var panel = document.getElementById('panel-visualize');
    if (panel && panel.scrollIntoView) { try { panel.scrollIntoView({block: 'start'}); } catch (e) {} }
  });

  // study-run-delete → _post_investigation_run_delete
  bindAll('.btn-delete-run', function(btn) {
    var runId = btn.dataset.runId;
    if (!confirm('Delete this run?')) return;
    api('POST', '/api/study-run-delete', {
      study: studyName(), run_id: runId,
    }).then(function() { location.reload(); });
  });

  // --- Viz ---


  // ----- Tests tab -----

  // Verdict -> pill colour (matches the behavioral pill palette).
  var _RC_PILL = {
    within_tol: ['#16a34a', '#fff', 'within tol'],
    drift:      ['#d97706', '#fff', 'drift'],
    mismatch:   ['#dc2626', '#fff', 'mismatch'],
    ungraded:   ['#64748b', '#fff', 'ungraded']
  };

  // Fill each `kind: report_card` test's mount with the embedded card + verdict.
  // Tests tab: report_card-kind rows no longer re-mount the full card (that lives
  // on the Report Cards tab). We only recolour each row's verdict pill from the
  // card's verdict, so the Tests row shows PASS/FAIL at a glance + links across.
  function _fillReportCardModules(spec) {
    var urls = (spec && spec.report_card_urls) || {};
    var pills = document.querySelectorAll('.report-card-verdict[data-card]');
    Array.prototype.forEach.call(pills, function(pill) {
      if (pill.dataset.filled) return;           // idempotent
      var card = pill.getAttribute('data-card');
      var rc = urls[card];
      if (!rc || !rc.url) {
        pill.title = 'report card ' + String(card) + ' not generated yet — run the comparison';
        pill.dataset.filled = '1';
        return;
      }
      var v = (rc.verdict || 'ungraded');
      var p = _RC_PILL[v] || _RC_PILL.ungraded;
      pill.style.background = p[0]; pill.style.color = p[1]; pill.textContent = p[2];
      pill.title = 'report card verdict: ' + p[2] + ' — view the full card on the Report Cards tab';
      pill.dataset.filled = '1';
    });
  }

  // C6: each `kind: report_card` row (Behavioral tests, below) now expands
  // INLINE with its own full _renderRichReportCard(card) — this top panel
  // would double-render every card if it ALSO emitted the per-card stack
  // (rc.url / rc.groups tables etc.). So it is narrowed to ONLY the
  // cross-card interactive plotly comparison, which has no per-row
  // equivalent and must not be lost. The host mount only exists in the DOM
  // when the template server-gated it on `comparison_plotly_url` (absent !=
  // empty — no empty box when there's no comparison to show); when there IS
  // a mount but no plotly URL (e.g. stale client-side spec), clear it rather
  // than leave the "Loading…" placeholder stuck.
  function _fillReportCardsTab(spec) {
    var host = document.getElementById('report-cards-panel');
    if (!host) return;
    var pUrl = spec && spec.comparison_plotly_url;
    if (!pUrl) {
      host.innerHTML = '';
      return;
    }
    host.innerHTML = '<details open style="margin:0">'
      + '<summary style="cursor:pointer;font-weight:700;color:#111827;font-size:1.02em">'
      + 'Interactive comparison — v2ecoli vs vEcoli (plotly)</summary>'
      + '<iframe class="viz-embed" src="' + escapeHtmlForTests(pUrl) + '" loading="lazy" '
      + 'style="width:100%;height:900px;border:1px solid #e2e8f0;border-radius:8px;background:#fff;margin-top:8px"></iframe>'
      + '</details>';
  }

  // C6: bind each report_card-kind row's inline <details> expander to
  // lazily mount that card's rich content — reusing _renderRichReportCard,
  // the SAME renderer the (now plotly-only) top panel used to call for
  // every card, so there is exactly one renderer and it fires once per card
  // (on first expand), not once per card PLUS once at the top. Idempotent —
  // safe to call again after the tests list re-renders.
  function _bindReportCardRowExpanders() {
    var rows = document.querySelectorAll('details.report-card-row-expander[data-card]');
    Array.prototype.forEach.call(rows, function (row) {
      if (row.dataset.bound) return;
      row.dataset.bound = '1';
      row.addEventListener('toggle', function () {
        if (!row.open) return;
        var mount = row.querySelector('.report-card-row-mount');
        if (!mount || mount.dataset.filled) return;
        mount.dataset.filled = '1';
        var card = row.getAttribute('data-card');
        mount.innerHTML = _renderRichReportCard(card);
      });
    });
  }

  // Verdict vocab: colour + glyph (matches the grade_card / render_html palette).
  var _RC_GL = {
    within_tol: ['#16a34a', '✓', 'within tol'],
    drift:      ['#d97706', '≈', 'drift'],
    mismatch:   ['#dc2626', '✗', 'mismatch'],
    ungraded:   ['#64748b', '−', 'ungraded']
  };

  function _rcPill(verdict) {
    var p = _RC_GL[verdict || 'ungraded'] || _RC_GL.ungraded;
    return '<span style="font-size:0.72em;font-family:monospace;padding:2px 10px;'
      + 'border-radius:9999px;background:' + p[0] + ';color:#fff">' + p[1] + ' ' + p[2] + '</span>';
  }

  function _rcCounts(groups) {
    var c = { within_tol: 0, drift: 0, mismatch: 0, ungraded: 0 };
    Object.keys(groups || {}).forEach(function (gn) {
      ((groups[gn] || {}).axes || []).forEach(function (a) {
        var v = a.verdict || 'ungraded';
        if (c[v] == null) c.ungraded++; else c[v]++;
      });
    });
    return c;
  }

  // Inline "1✓ 0≈ 3✗ 0−" tally used inside the dark header pill and group chips.
  function _rcTally(c) {
    return ['within_tol', 'drift', 'mismatch', 'ungraded'].map(function (v) {
      return '<span style="margin-left:8px;opacity:0.95">' + c[v] + _RC_GL[v][1] + '</span>';
    }).join('');
  }

  function _rcGroupChip(v, n) {
    var p = _RC_GL[v];
    return '<span style="display:inline-block;padding:2px 9px;border-radius:9999px;background:'
      + p[0] + ';color:#fff;font-size:0.72em;margin-left:5px">' + p[1] + ' ' + n + ' ' + p[2] + '</span>';
  }

  // The graded-scorecard look (dark header + overall pill w/ tally + per-group
  // count chips + per-axis tables) rendered from the study's verdict.json, PLUS
  // the rendered comparison trajectories (and an interactive plotly overlay when
  // one is available) in a drill-down.
  function _renderRichReportCard(card) {
    var e = escapeHtmlForTests;
    var rc = (window._study && window._study.report_card_urls || {})[card] || {};
    var groups = rc.groups || {};
    var counts = _rcCounts(groups);
    var overall = rc.verdict || 'ungraded';
    var op = _RC_GL[overall] || _RC_GL.ungraded;

    var header =
      '<div style="background:linear-gradient(135deg,#1f2937,#0b1220);color:#fff;'
      + 'padding:14px 18px;border-radius:10px 10px 0 0">'
      + '<div style="font-weight:700;font-size:1.02em;letter-spacing:0.01em">'
      + e(card) + ' — report card</div>'
      + '<div style="margin-top:9px"><span style="display:inline-block;padding:3px 12px;'
      + 'border-radius:9999px;background:' + op[0] + ';color:#fff;font-weight:700;'
      + 'font-size:0.82em;letter-spacing:0.04em">'
      + String(overall).toUpperCase().replace(/_/g, ' ') + _rcTally(counts) + '</span></div></div>';

    var sections = Object.keys(groups).map(function (gname) {
      var g = groups[gname] || {};
      var axes = g.axes || [];
      var gc = { within_tol: 0, drift: 0, mismatch: 0, ungraded: 0 };
      axes.forEach(function (a) { var v = a.verdict || 'ungraded'; if (gc[v] == null) gc.ungraded++; else gc[v]++; });
      var rows = axes.map(function (a) {
        var meter = a.meter || (a.value != null ? String(a.value) : '');
        var val = (a.value != null && typeof a.value === 'number') ? a.value.toPrecision(4) : '';
        return '<tr class="rc-row-' + (a.verdict || 'ungraded') + '">'
          + '<td style="padding:7px 10px;border-bottom:1px solid #eef2f7;border-left:3px solid ' + (_RC_GL[a.verdict] || _RC_GL.ungraded)[0] + '">'
          + '<div style="display:flex;align-items:center;gap:8px"><span style="font-weight:600;color:#1f2937">'
          + e(String(a.label || a.id || '')) + '</span>' + _rcPill(a.verdict) + '</div></td>'
          + '<td style="padding:7px 10px;border-bottom:1px solid #eef2f7;font-variant-numeric:tabular-nums;color:#334155">' + e(val) + '</td>'
          + '<td style="padding:7px 10px;border-bottom:1px solid #eef2f7;color:#475569;font-size:0.9em">' + e(String(meter)) + '</td>'
          + '</tr>';
      }).join('');
      return '<section style="background:#fff;border:1px solid #e5e7eb;border-top:0;padding:12px 14px">'
        + '<div style="display:flex;align-items:center;flex-wrap:wrap;gap:6px;margin-bottom:6px">'
        + '<h4 style="margin:0;font-size:0.98em;color:#111827">' + e(gname.replace(/_/g, ' ')) + '</h4>'
        + _rcGroupChip('within_tol', gc.within_tol) + _rcGroupChip('drift', gc.drift)
        + _rcGroupChip('mismatch', gc.mismatch) + _rcGroupChip('ungraded', gc.ungraded) + '</div>'
        + (axes.length
          ? '<table style="width:100%;border-collapse:collapse;font-size:0.9em">'
            + '<thead><tr style="text-align:left;color:#94a3b8;font-size:0.78em">'
            + '<th style="padding:4px 10px">Axis</th><th style="padding:4px 10px">Value</th>'
            + '<th style="padding:4px 10px">Summary</th></tr></thead><tbody>' + rows + '</tbody></table>'
          : '<div class="muted" style="padding:4px 10px">no axes recorded</div>')
        + '</section>';
    }).join('');

    // The actual comparison TRAJECTORIES are the study-level interactive plotly
    // (v2ecoli vs vEcoli time-series overlays) rendered once at the top of the
    // Report Cards tab — NOT rc.url. rc.url is the rendered report-card HTML,
    // i.e. the same scorecard already shown above as native tables; embedding it
    // under a "Comparison trajectories" label showed a second report card, which
    // is exactly the confusion we're removing. So no per-card iframe here.
    var viz = '';
    if (!Object.keys(groups).length) {
      viz = '<div class="muted" style="padding:8px">Verdict recorded, but the card body '
        + 'has not been rendered yet — run the comparison to generate it.</div>';
    }

    return '<div class="report-card-block" style="margin-bottom:28px;border-radius:10px;'
      + 'box-shadow:0 1px 3px rgba(0,0,0,0.06)">' + header + sections + '</div>' + viz;
  }

  // Tests tab entry point (Task 10): ONE audit for report cards + behavioral
  // gates. Renders the gate/audit summary strip, then fills the report-cards
  // subsection and the behavioral-tests list — each via its existing renderer,
  // single-sourced from spec.outcome_rollup / spec.latest_outcomes so the
  // strip can't drift from the row pills below it.
  function _loadTestsPanel(spec) {
    _renderTestsGateSummary(spec);
    _fillReportCardsTab(spec);
    loadTestsTab(spec);
    _loadQualityChecks(spec);
    _loadReproducibilityChecks(spec);
  }
  window._loadTestsPanel = _loadTestsPanel;

  // ── G5: Quality check group (rigor scorecard) ───────────────────────────
  // GET /api/study-rigor?study=<slug> → viva_superpowers.rigor.study_rigor,
  // already computed in CI but never rendered on the page until now. Fetched
  // client-side (same pattern as _loadReadouts / _loadAnalysisOutputs above)
  // into #check-group-quality, the mount templates/study-detail.html adds to
  // the Tests panel right after the gate summary.
  //
  // Rigor's own severity vocabulary (ok/warn/gap/not_applicable) is NOT the
  // G3 outcome-token vocabulary — in particular rigor's "gap" means "this
  // dimension was checked and found deficient" (closest to a FAILING test),
  // which is a different meaning from the G3 token map's pre-existing 'GAP'
  // entry (a report-card axis that was never assessed -> "not assessable").
  // Reusing that spelling would silently relabel a real deficiency as
  // "nothing to see here". So severities are proxied through the EXISTING
  // token whose MEANING matches (ok->PASS, warn->PARTIAL, gap->FAIL,
  // not_applicable->SKIP) rather than fed to outcomeLabel/_class/_glyph
  // verbatim — same four-value vocabulary + glyphs as the rest of the page,
  // honestly mapped.
  var _RIGOR_SEVERITY_PROXY = { ok: 'PASS', warn: 'PARTIAL', gap: 'FAIL', not_applicable: 'SKIP' };
  var _RIGOR_OUTCOME_COLORS = {
    'met':            { bg: '#d1fae5', fg: '#065f46' },
    'conditional':    { bg: '#fef3c7', fg: '#92400e' },
    'not-met':        { bg: '#fee2e2', fg: '#991b1b' },
    'not-assessable': { bg: '#f1f5f9', fg: '#475569' }
  };

  function _renderQualityDimension(d) {
    var e = escapeHtmlForTests;
    var sev = String((d && d.severity) || '').toLowerCase();
    var tok = _RIGOR_SEVERITY_PROXY[sev] || '';
    var cls = tok ? outcomeClass(tok) : 'not-assessable';
    var glyph = tok ? outcomeGlyph(tok) : '○';
    var label = tok ? outcomeLabel(tok) : 'not assessable';
    var oc = _RIGOR_OUTCOME_COLORS[cls] || _RIGOR_OUTCOME_COLORS['not-assessable'];
    var comments = ((d && d.comments) || []).join(' ');
    return '<li class="quality-check-item outcome-' + cls + '" data-severity="' + e(sev) + '" '
      + 'style="display:flex;gap:10px;align-items:flex-start;padding:7px 0;border-top:1px solid #f1f5f9">'
      + '<span class="outcome-chip outcome-' + cls + '" title="rigor severity: ' + e(sev || 'unknown') + '" '
      + 'style="font-size:0.75em;font-weight:600;padding:2px 9px;border-radius:9999px;flex-shrink:0;'
      + 'background:' + oc.bg + ';color:' + oc.fg + '">' + glyph + '&nbsp;' + e(label) + '</span>'
      + '<div><strong>' + e((d && (d.label || d.id)) || '') + '</strong>'
      + (comments ? ' <span class="muted" style="font-size:0.8em">' + e(comments) + '</span>' : '')
      + '<div class="muted" style="font-size:0.88em;margin-top:2px">' + e((d && d.detail) || '') + '</div>'
      + '</div></li>';
  }

  // Returns {state, html} for the #check-group-quality mount's INNER content
  // (the mount div itself keeps its id/class; only its contents are replaced).
  function _qualityCheckGroupHtml(rigor) {
    var e = escapeHtmlForTests;
    var header = '<div class="check-group-header" style="display:flex;align-items:center;'
      + 'gap:8px;flex-wrap:wrap"><strong>Quality</strong> '
      + '<span class="muted" style="font-size:0.85em">rigor scorecard &mdash; '
      + '<code>viva_superpowers.rigor</code></span>';
    if (!rigor || rigor.unavailable) {
      var reason = (rigor && rigor.reason) || 'could not be computed';
      return {
        state: 'unavailable',
        html: header + '</div><p class="empty-message">unavailable(' + e(reason) + ')</p>'
      };
    }
    var dims = (rigor && rigor.dimensions) || [];
    var score = (rigor && rigor.score) || {};
    var bits = [];
    if (score.gap) bits.push(score.gap + (score.gap === 1 ? ' gap' : ' gaps'));
    if (score.warn) bits.push(score.warn + ' warn');
    if (score.ok) bits.push(score.ok + ' ok');
    if (score.na) bits.push(score.na + ' n/a');
    var summaryText = bits.length ? bits.join(' · ') : (rigor.summary || '');
    header += summaryText
      ? ' <span class="muted" style="margin-left:auto;font-size:0.85em">' + e(summaryText) + '</span></div>'
      : '</div>';
    if (!dims.length) {
      return {
        state: 'empty',
        html: header + '<p class="empty-message">No rigor dimensions computed for this study.</p>'
      };
    }
    return {
      state: 'ready',
      html: header + '<ul class="quality-check-list" style="list-style:none;padding-left:0;margin:8px 0 0 0">'
        + dims.map(_renderQualityDimension).join('') + '</ul>'
    };
  }

  var _qualityChecksLoaded = false;
  function _loadQualityChecks(spec) {
    var host = document.getElementById('check-group-quality');
    if (!host) return;
    if (_qualityChecksLoaded) return;
    _qualityChecksLoaded = true;
    var slug = (spec && spec.name) || studyName();
    if (!slug) {
      host.dataset.state = 'unavailable';
      host.innerHTML = '<p class="empty-message">unavailable(no study slug)</p>';
      return;
    }
    fetch('/api/study-rigor?study=' + encodeURIComponent(slug), { headers: { Accept: 'application/json' } })
      .then(function(r) {
        return r.json().then(function(j) { return { ok: r.ok, status: r.status, json: j }; })
          .catch(function() { return { ok: r.ok, status: r.status, json: null }; });
      })
      .then(function(res) {
        var payload = res.json;
        if (!res.ok) {
          payload = { unavailable: true, reason: (payload && payload.error) || ('HTTP ' + res.status) };
        }
        var built = _qualityCheckGroupHtml(payload);
        host.dataset.state = built.state;
        host.innerHTML = built.html;
      })
      .catch(function() {
        host.dataset.state = 'unavailable';
        host.innerHTML = '<p class="empty-message">unavailable(request failed)</p>';
      });
  }
  window._loadQualityChecks = _loadQualityChecks;

  // ── G6: Reproducibility check group (L0-L5 study_audit) ─────────────────
  // GET /api/study-audit?study=<slug> → viva_superpowers.study_audit
  // (audit_workspace, filtered to this slug) — already computed in CI as the
  // reproducibility gate, never rendered on the page until now. Same fetch
  // pattern as _loadQualityChecks, into #check-group-reproducibility.
  //
  // Unlike rigor's ok/warn/gap/not_applicable (G5's severity proxy had to
  // dodge a real name collision with the pre-existing G3 'GAP' token —
  // see the comment above _RIGOR_SEVERITY_PROXY), study_audit's own
  // vocabulary is already exactly three-valued: pass/warn/fail, with no
  // token that collides with or means something different from a G3 token.
  // So the proxy here is a direct, honest match on MEANING, not a spelling
  // coincidence: pass (check satisfied) -> PASS, warn (soft/non-blocking
  // deficiency, tier="soft") -> PARTIAL, fail (check violated; tier may be
  // "hard" or "soft") -> FAIL. There is no study_audit status that means
  // "never assessed" (unlike rigor's not_applicable / the G3 SKIP/GAP
  // family), so that arm of the proxy is intentionally absent — an
  // individual check or level with no proxy match renders "not assessable"
  // via the same fallback _renderAuditCheckRow/_renderAuditLevelGroup use
  // for any unrecognized token, never fabricated as pass.
  var _AUDIT_STATUS_PROXY = { pass: 'PASS', warn: 'PARTIAL', fail: 'FAIL' };

  function _auditWorstStatus(checks) {
    var worst = 'pass';
    for (var i = 0; i < (checks || []).length; i++) {
      var st = String((checks[i] && checks[i].status) || '').toLowerCase();
      if (st === 'fail') return 'fail';
      if (st === 'warn') worst = 'warn';
    }
    return worst;
  }

  // Groups the flat checks[] list by ``level`` ("L0".."L5"), preserving the
  // order levels first appear in (study_audit already emits them in level
  // order), so the UI shows one row per level rather than re-listing rigor's
  // per-dimension style flat list — the mockup (Fable §10.1) shows "L0-L3
  // pass · L4 warn", a per-LEVEL state, not a per-check one.
  function _groupAuditChecksByLevel(checks) {
    var order = [];
    var byLevel = {};
    (checks || []).forEach(function(c) {
      var lvl = (c && c.level) || '?';
      if (!byLevel[lvl]) { byLevel[lvl] = []; order.push(lvl); }
      byLevel[lvl].push(c);
    });
    return order.map(function(lvl) { return { level: lvl, checks: byLevel[lvl] }; });
  }

  // "L0-L3 pass · L4 warn" — compress consecutive levels sharing the same
  // worst status into one range, per the Fable §10.1 mockup line.
  function _summarizeAuditLevels(groups) {
    var runs = [];
    groups.forEach(function(g) {
      var status = _auditWorstStatus(g.checks);
      var last = runs[runs.length - 1];
      if (last && last.status === status) {
        last.to = g.level;
      } else {
        runs.push({ from: g.level, to: g.level, status: status });
      }
    });
    return runs.map(function(r) {
      return (r.from === r.to ? r.from : (r.from + '-' + r.to)) + ' ' + r.status;
    }).join(' · ');
  }

  function _renderAuditCheckRow(c) {
    var e = escapeHtmlForTests;
    var status = String((c && c.status) || '').toLowerCase();
    var tok = _AUDIT_STATUS_PROXY[status] || '';
    var cls = tok ? outcomeClass(tok) : 'not-assessable';
    var glyph = tok ? outcomeGlyph(tok) : '○';
    var label = tok ? outcomeLabel(tok) : 'not assessable';
    var oc = _RIGOR_OUTCOME_COLORS[cls] || _RIGOR_OUTCOME_COLORS['not-assessable'];
    return '<li class="audit-check-item outcome-' + cls + '" data-status="' + e(status) + '" '
      + 'style="display:flex;gap:10px;align-items:flex-start;padding:5px 0 5px 20px;border-top:1px solid #f8fafc">'
      + '<span class="outcome-chip outcome-' + cls + '" title="audit status: ' + e(status || 'unknown') + '" '
      + 'style="font-size:0.72em;font-weight:600;padding:1px 8px;border-radius:9999px;flex-shrink:0;'
      + 'background:' + oc.bg + ';color:' + oc.fg + '">' + glyph + '&nbsp;' + e(label) + '</span>'
      + '<div><code style="font-size:0.85em">' + e((c && c.name) || '') + '</code>'
      + ' <span class="muted" style="font-size:0.78em">(' + e((c && c.tier) || '') + ')</span>'
      + ((c && c.detail) ? '<div class="muted" style="font-size:0.85em;margin-top:2px">' + e(c.detail) + '</div>' : '')
      + '</div></li>';
  }

  function _renderAuditLevelGroup(g) {
    var e = escapeHtmlForTests;
    var status = _auditWorstStatus(g.checks);
    var tok = _AUDIT_STATUS_PROXY[status] || '';
    var cls = tok ? outcomeClass(tok) : 'not-assessable';
    var glyph = tok ? outcomeGlyph(tok) : '○';
    var label = tok ? outcomeLabel(tok) : 'not assessable';
    var oc = _RIGOR_OUTCOME_COLORS[cls] || _RIGOR_OUTCOME_COLORS['not-assessable'];
    return '<li class="audit-level-item outcome-' + cls + '" data-level="' + e(g.level) + '" '
      + 'style="padding:7px 0;border-top:1px solid #f1f5f9">'
      + '<div style="display:flex;gap:10px;align-items:center">'
      + '<strong style="min-width:26px">' + e(g.level) + '</strong>'
      + '<span class="outcome-chip outcome-' + cls + '" title="' + e(g.level) + ' status: ' + e(status || 'unknown') + '" '
      + 'style="font-size:0.75em;font-weight:600;padding:2px 9px;border-radius:9999px;flex-shrink:0;'
      + 'background:' + oc.bg + ';color:' + oc.fg + '">' + glyph + '&nbsp;' + e(label) + '</span>'
      + '</div>'
      + '<ul style="list-style:none;padding-left:0;margin:2px 0 0 0">'
      + g.checks.map(_renderAuditCheckRow).join('') + '</ul></li>';
  }

  // Returns {state, html} for the #check-group-reproducibility mount's INNER
  // content (the mount div itself keeps its id/class; only its contents are
  // replaced) — same contract as _qualityCheckGroupHtml.
  function _reproducibilityCheckGroupHtml(audit) {
    var e = escapeHtmlForTests;
    var header = '<div class="check-group-header" style="display:flex;align-items:center;'
      + 'gap:8px;flex-wrap:wrap"><strong>Reproducibility</strong> '
      + '<span class="muted" style="font-size:0.85em">L0&ndash;L5 audit &mdash; '
      + '<code>viva_superpowers.study_audit</code></span>';
    if (!audit || audit.unavailable) {
      var reason = (audit && audit.reason) || 'could not be computed';
      return {
        state: 'unavailable',
        html: header + '</div><p class="empty-message">unavailable(' + e(reason) + ')</p>'
      };
    }
    var checks = audit.checks || [];
    if (!checks.length) {
      return {
        state: 'empty',
        html: header + '</div><p class="empty-message">No L0-L5 checks computed for this study.</p>'
      };
    }
    var groups = _groupAuditChecksByLevel(checks);
    var summaryText = _summarizeAuditLevels(groups);
    header += summaryText
      ? ' <span class="muted" style="margin-left:auto;font-size:0.85em">' + e(summaryText) + '</span></div>'
      : '</div>';
    return {
      state: 'ready',
      html: header + '<ul class="audit-level-list" style="list-style:none;padding-left:0;margin:8px 0 0 0">'
        + groups.map(_renderAuditLevelGroup).join('') + '</ul>'
    };
  }

  var _reproducibilityChecksLoaded = false;
  function _loadReproducibilityChecks(spec) {
    var host = document.getElementById('check-group-reproducibility');
    if (!host) return;
    if (_reproducibilityChecksLoaded) return;
    _reproducibilityChecksLoaded = true;
    var slug = (spec && spec.name) || studyName();
    if (!slug) {
      host.dataset.state = 'unavailable';
      host.innerHTML = '<p class="empty-message">unavailable(no study slug)</p>';
      return;
    }
    fetch('/api/study-audit?study=' + encodeURIComponent(slug), { headers: { Accept: 'application/json' } })
      .then(function(r) {
        return r.json().then(function(j) { return { ok: r.ok, status: r.status, json: j }; })
          .catch(function() { return { ok: r.ok, status: r.status, json: null }; });
      })
      .then(function(res) {
        var payload = res.json;
        if (!res.ok) {
          payload = { unavailable: true, reason: (payload && payload.error) || ('HTTP ' + res.status) };
        }
        var built = _reproducibilityCheckGroupHtml(payload);
        host.dataset.state = built.state;
        host.innerHTML = built.html;
      })
      .catch(function() {
        host.dataset.state = 'unavailable';
        host.innerHTML = '<p class="empty-message">unavailable(request failed)</p>';
      });
  }
  window._loadReproducibilityChecks = _loadReproducibilityChecks;

  // "N/M gates passed" score line ONLY. Every declared behavior test (kind:
  // behavioral or report_card) is a gate; the aggregate count comes from
  // spec.outcome_rollup (falling back to spec.latest_outcomes) so it can't
  // drift from the per-gate detail. The per-gate detail itself renders ONCE,
  // below, in the behavioral-tests list (#tests-list, server-rendered from
  // the same source) — this strip must not re-list the gates (Fable §4.6:
  // "one score line + one list", not the gate set rendered twice).
  function _renderTestsGateSummary(spec) {
    var host = document.getElementById('tests-gate-summary');
    if (!host) return;
    var tests = (spec && (spec.behavior_tests || spec.expected_behavior || spec.tests)) || [];
    var outcomes = (spec && spec.latest_outcomes) || {};
    var roll = (spec && spec.outcome_rollup) || null;

    if (!tests.length) {
      host.innerHTML = '<p class="empty-message">No gates declared for this study yet.</p>';
      return;
    }

    var passed = roll ? (roll.PASS || 0) : 0;
    var total = roll ? (roll.total || tests.length) : tests.length;
    if (!roll) {
      tests.forEach(function (t) {
        var o = t && t.name ? outcomes[t.name] : null;
        if (o && o.result === 'PASS') passed++;
      });
    }

    host.innerHTML = '<div style="font-weight:600">' + passed + '/' + total + ' gates passed</div>';
  }

  function loadTestsTab(spec) {
    var cfg = (spec && spec.tests) || {};
    var autoEl = document.getElementById('tests-auto-discover');
    var dsEl = document.getElementById('tests-data-source');
    if (autoEl) autoEl.textContent = String(cfg.auto_discover !== undefined ? cfg.auto_discover : true);
    if (dsEl) dsEl.textContent = cfg.data_source || 'latest_run';
    var summary = document.getElementById('tests-summary');
    if (!summary) return;

    // Single-sourced rollup from study_spec._latest_outcomes (spec.outcome_rollup)
    // so this header can't drift from the row pills / Conclusions rollup. Older
    // specs without it fall back to re-deriving from runs[].outcomes here.
    var roll = spec && spec.outcome_rollup;
    var passed = 0, failed = 0, skipped = 0, runRefs = 0;
    if (roll && typeof roll === 'object') {
      passed = roll.PASS || 0; failed = roll.FAIL || 0; skipped = roll.SKIP || 0;
      runRefs = roll.runs || 0;
    } else {
      (spec && spec.runs || []).forEach(function(r) {
        if (!r.outcomes) return;
        runRefs++;
        Object.keys(r.outcomes).forEach(function(tname) {
          var res = (r.outcomes[tname] || {}).result;
          if (res === 'PASS') passed++;
          else if (res === 'FAIL') failed++;
          else if (res === 'SKIP') skipped++;
        });
      });
    }

    if (passed + failed + skipped > 0) {
      var lastRun = (spec.runs || [])[spec.runs.length - 1] || {};
      summary.innerHTML =
        '<span class="ok">' + passed + ' passed</span>' +
        ' / <span class="fail">' + failed + ' failed</span>' +
        ' / <span class="skip">' + skipped + ' skipped</span>' +
        ' <span class="muted">(' + runRefs + ' run' + (runRefs === 1 ? '' : 's') + ' recorded; latest: ' +
        (lastRun.started_at || '?') + ')</span>';
    } else if (cfg.last_results) {
      var lr = cfg.last_results;
      summary.innerHTML =
        '<span class="ok">' + (lr.passed || 0) + ' passed</span>' +
        ' / <span class="fail">' + (lr.failed || 0) + ' failed</span>' +
        ' / <span class="skip">' + (lr.skipped || 0) + ' skipped</span>' +
        ' <span class="muted">(' + ((lr.duration_s || 0).toFixed(2)) + 's' +
        (lr.timestamp ? ', ' + lr.timestamp : '') + ')</span>';
    } else {
      summary.textContent = '— no test results yet — click "Run tests" to execute them or check the runs[] section in study.yaml';
    }

    // --- Per-test code-computed outcomes (spine B3) ---------------------
    // Render each test's LATEST code-computed outcome (measured_value /
    // result / operator / evaluated_by) connected to the run that produced
    // it and the pass_if band it was judged against — with the code-computed
    // value visually SEPARATE from any human-authored outcome and a
    // reconcile:divergent badge when they disagree. Follows the
    // param-enforcement-banner pattern (surfaced · connected · code-vs-authored).
    // Replaces the prior aggregate-only tally (now a one-line summary header).
    //
    // perTest[name] = {computed, authored, runIdent} — last run wins.
    var perTest = {};
    var cPassed = 0, cFailed = 0, cAgent = 0;
    var cAgree = 0, cDivergent = 0, cNoAuthored = 0;
    var anyComputed = false;
    (spec && spec.runs || []).forEach(function(r) {
      var co = r.computed_outcomes;
      if (!co || typeof co !== 'object' || Array.isArray(co)) return;
      var runIdent = r.run_id || r.name || '';
      Object.keys(co).forEach(function(tname) {
        if (tname === '_status') return;
        var entry = co[tname];
        if (!entry || typeof entry !== 'object') return;
        anyComputed = true;
        var authored = (r.outcomes && typeof r.outcomes === 'object') ? r.outcomes[tname] : null;
        perTest[tname] = {computed: entry, authored: authored || null, runIdent: runIdent};
        var evaluatedBy = entry.evaluated_by || '';
        if (evaluatedBy === 'code') {
          if (entry.result === 'PASS') cPassed++;
          else if (entry.result === 'FAIL') cFailed++;
          else cAgent++;
        } else {
          cAgent++;
        }
        var reconcile = entry.reconcile || '';
        if (reconcile === 'agree') cAgree++;
        else if (reconcile === 'divergent') cDivergent++;
        else if (reconcile === 'no_authored') cNoAuthored++;
      });
    });

    if (anyComputed) {
      // One-line summary header (kept; per-test detail now lives on each row).
      var compEl = document.getElementById('tests-computed-summary');
      if (!compEl) {
        compEl = document.createElement('div');
        compEl.id = 'tests-computed-summary';
        compEl.className = 'tests-summary muted';
        summary.insertAdjacentElement('afterend', compEl);
      }
      var cHtml =
        '<span class="muted">Code-computed: </span>' +
        '<span class="ok">' + cPassed + ' passed</span>' +
        ' / <span class="fail">' + cFailed + ' failed</span>' +
        ' / <span class="muted">' + cAgent + ' agent</span>';
      if (cDivergent > 0) {
        cHtml += '  <span class="fail" style="font-weight:600">' +
          '⚠ ' + escapeHtmlForTests(String(cDivergent)) +
          ' divergent from authored</span>';
      }
      var muted = [];
      if (cAgree > 0) muted.push(escapeHtmlForTests(String(cAgree)) + ' agree');
      if (cNoAuthored > 0) muted.push(escapeHtmlForTests(String(cNoAuthored)) + ' no_authored');
      if (muted.length) {
        cHtml += ' <span class="muted">(' + muted.join(', ') + ')</span>';
      }
      compEl.innerHTML = cHtml;

      // Per-test rows: inject a computed-outcome block into each test card.
      var testByName = {};
      (spec.behavior_tests || spec.expected_behavior || []).forEach(function(t) {
        if (t && t.name) testByName[t.name] = t;
      });
      Object.keys(perTest).forEach(function(tname) {
        var li = document.getElementById('bt-' + tname);
        if (!li) return;
        if (li.querySelector('.computed-outcome-row')) return;  // idempotent
        var passIf = (testByName[tname] || {}).pass_if || (testByName[tname] || {}).expect || null;
        li.insertAdjacentHTML('beforeend',
          _renderComputedOutcomeRow(tname, perTest[tname], passIf));
      });
    }
    _fillReportCardModules(spec);
    _bindReportCardRowExpanders();
  }

  // Render one test's code-computed outcome as a styled row: the measured
  // value + result + operator + evaluated_by in a CODE-COMPUTED chip, the
  // human-authored outcome in a SEPARATE AUTHORED chip, a prominent
  // reconcile:divergent badge when they disagree, a link to the run that
  // produced the value, and the pass_if band it was judged against.
  function _renderComputedOutcomeRow(tname, info, passIf) {
    var c = info.computed || {};
    var a = info.authored || null;
    var runIdent = info.runIdent || '';
    var e = escapeHtmlForTests;
    var divergent = (c.reconcile === 'divergent');

    var mv = c.measured_value;
    var mvStr;
    if (mv == null) mvStr = '—';
    else if (typeof mv === 'object') mvStr = JSON.stringify(mv);
    else mvStr = String(mv);
    if (mvStr.length > 220) mvStr = mvStr.slice(0, 217) + '…';

    // CODE-COMPUTED chip.
    var codeBits = [];
    if (c.result != null) codeBits.push('<strong>' + e(String(c.result)) + '</strong>');
    if (c.operator) codeBits.push('op <code>' + e(String(c.operator)) + '</code>');
    codeBits.push('by <code>' + e(String(c.evaluated_by || '?')) + '</code>');
    var codeChip =
      '<span class="outcome-chip outcome-chip-computed" ' +
      'style="display:inline-block;padding:4px 8px;border-radius:4px;background:#eef2ff;' +
      'border:1px solid #c7d2fe;color:#3730a3;font-size:0.82em">' +
      '<span class="muted" style="font-size:0.85em">code computed</span> ' +
      codeBits.join(' · ') + '</span>';

    // SEPARATE AUTHORED chip (only when an authored outcome exists).
    var authoredChip = '';
    if (a && (a.result != null)) {
      authoredChip =
        ' <span class="outcome-chip outcome-chip-authored" ' +
        'style="display:inline-block;padding:4px 8px;border-radius:4px;background:#f8fafc;' +
        'border:1px solid #e2e8f0;color:#475569;font-size:0.82em">' +
        '<span class="muted" style="font-size:0.85em">authored</span> ' +
        '<strong>' + e(String(a.result)) + '</strong></span>';
    }

    var divBadge = divergent
      ? ' <span class="reconcile-divergent" ' +
        'style="display:inline-block;padding:4px 8px;border-radius:4px;background:#fee2e2;' +
        'border:1px solid #fca5a5;color:#991b1b;font-weight:600;font-size:0.82em">' +
        '⚠ reconcile: divergent</span>'
      : '';

    var runLink = runIdent
      ? '<div class="muted small" style="margin-top:4px">from run ' +
        '<a href="#run-' + e(runIdent) + '" onclick="_setStudyTab(\'simulate\')" ' +
        'style="color:#3b82f6">' + e(runIdent) + '</a></div>'
      : '';

    var bandLine = passIf
      ? '<div class="pass_if-band muted small" style="margin-top:2px">judged against ' +
        '<code>pass_if: ' + e(JSON.stringify(passIf)) + '</code></div>'
      : '';

    var detail = (c.detail || c.reason)
      ? '<div class="muted small" style="margin-top:2px">' + e(String(c.detail || c.reason)) + '</div>'
      : '';

    return '<div class="computed-outcome-row" ' +
      'style="margin-top:6px;padding:8px 10px;background:#fff;border:1px solid ' +
      (divergent ? '#fca5a5' : '#e2e8f0') + ';border-radius:4px;font-size:0.85em">' +
      '<div><strong>measured_value:</strong> <code>' + e(mvStr) + '</code></div>' +
      '<div style="margin-top:4px;display:flex;gap:6px;flex-wrap:wrap;align-items:center">' +
      codeChip + authoredChip + divBadge + '</div>' +
      runLink + bandLine + detail +
      '</div>';
  }

  function escapeHtmlForTests(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function(c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
  }

  function renderTestResults(body) {
    var list = document.getElementById('tests-list');
    if (!list) return;
    list.innerHTML = '';
    if (body.note === 'no tests directory') {
      list.innerHTML = '<li class="placeholder">No tests/ directory found in this study.</li>';
      return;
    }
    var icons = {passed: '✅', failed: '❌', skipped: '⏭'};
    (body.tests || []).forEach(function(t) {
      var li = document.createElement('li');
      li.className = 'test-row test-' + t.outcome;
      var icon = icons[t.outcome] || '•';
      var tb = t.traceback
        ? '<details><summary>detail</summary><pre>' + escapeHtmlForTests(t.traceback) + '</pre></details>'
        : '';
      var dur = t.duration
        ? '<span class="test-duration">' + (t.duration).toFixed(3) + 's</span>' : '';
      li.innerHTML =
        '<span class="test-icon">' + icon + '</span>' +
        '<code class="test-nodeid">' + escapeHtmlForTests(t.nodeid) + '</code>' +
        dur + tb;
      list.appendChild(li);
    });
    var s = body.summary || {};
    var summary = document.getElementById('tests-summary');
    if (summary) {
      summary.innerHTML =
        '<span class="ok">' + (s.passed || 0) + ' passed</span>' +
        ' / <span class="fail">' + (s.failed || 0) + ' failed</span>' +
        ' / <span class="skip">' + (s.skipped || 0) + ' skipped</span>' +
        ' <span class="muted">(' + ((s.duration_s || 0).toFixed(2)) + 's)</span>' +
        (body.note ? ' <span class="muted" style="font-style:italic">— ' + escapeHtmlForTests(body.note) + '</span>' : '');
    }
  }

  function runStudyTests() {
    var btn = document.getElementById('run-tests-btn');
    if (!btn) return;
    btn.disabled = true;
    btn.textContent = 'Running…';
    fetch('/api/study-tests-run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({study: studyName()}),
    }).then(function(resp) {
      return resp.json().then(function(d) { return {status: resp.status, body: d}; });
    }).then(function(r) {
      if (r.status !== 200) {
        alert('Test run failed: ' + (r.body && r.body.error || r.status));
        return;
      }
      renderTestResults(r.body);
    }).catch(function(err) {
      alert('Test run error: ' + err);
    }).then(function() {
      btn.disabled = false;
      btn.textContent = 'Run tests';
    });
  }

  var runBtn = document.getElementById('run-tests-btn');
  if (runBtn) {
    runBtn.addEventListener('click', runStudyTests);
  }

  // ── Stage-3c: Tracked Feedback panel ─────────────────────────────────────
  // Renders open/addressed/dismissed items from window._study.feedback_tracked
  // into #feedback-tracked-panel (Overview tab).  Idempotent — skips if already
  // populated.  Escapes all user-supplied text.  Renders nothing when empty.
  // Pure render, no AI.
  function _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function(c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
  }

  function _renderFeedbackTrackedPanel() {
    var container = document.getElementById('feedback-tracked-panel');
    if (!container) return;               // anchor missing — template version mismatch
    if (container.dataset.rendered) return; // idempotent
    container.dataset.rendered = '1';

    var spec = window._study || {};
    var ft = spec.feedback_tracked;
    if (!ft || !ft.items || ft.items.length === 0) return;  // nothing to show

    var summary = ft.summary || {};
    var openCt  = summary.open      || 0;
    var addrCt  = summary.addressed || 0;
    var disCt   = summary.dismissed || 0;
    var total   = summary.total     || ft.items.length;

    // Status badge colours
    var badgeCss = {
      open:      'background:#fef3c7;color:#92400e;',
      addressed: 'background:#d1fae5;color:#065f46;',
      dismissed: 'background:#f1f5f9;color:#64748b;text-decoration:line-through;',
    };

    var itemsHtml = '';
    (ft.items || []).forEach(function(item) {
      var status   = item.status || 'open';
      var badgeStyle = badgeCss[status] || badgeCss.open;
      var badgeHtml  =
        '<span style="' + badgeStyle +
        'padding:1px 8px;border-radius:9999px;font-size:0.78em;' +
        'font-family:ui-monospace,monospace;margin-right:6px">' +
        _esc(status) + '</span>';

      // G7: honest attribution — item.author is feedback_tracking's recorded
      // raiser (viva_superpowers.feedback_tracking.study_feedback_tracked);
      // item.ts is its timestamp. Never blank: attributionText renders the
      // literal "unattributed" token when author is absent, with a human/
      // agent glyph on whatever actor IS recorded (never guessed from the
      // bare name — see the actorKind comment above).
      var authorWhen = (item.ts || '').replace('T', ' ').replace('Z', ' UTC');
      var metaHtml =
        '<span class="muted" style="font-size:0.82em">' +
        '<span class="actor-glyph" title="actor kind: ' + _esc(actorKind(item.author)) + '">' +
        actorGlyph(item.author) + '</span> ' +
        _esc(attributionText(item.author, authorWhen)) +
        ' · <code style="font-size:0.9em">' + _esc(item.section || '') + '</code>' +
        '</span>';

      var textHtml = '<p style="margin:4px 0;font-size:0.92em">' + _esc(item.text || '') + '</p>';

      var responseHtml = '';
      if (status === 'addressed' && item.response) {
        // G7: honest attribution for the responder (item.responded_by /
        // .responded_at, same source). Always rendered — "unattributed" when
        // no responder is recorded, never silently omitted.
        responseHtml =
          '<div style="margin:6px 0 0 0;padding:8px 12px;background:#f0fdf4;' +
          'border-left:3px solid #10b981;border-radius:4px;font-size:0.88em">' +
          '<strong style="font-size:0.85em;color:#065f46">Response — ' +
          '<span class="actor-glyph" title="actor kind: ' + _esc(actorKind(item.responded_by)) + '">' +
          actorGlyph(item.responded_by) + '</span> ' +
          _esc(attributionText(item.responded_by, item.responded_at)) +
          ':</strong>' +
          '<pre style="white-space:pre-wrap;margin:4px 0 0 0;font-family:inherit;' +
          'font-size:0.92em;color:#374151">' + _esc(item.response) + '</pre>' +
          '</div>';
      }

      itemsHtml +=
        '<div style="padding:10px 14px;border-bottom:1px solid #f1f5f9">' +
        '<div style="display:flex;align-items:flex-start;gap:6px;flex-wrap:wrap;margin-bottom:4px">' +
        badgeHtml + metaHtml +
        '</div>' +
        textHtml +
        responseHtml +
        '</div>';
    });

    var summaryHtml =
      '<span style="font-size:0.9em">' +
      '<span style="color:#92400e">' + openCt + ' open</span>' +
      ' / <span style="color:#065f46">' + addrCt + ' addressed</span>' +
      ' / <span style="color:#64748b">' + disCt + ' dismissed</span>' +
      ' <span class="muted">(' + total + ' total)</span>' +
      '</span>';

    // ── SP3b: proposed feedback → action surface (read-only render + Apply) ──
    // The dashboard NEVER computes the action — it renders the pbg-supplied
    // feedback_actions (kind + proposed_text + open/applied status) and applies
    // an open action by POSTing item_id to /api/feedback-apply-action.
    var actionsSectionHtml = _renderFeedbackActionsSection();

    container.innerHTML =
      '<div class="overview-section" style="margin-top:18px">' +
      '<h2 class="overview-label">Expert Feedback</h2>' +
      '<div style="margin-bottom:10px">' + summaryHtml + '</div>' +
      '<div style="border:1px solid #e2e8f0;border-radius:6px;overflow:hidden">' +
      itemsHtml +
      '</div>' +
      actionsSectionHtml +
      '</div>';

    _wireFeedbackApplyButtons(container);
  }

  // Build the "Proposed Actions" sub-panel from window._study.feedback_actions.
  // Each item that carries an action shows its kind + proposed_text + an
  // open/applied badge; open actions get an Apply button. Returns '' when there
  // are no actions to show. Pure render — escapes all text.
  function _actionBadgeCss(status) {
    return ({
      open:      'background:#fef3c7;color:#92400e;',
      applied:   'background:#d1fae5;color:#065f46;',
      dismissed: 'background:#f1f5f9;color:#64748b;text-decoration:line-through;',
    })[status] || 'background:#fef3c7;color:#92400e;';
  }

  function _renderFeedbackActionsSection() {
    var spec = window._study || {};
    var fa = spec.feedback_actions;
    if (!fa || !fa.items || fa.items.length === 0) return '';
    var withActions = (fa.items || []).filter(function(it) { return it && it.action; });
    if (withActions.length === 0) return '';

    var rows = '';
    withActions.forEach(function(it) {
      var action = it.action || {};
      var status = it.status || 'open';
      var badge =
        '<span style="' + _actionBadgeCss(status) +
        'padding:1px 8px;border-radius:9999px;font-size:0.78em;' +
        'font-family:ui-monospace,monospace;margin-right:6px">' +
        _esc(status) + '</span>';
      var kindChip =
        '<code style="font-size:0.82em;background:#eef2ff;color:#3730a3;' +
        'padding:1px 6px;border-radius:4px">' + _esc(action.kind || '') + '</code>';
      var target = action.target_finding
        ? ' <span class="muted" style="font-size:0.82em">→ ' + _esc(action.target_finding) + '</span>'
        : '';
      var applyBtn = (status === 'open')
        ? '<button type="button" class="feedback-apply-btn" data-item-id="' +
          _esc(it.item_id) + '" style="margin-left:auto;padding:2px 10px;' +
          'font-size:0.82em;border:1px solid #6366f1;background:#eef2ff;' +
          'color:#3730a3;border-radius:4px;cursor:pointer">Apply</button>'
        : '';
      rows +=
        '<div style="padding:8px 14px;border-bottom:1px solid #f1f5f9">' +
        '<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">' +
        badge + kindChip + target + applyBtn +
        '</div>' +
        '<p style="margin:4px 0 0 0;font-size:0.9em;color:#374151">' +
        _esc(action.proposed_text || '') + '</p>' +
        '<p class="muted" style="margin:2px 0 0 0;font-size:0.78em">' +
        _esc((it.text || '').slice(0, 140)) + '</p>' +
        '</div>';
    });

    return (
      '<div style="margin-top:12px">' +
      '<h3 style="font-size:0.9em;color:#475569;margin:0 0 6px 0">Proposed Actions</h3>' +
      '<div style="border:1px solid #e2e8f0;border-radius:6px;overflow:hidden">' +
      rows +
      '</div>' +
      '</div>'
    );
  }

  function _wireFeedbackApplyButtons(container) {
    var btns = container.querySelectorAll('.feedback-apply-btn');
    Array.prototype.forEach.call(btns, function(btn) {
      btn.addEventListener('click', function() {
        var itemId = btn.dataset.itemId;
        if (!itemId) return;
        btn.disabled = true;
        btn.textContent = 'Applying…';
        fetch('/api/feedback-apply-action', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({item_id: itemId}),
        }).then(function(r) { return r.json().then(function(j) { return {ok: r.ok, j: j}; }); })
          .then(function(res) {
            if (res.ok && (res.j.applied || res.j.already_applied)) {
              btn.textContent = 'Applied';
              btn.style.borderColor = '#10b981';
              btn.style.background = '#d1fae5';
              btn.style.color = '#065f46';
            } else {
              btn.disabled = false;
              btn.textContent = 'Apply';
              alert('Apply failed: ' + (res.j && res.j.error || 'unknown error'));
            }
          })
          .catch(function(e) {
            btn.disabled = false;
            btn.textContent = 'Apply';
            alert('Apply failed: ' + (e && e.message || e));
          });
      });
    });
  }

  // ── DataSource bootstrap (client-fetch seam, sub-project #1) ─────────────
  // Populate window._study via a fetch when the Jinja embed is absent.
  // The renderers (loadTestsTab, _renderFeedbackTrackedPanel,
  // etc.) are unchanged — they still read window._study.  Only acquisition changes.

  function _showStudyLoadError(e) {
    var el = document.getElementById('study-root') || document.body;
    el.innerHTML =
      '<div style="padding:2rem;color:#dc2626">' +
      'Could not load study data: ' + String(e && e.message || e) +
      '</div>';
  }

  async function _bootstrapStudy() {
    if (!window._study && window.DataSource && window._studyName) {
      try {
        window._study = await window.DataSource.loadStudy(window._studyName);
      } catch (e) {
        _showStudyLoadError(e);
        return false;
      }
    }
    return !!window._study;
  }

  function _runStudyInit() {
    // All renderers that need window._study to be populated.
    _renderFeedbackTrackedPanel();
    _renderReadinessPanel();
    _populateConclusionVerdictBadges();
    // Open the Overview tab on load — unless a ?tab=<kind> deep-link asks
    // for a specific tab. Needs-attention items link here with
    // ?tab=conclusions so a click lands on the verdict that triggered the alert.
    var _tab = 'overview';
    try {
      var _q = new URLSearchParams(window.location.search).get('tab');
      if (_q && document.querySelector('.study-pillar[data-kind="' + _q + '"]')) _tab = _q;
    } catch (_e) { /* no URLSearchParams — keep overview */ }
    _setStudyTab(_tab);
  }

  // ── C2 — conclusion verdicts: read precomputed block from window._study.derived ─
  // Computed server-side by study_derivations.derived_block(). Rendering unchanged.
  function _populateConclusionVerdictBadges() {
    var badges = document.querySelectorAll('[data-verdict-track]');
    if (!badges.length) return;
    var cv = ((window._study || {}).derived || {}).conclusion_verdicts || {
      biological_validation: { result: 'PENDING' },
      regression_compatibility: { result: 'PENDING' },
      explanatory_gain: { result: 'GAP' }
    };
    var colors = {
      PASS: ['#dcfce7', '#166534'], PARTIAL: ['#fef3c7', '#92400e'],
      FAIL: ['#fee2e2', '#991b1b'], GAP: ['#f1f5f9', '#475569'], PENDING: ['#f1f5f9', '#475569']
    };
    badges.forEach(function(el) {
      var track = el.getAttribute('data-verdict-track');
      var res = (cv[track] || {}).result || 'PENDING';
      var col = colors[res] || colors.PENDING;
      el.textContent = res;
      el.style.background = col[0];
      el.style.color = col[1];
    });
  }


  // Memoized GET /api/report-lint — sole consumer is the readiness panel
  // below (fetched once, cached for the page's lifetime).
  var _reportLintPromise = null;
  function _reportLint() {
    if (!_reportLintPromise) {
      _reportLintPromise = fetch('/api/report-lint')
        .then(function (r) { return r.ok ? r.json() : { findings: [] }; })
        .catch(function () { return { findings: [] }; });
    }
    return _reportLintPromise;
  }

  // Readiness panel: inline "⚠ N readiness gaps" / "✓ ready" link in the
  // header status row, click-to-expand. Fetches the deterministic report
  // linter (GET /api/report-lint), filters to THIS study, and buckets by
  // severity. AI-free — pure deterministic output, connected to its source
  // (the linter) and labeled as such. Sole consumer of _reportLint.
  function _renderReadinessPanel() {
    var container = document.getElementById('readiness-panel');
    if (!container || container.dataset.rendered) return;
    container.dataset.rendered = '1';
    var slug = container.getAttribute('data-slug') || studyName() || '';
    _reportLint()
      .then(function (j) {
        var findings = (j.findings || []).filter(function (f) {
          return (f.study || '') === slug;
        });
        var sev = { error: 0, warning: 0, info: 0 };
        findings.forEach(function (f) {
          var s = f.severity || 'info';
          if (sev[s] != null) sev[s]++; else sev.info++;
        });
        var gaps = sev.error + sev.warning;
        var head, col;
        if (!findings.length) { head = '✓ ready'; col = '#166534'; }
        else if (gaps) { head = '⚠ ' + gaps + ' readiness gap' + (gaps === 1 ? '' : 's'); col = '#92400e'; }
        else { head = 'ℹ ' + sev.info + ' note' + (sev.info === 1 ? '' : 's'); col = '#1e40af'; }

        if (!findings.length) {
          container.innerHTML = '<span class="readiness-inline" style="font-size:0.85em;color:' + col + '" '
            + 'title="code-computed by the report linter (deterministic)">' + head + '</span>';
          return;
        }
        // Compact link; click toggles the gap breakdown below the status row.
        var byCheck = {};
        findings.forEach(function (f) { var c = f.check || 'other'; (byCheck[c] = byCheck[c] || []).push(f); });
        var checks = Object.keys(byCheck).sort(function (a, b) { return byCheck[b].length - byCheck[a].length; });
        var breakdown = checks.map(function (c) { return byCheck[c].length + '× ' + _esc(c); }).join(' &nbsp;·&nbsp; ');
        var groups = checks.map(function (c) {
          var items = byCheck[c].map(function (f) {
            var s = f.severity || 'info';
            var dot = s === 'error' ? '#dc2626' : (s === 'warning' ? '#f59e0b' : '#3b82f6');
            return '<li style="margin-top:3px"><span style="color:' + dot + ';font-weight:700">●</span> ' + _esc(f.message || '') + '</li>';
          }).join('');
          return '<div style="margin-top:9px"><code>' + _esc(c) + '</code> '
            + '<span class="muted" style="font-size:0.82em">(' + byCheck[c].length + ')</span>'
            + '<ul style="margin:3px 0 0 18px;font-size:0.9em;padding:0">' + items + '</ul></div>';
        }).join('');
        container.innerHTML =
          '<details class="readiness-inline">'
          + '<summary style="font-size:0.85em;color:' + col + ';cursor:pointer;list-style:none;outline:none" '
          + 'title="code-computed by the report linter (deterministic) — click to expand">' + head + '</summary>'
          + '<div style="margin-top:6px;padding:8px 12px;border:1px solid #e2e8f0;border-radius:6px;font-size:0.85em" class="readiness-inline-body">'
          + '<div class="muted" style="font-size:0.9em">' + breakdown + '</div>'
          + groups
          + '</div>'
          + '</details>';
      })
      .catch(function () { container.dataset.rendered = ''; });
  }

  // Entry point: fetch the spec if needed, then run init.
  (async function () {
    if (await _bootstrapStudy()) { _runStudyInit(); }
  })();

  // Embed-viz cards (Fable §4.5, Task V3): unlike the native gallery / chart
  // sources (async, wired via _wireFigureRunLinks after their fetch lands),
  // embed cards are server-rendered directly into the template — present in
  // the DOM as soon as this script (loaded at the end of <body>) runs. Wire
  // their run-links once here with the same delegated listener rather than
  // duplicate the click handling.
  _wireFigureRunLinks(document.getElementById('visualize-section'));

  // --- URL hash → Runs tab + scroll to run row ---
  // Links from the Simulations DB (walkthrough.js) land at
  //   /studies/<slug>#run-<runId>
  // Switch to the Runs tab and scroll the target row into view.
  function _applyRunHash() {
    var h = (window.location.hash || '');
    if (h.indexOf('#run-') === 0 || h === '#runs') {
      _setStudyTab('simulate');
      if (h.indexOf('#run-') === 0) {
        var el = document.getElementById(h.slice(1));  // id="run-<runId>"
        if (el && el.scrollIntoView) { try { el.scrollIntoView({block: 'center'}); el.style.outline = '2px solid #2b6cb0'; } catch (e) {} }
      }
    }
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _applyRunHash);
  } else {
    _applyRunHash();
  }
  window.addEventListener('hashchange', _applyRunHash);

})();
