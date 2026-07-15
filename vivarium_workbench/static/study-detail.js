// study-detail.js — wires the six-card Study Detail page to /api/study-* routes.
(function() {
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

  // Map a panel kind -> its pillar by reading the member button's data-pillar
  // (DOM is the source of truth, so v3/v4 conditional tab sets are always correct).
  function _pillarForKind(kind) {
    var btn = document.querySelector('.study-tab[data-kind="' + kind + '"]');
    return btn ? (btn.dataset.pillar || '') : '';
  }

  function _showPillarSubnav(pillar) {
    // pillar buttons
    document.querySelectorAll('.study-pillar').forEach(function (b) {
      b.classList.toggle('active', b.dataset.pillar === pillar);
    });
    // member buttons: only the active pillar's are visible
    var members = 0;
    document.querySelectorAll('#study-subnav .study-tab').forEach(function (b) {
      var mine = b.dataset.pillar === pillar;
      b.style.display = mine ? '' : 'none';
      if (mine) members++;
    });
    // Single-member pillar (e.g. Compose) → hide the sub-nav row.
    var subnav = document.getElementById('study-subnav');
    if (subnav) subnav.style.display = (members <= 1) ? 'none' : '';
  }

  function _setStudyTab(kind) {
    var pillar = _pillarForKind(kind);
    if (pillar) _showPillarSubnav(pillar);
    document.querySelectorAll('.study-tab').forEach(function (b) {
      b.classList.toggle('active', b.dataset.kind === kind);
    });
    document.querySelectorAll('.study-tab-panel').forEach(function (p) {
      p.classList.toggle('active', p.dataset.kind === kind);
    });
    if (kind === 'tests') { loadTestsTab(window._study); }
    if (kind === 'visualize') { _loadReadouts(); _loadCharts('viz-charts-panel'); }
    if (kind === 'data') { _loadAnalysisOutputs(); }
    if (kind === 'simulate') { _renderReproduceCard(); }
  }
  window._setStudyTab = _setStudyTab;

  function _renderReproduceCard() {
    var host = document.getElementById('reproduce-card');
    if (!host) return;
    var rc = (window._study && window._study.run_commands) || null;
    if (!rc) { host.style.display = 'none'; return; }
    var e = escapeHtmlForTests;
    function chip(cmd) {
      return '<code style="display:inline-block;padding:2px 6px;background:#fff;'
        + 'border:1px solid #e2e8f0;border-radius:3px">' + e(cmd) + '</code>'
        + ' <button class="cli-copy" data-cmd="' + e(cmd)
        + '" style="font-size:0.8em;cursor:pointer">copy</button>';
    }
    var html = '<strong>Reproduce / run (CLI)</strong><br/>'
      + '<div style="margin-top:4px">' + chip(rc.baseline) + '</div>';
    (rc.variants || []).forEach(function (v) {
      html += '<div style="margin-top:3px">' + chip(v.cmd) + '</div>';
    });
    host.innerHTML = html;
    host.querySelectorAll('.cli-copy').forEach(function (b) {
      b.addEventListener('click', function () {
        navigator.clipboard && navigator.clipboard.writeText(b.dataset.cmd);
      });
    });
  }

  // Click a pillar -> reveal its member sub-nav and open its first member panel.
  function _setStudyPillar(pillar) {
    _showPillarSubnav(pillar);
    var first = document.querySelector('#study-subnav .study-tab[data-pillar="' + pillar + '"]');
    if (first) _setStudyTab(first.dataset.kind);
  }
  window._setStudyPillar = _setStudyPillar;

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
    var head = '<table class="observables-table" style="width:100%; border-collapse: collapse;"><thead><tr>'
      + ['Name', 'Store path', 'Emitted?', 'Indexed by', 'Units', 'Description'].map(function(h) {
          return '<th style="text-align:left; padding:6px; border-bottom:1px solid #e2e8f0;">' + h + '</th>';
        }).join('') + '</tr></thead><tbody>';
    var body = (j.rows || []).map(function(o) {
      var idx = o.index_by ? '<code style="font-size:0.85em;">' + e(o.index_by.type) + '=' + e(o.index_by.value) + '</code>'
                           : '<span class="muted">—</span>';
      return '<tr style="border-bottom:1px solid #f1f5f9;" data-readout="' + e(o.name) + '">'
        + '<td style="padding:6px; vertical-align:top;"><code>' + e(o.name) + '</code></td>'
        + '<td style="padding:6px; vertical-align:top;"><code style="font-size:0.85em;">' + e(o.store_path || '') + '</code></td>'
        + '<td style="padding:6px; vertical-align:top; font-size:0.75em;">' + _emitStatusBadge(o.emit_status) + '</td>'
        + '<td style="padding:6px; vertical-align:top;">' + idx + '</td>'
        + '<td style="padding:6px; vertical-align:top; font-size:0.9em;">' + e(o.units || '') + '</td>'
        + '<td style="padding:6px; vertical-align:top; max-width:380px; font-size:0.9em;">' + e(o.description || '') + '</td>'
        + '</tr>';
    }).join('');
    return note + head + body + '</tbody></table>';
  }

  // ── Charts panel: inline SVGs from /api/study-charts ─────────────────────
  // Lives in the Visualizations tab only. Memoized per panel id.
  // Merges two sources returned by the server:
  //   live   — generated from runs.db at request time
  //   static — pre-rendered SVGs under studies/<name>/charts/
  var _chartsLoadedFor = {};
  function _renderChartCard(c) {
    var title = c.title
      ? '<div class="chart-title">' + c.title + '</div>'
      : '';
    // SVG records carry inline markup in c.svg; PNG/GIF records carry a
    // self-contained data-URI in c.img (rendered as <img>).
    var media = c.img
      ? '<img class="chart-img" src="' + c.img + '" alt="' + (c.key || 'chart') + '" loading="lazy">'
      : (c.svg || '');
    return '<div class="chart-card">' + title + media +
           '<div class="chart-caption">' + (c.caption || '') + '</div></div>';
  }
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
              ? '<p class="muted" style="margin:0">No <code>runs.db</code> and no static charts under <code>studies/' + studyName() + '/charts/</code>.</p>'
              : '<p class="muted" style="margin:0">No chart data available for this study.</p>';
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
      })
      .catch(function(e) {
        panel.innerHTML = '<p class="muted" style="color:#dc2626">Chart load failed: ' + (e && e.message || e) + '</p>';
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

  // critique #19 — a failing study should seed a DIAGNOSTIC child. Recognises
  // both the normalized result (FAIL / PARTIAL) and the raw roll-up verdict
  // (failed / needs_calibration).
  function _isFailingVerdict(s) {
    s = s || {};
    var r = String(((s.computed_gate_verdict || {}).result) || s.gate_status || '')
      .trim().toLowerCase();
    return r === 'fail' || r === 'failed' || r === 'partial' || r === 'needs_calibration';
  }

  // ── Seed a new study from a finding's next_action ────────────────────────
  // Delegates to the shared pbg seed mechanism via {parent, finding_id};
  // the finding seeds STANDALONE (no pre-existing followup proposal needed).
  // An optional studyType (e.g. 'diagnostic' when the parent failed, critique
  // #19) is threaded through to the pbg writer so the child is typed.
  function _seedFromFinding(parentStudyName, findingId, studyType) {
    var diag = studyType === 'diagnostic';
    var msg = diag
      ? 'Seed a DIAGNOSTIC study from this finding?\n\nThe parent study did not pass, so a new study_type: diagnostic study.yaml will be created under studies/<new-name>/ to diagnose the failure, stamped with the parent + failing-test lineage.'
      : 'Seed a new study from this finding?\n\nA new study.yaml will be created under studies/<new-name>/ pre-populated from the finding\'s next_action, and the finding will be stamped with the seeded study.';
    if (!confirm(msg)) {
      return;
    }
    var body = {parent: parentStudyName, finding_id: findingId};
    if (studyType) body.study_type = studyType;
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
  window._seedFromFinding = _seedFromFinding;

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
  document.querySelectorAll('[data-narrative-path]').forEach(function(el) {
    var tag = (el.tagName || '').toLowerCase();
    // Selects save on change (immediate, no need to wait for blur). Text
    // inputs + textareas save on blur so the user can type without round-
    // tripping per keystroke.
    var evt = (tag === 'select') ? 'change' : 'blur';
    el.addEventListener(evt, function() { _saveNarrative(el); });
  });

  var statusSel = document.getElementById('status-select');
  if (statusSel) {
    statusSel.addEventListener('change', function() {
      _saveOverviewField('status', statusSel.value);
    });
  }


  // --- Helpers: attach a click handler to every button matching a CSS class ---
  function bindAll(selector, handler) {
    document.querySelectorAll(selector).forEach(function(btn) {
      btn.addEventListener('click', function(ev) { handler(btn, ev); });
    });
  }

  function studyName() { return window._studyName; }

  // Fetch the param schema for a composite and render an input form.
  // currentOverrides: {} or existing overrides (for edit flow).
  // Returns a Promise<{collect, ok}>: collect() reads back the current input
  // values and returns an overrides dict; ok=false if fetch failed (containerEl
  // shows the error message in that case).
  function renderParamForm(containerEl, specId, currentOverrides) {
    var overridesJson = encodeURIComponent(JSON.stringify(currentOverrides || {}));
    return fetch('/api/composite-resolve?id=' +
                 encodeURIComponent(specId) + '&overrides=' + overridesJson)
      .then(function(r) { return r.json().then(function(b) { return {status: r.status, body: b}; }); })
      .then(function(r) {
        if (r.status !== 200) {
          containerEl.innerHTML = '<p class="error">Could not resolve composite: ' +
            (r.body && r.body.error || r.status) + '</p>';
          return {collect: function() { return {}; }, ok: false};
        }
        var params = r.body.parameters || {};
        containerEl.innerHTML = '';
        var inputs = {};
        Object.keys(params).forEach(function(k) {
          var def = params[k] || {};
          var type = def.type || 'string';
          var current = (currentOverrides && k in currentOverrides) ? currentOverrides[k] : def.default;
          var row = document.createElement('div');
          row.className = 'param-row';
          var label = document.createElement('label');
          label.className = 'param-label';
          var nameSpan = document.createElement('span');
          nameSpan.innerHTML = '<code>' + k + '</code> <span class="muted">(' + type + ')</span>';
          var input = document.createElement('input');
          input.className = 'param-input';
          input.dataset.paramKey = k;
          input.dataset.paramType = type;
          if (type === 'integer' || type === 'number' || type === 'float') {
            input.type = 'number';
            input.step = (type === 'integer') ? '1' : 'any';
          } else if (type === 'boolean') {
            input.type = 'checkbox';
            if (current === true) input.checked = true;
          } else {
            input.type = 'text';
          }
          if (input.type !== 'checkbox' && current !== undefined && current !== null) {
            input.value = current;
          }
          label.appendChild(nameSpan);
          label.appendChild(input);
          row.appendChild(label);
          if (def.description) {
            var desc = document.createElement('div');
            desc.className = 'param-desc muted';
            desc.textContent = def.description;
            row.appendChild(desc);
          }
          containerEl.appendChild(row);
          inputs[k] = input;
        });
        var collect = function() {
          var out = {};
          Object.keys(inputs).forEach(function(k) {
            var el = inputs[k];
            var t = el.dataset.paramType;
            if (t === 'boolean') out[k] = !!el.checked;
            else if (t === 'integer') out[k] = el.value === '' ? null : parseInt(el.value, 10);
            else if (t === 'number' || t === 'float') out[k] = el.value === '' ? null : parseFloat(el.value);
            else out[k] = el.value;
          });
          // Remove null/empty entries (don't send them as overrides).
          Object.keys(out).forEach(function(k) {
            if (out[k] === null || out[k] === '' || out[k] === undefined) delete out[k];
          });
          return out;
        };
        return {collect: collect, ok: true};
      });
  }
  // Not exposed on window; consumed internally by the Variants tab.

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
    window.location = '/api/study-export?study=' + encodeURIComponent(studyName());
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
  bindAll('.btn-run-baseline', function(btn) {
    var entryName = btn.dataset.baselineName;
    api('POST', '/api/study-run-baseline', {
      study: studyName(), composite: entryName
    }).then(function(r) {
      if (r.status === 200) location.reload();
      else alert('Run failed: ' + (r.body && r.body.error || r.status));
    });
  });

  bindAll('.btn-baseline-remove', function(btn) {
    var entryName = btn.dataset.baselineName;
    if (!confirm('Remove baseline composite "' + entryName + '"?')) return;
    api('POST', '/api/study-baseline-remove', {
      study: studyName(), name: entryName
    }).then(function(r) {
      if (r.status === 200) location.reload();
      else if (r.status === 409 && r.body.dependents) {
        alert('Cannot remove: variants depend on this composite (' +
              r.body.dependents.join(', ') + '). Delete those variants first.');
      } else {
        alert('Remove failed: ' + (r.body && r.body.error || r.status));
      }
    });
  });

  function _submitBaselineAdd(ev) {
    ev.preventDefault();
    var form = ev.target;
    var params = {};
    var raw = form.params.value.trim();
    if (raw) {
      try { params = JSON.parse(raw); }
      catch (e) { alert('Params must be valid JSON.'); return false; }
    }
    api('POST', '/api/study-baseline-add', {
      study: studyName(),
      name: form.name.value.trim(),
      composite: form.composite.value.trim(),
      params: params
    }).then(function(r) {
      if (r.status === 200) location.reload();
      else alert('Add failed: ' + (r.body && r.body.error || r.status));
    });
    return false;
  }
  window._submitBaselineAdd = _submitBaselineAdd;

  // ===== Variants tab handlers =====

  // Variant add — base-composite dropdown changes trigger param-form render.
  var _currentVariantAddCollect = null;
  function _onBaseCompositeChange(selectEl) {
    var opt = selectEl.options[selectEl.selectedIndex];
    var specId = opt.dataset.compositeId || '';
    var container = document.getElementById('variant-new-params');
    if (!specId) {
      container.innerHTML = '<p class="muted">Pick a base composite to see its parameters.</p>';
      _currentVariantAddCollect = null;
      return;
    }
    container.innerHTML = '<p class="muted">Loading parameters…</p>';
    renderParamForm(container, specId, {}).then(function(result) {
      _currentVariantAddCollect = result.collect;
    });
  }
  window._onBaseCompositeChange = _onBaseCompositeChange;

  function _submitVariantAdd(ev) {
    ev.preventDefault();
    var form = ev.target;
    var name = form.name.value.trim();
    var baseComposite = form.base_composite.value;
    if (!name || !baseComposite) { alert('Name and base composite are required.'); return false; }
    var overrides = _currentVariantAddCollect ? _currentVariantAddCollect() : {};
    api('POST', '/api/study-variant-add', {
      study: studyName(), name: name, base_composite: baseComposite,
      parameter_overrides: overrides
    }).then(function(r) {
      if (r.status === 200) location.reload();
      else alert('Add variant failed: ' + (r.body && r.body.error || r.status));
    });
    return false;
  }
  window._submitVariantAdd = _submitVariantAdd;

  // Variant edit — populate dialog, render param form with current overrides, then save.
  var _currentVariantEditCollect = null;
  bindAll('.btn-variant-edit', function(btn) {
    var variantName = btn.dataset.variantName;
    var variant = (window._study.variants || []).filter(function(v) { return v.name === variantName; })[0];
    if (!variant) { alert('Variant not found in local spec.'); return; }
    var baseEntry = (window._study.baseline || []).filter(function(b) { return b.name === variant.base_composite; })[0];
    if (!baseEntry) { alert('Variant references a base composite that no longer exists.'); return; }
    document.getElementById('variant-edit-name').textContent = variantName;
    var container = document.getElementById('variant-edit-params');
    container.innerHTML = '<p class="muted">Loading parameters…</p>';
    renderParamForm(container, baseEntry.composite, variant.parameter_overrides || {}).then(function(result) {
      _currentVariantEditCollect = result.collect;
      document.getElementById('variant-edit-dialog').dataset.variantName = variantName;
      document.getElementById('variant-edit-dialog').showModal();
    });
  });

  function _submitVariantEdit(ev) {
    ev.preventDefault();
    var dialog = document.getElementById('variant-edit-dialog');
    var variantName = dialog.dataset.variantName;
    var overrides = _currentVariantEditCollect ? _currentVariantEditCollect() : {};
    api('POST', '/api/study-variant-set-params', {
      study: studyName(), variant: variantName, parameter_overrides: overrides
    }).then(function(r) {
      if (r.status === 200) location.reload();
      else alert('Save failed: ' + (r.body && r.body.error || r.status));
    });
    return false;
  }
  window._submitVariantEdit = _submitVariantEdit;

  bindAll('.btn-variant-delete', function(btn) {
    var variantName = btn.dataset.variantName;
    if (!confirm('Delete variant "' + variantName + '"?')) return;
    api('POST', '/api/study-variant-delete', {
      study: studyName(), variant: variantName
    }).then(function(r) {
      if (r.status === 200) location.reload();
      else alert('Delete failed: ' + (r.body && r.body.error || r.status));
    });
  });

  bindAll('.btn-variant-run', function(btn) {
    var variantName = btn.dataset.variantName;
    api('POST', '/api/study-run-variant', {
      study: studyName(), variant: variantName
    }).then(function(r) {
      if (r.status === 200) location.reload();
      else alert('Run failed: ' + (r.body && r.body.error || r.status));
    });
  });

  // ===== Interventions tab handlers =====

  function _submitInterventionAdd(ev) {
    ev.preventDefault();
    var form = ev.target;
    api('POST', '/api/study-intervention-add', {
      study: studyName(),
      name: form.name.value.trim(),
      description: form.description.value
    }).then(function(r) {
      if (r.status === 200) location.reload();
      else alert('Add failed: ' + (r.body && r.body.error || r.status));
    });
    return false;
  }
  window._submitInterventionAdd = _submitInterventionAdd;

  bindAll('.btn-intervention-delete', function(btn) {
    var name = btn.dataset.interventionName;
    if (!confirm('Delete intervention "' + name + '"?')) return;
    api('POST', '/api/study-intervention-delete', {
      study: studyName(), name: name
    }).then(function(r) {
      if (r.status === 200) location.reload();
      else alert('Delete failed: ' + (r.body && r.body.error || r.status));
    });
  });

  // Inline-edit intervention descriptions. Uses a click-to-textarea pattern
  // parallel to makeEditable but POSTs to the intervention-update endpoint.
  document.querySelectorAll('[data-editable-intervention]').forEach(function(el) {
    el.addEventListener('click', function() {
      var name = el.dataset.editableIntervention;
      var current = el.textContent;
      var t = document.createElement('textarea');
      t.value = current;
      t.style.width = '100%';
      t.rows = 3;
      el.replaceWith(t);
      t.focus();
      t.addEventListener('blur', function() {
        api('POST', '/api/study-intervention-update', {
          study: studyName(), name: name, description: t.value
        }).then(function(r) {
          if (r.status === 200) location.reload();
          else { alert('Update failed: ' + (r.body && r.body.error || r.status)); }
        });
      });
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
  function _fillReportCardModules(spec) {
    var urls = (spec && spec.report_card_urls) || {};
    var mounts = document.querySelectorAll('.report-card-mount');
    Array.prototype.forEach.call(mounts, function(mount) {
      if (mount.dataset.filled) return;          // idempotent
      var card = mount.getAttribute('data-card');
      var rc = urls[card];
      if (!rc || !rc.url) {
        mount.innerHTML = '<div class="muted" style="padding:8px">report card '
          + escapeHtmlForTests(String(card)) + ' not generated yet — run the comparison.</div>';
        mount.dataset.filled = '1';
        return;
      }
      mount.innerHTML =
        '<iframe class="viz-embed" src="' + escapeHtmlForTests(rc.url) + '" loading="lazy" '
        + 'style="width:100%;height:520px;border:1px solid #2a313c;border-radius:8px"></iframe>';
      // recolour this test's verdict pill
      var li = mount.closest('.expected-behavior-item');
      var pill = li && li.querySelector('.report-card-verdict');
      var v = (rc.verdict || 'ungraded');
      var p = _RC_PILL[v] || _RC_PILL.ungraded;
      if (pill) { pill.style.background = p[0]; pill.style.color = p[1]; pill.textContent = p[2]; }
      mount.dataset.filled = '1';
    });
  }

  function loadTestsTab(spec) {
    var cfg = (spec && spec.tests) || {};
    var autoEl = document.getElementById('tests-auto-discover');
    var dsEl = document.getElementById('tests-data-source');
    if (autoEl) autoEl.textContent = String(cfg.auto_discover !== undefined ? cfg.auto_discover : true);
    if (dsEl) dsEl.textContent = cfg.data_source || 'latest_run';
    var summary = document.getElementById('tests-summary');
    if (!summary) return;

    // Prefer aggregated outcomes from runs[].outcomes (the v3-shape result
    // recording), falling back to legacy tests.last_results.
    var passed = 0, failed = 0, skipped = 0, runRefs = 0;
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

      var metaHtml =
        '<span class="muted" style="font-size:0.82em">' +
        _esc(item.author || '') + ' · ' + _esc((item.ts || '').replace('T', ' ').replace('Z', ' UTC')) +
        ' · <code style="font-size:0.9em">' + _esc(item.section || '') + '</code>' +
        '</span>';

      var textHtml = '<p style="margin:4px 0;font-size:0.92em">' + _esc(item.text || '') + '</p>';

      var responseHtml = '';
      if (status === 'addressed' && item.response) {
        responseHtml =
          '<div style="margin:6px 0 0 0;padding:8px 12px;background:#f0fdf4;' +
          'border-left:3px solid #10b981;border-radius:4px;font-size:0.88em">' +
          '<strong style="font-size:0.85em;color:#065f46">Response' +
          (item.responded_by ? ' (' + _esc(item.responded_by) + ')' : '') +
          (item.responded_at ? ' — ' + _esc(item.responded_at) : '') +
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
    _renderSpineSummary();
    _populateConclusionVerdictBadges();
    // Open Understand/Overview and show only Understand's sub-nav on load.
    _setStudyTab('overview');
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


  // Memoized GET /api/report-lint — shared by the readiness panel AND the
  // spine-summary panel so the deterministic linter is fetched once.
  var _reportLintPromise = null;
  function _reportLint() {
    if (!_reportLintPromise) {
      _reportLintPromise = fetch('/api/report-lint')
        .then(function (r) { return r.ok ? r.json() : { findings: [] }; })
        .catch(function () { return { findings: [] }; });
    }
    return _reportLintPromise;
  }

  // Spine C1a: "Spine at a glance" — a compact RE-PRESENTATION of the spine's
  // already-computed A+B content (verdict / why / acceptance / readiness /
  // next), each row linking to its detail tab/section. Reuses window._study
  // (computed_gate_verdict, findings, spine_acceptance, follow-ups) + the
  // /api/report-lint fetch — NO recompute, AI-free. Each row tolerates absence.
  function _renderSpineSummary() {
    var container = document.getElementById('spine-summary');
    if (!container || container.dataset.rendered) return;
    container.dataset.rendered = '1';
    var s = window._study || {};
    var e = _spineEsc;
    // Fixed display order: verdict → why → acceptance → readiness → next.
    var ORDER = ['Verdict', 'Why', 'Acceptance', 'Readiness', 'Next'];
    var slots = {};

    function _row(key, body, jump) {
      slots[key] = '<div class="spine-row spine-row-' + key.toLowerCase() + '">'
        + '<span class="spine-key">' + key + '</span>'
        + '<span class="spine-val">' + body + '</span>'
        + (jump ? '<span class="spine-jump">' + jump + '</span>' : '')
        + '</div>';
    }
    function _flush() { _flushSpineSummary(container, ORDER, slots); }

    // ── Verdict — the code-computed gate verdict + the A2 divergence chip ──
    var cgv = s.computed_gate_verdict || {};
    if (cgv.result) {
      var chip = cgv.diverges_from_authored
        ? '<span class="spine-chip-warn" title="code-computed verdict disagrees with the authored gate_status">'
          + '⚠ code: ' + e(cgv.result) + ' · authored: ' + e(s.gate_status || '—') + '</span>'
        : '';
      // critique #18 — pre-registered ✓ / post-hoc ⚠ chip in the Verdict row.
      var preregChip = _preregChipHtml(s);
      _row('Verdict',
        '<strong>' + e(cgv.result) + '</strong> '
        + '<span class="spine-label">code-computed</span> ' + chip + preregChip,
        '<a href="#" onclick="_setStudyTab(\'conclusions\');return false">details →</a>');
    }

    // ── Why — the primary finding statement + its divergence_factor ────────
    var findings = s.findings || [];
    var fwhy = findings.filter(function (f) {
      return f && (f.classification || '') === 'primary';
    })[0] || findings[0];
    if (fwhy && fwhy.statement) {
      var ev = fwhy.evidence || {};
      var dv = (ev.divergence_factor != null)
        ? ' <span class="spine-div">×' + e(ev.divergence_factor) + ' vs expected</span>' : '';
      _row('Why', e(fwhy.statement) + dv,
        '<a href="#" onclick="_setStudyTab(\'overview\');'
        + 'var el=document.querySelector(\'.findings-section\');'
        + 'if(el)el.scrollIntoView({behavior:\'smooth\',block:\'start\'});return false">finding →</a>');
    }

    // ── Acceptance — the investigation criterion this study covers (A1) ────
    var sa = s.spine_acceptance || {};
    var crit = (sa.criteria || [])[0];
    if (crit) {
      var inv = sa.investigation || '';
      // Deep-link to the investigation detail (SPA reads ?investigation=<name>)
      // + the acceptance roll-up anchor the report renders (<inv>-acceptance-rollup).
      var aLink = inv
        ? '<a href="/?investigation=' + encodeURIComponent(inv) + '#'
          + e(inv) + '-acceptance-rollup">' + e(inv) + ' →</a>'
        : '';
      _row('Acceptance',
        e(crit.behavior || crit.study || '') + ': <strong>' + e(crit.result || '—') + '</strong> '
        + '<span class="spine-label">code-computed</span>',
        aLink);
    }

    // ── Readiness — the A3 ✓/⚠ summary (from the report linter) ────────────
    var slug = container.getAttribute('data-slug') || studyName() || '';
    _reportLint().then(function (j) {
      var fs = (j.findings || []).filter(function (f) { return (f.study || '') === slug; });
      var gaps = fs.filter(function (f) {
        var sv = f.severity || 'info'; return sv === 'error' || sv === 'warning';
      }).length;
      var head = !fs.length ? '✓ Ready'
        : (gaps ? '⚠ ' + gaps + ' gap' + (gaps === 1 ? '' : 's')
                : 'ℹ ' + fs.length + ' note' + (fs.length === 1 ? '' : 's'));
      _row('Readiness',
        e(head) + ' <span class="spine-label">code-computed by the report linter</span>',
        '<a href="#" onclick="var el=document.getElementById(\'readiness-panel\');'
        + 'if(el)el.scrollIntoView({behavior:\'smooth\',block:\'start\'});return false">readiness →</a>');
      _flush();
    });

    // ── Next — the top next_action / follow-up ─────────────────────────────
    var next = null;
    var nextFindingId = null;
    var nextActionType = null;
    for (var i = 0; i < findings.length; i++) {
      if (findings[i] && findings[i].next_action) {
        next = findings[i].next_action;
        nextFindingId = findings[i].id || null;
        nextActionType = findings[i].next_action_type || null;   // critique #7
        break;
      }
    }
    if (!next) {
      var fu = (s.follow_up_studies || [])[0];
      if (fu && fu.title) next = fu.title;
    }
    if (!next) {
      var di = (s.discovery_implications || {}).followup_study_proposals || [];
      if (di[0] && di[0].title) next = di[0].title;
    }
    if (next) {
      // critique #19 — when this study FAILED (or needs calibration), the
      // seeded child should be a diagnostic study. Pass study_type=diagnostic
      // through the existing seed button (the pbg writer stamps the child).
      var failing = _isFailingVerdict(s);
      // When the "Next" is a finding's next_action, offer to seed a child
      // study from it directly (delegates to the shared pbg seed mechanism).
      var nextAction;
      if (nextFindingId) {
        var seedType = failing ? 'diagnostic' : '';
        var label = failing ? 'seed diagnostic study →' : 'seed study from this finding →';
        nextAction = '<a href="#" onclick="_seedFromFinding(' +
          JSON.stringify(studyName()) + ',' + JSON.stringify(nextFindingId) +
          ',' + JSON.stringify(seedType) +
          ');return false">' + label + '</a>';
      } else {
        nextAction = '<a href="#" onclick="_setStudyTab(\'conclusions\');return false">decide →</a>';
      }
      _row('Next', e(next) + _nextActionTypeChipHtml(nextActionType), nextAction);
    }

    // First synchronous flush (readiness fills its slot async above).
    _flush();
  }

  function _flushSpineSummary(container, order, slots) {
    var html = order.map(function (k) { return slots[k] || ''; }).join('');
    if (!html) { container.innerHTML = ''; return; }
    // critique #10 — surface the study_type badge in the panel head.
    var typeBadge = _studyTypeBadgeHtml(window._study || {});
    container.innerHTML = '<div class="spine-summary-head">Spine at a glance'
      + typeBadge + '</div>' + html;
  }

  function _spineEsc(v) {
    return String(v == null ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // ── Wave 3a workflow-typing chips (critiques #10 / #7 / #18) ──────────────
  // study_type (#10): study_type → kind → study_kind alias → 'standard' default.
  // Kept in sync with single_study_report._study_type + rigor._study_type.
  var _STUDY_TYPES = {exploratory: 1, confirmatory: 1, diagnostic: 1,
                      adversarial: 1, standard: 1};
  var _STUDY_TYPE_COLORS = {
    exploratory:  ['#e0e7ff', '#3730a3'], confirmatory: ['#dcfce7', '#166534'],
    diagnostic:   ['#fef3c7', '#92400e'], adversarial:  ['#fee2e2', '#991b1b'],
    standard:     ['#f1f5f9', '#475569']
  };
  function _studyType(s) {
    s = s || {};
    var keys = ['study_type', 'kind', 'study_kind'];
    for (var i = 0; i < keys.length; i++) {
      var v = s[keys[i]];
      if (typeof v === 'string' && v.trim()) {
        var t = v.trim().toLowerCase();
        if (_STUDY_TYPES[t]) return t;
      }
    }
    return 'standard';
  }
  function _studyTypeBadgeHtml(s) {
    s = s || {};
    var explicit = ['study_type', 'kind', 'study_kind'].some(function (k) {
      return typeof s[k] === 'string' && s[k].trim();
    });
    var t = _studyType(s);
    if (!explicit || t === 'standard') return '';
    var c = _STUDY_TYPE_COLORS[t] || ['#f1f5f9', '#475569'];
    return '<span class="study-type-badge" title="study type (critique #10)" '
      + 'style="display:inline-block;padding:1px 9px;border-radius:9999px;'
      + 'font-weight:600;font-size:0.75em;background:' + c[0] + ';color:' + c[1]
      + ';margin-left:8px;vertical-align:middle">' + _spineEsc(t) + '</span>';
  }

  // next_action_type (#7) — known values get a blue chip, unknowns amber.
  var _NEXT_ACTION_TYPES = {
    replicate: 1, calibrate: 1, ablate: 1, adversarially_probe: 1,
    refine_representation: 1, split_hypothesis: 1, retire_hypothesis: 1,
    escalate_model: 1
  };
  function _nextActionTypeChipHtml(nat) {
    if (typeof nat !== 'string' || !nat.trim()) return '';
    var v = nat.trim();
    var known = !!_NEXT_ACTION_TYPES[v];
    var bg = known ? '#dbeafe' : '#fef9c3';
    var fg = known ? '#1e40af' : '#854d0e';
    return '<span class="next-action-type" title="next action type (critique #7)" '
      + 'style="display:inline-block;padding:1px 8px;border-radius:9999px;background:'
      + bg + ';color:' + fg + ';font-weight:600;font-size:0.72em;margin-left:6px;'
      + 'vertical-align:middle">' + _spineEsc(v) + '</span>';
  }

  // preregistration (#18) — chip from window._study.preregistration_status,
  // which the server (study_verdict.preregistration_status) attaches on the
  // report-data path. Omitted when no preregistered block was declared.
  function _preregChipHtml(s) {
    var ps = (s || {}).preregistration_status;
    if (!ps || !ps.preregistered) return '';
    var bg, fg, label, title, before = ps.registered_before_run;
    if (before === true) {
      bg = '#dcfce7'; fg = '#166534'; label = 'pre-registered ✓';
      title = 'criteria registered before the canonical run';
    } else if (before === false) {
      bg = '#fef3c7'; fg = '#92400e'; label = 'post-hoc ⚠';
      title = 'criteria registered AFTER the run started';
    } else {
      bg = '#e2e8f0'; fg = '#475569'; label = 'pre-registered (timing unknown)';
      title = 'registered_at or run start time missing';
    }
    if (ps.criteria_match === false) {
      label += ' · thresholds drifted';
      title += '; pre-registered thresholds differ from the current behavior tests';
    }
    return '<span class="prereg-chip" title="' + _spineEsc(title) + '" '
      + 'style="display:inline-block;padding:1px 9px;border-radius:9999px;'
      + 'font-weight:600;font-size:0.75em;background:' + bg + ';color:' + fg
      + ';margin-left:8px;vertical-align:middle">' + _spineEsc(label) + '</span>';
  }

  // Spine A3: per-study readiness panel. Fetches the deterministic report
  // linter (GET /api/report-lint), filters to THIS study, and renders a
  // ✓ ready / ⚠ N gaps badge with the lint findings (severity-coloured).
  // Mirrors the param-enforcement banner: surfaced, connected to its source
  // (the linter), labeled code-computed. AI-free — pure deterministic output.
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
        var head, bg, bd, col;
        if (!findings.length) { head = '✓ Ready'; bg = '#f0fdf4'; bd = '#16a34a'; col = '#166534'; }
        else if (gaps) { head = '⚠ ' + gaps + ' gap' + (gaps === 1 ? '' : 's'); bg = '#fffbeb'; bd = '#f59e0b'; col = '#92400e'; }
        else { head = 'ℹ ' + sev.info + ' note' + (sev.info === 1 ? '' : 's'); bg = '#eff6ff'; bd = '#3b82f6'; col = '#1e40af'; }
        var lbl = '<span class="muted" style="font-size:0.85em">code-computed by the report linter (deterministic)</span>';
        if (!findings.length) {
          container.innerHTML =
            '<div class="readiness-banner" style="margin:8px 0 14px 0;padding:10px 14px;background:' + bg
            + ';border:1px solid ' + bd + ';border-left-width:5px;border-radius:6px;color:' + col + '">'
            + '<strong>Readiness: ' + head + '</strong> ' + lbl + '</div>';
          return;
        }
        // Key info on top: per-check breakdown (most frequent first), full list behind a dropdown.
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
          '<details class="readiness-banner" style="margin:8px 0 14px 0;background:' + bg
          + ';border:1px solid ' + bd + ';border-left-width:5px;border-radius:6px;color:' + col + '">'
          + '<summary style="padding:10px 14px;cursor:pointer;list-style:none;outline:none">'
          + '<strong>Readiness: ' + head + '</strong> ' + lbl
          + '<div class="muted" style="font-size:0.82em;margin-top:5px">' + breakdown
          + ' &nbsp;·&nbsp; <span style="opacity:.7;font-style:italic">click to expand</span></div>'
          + '</summary>'
          + '<div style="padding:2px 14px 12px 14px">' + groups + '</div>'
          + '</details>';
      })
      .catch(function () { container.dataset.rendered = ''; });
  }

  // Entry point: fetch the spec if needed, then run init.
  (async function () {
    if (await _bootstrapStudy()) { _runStudyInit(); }
  })();

  // ---- Remote run (smsvpctest) -------------------------------------------
  // Remote-run thin client (WS1, two-phase): build → poll → submit → poll → land.
  // Each step is one stateless sms-api call via /api/remote-run-{build,submit,
  // land,poll}; the JS drives the phases (sms-api owns async/state).
  var _remoteRunTimer = null;
  var _remoteRunState = {};

  function _rrProg() { return document.getElementById('remote-run-progress'); }
  function _rrBtn() { return document.getElementById('remote-run-btn'); }
  function _rrResetBtn() { var b = _rrBtn(); if (b) { b.disabled = false; b.textContent = window._remoteRunPinned ? '▶ Run on remote (pinned)' : '▶ Run on remote'; } }

  // Pinned mode: relabel the run card + flip _submitRemoteRun to the no-push,
  // no-login pinned path. Called on study-detail load (live backend only).
  function _initRemoteRunPinned() {
    var panel = document.getElementById('remote-run-panel');
    if (!panel) return;
    fetch('/api/remote-run-config').then(function(r) { return r.json(); }).then(function(cfg) {
      if (!cfg || !cfg.pinned) return;
      window._remoteRunPinned = true;
      var shortSha = String(cfg.commit || '').slice(0, 12);
      var label = (cfg.branch || 'main') + (shortSha ? ' @ ' + shortSha : '');
      var h3 = panel.querySelector('h3');
      var p = panel.querySelector('p.muted');
      var btn = _rrBtn();
      if (h3) h3.textContent = 'Run against pinned build (' + label + ')';
      if (p) p.innerHTML = 'Runs on the Ray backend against the pinned, already-built simulator '
        + '(<code>' + escapeHtmlForTests(label) + '</code>). Results land as a run on this study. '
        + 'No push or GitHub login required.'
        + (cfg.build_error ? '<br><span class="inv-run-err">⚠ ' + escapeHtmlForTests(cfg.build_error) + '</span>' : '');
      if (btn) btn.textContent = '▶ Run on remote (pinned)';
    }).catch(function() { /* leave the stock build-first card as-is */ });
  }
  window._initRemoteRunPinned = _initRemoteRunPinned;
  function _rrErr(msg) { _stopRrTween(); var p = _rrProg(); if (p) { p.hidden = false; p.innerHTML = '<div class="inv-run-err">' + msg + '</div>'; } _rrResetBtn(); }

  // ---- Progress-track adapter (Plan 7 / WS-2) ----------------------------
  // _renderRemoteRunProgress keeps its existing {build, run, note, landBtn,
  // landed, runDetail, phase} opts contract (so its ~11 call sites are
  // unchanged) but now drives the reusable ProgressTrack milestone bar instead
  // of the two-row text stepper. The one new opt threaded by the pollers is
  // `phase` (the raw sms-api phase) so we can tell Queued from Running — the
  // dashboard collapses both to run:'running' otherwise.
  var _RR_STAGE_KEYS = ['resolve', 'submit', 'queued', 'running', 'done', 'landed'];
  // Typical wall-clock per long stage (SAVE_SLOT "Pinned-build live facts":
  // Ray-provision+ParCa ≈ 8 min queued, compute ≈ 5 min running). Drives the
  // honest time-based soft-fill only; the bar snaps to the milestone on the
  // real transition.
  var _RR_TYPICAL_MS = { resolve: 120000, submit: 15000, queued: 480000, running: 300000 };

  function _rrSoftFor(key) {
    var t = _RR_TYPICAL_MS[key];
    if (!t) return null;
    _remoteRunState._stageStarts = _remoteRunState._stageStarts || {};
    if (!_remoteRunState._stageStarts[key]) _remoteRunState._stageStarts[key] = Date.now();
    return { startedAt: _remoteRunState._stageStarts[key], typicalMs: t };
  }

  // Translate the build/run/phase opts into a ProgressTrack `stages` model.
  function _rrDeriveStages(opts) {
    var pinned = !!window._remoteRunPinned;
    var stages = [
      { key: 'resolve', label: pinned ? 'Resolve' : 'Build' },
      { key: 'submit', label: 'Submit' },
      { key: 'queued', label: 'Queued' },
      { key: 'running', label: 'Running' },
      { key: 'done', label: 'Done' },
      { key: 'landed', label: 'Landed' },
    ];
    var m = { mode: 'stages', stages: stages, done: [], active: null, failed: null, soft: null };
    if (opts.landed) { m.done = _RR_STAGE_KEYS.slice(); return m; }        // fully landed
    if (opts.build === 'failed') { m.failed = 'resolve'; return m; }
    if (opts.build !== 'done') { m.active = 'resolve'; m.soft = _rrSoftFor('resolve'); return m; }
    m.done.push('resolve');
    if (opts.run === 'failed') {
      m.done.push('submit');
      m.failed = (opts.phase === 'queued') ? 'queued' : 'running';
      if (m.failed === 'running') m.done.push('queued');
      return m;
    }
    if (opts.landBtn || opts.run === 'done') {                            // run complete; landing is the outstanding manual step
      m.done.push('submit', 'queued', 'running', 'done');
      return m;
    }
    if (opts.run === 'running') {
      m.done.push('submit');
      if (opts.phase === 'queued') { m.active = 'queued'; m.soft = _rrSoftFor('queued'); }
      else { m.done.push('queued'); m.active = 'running'; m.soft = _rrSoftFor('running'); }
      return m;
    }
    m.active = 'submit'; m.soft = _rrSoftFor('submit');                    // build done, run pending → submitting
    return m;
  }

  // Soft-fill tween: repaints only the active segment ~4×/s while a soft stage
  // is running (ProgressTrack.tick is a no-op DOM-diff otherwise). Cancels on
  // terminal/failed/reset or if the mount leaves the DOM.
  var _rrTween = null;
  function _stopRrTween() { if (_rrTween) { clearInterval(_rrTween); _rrTween = null; } }
  function _startRrTween() {
    if (_rrTween) return;
    _rrTween = setInterval(function () {
      var mount = _remoteRunState._ptMount, m = _remoteRunState._ptModel;
      if (!mount || !document.body.contains(mount) || !m || !window.ProgressTrack || !(m.active && m.soft)) {
        _stopRrTween(); return;
      }
      window.ProgressTrack.tick(mount, m);
    }, 250);
  }

  // Legacy two-row stepper — retained as a graceful fallback if ProgressTrack
  // failed to load (e.g. a stale cached page missing the new <script>).
  function _renderRemoteRunProgressLegacy(opts) {
    var p = _rrProg(); if (!p) return;
    p.hidden = false;
    var icon = {pending: '⋯', running: '▶', queued: '⋯', built: '✓', done: '✓', failed: '✗', unreachable: '⚠'};
    function row(name, st, detail) {
      return '<div class="inv-run-item inv-run-' + (st || 'pending') + '">'
        + '<span class="inv-run-icon">' + (icon[st] || '⋯') + '</span> '
        + '<code>' + name + '</code>'
        + (detail ? ' <span class="muted">' + escapeHtmlForTests(detail) + '</span>' : '') + '</div>';
    }
    var land = opts.landBtn
      ? '<button type="button" class="btn-mini" id="remote-run-land-btn" onclick="_landRemoteRun()">⬇ Land results locally</button>' : '';
    var landed = opts.landed
      ? '<div class="inv-run-progress-banner"><strong>✓ Landed</strong> <code>' + escapeHtmlForTests(opts.landed) + '</code> — refresh to see it.</div>' : '';
    p.innerHTML = (opts.note ? '<div class="inv-run-progress-banner">' + opts.note + '</div>' : '')
      + '<div class="inv-run-list">' + row('build', opts.build, opts.buildDetail) + row('run', opts.run, opts.runDetail) + '</div>'
      + land + landed;
  }

  function _renderRemoteRunProgress(opts) {
    var p = _rrProg(); if (!p) return;
    p.hidden = false;
    if (!window.ProgressTrack) { _renderRemoteRunProgressLegacy(opts); return; }
    // Shell: [.rr-track][.rr-extras] — ProgressTrack owns only the track
    // subtree, so the land button / landed banner survive its rebuild-on-change.
    var track = p.querySelector('.rr-track');
    var extras = p.querySelector('.rr-extras');
    if (!track || !extras) {
      p.innerHTML = '<div class="rr-track"></div><div class="rr-extras"></div>';
      track = p.querySelector('.rr-track');
      extras = p.querySelector('.rr-extras');
    }
    var model = _rrDeriveStages(opts);
    model.note = opts.note || '';
    model.detail = opts.runDetail || opts.buildDetail || '';
    _remoteRunState._ptModel = model;
    _remoteRunState._ptMount = track;
    window.ProgressTrack.render(track, model);
    var land = opts.landBtn
      ? '<button type="button" class="btn-mini" id="remote-run-land-btn" onclick="_landRemoteRun()">⬇ Land results locally</button>' : '';
    var landed = opts.landed
      ? '<div class="inv-run-progress-banner"><strong>✓ Landed</strong> <code>' + escapeHtmlForTests(opts.landed) + '</code> — refresh to see it.</div>' : '';
    extras.innerHTML = land + landed;
    if (model.active && model.soft) _startRrTween(); else _stopRrTween();
  }

  function _submitRemoteRun(ev) {
    ev.preventDefault();
    var form = ev.target;
    var btn = _rrBtn();
    _remoteRunState = {
      study: studyName(),
      runOpts: {
        num_generations: parseInt(form.num_generations.value, 10) || 1,
        num_seeds: parseInt(form.num_seeds.value, 10) || 1,
        run_parca: !!form.run_parca.checked,
      },
    };
    if (window._remoteRunPinned) {
      // Pinned mode: no push/build/login — resolve the already-built simulator
      // and go straight to submit (phase "built" comes back immediately).
      if (btn) { btn.disabled = true; btn.textContent = 'Resolving pinned build…'; }
      fetch('/api/remote-run-pinned-build', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({study: _remoteRunState.study}),
      }).then(function(r) { return r.json().then(function(j) { return {status: r.status, body: j}; }); })
        .then(function(res) {
          if (res.status !== 202 || !res.body.simulator_id) {
            _rrErr('Could not resolve pinned build: ' + escapeHtmlForTests((res.body && res.body.error) || res.status)); return;
          }
          _remoteRunState.simulator_id = res.body.simulator_id;
          _remoteRunState.commit = res.body.commit;
          _renderRemoteRunProgress({build: 'done', run: 'running',
            note: '<strong>Using pinned build.</strong> <span class="muted">'
              + escapeHtmlForTests((res.body.branch || '') + ' @ ' + String(res.body.commit || '').slice(0, 12))
              + '</span> Submitting run…'});
          _submitRun();
        }).catch(function(err) { _rrErr('Network error: ' + escapeHtmlForTests(String(err))); });
      return false;
    }
    if (btn) { btn.disabled = true; btn.textContent = 'Starting build…'; }
    fetch('/api/remote-run-build', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({study: _remoteRunState.study}),
    }).then(function(r) { return r.json().then(function(j) { return {status: r.status, body: j}; }); })
      .then(function(res) {
        if (res.status === 401) { _rrErr('Log in with GitHub (top-right on the main dashboard) to run remotely.'); return; }
        if (res.status !== 202 || !res.body.simulator_id) {
          _rrErr('Could not start build: ' + escapeHtmlForTests((res.body && res.body.error) || res.status)); return;
        }
        _remoteRunState.simulator_id = res.body.simulator_id;
        _remoteRunState.commit = res.body.commit;
        _renderRemoteRunProgress({build: 'running', run: 'pending',
          note: '<strong>Building simulator…</strong> <span class="muted">'
            + escapeHtmlForTests((res.body.branch || '') + ' @ ' + String(res.body.commit || '').slice(0, 12)) + '</span>'});
        _pollBuild();
      }).catch(function(err) { _rrErr('Network error: ' + escapeHtmlForTests(String(err))); });
    return false;
  }

  function _pollPhase(query, onPoll) {
    if (_remoteRunTimer) clearTimeout(_remoteRunTimer);
    var consecutiveErrors = 0;
    function tick() {
      fetch('/api/remote-run-poll?' + query)
        .then(function(r) { return r.json().then(function(j) { return {status: r.status, body: j}; }); })
        .then(function(res) {
          if (res.status === 502 || (res.body && res.body.reachable === false)) {
            _renderRemoteRunProgress({build: _remoteRunState._buildPhase || 'running', run: _remoteRunState._runPhase || 'pending', phase: _remoteRunState._runPhase,
              note: '<strong class="inv-run-err">⚠ ' + escapeHtmlForTests((res.body && res.body.reason) || 'sms-api unreachable') + '</strong> <span class="muted">retrying…</span>'});
            _remoteRunTimer = setTimeout(tick, 4000); return;
          }
          if (res.status !== 200) {
            consecutiveErrors += 1;
            if (consecutiveErrors < 3) { _remoteRunTimer = setTimeout(tick, 3000); return; }
            _rrErr('Poll error ' + escapeHtmlForTests(res.status)); return;
          }
          consecutiveErrors = 0;
          onPoll(res.body, function() { _remoteRunTimer = setTimeout(tick, 2500); });
        }).catch(function(err) {
          consecutiveErrors += 1;
          if (consecutiveErrors < 4) { _remoteRunTimer = setTimeout(tick, 3000); return; }  // tolerate tunnel blips
          _rrErr('Network error while polling: ' + escapeHtmlForTests(String(err)));
        });
    }
    tick();
  }

  function _pollBuild() {
    _pollPhase('simulator_id=' + encodeURIComponent(_remoteRunState.simulator_id), function(body, again) {
      _remoteRunState._buildPhase = body.phase;
      if (body.phase === 'failed') {
        _renderRemoteRunProgress({build: 'failed', run: 'pending',
          note: '<strong class="inv-run-err">✗ Build failed.</strong> ' + escapeHtmlForTests(body.error || body.raw_status || '')});
        _rrResetBtn(); return;
      }
      if (body.phase === 'built') {
        _renderRemoteRunProgress({build: 'done', run: 'running', note: '<strong>Build done. Submitting run…</strong>'});
        _submitRun(); return;
      }
      _renderRemoteRunProgress({build: 'running', run: 'pending',
        note: '<strong>Building simulator…</strong> <span class="muted">' + escapeHtmlForTests(body.raw_status || '') + '</span>'});
      again();
    });
  }

  function _submitRun() {
    var b = Object.assign({study: _remoteRunState.study, simulator_id: _remoteRunState.simulator_id}, _remoteRunState.runOpts);
    fetch('/api/remote-run-submit', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b),
    }).then(function(r) { return r.json().then(function(j) { return {status: r.status, body: j}; }); })
      .then(function(res) {
        if (res.status !== 202 || !res.body.simulation_id) {
          _renderRemoteRunProgress({build: 'done', run: 'failed',
            note: '<strong class="inv-run-err">✗ Could not submit run:</strong> ' + escapeHtmlForTests((res.body && res.body.error) || res.status)});
          _rrResetBtn(); return;
        }
        _remoteRunState.simulation_id = res.body.simulation_id;
        _pollRun();
      }).catch(function(err) { _rrErr('Network error submitting run: ' + escapeHtmlForTests(String(err))); });
  }

  function _pollRun() {
    _pollPhase('simulation_id=' + encodeURIComponent(_remoteRunState.simulation_id), function(body, again) {
      _remoteRunState._runPhase = body.phase;
      var simRef = 'sim ' + _remoteRunState.simulation_id;
      if (body.phase === 'failed') {
        _renderRemoteRunProgress({build: 'done', run: 'failed',
          note: '<strong class="inv-run-err">✗ Run failed.</strong> ' + escapeHtmlForTests(body.error || body.raw_status || '') + ' <span class="muted">(' + simRef + ')</span>'});
        _rrResetBtn(); return;
      }
      if (body.phase === 'done') {
        _renderRemoteRunProgress({build: 'done', run: 'done', landBtn: true,
          note: '<strong>✓ Run complete</strong> <span class="muted">(' + simRef + ')</span> — land the results to view them.'});
        _rrResetBtn(); return;
      }
      var label = body.phase === 'queued' ? 'Queued on AWS Batch…' : 'Running…';
      _renderRemoteRunProgress({build: 'done', run: 'running', runDetail: body.raw_status, phase: body.phase,
        note: '<strong>' + label + '</strong> <span class="muted">(' + simRef + ')</span>'});
      again();
    });
  }

  function _landRemoteRun() {
    var lb = document.getElementById('remote-run-land-btn');
    if (lb) { lb.disabled = true; lb.textContent = 'Landing…'; }
    fetch('/api/remote-run-land', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({study: _remoteRunState.study, simulation_id: _remoteRunState.simulation_id, commit: _remoteRunState.commit}),
    }).then(function(r) { return r.json().then(function(j) { return {status: r.status, body: j}; }); })
      .then(function(res) {
        if (res.status !== 200 || !res.body.run_id) {
          _rrErr('Land failed: ' + escapeHtmlForTests((res.body && res.body.error) || res.status)); return;
        }
        _renderRemoteRunProgress({build: 'done', run: 'done', landed: res.body.run_id});
      }).catch(function(err) { _rrErr('Network error landing: ' + escapeHtmlForTests(String(err))); });
  }

  window._submitRemoteRun = _submitRemoteRun;
  window._landRemoteRun = _landRemoteRun;

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
