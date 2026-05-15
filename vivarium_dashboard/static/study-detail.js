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
  }
  window._setStudyTab = _setStudyTab;

  // --- Inline-edit (objective + conclusion) ---
  function makeEditable(el, savePath, field, placeholder) {
    if (!el) return;
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
        var body = {study: window._studyName};
        body[field] = t.value;
        api('POST', savePath, body).then(function() {
          el.textContent = t.value || placeholder;
        });
      });
    });
  }
  makeEditable(
    document.getElementById('objective-text'),
    '/api/study-set-objective', 'text',
    '(blank — click to write)'
  );
  makeEditable(
    document.getElementById('conclusion-text'),
    '/api/study-set-conclusion', 'conclusion',
    '(blank)'
  );

  // --- Helpers: attach a click handler to every button matching a CSS class ---
  function bindAll(selector, handler) {
    document.querySelectorAll(selector).forEach(function(btn) {
      btn.addEventListener('click', function(ev) { handler(btn, ev); });
    });
  }

  function studyName() { return window._studyName; }

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
  // study-run-baseline → _post_investigation_run, expects body key "name"
  bindAll('.btn-run-baseline', function() {
    api('POST', '/api/study-run-baseline', {name: studyName(), investigation: studyName()})
      .then(function(res) {
        if (res.status === 200) location.reload();
        else alert(res.body.error || 'Run failed');
      });
  });

  // study-set-baseline-params → _post_study_set_baseline_params_for_test, key "study"
  bindAll('.btn-edit-baseline-params', function() {
    var params = (window._study && window._study.baseline && window._study.baseline.params) || {};
    var text = prompt('Edit baseline params (JSON):', JSON.stringify(params, null, 2));
    if (text == null) return;
    try {
      var parsed = JSON.parse(text);
      api('POST', '/api/study-set-baseline-params', {study: studyName(), params: parsed})
        .then(function() { location.reload(); });
    } catch (e) {
      alert('Invalid JSON: ' + e.message);
    }
  });

  // --- Variants ---
  // study-variant-add → _post_investigation_composite_perturb, key "investigation"
  bindAll('.btn-add-variant', function() {
    var name = prompt('Variant name:');
    if (!name) return;
    var desc = prompt('Description:', '') || '';
    var po = prompt('Parameter overrides (JSON, e.g. {"rate": 2.0}):', '{}') || '{}';
    try { po = JSON.parse(po); } catch (e) { return alert('Invalid JSON'); }
    api('POST', '/api/study-variant-add', {
      investigation: studyName(), study: studyName(),
      name: name,
      extends: 'baseline', description: desc, parameter_overrides: po,
    }).then(function(res) {
      if (res.status === 200) location.reload();
      else alert(res.body.error || 'Add variant failed');
    });
  });

  // study-run-variant → _post_investigation_run_one, key "investigation"
  bindAll('.btn-run-variant', function(btn) {
    var variant = btn.dataset.variant;
    api('POST', '/api/study-run-variant', {
      investigation: studyName(), study: studyName(),
      sim_name: variant, variant: variant,
    }).then(function() { location.reload(); });
  });

  bindAll('.btn-edit-variant', function() {
    alert('Edit variant not implemented in Phase 1 — delete + re-add for now.');
  });

  bindAll('.btn-delete-variant', function(btn) {
    var variant = btn.dataset.variant;
    if (!confirm('Delete variant ' + variant + '?')) return;
    api('POST', '/api/study-variant-delete',
        {study: studyName(), variant: variant})
      .then(function(res) {
        if (res.status === 200) location.reload();
        else alert(res.body.error || 'Delete variant failed');
      });
  });

  // --- Runs ---
  bindAll('.btn-view-run', function(btn) {
    var runId = btn.dataset.runId;
    window.open('/composite-explorer?run_id=' + encodeURIComponent(runId), '_blank');
  });

  // study-run-delete → _post_investigation_run_delete, key "investigation" + "run_id"
  bindAll('.btn-delete-run', function(btn) {
    var runId = btn.dataset.runId;
    if (!confirm('Delete this run?')) return;
    api('POST', '/api/study-run-delete', {
      investigation: studyName(), study: studyName(), run_id: runId,
    }).then(function() { location.reload(); });
  });

  // study-runs-clear → _post_investigation_runs_clear, key "investigation"
  bindAll('.btn-clear-runs', function() {
    if (!confirm('Clear ALL runs in this study?')) return;
    api('POST', '/api/study-runs-clear', {
      investigation: studyName(), study: studyName(),
    }).then(function() { location.reload(); });
  });

  // study-comparison-add → _post_investigation_comparison_add, key "investigation"
  bindAll('.btn-compare-selected', function() {
    var ids = [];
    document.querySelectorAll('.run-compare-checkbox:checked').forEach(function(c) {
      ids.push(c.value);
    });
    if (ids.length < 2) return alert('Select at least two runs.');
    api('POST', '/api/study-comparison-add', {
      investigation: studyName(), study: studyName(), run_ids: ids,
    }).then(function(res) {
      if (res.status === 200) location.reload();
      else alert(res.body.error || 'Compare failed');
    });
  });

  // --- Viz ---
  bindAll('.btn-add-viz', function() {
    alert('Add visualization: not implemented in Phase 1.');
  });

  // --- Conclusion ---
  // study-set-conclusion → _post_investigation_set_conclusions, key "investigation"
  // but also aliased to set-conclusion which uses "investigation" key.
  bindAll('.btn-mark-complete', function() {
    api('POST', '/api/study-set-conclusion', {
      investigation: studyName(), study: studyName(), mark_complete: true,
    }).then(function() { location.reload(); });
  });
})();
