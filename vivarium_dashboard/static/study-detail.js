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
  function _setStudyTab(kind) {
    document.querySelectorAll('.study-tab').forEach(function(b) {
      b.classList.toggle('active', b.dataset.kind === kind);
    });
    document.querySelectorAll('.study-tab-panel').forEach(function(p) {
      p.classList.toggle('active', p.dataset.kind === kind);
    });
    if (kind === 'tests') {
      loadTestsTab(window._study);
    }
    if (kind === 'conclusions') {
      _loadConclusionsTab(window._study);
    }
    if (kind === 'visualizations') {
      _loadCharts('viz-charts-panel');
    }
    if (kind === 'observables') {
      _loadReadoutValidation();
    }
  }
  window._setStudyTab = _setStudyTab;

  // ── Spine B2: readout validation badges ──────────────────────────────────
  // Fetch /api/study-observable-check (SP2b-i, the never-fabricate guard) and
  // badge each readout row with the COMPUTED validation status
  // (ok / unresolved / not_in_structure / aspirational) BESIDE the authored
  // status, labeled "validated against the composite", so a phantom readout
  // (not_in_structure) is visible at the source. Tolerates the endpoint failing
  // or being absent (no badge, no error) — the composite must build (~3s).
  // not_in_structure links to the re-author guidance (/api/observables).
  var _readoutValidationLoaded = false;
  function _loadReadoutValidation() {
    if (_readoutValidationLoaded) return;
    _readoutValidationLoaded = true;
    var slug = studyName();
    if (!slug) return;
    fetch('/api/study-observable-check?study=' + encodeURIComponent(slug),
          {headers: {Accept: 'application/json'}})
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(j) {
        if (!j || !Array.isArray(j.readouts)) return;  // tolerate failure
        var byName = {};
        j.readouts.forEach(function(o) { if (o && o.name) byName[o.name] = o; });
        document.querySelectorAll('.readout-validation').forEach(function(el) {
          var o = byName[el.getAttribute('data-readout')];
          if (!o) return;
          el.innerHTML = _readoutValidationBadge(o.status, o.detail);
        });
      })
      .catch(function() { /* tolerate — no badge */ });
  }

  function _readoutValidationBadge(status, detail) {
    var e = escapeHtmlForTests;
    var styles = {
      ok:              {bg: '#d1fae5', fg: '#065f46', bd: '#6ee7b7', glyph: '✓', label: 'ok'},
      unresolved:      {bg: '#fef3c7', fg: '#92400e', bd: '#fcd34d', glyph: '⚠', label: 'unresolved'},
      not_in_structure:{bg: '#fee2e2', fg: '#991b1b', bd: '#fca5a5', glyph: '✗', label: 'not_in_structure'},
      aspirational:    {bg: '#f1f5f9', fg: '#475569', bd: '#cbd5e1', glyph: '⏳', label: 'aspirational'},
    };
    var s = styles[status] || {bg: '#f1f5f9', fg: '#475569', bd: '#cbd5e1', glyph: '•', label: status || '—'};
    var badge =
      '<span class="readout-validation-badge" title="' + e(detail || '') + '" ' +
      'style="display:inline-block;padding:2px 8px;border-radius:9999px;background:' + s.bg +
      ';color:' + s.fg + ';border:1px solid ' + s.bd + '">' + s.glyph + ' ' + e(s.label) + '</span>';
    if (status === 'not_in_structure') {
      // Re-author guidance: the in-page bigraph picker resolves real paths;
      // /api/observables backs it. Point the reader at the picker to fix the
      // phantom readout at the source.
      badge += ' <a href="#bigraph-picker-details" ' +
        'onclick="var d=document.getElementById(\'bigraph-picker-details\');if(d)d.open=true;" ' +
        'class="muted" style="font-size:0.9em" title="re-author against /api/observables">re-author →</a>';
    }
    return badge;
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
        var live = d.charts.filter(function(c) { return (c.source || 'live') === 'live'; });
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
  // the Build-tab Model block. stateUrl points at the live composite-state
  // endpoint; the loom unwraps {state}.
  function _openCompositeLoom(composite) {
    if (!composite) return;
    var stateUrl = '/api/composite-state?ref=' + encodeURIComponent(composite);
    var u = '/bigraph-loom/index.html?static=1&stateUrl=' + encodeURIComponent(stateUrl);
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

  // --- Conclusions tab: split/join helpers + load/save ---
  function _splitConclusion(md) {
    var sections = { Claims: '', Evidence: '', Limitations: '', 'Next steps': '' };
    if (!md) return sections;
    var parts = md.split(/(?:^|\n)##\s+/);
    if (parts.length === 1) {
      sections.Claims = parts[0].trim();
      return sections;
    }
    var preamble = parts.shift();
    if (preamble && preamble.trim()) sections.Claims = preamble.trim();
    parts.forEach(function(chunk) {
      var nl = chunk.indexOf('\n');
      var header = (nl === -1 ? chunk : chunk.slice(0, nl)).trim();
      var body = (nl === -1 ? '' : chunk.slice(nl + 1)).trim();
      if (header in sections) {
        if (sections[header]) sections[header] += '\n\n' + body;
        else sections[header] = body;
      }
    });
    return sections;
  }

  function _joinConclusion(sections) {
    var labels = ['Claims', 'Evidence', 'Limitations', 'Next steps'];
    var parts = labels.map(function(label) {
      var body = (sections[label] || '').trim();
      return '## ' + label + (body ? '\n\n' + body : '');
    });
    return parts.join('\n\n') + '\n';
  }

  function _loadConclusionsTab(study) {
    var s = _splitConclusion((study && study.conclusion) || '');
    var ids = { Claims: 'conclusion-claims', Evidence: 'conclusion-evidence',
                Limitations: 'conclusion-limitations', 'Next steps': 'conclusion-next-steps' };
    Object.keys(ids).forEach(function(label) {
      var el = document.getElementById(ids[label]);
      if (el) el.value = s[label] || '';
    });
  }

  function _saveConclusion() {
    var sections = {
      Claims:       (document.getElementById('conclusion-claims') || {}).value || '',
      Evidence:     (document.getElementById('conclusion-evidence') || {}).value || '',
      Limitations:  (document.getElementById('conclusion-limitations') || {}).value || '',
      'Next steps': (document.getElementById('conclusion-next-steps') || {}).value || '',
    };
    return fetch('/api/study-set-conclusion', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({study: studyName(), text: _joinConclusion(sections)}),
    });
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

  ['conclusion-claims', 'conclusion-evidence', 'conclusion-limitations', 'conclusion-next-steps'].forEach(function(id) {
    var el = document.getElementById(id);
    if (el) el.addEventListener('blur', _saveConclusion);
  });

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

  // W24 — "View as skeptic": render the single-study report reordered for a
  // skeptical reviewer (audit trail → rigor → controls → alternatives →
  // limitations → open debts → verdicts/biology/viz) and open it. The server
  // route renders the skeptic view from ?skeptic=1 / body flag; we just open
  // the resulting HTML file.
  bindAll('.btn-view-skeptic', function(btn) {
    btn.disabled = true;
    api('POST', '/api/study-report-single?skeptic=1',
        {study: studyName(), skeptic: true})
      .then(function(res) {
        btn.disabled = false;
        if (res.status === 200 && res.body && res.body.html_path) {
          window.open('/' + res.body.html_path.replace(/^\/+/, ''), '_blank');
        } else {
          alert((res.body && res.body.error) || 'Could not render skeptic view.');
        }
      })
      .catch(function() { btn.disabled = false; });
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
    var runId = btn.dataset.runId;
    window.open('/composite-explorer?run_id=' + encodeURIComponent(runId), '_blank');
  });

  // ptools-launch → _get_ptools_launch
  bindAll('.btn-launch-ptools', function(btn) {
    var runId = btn.dataset.runId;
    var study = studyName();
    var url = '/api/ptools-launch/' + encodeURIComponent(study) + '?run=' + encodeURIComponent(runId);
    fetch(url).then(function(r) {
      return r.json().then(function(d) { return {status: r.status, body: d}; });
    }).then(function(res) {
      var b = res.body;
      if (res.status === 200 && b.url) {
        window.open(b.url, '_blank');
      } else if (b && b.error === 'ptools_server_url not configured') {
        alert('PTools not configured.\nSet ui.ptools_server_url in workspace.yaml.');
      } else if (b && b.available && b.available.length === 0) {
        alert('No ptools TSV results found for this run.\nRun the ptools analyses first.');
      } else {
        alert('PTools launch failed: ' + (b && b.error || res.status));
      }
    });
  });

  // study-run-delete → _post_investigation_run_delete
  bindAll('.btn-delete-run', function(btn) {
    var runId = btn.dataset.runId;
    if (!confirm('Delete this run?')) return;
    api('POST', '/api/study-run-delete', {
      study: studyName(), run_id: runId,
    }).then(function() { location.reload(); });
  });

  // study-runs-clear → _post_investigation_runs_clear
  bindAll('.btn-clear-runs', function() {
    if (!confirm('Clear ALL runs in this study?')) return;
    api('POST', '/api/study-runs-clear', {
      study: studyName(),
    }).then(function() { location.reload(); });
  });

  // study-comparison-add → _post_investigation_comparison_add
  bindAll('.btn-compare-selected', function() {
    var ids = [];
    document.querySelectorAll('.run-compare-checkbox:checked').forEach(function(c) {
      ids.push(c.value);
    });
    if (ids.length < 2) return alert('Select at least two runs.');
    api('POST', '/api/study-comparison-add', {
      study: studyName(), run_ids: ids,
    }).then(function(res) {
      if (res.status === 200) location.reload();
      else alert(res.body.error || 'Compare failed');
    });
  });

  // --- Viz ---
  // NOTE: .btn-view-run intentionally left as-is (broken URL is a follow-up task).
  bindAll('.btn-add-viz', function() {
    // The add-viz modal lives on the main dashboard page. Take the user there.
    location.href = '/#composite-explore?study=' + encodeURIComponent(studyName());
  });

  // --- Conclusion ---
  // study-set-conclusion → _post_investigation_set_conclusions, key "investigation"
  // but also aliased to set-conclusion which uses "investigation" key.
  bindAll('.btn-mark-complete', function() {
    api('POST', '/api/study-set-conclusion', {
      investigation: studyName(), study: studyName(), mark_complete: true,
    }).then(function() { location.reload(); });
  });

  // ----- Tests tab -----

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
        '<a href="#run-' + e(runIdent) + '" onclick="_setStudyTab(\'runs\')" ' +
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
  // The renderers (loadTestsTab, _loadConclusionsTab, _renderFeedbackTrackedPanel,
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
    _autoReadouts();
  }

  // ── C2 — derive the 3-track conclusion verdicts (read-only, computed) ───
  // Rules kept IDENTICAL to single_study_report.py (_derive_conclusion_verdicts)
  // and walkthrough.js (_deriveConclusionVerdicts) so every surface shows the
  // same badge. The .basis textareas remain authored inputs.
  var _GATE_RESULT_NORM = {
    pass: 'PASS', passed: 'PASS', ok: 'PASS',
    fail: 'FAIL', failed: 'FAIL',
    partial: 'PARTIAL', mixed: 'PARTIAL', needs_calibration: 'PARTIAL'
  };
  var _RUN_ERRORED = {error: 1, errored: 1, failed: 1, crashed: 1, fail: 1};
  var _RUN_COMPLETED = {completed: 1, complete: 1, success: 1, succeeded: 1, ok: 1, done: 1, finished: 1};
  function _normGateResult(v) {
    return _GATE_RESULT_NORM[String(v == null ? '' : v).trim().toLowerCase()] || 'PENDING';
  }
  function _deriveConclusionVerdicts(s) {
    s = s || {};
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

    return {
      biological_validation:    {result: bio},
      regression_compatibility: {result: reg},
      explanatory_gain:         {result: exp}
    };
  }
  function _populateConclusionVerdictBadges() {
    var badges = document.querySelectorAll('[data-verdict-track]');
    if (!badges.length) return;
    var cv = _deriveConclusionVerdicts(window._study || {});
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

  // Auto-derive the Readouts tab from the composite's bigraph state when the
  // study declares no readouts/observables — so the tab is never empty and is
  // connected to the actual simulation. Lists the composite's scalar/array
  // stores (the quantities a readout can target) with their paths + current
  // values, fetched from /api/composite-state?ref=<baseline composite>.
  function _autoReadouts() {
    var host = document.getElementById('auto-readouts');
    if (!host) return;
    var composite = host.getAttribute('data-composite') || '';
    if (!composite) return;
    var e = escapeHtmlForTests;
    api('GET', '/api/composite-state?ref=' + encodeURIComponent(composite)).then(function (res) {
      if (!res || res.status !== 200 || !res.body || !res.body.state) return;
      var st = res.body.state;
      // The endpoint returns the composite DOCUMENT; the bigraph state
      // (the actual stores) is nested under its `state` field.
      if (st && st.state && typeof st.state === 'object') st = st.state;
      var rows = [];
      Object.keys(st).forEach(function (k) {
        var v = st[k];
        // Skip process/step nodes (objects with _type/address/config); keep the
        // scalar/array stores — those are the observable quantities.
        if (v && typeof v === 'object' && (v._type || v.address || v.config || v.inputs || v.outputs)) return;
        var kind = (typeof v === 'number') ? 'scalar'
                 : (Array.isArray(v) ? ('array (' + v.length + ')') : typeof v);
        var preview = (typeof v === 'number') ? String(Math.round(v * 1000) / 1000)
                    : (Array.isArray(v) ? '' : (typeof v === 'string' ? v : ''));
        rows.push('<tr style="border-bottom:1px solid #f1f5f9">'
          + '<td style="padding:6px"><code>' + e(k) + '</code></td>'
          + '<td style="padding:6px"><code style="font-size:0.85em">' + e(k) + '</code></td>'
          + '<td style="padding:6px" class="muted small">' + e(kind) + '</td>'
          + '<td style="padding:6px" class="muted small">' + e(preview) + '</td></tr>');
      });
      if (!rows.length) return;
      host.innerHTML =
        '<p class="muted" style="font-size:0.88em">Auto-derived from the composite’s bigraph state '
        + '(<code>' + e(composite) + '</code>) — the stores this study can read out. Declare them under '
        + '<code>readouts:</code> in study.yaml to pin units, descriptions, and pass/fail bands.</p>'
        + '<table class="observables-table" style="width:100%;border-collapse:collapse">'
        + '<thead><tr>'
        + '<th style="text-align:left;padding:6px;border-bottom:1px solid #e2e8f0">Store</th>'
        + '<th style="text-align:left;padding:6px;border-bottom:1px solid #e2e8f0">Path</th>'
        + '<th style="text-align:left;padding:6px;border-bottom:1px solid #e2e8f0">Kind</th>'
        + '<th style="text-align:left;padding:6px;border-bottom:1px solid #e2e8f0">Current value</th>'
        + '</tr></thead><tbody>' + rows.join('') + '</tbody></table>';
    }).catch(function () {});
  }
  window._autoReadouts = _autoReadouts;

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
        '<a href="#" onclick="_setStudyTab(\'overview\');return false">details →</a>');
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
        '<a href="#" onclick="_setStudyTab(\'overview\');'
        + 'var el=document.getElementById(\'readiness-panel\');'
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

})();
