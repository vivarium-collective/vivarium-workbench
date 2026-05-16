// vivarium_dashboard/static/investigations.js
(function () {
  var state = { plans: [], activeSlug: null };

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }

  function loadInvestigations() {
    return fetch('/api/plans').then(function (r) { return r.json(); }).then(function (plans) {
      state.plans = plans || [];
      renderList();
    }).catch(function (e) {
      console.error('Failed to load investigations:', e);
    });
  }

  function renderList() {
    var ul = document.getElementById('investigations-list');
    if (!ul) return;
    ul.innerHTML = '';
    if (!state.plans.length) {
      var li = document.createElement('li');
      li.className = 'placeholder';
      li.textContent = 'No investigations yet. Click "+ New investigation" to start one.';
      ul.appendChild(li);
      // Show list, hide detail.
      ul.hidden = false;
      var detail = document.getElementById('investigation-detail');
      if (detail) detail.hidden = true;
      return;
    }
    state.plans.forEach(function (p) {
      var li = document.createElement('li');
      li.className = 'investigation-card';
      li.innerHTML =
        '<a class="investigation-title">' + escapeHtml(p.name) + '</a>' +
        ' <span class="badge status-' + escapeHtml(p.status) + '">' + escapeHtml(p.status) + '</span>' +
        ' <span class="muted">' + escapeHtml(p.n_studies) + ' studies</span>' +
        '<p class="muted">' + escapeHtml((p.objective || '').slice(0, 200)) + '</p>';
      li.querySelector('.investigation-title').addEventListener('click', function () {
        openInvestigation(p.slug);
      });
      ul.appendChild(li);
    });
    var detail = document.getElementById('investigation-detail');
    if (detail) detail.hidden = true;
    ul.hidden = false;
  }

  function openInvestigation(slug) {
    state.activeSlug = slug;
    return fetch('/api/plan/' + encodeURIComponent(slug)).then(function (r) {
      if (!r.ok) { alert('Failed to load investigation: ' + r.status); throw r; }
      return r.json();
    }).then(function (plan) {
      var list = document.getElementById('investigations-list');
      var detail = document.getElementById('investigation-detail');
      if (list) list.hidden = true;
      if (detail) detail.hidden = false;

      var title = document.getElementById('investigation-title');
      if (title) title.textContent = plan.name || '';
      var obj = document.getElementById('investigation-objective');
      if (obj) obj.textContent = plan.objective || '';
      var hyp = document.getElementById('investigation-hypothesis');
      if (hyp) hyp.textContent = plan.hypothesis || '';

      var completes = (plan.studies || []).filter(function (s) {
        return s.derived_status === 'complete';
      }).length;
      var statusEl = document.getElementById('investigation-status');
      if (statusEl) statusEl.textContent =
        'Status: ' + (plan.status || 'planned') + ' (' + completes + '/' + (plan.studies || []).length + ' studies complete)';

      var refs = document.getElementById('investigation-references');
      if (refs) {
        refs.innerHTML = '';
        (plan.references || []).forEach(function (r) {
          var li = document.createElement('li');
          li.innerHTML = '📄 <a href="/' + escapeHtml(r.file) + '">' + escapeHtml(r.label || r.file) + '</a>';
          refs.appendChild(li);
        });
      }

      var cards = document.getElementById('investigation-study-cards');
      if (cards) {
        cards.innerHTML = '';
        (plan.studies || []).forEach(function (s, i) {
          var li = document.createElement('li');
          var icon = ({complete: '✅', 'in-progress': '🔄', blocked: '⏸', planned: '⏳'})[s.derived_status] || '•';
          var gateNote = s.gate ? '<span class="muted">(gate: ' + escapeHtml(s.gate) + ')</span>' : '';
          li.className = 'study-card study-' + escapeHtml(s.derived_status);
          li.innerHTML =
            '<span class="study-icon">' + icon + '</span>' +
            ' <a class="study-link">' + (i + 1) + '. ' + escapeHtml(s.study) + '</a>' +
            ' <span class="study-status">' + escapeHtml(s.derived_status) + '</span> ' +
            gateNote;
          li.querySelector('.study-link').addEventListener('click', function () {
            // Navigate to the study detail.
            location.hash = '#studies/' + encodeURIComponent(s.study);
          });
          cards.appendChild(li);
        });
      }
    });
  }

  function backToList() {
    var list = document.getElementById('investigations-list');
    var detail = document.getElementById('investigation-detail');
    if (list) list.hidden = false;
    if (detail) detail.hidden = true;
    state.activeSlug = null;
  }

  function openCreateDialog() {
    var dialog = document.getElementById('new-investigation-dialog');
    if (!dialog) return;
    if (typeof dialog.showModal === 'function') {
      dialog.showModal();
    } else {
      dialog.setAttribute('open', '');
    }
    var submit = document.getElementById('new-inv-submit');
    if (!submit) return;
    // Attach a one-shot handler.
    submit.addEventListener('click', function () {
      var name = document.getElementById('new-inv-name').value.trim();
      var objective = document.getElementById('new-inv-objective').value;
      var hypothesis = document.getElementById('new-inv-hypothesis').value;
      var studiesStr = document.getElementById('new-inv-studies').value;
      var studies = studiesStr.split(',').map(function (s) { return s.trim(); })
        .filter(function (s) { return s.length; })
        .map(function (s) { return { study: s }; });
      if (!name) return;
      fetch('/api/plan-create', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name: name, objective: objective, hypothesis: hypothesis, studies: studies}),
      }).then(function (r) {
        if (!r.ok) return r.json().then(function (err) {
          alert('Failed: ' + (err.error || r.status));
        });
        loadInvestigations();
      });
    }, {once: true});
  }

  // Wire button event listeners after DOM is ready.
  document.addEventListener('DOMContentLoaded', function () {
    var btn = document.getElementById('new-investigation-btn');
    if (btn) btn.addEventListener('click', openCreateDialog);
    var back = document.getElementById('investigation-back');
    if (back) back.addEventListener('click', backToList);
  });

  // Expose for the page-router (walkthrough.js's _switchPage).
  window.loadInvestigations = loadInvestigations;
})();
