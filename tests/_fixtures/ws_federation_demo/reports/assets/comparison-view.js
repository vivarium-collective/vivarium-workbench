// vivarium_workbench/static/comparison-view.js
// Results tab, cross-engine comparison view: for a remote batch run whose state
// carries `comparisons`, /api/study-results returns `comparison` (models.StudyComparison).
// This renders it as a bucket summary + search/filter + a list of collapsed
// models; opening one shows its engine-by-engine NRMSE heatmap and each
// engine's status/runtime. Pure string builders (exported for node tests) plus
// one `render` entry point. ZERO network calls, so it works unchanged in a
// published read-only snapshot. Every server-supplied string goes through esc().
(function (global) {
  'use strict';

  var PAGE = 50;   // rows shown before "Show more" (a corpus can be 1000+ models)

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function fmt(v) {
    if (typeof v !== 'number' || !isFinite(v)) return '—';
    if (v === 0) return '0';
    var a = Math.abs(v);
    return (a < 1e-3 || a >= 1e3) ? v.toExponential(1) : String(Math.round(v * 1000) / 1000);
  }

  // NRMSE -> heat level 0 (agree) .. 4 (>10% apart); -1 = no value.
  function heatLevel(v) {
    if (typeof v !== 'number' || !isFinite(v)) return -1;
    if (v <= 0.001) return 0;
    if (v <= 0.01) return 1;
    if (v <= 0.05) return 2;
    if (v <= 0.1) return 3;
    return 4;
  }

  // Engines whose run did not succeed in this job (anything but status "ok").
  function problemEngines(job) {
    var out = [], runs = job.runs || {};
    Object.keys(runs).forEach(function (e) {
      if (runs[e] && runs[e].status !== 'ok') out.push(e);
    });
    return out;
  }

  // jobs filtered by free text (model/job/engine names), bucket label, and an
  // optional "only jobs where an engine did not run cleanly" toggle.
  function filterJobs(jobs, f) {
    f = f || {};
    var q = String(f.q || '').trim().toLowerCase();
    return (jobs || []).filter(function (j) {
      if (f.bucket && (j.bucket_label || j.bucket || 'unclassified') !== f.bucket) return false;
      if (f.problems && !problemEngines(j).length) return false;
      if (!q) return true;
      return (j.model + ' ' + j.job + ' ' + (j.engines || []).join(' ')).toLowerCase().indexOf(q) !== -1;
    });
  }

  // Engine-by-engine NRMSE grid. The matrix has no diagonal (an engine vs
  // itself) — drawn as an em dash. Cell value is printed, not just colored.
  function heatmapHtml(job) {
    var engines = (job.engines && job.engines.length) ? job.engines : Object.keys(job.matrix || {});
    if (!engines.length) return '';
    var h = '<table class="cv-heat"><thead><tr><th></th>' +
      engines.map(function (e) { return '<th scope="col">' + esc(e) + '</th>'; }).join('') +
      '</tr></thead><tbody>';
    engines.forEach(function (a) {
      h += '<tr><th scope="row">' + esc(a) + '</th>';
      engines.forEach(function (b) {
        var v = a === b ? null : ((job.matrix || {})[a] || {})[b];
        var lvl = a === b ? -2 : heatLevel(v);
        var cls = lvl === -2 ? 'cv-self' : (lvl < 0 ? 'cv-na' : 'cv-h' + lvl);
        h += '<td class="' + cls + '" title="' + esc(a + ' vs ' + b) + '">' +
          (a === b ? '—' : fmt(v)) + '</td>';
      });
      h += '</tr>';
    });
    return h + '</tbody></table>';
  }

  function runsHtml(job) {
    var runs = job.runs || {}, names = Object.keys(runs).sort();
    if (!names.length) return '';
    return '<table class="cv-runs"><thead><tr><th>Engine</th><th>Status</th>' +
      '<th class="cv-num">Runtime (s)</th><th class="cv-num">Points</th><th>Note</th></tr></thead><tbody>' +
      names.map(function (e) {
        var r = runs[e] || {};
        var ok = r.status === 'ok';
        return '<tr><td>' + esc(e) + '</td>' +
          '<td><span class="cv-pill ' + (ok ? 'cv-ok' : 'cv-bad') + '">' + esc(r.status || 'unknown') + '</span></td>' +
          '<td class="cv-num">' + fmt(r.runtime_s) + '</td>' +
          '<td class="cv-num">' + (r.n_points == null ? '—' : esc(r.n_points)) + '</td>' +
          '<td class="cv-err">' + esc(r.error || '') + '</td></tr>';
      }).join('') + '</tbody></table>';
  }

  function jobDetailHtml(job) {
    return '<div class="cv-detail">' +
      '<div class="cv-detail-col"><div class="cv-sub">NRMSE between engines</div>' + heatmapHtml(job) +
      (job.closeness_bucket_label
        ? '<div class="cv-note">Closeness: ' + esc(job.closeness_bucket_label) + '</div>' : '') +
      '</div><div class="cv-detail-col"><div class="cv-sub">Engine runs</div>' + runsHtml(job) + '</div></div>';
  }

  function rowHtml(job, i) {
    var lvl = heatLevel(job.max_nrmse);
    var bad = problemEngines(job);
    return '<details class="cv-row" data-i="' + i + '"><summary>' +
      '<span class="cv-model">' + esc(job.model) + '</span>' +
      '<span class="cv-job">' + esc(job.job) + '</span>' +
      '<span class="cv-pill cv-b' + (lvl < 0 ? 'na' : lvl) + '">' + esc(job.bucket_label || job.bucket || 'unclassified') + '</span>' +
      '<span class="cv-num" title="worst pairwise NRMSE">' + fmt(job.max_nrmse) + '</span>' +
      '<span class="cv-worst">' + (job.worst_pair ? esc(job.worst_pair.join(' vs ')) : '') + '</span>' +
      (bad.length ? '<span class="cv-pill cv-bad" title="' + esc(bad.join(', ')) + ' did not run">' +
        bad.length + ' engine' + (bad.length === 1 ? '' : 's') + ' not run</span>' : '') +
      '</summary><div class="cv-body"></div></details>';
  }

  function summaryHtml(c, active) {
    return '<div class="cv-chips">' +
      '<button type="button" class="cv-chip' + (!active ? ' cv-on' : '') + '" data-bucket="">All <b>' + esc(c.n_jobs) + '</b></button>' +
      (c.buckets || []).map(function (b) {
        return '<button type="button" class="cv-chip' + (active === b.label ? ' cv-on' : '') +
          '" data-bucket="' + esc(b.label) + '">' + esc(b.label) + ' <b>' + esc(b.count) + '</b></button>';
      }).join('') + '</div>';
  }

  // Mounts the whole view into `mount`. Delegated listeners are attached once.
  function render(mount, c, opts) {
    opts = opts || {};
    var state = { q: '', bucket: '', problems: false, shown: PAGE, list: [] };
    mount.innerHTML =
      '<div class="cv">' +
      '<p class="muted cv-head">' + (opts.runLabel ? 'From run <code>' + esc(opts.runLabel) + '</code> — ' : '') +
        esc(c.n_models) + ' model' + (c.n_models === 1 ? '' : 's') + ', ' + esc(c.n_jobs) + ' comparison' +
        (c.n_jobs === 1 ? '' : 's') + ' across ' + esc((c.engines || []).join(', ')) +
        '. Worst disagreement first; NRMSE is the normalised RMSE between two engines’ trajectories (lower = closer).</p>' +
      '<div class="cv-sum"></div>' +
      '<div class="cv-tools"><input type="search" class="cv-q" placeholder="Filter by model, job or engine" aria-label="Filter comparisons">' +
      '<label class="cv-prob"><input type="checkbox" class="cv-p"> only where an engine did not run</label>' +
      '<span class="cv-count muted"></span></div>' +
      '<div class="cv-list"></div><button type="button" class="cv-more">Show more</button></div>';
    // Listeners live on the freshly built root, so a re-render (force reload)
    // replaces them with the markup instead of stacking them on `mount`.
    var root = mount.querySelector('.cv');
    var sum = root.querySelector('.cv-sum'), list = root.querySelector('.cv-list'),
        more = root.querySelector('.cv-more'), count = root.querySelector('.cv-count');

    function draw() {
      state.list = filterJobs(c.jobs, state);
      sum.innerHTML = summaryHtml(c, state.bucket);
      var n = Math.min(state.shown, state.list.length);
      list.innerHTML = state.list.slice(0, n).map(rowHtml).join('') ||
        '<p class="empty-message">No comparisons match this filter.</p>';
      count.textContent = 'Showing ' + n + ' of ' + state.list.length;
      more.style.display = n < state.list.length ? '' : 'none';
    }

    root.addEventListener('click', function (e) {
      var t = e.target;
      if (!t || !t.closest) return;
      var chip = t.closest('.cv-chip');
      if (chip) { state.bucket = chip.getAttribute('data-bucket') || ''; state.shown = PAGE; draw(); return; }
      if (t.closest('.cv-more')) { state.shown += PAGE; draw(); }
    });
    root.addEventListener('input', function (e) {
      if (e.target.classList && e.target.classList.contains('cv-q')) { state.q = e.target.value; state.shown = PAGE; draw(); }
    });
    root.addEventListener('change', function (e) {
      if (e.target.classList && e.target.classList.contains('cv-p')) { state.problems = !!e.target.checked; state.shown = PAGE; draw(); }
    });
    // Details are built on first open — a 1000-model corpus stays cheap.
    list.addEventListener('toggle', function (e) {
      var d = e.target;
      if (!d.open || d.getAttribute('data-built')) return;
      var job = state.list[+d.getAttribute('data-i')];
      if (!job) return;
      d.querySelector('.cv-body').innerHTML = jobDetailHtml(job);
      d.setAttribute('data-built', '1');
    }, true);
    draw();
  }

  var api = {
    render: render, filterJobs: filterJobs, heatLevel: heatLevel, heatmapHtml: heatmapHtml,
    runsHtml: runsHtml, rowHtml: rowHtml, problemEngines: problemEngines, esc: esc, PAGE: PAGE,
  };
  global.ComparisonView = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
