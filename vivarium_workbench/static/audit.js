// vivarium_workbench/static/audit.js
// Read-only Audit view: renders the L0-L5 study-reproducibility audit report
// (GET /api/audit → viva_superpowers.study_audit) as a summary header plus a
// compact per-study / per-investigation block listing each check as
// "✓/⚠/✗ L# check-name — detail", colored by status. Purely presentational and
// self-contained (CSP-safe, no external assets). Renders in BOTH live and
// published-snapshot modes; when the backend/snapshot file is absent it degrades
// to an "audit unavailable in this snapshot" notice rather than erroring.
//
// Wiring mirrors the SPA's other lazy-loaded pages: walkthrough.js `_switchPage`
// calls window._loadAudit() the first time the #audit page is activated.
(function (global) {
  'use strict';

  var STATUS = {
    pass: { glyph: '✓', color: '#0d9488', label: 'pass' },
    warn: { glyph: '⚠', color: '#d97706', label: 'warn' },
    fail: { glyph: '✗', color: '#e11d48', label: 'fail' },
  };

  // L0 (red) → L5 (green) reproducibility ramp; '—' (ungraded) = gray.
  var GRADE_COLORS = ['#ef4444', '#f97316', '#eab308', '#84cc16', '#22c55e', '#16a34a'];
  function _gradeColor(level) {
    return (typeof level === 'number' && level >= 0 && level <= 5) ? GRADE_COLORS[level] : '#94a3b8';
  }
  function _gradeBadge(grade) {
    if (!grade) return '';
    var title = grade.blocked_by
      ? 'reproducibility ' + grade.label + ' — blocked at ' + grade.blocked_by.level + ': ' + grade.blocked_by.name
      : 'reproducibility ' + grade.label + ' — fully rebuildable';
    return '<span title="' + _esc(title) + '" style="flex:none;font-weight:700;font-size:0.8em;' +
      'padding:1px 9px;border-radius:9999px;letter-spacing:0.03em;background:' + _gradeColor(grade.level) +
      ';color:#fff">' + _esc(grade.label) + '</span>';
  }

  function _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  function _container() {
    return document.getElementById('audit-render');
  }

  function _checkRowHtml(c) {
    var st = STATUS[c.status] || { glyph: '?', color: '#94a3b8', label: c.status };
    var detail = c.detail
      ? '<span style="color:#64748b"> — ' + _esc(c.detail) + '</span>'
      : '';
    return '<div style="display:flex;gap:8px;align-items:flex-start;margin:2px 0;font-size:0.85em;line-height:1.4">' +
      '<span style="flex:none;color:' + st.color + ';font-weight:700;width:1.1em;text-align:center">' + st.glyph + '</span>' +
      '<span style="flex:none;color:#94a3b8;font-variant-numeric:tabular-nums;width:1.8em">' + _esc(c.level) + '</span>' +
      '<span style="flex:1;color:#334155"><span style="font-weight:600">' + _esc(c.name) + '</span>' + detail + '</span>' +
      '</div>';
  }

  function _auditBlockHtml(kind, audit) {
    var grade = audit.grade;
    var structural = (audit.checks || []);
    var worst = STATUS[audit.worst] || { glyph: '?', color: '#94a3b8', label: audit.worst };

    // Body: the L0-L5 reproducibility ladder (studies carry grade.checks); an
    // investigation shows a member/blocked summary. Structural checks (when the
    // upstream audit is present) follow.
    var body;
    if (grade && grade.checks && grade.checks.length) {
      body = grade.checks.map(_checkRowHtml).join('');
    } else if (grade) {
      var members = (grade.n_members != null)
        ? _esc(grade.n_members) + ' member' + (grade.n_members === 1 ? '' : 's') + ' · ' : '';
      var status = grade.blocked_by
        ? 'blocked at ' + _esc(grade.blocked_by.level) + ' — ' + _esc(grade.blocked_by.name)
        : 'fully rebuildable (L5)';
      body = '<div style="font-size:0.83em;color:#64748b;margin:2px 0">' + members + status + '</div>';
    } else {
      body = '';
    }
    body += structural.map(_checkRowHtml).join('');

    // Header: grade badge (headline) + slug + kind; the structural worst-pill
    // only appears when there IS a structural audit to summarize.
    var worstPill = structural.length
      ? '<span style="flex:none;color:' + worst.color + ';font-weight:700">' + worst.glyph + '</span>' +
        '<span style="flex:none;font-size:0.8em;padding:0 8px;border-radius:9999px;background:' + worst.color + ';color:#fff">' + _esc(worst.label) + '</span>'
      : '';
    return '<div style="border:1px solid #e5e7eb;border-radius:6px;padding:10px 12px;margin:8px 0;background:#fff">' +
      '<div style="display:flex;gap:8px;align-items:center;margin-bottom:6px">' +
      _gradeBadge(grade) +
      '<span style="flex:1;font-weight:600;color:#1e293b">' + _esc(audit.slug) + '</span>' +
      '<span style="flex:none;font-size:0.7em;text-transform:uppercase;letter-spacing:0.05em;color:#94a3b8">' + _esc(kind) + '</span>' +
      worstPill +
      '</div>' + body + '</div>';
  }

  function _summaryHtml(report) {
    var summary = report.summary || {};
    var nStudies = summary.n_studies != null ? summary.n_studies : (report.studies || []).length;
    var nInv = summary.n_investigations != null ? summary.n_investigations : (report.investigations || []).length;
    var nHard = summary.hard_failures || 0;
    var hardColor = nHard > 0 ? STATUS.fail.color : STATUS.pass.color;

    // L0-L5 grade histogram (highest → lowest), colored by level.
    var dist = summary.grade_distribution || {};
    var chips = ['L5', 'L4', 'L3', 'L2', 'L1', 'L0', '—'].filter(function (k) { return dist[k]; })
      .map(function (k) {
        var lvl = k === '—' ? -1 : parseInt(k.slice(1), 10);
        return '<span title="' + _esc(k) + ': ' + dist[k] + '" style="font-size:0.8em;font-weight:600;padding:1px 8px;' +
          'border-radius:9999px;background:' + _gradeColor(lvl) + ';color:#fff">' + _esc(k) + ' ' + dist[k] + '</span>';
      }).join('');
    var histo = chips
      ? '<div style="display:flex;gap:5px;align-items:center;flex-wrap:wrap"><span style="color:#94a3b8;font-size:0.78em;' +
        'text-transform:uppercase;letter-spacing:0.05em">grade</span>' + chips + '</div>'
      : '';

    return '<div style="display:flex;gap:24px;align-items:baseline;flex-wrap:wrap;padding:12px 14px;' +
      'border:1px solid #e5e7eb;border-radius:6px;background:#f8fafc;margin-bottom:12px">' +
      '<div style="font-weight:700;font-size:1.05em;color:#1e293b">Reproducibility audit</div>' +
      '<div style="color:#475569"><strong>' + nStudies + '</strong> studies</div>' +
      '<div style="color:#475569"><strong>' + nInv + '</strong> investigations</div>' +
      (nHard ? '<div style="color:' + hardColor + '"><strong>' + nHard + '</strong> hard failure' + (nHard === 1 ? '' : 's') + '</div>' : '') +
      histo +
      '</div>';
  }

  function _notice(html) {
    return '<div style="padding:16px;color:#64748b;font-size:0.9em">' + html + '</div>';
  }

  function _render(report) {
    var el = _container();
    if (!el) return;
    var studies = report.studies || [];
    var investigations = report.investigations || [];
    var parts = [_summaryHtml(report)];
    if (report.error) {
      parts.push('<div style="padding:8px 12px;margin-bottom:10px;border-radius:6px;background:#fef3c7;' +
        'color:#92400e;font-size:0.85em">Audit degraded: ' + _esc(report.error) + '</div>');
    }
    if (!studies.length && !investigations.length) {
      parts.push(_notice('No studies or investigations to audit in this workspace.'));
    } else {
      studies.forEach(function (a) { parts.push(_auditBlockHtml('study', a)); });
      investigations.forEach(function (a) { parts.push(_auditBlockHtml('investigation', a)); });
    }
    el.innerHTML = parts.join('');
  }

  // Lazy render on first (and subsequent) activation of the #audit page.
  function loadAudit() {
    var el = _container();
    if (!el) return;
    el.innerHTML = global.ProgressTrack ? global.ProgressTrack.loadingHtml('Loading audit…') : _notice('Loading audit…');
    var ds = global.DataSource;
    if (!ds || typeof ds.getAudit !== 'function') {
      el.innerHTML = _notice('Audit unavailable — data source not loaded.');
      return;
    }
    ds.getAudit().then(function (report) {
      _render(report || { studies: [], investigations: [] });
    }).catch(function () {
      // Snapshot bundles without api/audit.json (or a missing live backend)
      // 404 → _get throws. Degrade gracefully rather than surfacing an error.
      var snapshot = document.body && document.body.classList.contains('snapshot');
      el.innerHTML = _notice(snapshot
        ? 'Audit unavailable in this snapshot.'
        : 'Audit unavailable — the backend did not return a report.');
    });
  }

  global._loadAudit = loadAudit;
  global.AuditView = { render: loadAudit, _render: _render, _auditBlockHtml: _auditBlockHtml };
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { _render: _render, _auditBlockHtml: _auditBlockHtml };
  }
})(typeof window !== 'undefined' ? window : globalThis);
