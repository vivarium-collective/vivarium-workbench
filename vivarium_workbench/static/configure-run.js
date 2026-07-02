// Shared "Configure & Run" widget (SP-C). Native, embeddable in the Composite
// Explorer, Composites list, and study Runs tab. mount(el, {composite, target, study}).
(function () {
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
    return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]; }); }

  // Build a config form from composite-resolve's parameters:
  // {name: {type:"string"|"float"|"int"|"bool"|"integer"|"boolean", default, description}}. May be null/{}.
  // Accepts both legacy aliases (int/bool) and canonical vocabulary (integer/boolean).
  function _buildConfigForm(parameters) {
    var params = parameters || {};
    var names = Object.keys(params);
    if (!names.length) return '<p class="muted">This composite has no configurable parameters.</p>';
    return names.map(function (name) {
      var p = params[name] || {};
      var t = p.type, d = p.default, desc = p.description || "";
      var inputId = "cfg-" + name;
      var field;
      if (t === "bool" || t === "boolean") {
        field = '<input type="checkbox" id="' + esc(inputId) + '" data-param="' + esc(name) +
          '" data-type="bool"' + (d ? " checked" : "") + ">";
      } else if (t === "float" || t === "int" || t === "integer") {
        var normT = (t === "integer") ? "int" : t;
        field = '<input type="number" id="' + esc(inputId) + '" data-param="' + esc(name) +
          '" data-type="' + esc(normT) + '" value="' + esc(d) + '"' + (normT === "float" ? ' step="any"' : "") + ">";
      } else {
        // Object/list-typed params (e.g. config_overrides) have a structured
        // default — don't render "[object Object]"; show an empty JSON field.
        var isObj = (d != null && typeof d === "object");
        var sval = isObj ? "" : (d == null ? "" : d);
        var ph = isObj ? ' placeholder="(advanced — JSON object)"' : '';
        field = '<input type="text" id="' + esc(inputId) + '" data-param="' + esc(name) +
          '" data-type="string" value="' + esc(sval) + '"' + ph + ">";
      }
      return '<label class="cfg-row" title="' + esc(desc) + '"><span class="cfg-name">' +
        esc(name) + "</span>" + field + "</label>";
    }).join("");
  }

  // Read the form back into a type-cast overrides dict (only changed-from-default need not be
  // tracked — send all; the runner overlays them).
  function _collectOverrides(formEl, parameters) {
    var out = {};
    var inputs = formEl.querySelectorAll("[data-param]");
    for (var i = 0; i < inputs.length; i++) {
      var el = inputs[i], name = el.getAttribute("data-param"), type = el.getAttribute("data-type");
      if (type === "bool" || type === "boolean") out[name] = !!el.checked;
      else if (type === "float") out[name] = parseFloat(el.value);
      else if (type === "int" || type === "integer") out[name] = parseInt(el.value, 10);
      else out[name] = el.value;
    }
    return out;
  }

  var ctxState = {};
  function mount(el, ctx) {
    ctxState = ctx || {};
    el.innerHTML = '<div class="cfg-run"><div class="cfg-loading">Loading composite…</div></div>';
    var id = ctxState.composite;
    if (!id) { el.querySelector(".cfg-run").innerHTML = '<p class="muted">Pick a composite to configure.</p>'; return; }
    // Read-only (published) bundle has no live backend — resolve the composite's
    // parameters from the static snapshot and render a DISABLED preview, instead
    // of hitting the live /api/composite-resolve (which 404s in a static bundle).
    var isSnapshot = document.body.classList.contains("snapshot")
      || !!(window.__DASH_CONFIG__ && window.__DASH_CONFIG__.mode === "snapshot");
    var p = (isSnapshot && window.DataSource && window.DataSource.loadCompositeResolve)
      ? window.DataSource.loadCompositeResolve(id)
      : fetch("/api/composite-resolve?id=" + encodeURIComponent(id) + "&overrides=%7B%7D")
          .then(function (r) { return r.text().then(function (t) { try { return JSON.parse(t); } catch (e) { return { error: "HTTP " + r.status }; } }); });
    p.then(function (d) {
        var box = el.querySelector(".cfg-run");
        if (!d || d.error || d.unresolved) {
          box.innerHTML = isSnapshot
            ? '<p class="muted">Configure &amp; Run is a live action — open this composite on a running dashboard to set parameters and run it. (No preview is available for this composite in the read-only bundle.)</p>'
            : '<div class="inv-run-err">' + esc((d && (d.error || "composite not found")) ) + "</div>";
          return;
        }
        box.innerHTML =
          '<h4>' + esc(d.name || id) + '</h4>' +
          '<form class="cfg-form">' + _buildConfigForm(d.parameters) + '</form>' +
          '<label class="cfg-row"><span class="cfg-name">steps</span>' +
          '<input type="number" class="cfg-steps" value="' + esc(d.default_n_steps != null ? d.default_n_steps : 5) + '"></label>' +
          (isSnapshot
            ? '<div class="cfg-actions"><button type="button" class="btn-mini" disabled title="Run on a live dashboard">▶ Run</button>' +
              ' <span class="muted">read-only preview — open this composite on a live dashboard to configure &amp; run</span></div>'
            : '<div class="cfg-actions"><button type="button" class="btn-mini cfg-run-btn">▶ Run</button></div>' +
              '<div class="cfg-status" hidden></div>');
        if (isSnapshot) {
          // make it a clear, non-interactive preview
          var inputs = box.querySelectorAll("input, select, textarea");
          for (var i = 0; i < inputs.length; i++) inputs[i].disabled = true;
        } else {
          ConfigureRun._wireRun(el, d);   // defined in Task 5
        }
      })
      .catch(function(e) { var box = el.querySelector(".cfg-run"); if (box) box.innerHTML = '<div class="inv-run-err">Network error: ' + esc(String(e)) + "</div>"; });
  }

  window.ConfigureRun = {
    mount: mount, _buildConfigForm: _buildConfigForm, _collectOverrides: _collectOverrides,
    _ctx: function () { return ctxState; },
  };

  function _post(url, body) {
    return fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then(function (r) { return r.text().then(function (t) { var j; try { j = JSON.parse(t); } catch (e) { j = { error: "HTTP " + r.status }; } return { status: r.status, body: j }; }); });
  }
  function _status(el, html) { var s = el.querySelector(".cfg-status"); if (s) { s.hidden = false; s.innerHTML = html; } }

  function _poll(el, statusUrl) {
    var tries = 0;
    var ticks = 0;
    var MAX_TICKS = 240;
    function _reenableBtn() { var b = el.querySelector(".cfg-run-btn"); if (b) b.disabled = false; }
    function tick() {
      if (ticks >= MAX_TICKS) {
        _status(el, '<span class="inv-run-err">still running — check the Simulations DB</span>');
        _reenableBtn();
        return;
      }
      ticks += 1;
      fetch(statusUrl).then(function (r) { return r.json(); }).then(function (d) {
        var phase = String((d && (d.status || d.phase)) || "").toLowerCase();
        if (phase === "completed" || phase === "done") { _onDone(el); return; }
        if (phase === "failed" || phase === "error" || phase === "orphaned" || phase === "cancelled") {
          _status(el, '<span class="inv-run-err">✗ Failed (' + esc(phase) + ')</span>');
          _reenableBtn();
          return;
        }
        _status(el, "Running… (" + esc(phase || "queued") + ")");
        setTimeout(tick, 2500);
      }).catch(function () { tries += 1; if (tries < 4) setTimeout(tick, 3000); else { _status(el, '<span class="inv-run-err">poll error</span>'); _reenableBtn(); } });
    }
    tick();
  }

  function _onDone(el) {
    var ctx = ctxState, run = el._lastRunId || "";
    var actions = '<button type="button" class="btn-mini cfg-view-btn">View results</button>';
    if (ctx.target !== "study") actions += ' <button type="button" class="btn-mini cfg-savevar-btn">Save as variant</button>';
    actions += ' <button type="button" class="btn-mini cfg-del-btn">Delete</button>';
    _status(el, '<strong>✓ Done</strong> <code>' + esc(run) + '</code> ' + actions);
    var sv = el.querySelector(".cfg-savevar-btn"); if (sv) sv.onclick = function () { _saveAsVariant(el); };
    var dl = el.querySelector(".cfg-del-btn"); if (dl) dl.onclick = function () { _deleteRun(el); };
  }

  function _wireRun(el, resolved) {
    var btn = el.querySelector(".cfg-run-btn");
    btn.onclick = function () {
      var overrides = _collectOverrides(el.querySelector(".cfg-form"), resolved.parameters);
      var steps = parseInt(el.querySelector(".cfg-steps").value, 10) || 5;
      btn.disabled = true; _status(el, "Starting…");
      if (ctxState.target === "study") _runStudy(el, overrides, steps);
      else _runAdhoc(el, resolved.id || ctxState.composite, overrides, steps);
    };
  }

  function _runAdhoc(el, id, overrides, steps) {
    function _reenableBtn() { var b = el.querySelector(".cfg-run-btn"); if (b) b.disabled = false; }
    _post("/api/composite-test-run", { id: id, overrides: overrides, steps: steps }).then(function (res) {
      if (res.status !== 202 || !res.body.run_id) { _status(el, '<span class="inv-run-err">' + esc((res.body && res.body.error) || res.status) + '</span>'); _reenableBtn(); return; }
      el._lastRunId = res.body.run_id;
      _poll(el, "/api/composite-run/" + encodeURIComponent(res.body.run_id) + "/status");
    }).catch(function (e) { _status(el, '<span class="inv-run-err">' + esc(String(e)) + '</span>'); _reenableBtn(); });
  }

  function _runStudy(el, overrides, steps) {
    // Study context: the study's baseline composite + this config = the variant run.
    // (Local pipeline runs sync; reload the Runs tab on done.)
    function _reenableBtn() { var b = el.querySelector(".cfg-run-btn"); if (b) b.disabled = false; }
    _post("/api/study-run-baseline", { study: ctxState.study, overrides: overrides, steps: steps }).then(function (res) {
      if (res.status !== 200) { _status(el, '<span class="inv-run-err">' + esc((res.body && res.body.error) || res.status) + '</span>'); _reenableBtn(); return; }
      _status(el, '<strong>✓ Run complete</strong> — refresh the Runs tab.');
    }).catch(function (e) { _status(el, '<span class="inv-run-err">' + esc(String(e)) + '</span>'); _reenableBtn(); });
  }

  function _saveAsVariant(el) {
    var name = window.prompt("Variant name:"); if (!name) return;
    var study = ctxState.study || window.prompt("Save into which study (slug)?"); if (!study) return;
    _post("/api/save-run-as-variant", { run_id: el._lastRunId, study: study, variant_name: name }).then(function (res) {
      _status(el, res.status === 200 ? '<strong>✓ Saved as variant</strong> ' + esc(name) : '<span class="inv-run-err">' + esc((res.body && res.body.error) || res.status) + '</span>');
    }).catch(function (e) { _status(el, '<span class="inv-run-err">' + esc(String(e)) + '</span>'); });
  }

  function _deleteRun(el) {
    if (!window.confirm("Delete this run?")) return;
    var db = ctxState.dbPath || "";
    _post("/api/run-delete", { run_id: el._lastRunId, db_path: db }).then(function (res) {
      _status(el, res.status === 200 ? "Deleted." : '<span class="inv-run-err">' + esc((res.body && res.body.error) || res.status) + '</span>');
    }).catch(function (e) { _status(el, '<span class="inv-run-err">' + esc(String(e)) + '</span>'); });
  }

  // expose for _wireRun call in mount() + tests
  window.ConfigureRun._wireRun = _wireRun;
  window.ConfigureRun._runAdhoc = _runAdhoc;
  window.ConfigureRun._runStudy = _runStudy;
  window.ConfigureRun._saveAsVariant = _saveAsVariant;
  window.ConfigureRun._deleteRun = _deleteRun;
})();
